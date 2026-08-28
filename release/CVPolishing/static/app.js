// -*- coding: utf-8 -*-
// JD 匹配简历优化器 前端逻辑：输入 -> SSE 流式优化 -> 结果展示 / 复制 / 下载

const $ = (sel) => document.querySelector(sel);

const ui = {
  jd: $("#jd"),
  resume: $("#resume"),
  version: $("#version"),
  searchToggle: $("#searchToggle"),
  run: $("#run"),
  status: $("#status"),
  resultResume: $("#resultResume"),
  resultNotes: $("#resultNotes"),
  resultRefs: $("#resultRefs"),
  progress: $("#progress"),
  copyResume: $("#copyResume"),
  downloadResume: $("#downloadResume"),
  saveResume: $("#saveResume"),
  gotoInterview: $("#gotoInterview"),
  scoreOpt: $("#scoreOpt"),
  scoreBoth: $("#scoreBoth"),
  scoreOrig: $("#scoreOrig"),
  scoreCompare: $("#scoreCompare"),
  scoreResult: $("#scoreResult"),
  jdImage: $("#jdImage"),
  ocrBtn: $("#ocrBtn"),
  ocrStatus: $("#ocrStatus"),
  runAuto: $("#runAuto"),
  targetScore: $("#targetScore"),
  traceView: $("#traceView"),
  traceSummary: $("#traceSummary"),
  busView: $("#busView"),
  refreshTrace: $("#refreshTrace"),
  refreshBus: $("#refreshBus"),
  skillGrid: $("#skillGrid"),
  skillSearch: $("#skillSearch"),
  skillEmpty: $("#skillEmpty"),
  skillModal: $("#skillModal"),
  skillModalBody: $("#skillModalBody"),
};

// 简历与 JD 按登录用户存储在服务端（/api/resume），保证用户之间数据隔离。
// resumeStore 是本页内存副本，每次持久化整体写回。
const resumeStore = { original: "", optimized: "", jd: "" };

// 用 sendBeacon 提交：即便紧接着发生页面跳转，请求也能可靠送达服务端。
function persistResume(patch) {
  Object.assign(resumeStore, patch);
  const blob = new Blob([JSON.stringify(resumeStore)], { type: "application/json" });
  navigator.sendBeacon("/api/resume", blob);
}

// 载入当前登录用户已保存的 JD / 简历到内存副本
async function loadResumeStore() {
  const resp = await fetch("/api/resume");
  if (!resp.ok) return;
  const r = await resp.json();
  resumeStore.original = r.original || "";
  resumeStore.optimized = r.optimized || "";
  resumeStore.jd = r.jd || "";
}

// 简历优化进入时的默认示例（即测试简历）。如需换成你自己的测试简历，直接替换此常量内容即可。
const DEFAULT_RESUME = `张三 | 高级产品经理
电话：138-0000-0000  邮箱：zhangsan@example.com  城市：上海

【个人简介】
8 年互联网产品经验，专注 B 端SaaS与增长方向，主导过从 0 到 1 的产品孵化与商业化落地。

【工作经历】
2020.03 - 至今  XX科技有限公司  高级产品经理
- 负责核心数据平台从 0 到 1 搭建，上线 6 个月接入 30+ 业务线，日均调用量超 200 万次。
- 搭建增长实验体系，通过 AB 测试将新用户次日留存提升 12%。

2016.07 - 2020.02  YY网络  产品经理
- 主导会员体系重构，付费转化率提升 8%，年营收增加约 1500 万。

【项目经历】
数据中台项目：统筹需求、设计、研发三方，交付周期缩短 40%。

【教育背景】
2012.09 - 2016.06  XX大学  信息管理与信息系统  本科`;

let running = false;

// ---------- 标签页 ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tabpanel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "skill") { skillFilter === "online" ? loadOnlineSkills() : loadSkills(); }
  });
});

function logProgress(msg) {
  const line = document.createElement("div");
  line.className = "line";
  line.textContent = msg;
  ui.progress.appendChild(line);
  ui.progress.scrollTop = ui.progress.scrollHeight;
}

function resetOutput() {
  ui.resultResume.value = "";
  ui.resultNotes.innerHTML = '<div class="empty">暂无优化说明</div>';
  ui.resultRefs.innerHTML = '<li class="empty">暂无参考来源</li>';
  ui.progress.innerHTML = "";
  // 新一次运行：清空折叠面板展开状态
  openSections.clear();
  openBuses.clear();
}

// ---------- 主流程 ----------
async function runOptimize() {
  if (running) return;
  const jd = ui.jd.value.trim();
  const resume = ui.resume.value.trim();
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD"; return; }
  if (!resume) { ui.status.textContent = "请先填写简历原文"; return; }

  const myGen = ++optGen; // 令牌：本次请求的唯一标识
  running = true;
  ui.run.disabled = true;
  ui.status.textContent = "优化中…";
  resetOutput();
  // 记住 JD 与原始简历，供面试页复用
  persistResume({ jd, original: resume });

  const buf = { resume: "", notes: "" };
  let refs = [];

  try {
    const resp = await fetch("/api/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd, resume, version: ui.version.value, enable_search: ui.searchToggle.checked,
      }),
    });
    if (!resp.ok) {
      if (myGen !== optGen) return; // 已被新请求取代，不写 UI
      // 普通用户免费优化次数用尽等会员专属拦截
      let payload = {};
      try { payload = await resp.json(); } catch (e) {}
      if (payload.need_vip) {
        ui.status.textContent = payload.error || "免费优化次数已用完，请开通 VIP。";
        const go = confirm((payload.error || "免费优化次数已用完。") + "\n点击「确定」前往会员中心开通 VIP。");
        if (go) window.location.href = "/vip";
      } else {
        ui.status.textContent = payload.error || ("优化失败：" + resp.status);
      }
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      const parts = acc.split("\n\n");
      acc = parts.pop();
      for (const part of parts) {
        const line = part.replace(/^data:\s*/, "").trim();
        if (!line || line === "[DONE]") continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        // 令牌校验：被新请求取代后，旧流事件一律不写 UI（但后台读取仍继续，不中断）
        if (myGen !== optGen) continue;
        handleEvent(ev, buf, () => { refs = buf._refs || refs; });
        if (ev.type === "result") {
          refs = ev.references || [];
        }
      }
    }
    if (myGen !== optGen) return; // 流结束前已被新请求取代，不落定旧结果
    renderResult(buf, refs);
    ui.status.textContent = "优化完成，可直接复制或下载投递。";
  } catch (e) {
    if (myGen !== optGen) return;
    ui.status.textContent = "请求失败：" + e.message;
    logProgress("请求失败：" + e.message);
  } finally {
    // 仅当本次请求仍是当前令牌时，才释放运行锁（避免旧请求把新请求锁释放）
    if (myGen === optGen) {
      running = false;
      ui.run.disabled = false;
    }
  }
}

function handleEvent(ev, buf) {
  if (ev.type === "progress") {
    logProgress(ev.message);
  } else if (ev.type === "delta") {
    // 实时把增量追加到简历展示区（最终会按分隔标记整理）
    buf.resume += ev.text;
    ui.resultResume.value = buf.resume;
    ui.resultResume.scrollTop = ui.resultResume.scrollHeight;
  } else if (ev.type === "error") {
    ui.status.textContent = ev.message;
    logProgress(ev.message);
  } else if (ev.type === "result") {
    buf.resume = ev.resume || buf.resume;
    buf.notes = ev.notes || "";
  } else if (ev.type === "score" && ev.phase === "pre") {
    // 优化前基线评分：先诊断再优化，存入 buf 供结果区展示
    buf.preScore = {
      total: ev.total,
      dimensions: ev.dimensions || [],
      summary: ev.summary || "",
    };
  }
}

function renderResult(buf, refs) {
  ui.resultResume.value = buf.resume || "";
  // 优化结果自动存到当前用户名下，进入面试页可直接复用
  persistResume({ optimized: buf.resume || "" });
  // 优化说明渲染为列表
  const notes = (buf.notes || "").split("\n").map((s) => s.trim()).filter(Boolean);
  const notesHtml = notes.length
    ? "<ul>" + notes.map((n) => `<li>${escapeHtml(n.replace(/^[-•]\s*/, ""))}</li>`).join("") + "</ul>"
    : '<div class="empty">本次未返回结构化优化说明</div>';
  ui.resultNotes.innerHTML = notesHtml;
  // 参考来源
  if (refs && refs.length) {
    ui.resultRefs.innerHTML = refs.map((t) => `<li>${escapeHtml(t)}</li>`).join("");
  }
  // 原始简历评分卡（诊断先行，独立展示在开始优化结果区）
  // 幂等：避免多次 renderResult（如切换 Tab 触发重渲染）重复追加同一卡片
  if (buf.preScore && (buf.preScore.total || (buf.preScore.dimensions && buf.preScore.dimensions.length))) {
    const container = ui.resultRefs.parentElement;
    if (!container.querySelector(".prescore-block")) {
      container.insertAdjacentHTML(
        "beforeend",
        '<div class="prescore-block">' +
        '<div class="prescore-title">原始简历评分（优化前诊断）</div>' +
        scoreCardHtml(buf.preScore) +
        '</div>'
      );
    }
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 复制 / 下载 ----------
ui.copyResume.addEventListener("click", () => {
  const text = ui.resultResume.value;
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    ui.status.textContent = "已复制到剪贴板。";
  });
});

ui.downloadResume.addEventListener("click", async () => {
  const text = ui.resultResume.value;
  if (!text) return;
  // PDF 导出为会员专属功能
  if (!(window.CVP_USER && window.CVP_USER.vip)) {
    ui.status.textContent = "下载 PDF 为会员专属功能，请前往「会员中心」开通 VIP 后使用。";
    const go = confirm("导出 PDF 简历（标准简历模板）为会员专属功能。\n点击「确定」前往会员中心开通 VIP。");
    if (go) window.location.href = "/vip";
    return;
  }
  try {
    const resp = await fetch("/api/resume/export-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume: text, name: "标准简历" }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert("PDF 导出失败：" + (err.error || resp.status));
      return;
    }
    const blob = await resp.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "标准简历.pdf";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert("PDF 导出失败：" + e.message);
  }
});

// ---------- 保存 / 跳转面试页 ----------
function saveContext() {
  // 优先携带「优化后简历」，未优化时退回原始简历，确保面试页拿到最有价值的数据
  const resume = ui.resultResume.value.trim() || ui.resume.value.trim();
  persistResume({
    original: ui.resume.value.trim(),
    optimized: ui.resultResume.value.trim(),
    jd: ui.jd.value.trim(),
  });
  return resume;
}

ui.saveResume.addEventListener("click", () => {
  if (!ui.resultResume.value.trim()) {
    ui.status.textContent = "暂无优化结果可保存。";
    return;
  }
  saveContext();
  ui.status.textContent = "已保存优化结果，可在「面试模拟」页加载复用。";
});

ui.gotoInterview.addEventListener("click", () => {
  saveContext();
  window.location.href = "/interview";
});

// ---------- 简历评分 ----------
async function runScore(mode) {
  const jd = ui.jd.value.trim();
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD 再评分"; return; }
  const optimized = ui.resultResume.value.trim();
  let before, after;
  if (mode === "opt") {
    before = optimized;
    if (!before) { ui.status.textContent = "请先生成优化后简历再评分"; return; }
    after = null;
  } else if (mode === "both") {
    before = ui.resume.value.trim();
    after = optimized;
    if (!before) { ui.status.textContent = "请填写原始简历"; return; }
    if (!after) { ui.status.textContent = "请先生成优化后简历再做对比"; return; }
  } else { // orig
    before = ui.resume.value.trim();
    if (!before) { ui.status.textContent = "请填写原始简历"; return; }
    after = null;
  }

  ui.status.textContent = "评分中…";
  ui.scoreResult.innerHTML = '<div class="score-loading">AI 正在打分…</div>';

  let result = null;
  try {
    const resp = await fetch("/api/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd, before, after }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      const parts = acc.split("\n\n");
      acc = parts.pop();
      for (const part of parts) {
        const line = part.replace(/^data:\s*/, "").trim();
        if (!line || line === "[DONE]") continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        if (ev.type === "progress") {
          ui.scoreResult.innerHTML = `<div class="score-loading">${escapeHtml(ev.message)}</div>`;
        } else if (ev.type === "error") {
          ui.status.textContent = ev.message;
          ui.scoreResult.innerHTML = `<div class="empty">${escapeHtml(ev.message)}</div>`;
          return;
        } else if (ev.type === "result") {
          result = ev;
        }
      }
    }
    if (result) {
      renderScore(result);
      ui.status.textContent = "评分完成。";
    }
  } catch (e) {
    ui.status.textContent = "请求失败：" + e.message;
    ui.scoreResult.innerHTML = `<div class="empty">请求失败：${escapeHtml(e.message)}</div>`;
  }
}

function scoreCardHtml(one, title) {
  if (!one || !one.dimensions || !one.dimensions.length) {
    return `<div class="score-card"><div class="score-card-title">${escapeHtml(title)}</div><div class="empty">无评分数据</div></div>`;
  }
  const bars = one.dimensions.map((d) => {
    const pct = d.score;
    return `<div class="score-dim">
      <div class="score-dim-head"><span>${escapeHtml(d.name)}</span><span class="score-num">${d.score}</span></div>
      <div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div>
      <div class="score-comment">${escapeHtml(d.comment || "")}</div>
    </div>`;
  }).join("");

  let badge = "";
  if (one.total >= 85) badge = "优秀";
  else if (one.total >= 70) badge = "良好";
  else if (one.total >= 55) badge = "及格";
  else badge = "待提升";

  return `<div class="score-card">
    <div class="score-card-head">
      <span class="score-card-title">${escapeHtml(title)}</span>
      <span class="score-total">${one.total}<small>/100</small></span>
      <span class="score-badge">${badge}</span>
    </div>
    ${bars}
    <div class="score-summary">${escapeHtml(one.summary || "")}</div>
  </div>`;
}

function renderScore(result) {
  if (result.mode === "compare") {
    const diff = (result.after ? result.after.total : 0) - (result.before ? result.before.total : 0);
    const diffHtml = `<div class="score-compare-bar">
      <span>优化前 ${result.before.total} → 优化后 ${result.after.total}</span>
      <span class="score-diff ${diff >= 0 ? "up" : "down"}">${diff >= 0 ? "+" : ""}${diff} 分</span>
    </div>
    ${result.improvement ? `<div class="score-improve">${escapeHtml(result.improvement)}</div>` : ""}`;
    ui.scoreResult.innerHTML = diffHtml + scoreCardHtml(result.before, "优化前") + scoreCardHtml(result.after, "优化后");
  } else {
    ui.scoreResult.innerHTML = scoreCardHtml(result.before, "评分结果");
  }
}

ui.scoreOpt.addEventListener("click", () => runScore("opt"));
ui.scoreBoth.addEventListener("click", () => runScore("both"));
ui.scoreOrig.addEventListener("click", () => runScore("orig"));
ui.scoreCompare.addEventListener("click", runCompare);

// ---------- 优化对比 Agent（同步进行优化评分对比） ----------
// 并发调用「文字对比」与「评分对比（前 vs 后）」两个 Agent，结果在一处合并展示。
async function runCompare() {
  const jd = ui.jd.value.trim();
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD 再对比"; return; }
  const before = ui.resume.value.trim();
  const after = ui.resultResume.value.trim();
  if (!before) { ui.status.textContent = "请填写原始简历"; return; }
  if (!after) { ui.status.textContent = "请先生成优化后简历再做对比"; return; }

  ui.status.textContent = "对比与评分中…";
  ui.scoreResult.innerHTML = '<div class="score-loading">AI 正在同步进行「优化对比」与「优化评分对比」…</div>';

  try {
    const [compareText, scoreResult] = await Promise.all([
      fetchCompareText(jd, before, after),
      fetchScoreBoth(jd, before, after),
    ]);
    if (scoreResult && scoreResult.error) {
      ui.scoreResult.innerHTML = `<div class="empty">${escapeHtml(scoreResult.error)}</div>`;
      ui.status.textContent = scoreResult.error;
      return;
    }
    renderCompareWithScore(compareText, scoreResult);
    ui.status.textContent = "优化对比与评分完成。";
  } catch (e) {
    ui.status.textContent = "请求失败：" + e.message;
    ui.scoreResult.innerHTML = `<div class="empty">请求失败：${escapeHtml(e.message)}</div>`;
  }
}

// 拉取「文字对比」Agent 的最终文本
function fetchCompareText(jd, before, after) {
  return new Promise((resolve, reject) => {
    fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd, before, after }),
    }).then((resp) => {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let acc = "", text = "";
      const pump = () => reader.read().then(({ value, done }) => {
        if (done) { resolve(text); return; }
        acc += decoder.decode(value, { stream: true });
        const parts = acc.split("\n\n");
        acc = parts.pop();
        for (const part of parts) {
          const line = part.replace(/^data:\s*/, "").trim();
          if (!line || line === "[DONE]") continue;
          let ev;
          try { ev = JSON.parse(line); } catch (e) { continue; }
          if (ev.type === "delta") text += ev.text;
          else if (ev.type === "result") text = ev.text;
          else if (ev.type === "error") { reject(new Error(ev.message)); return; }
        }
        pump();
      }).catch(reject);
      pump();
    }).catch(reject);
  });
}

// 拉取「评分对比（前 vs 后）」Agent 的结构化结果
function fetchScoreBoth(jd, before, after) {
  return new Promise((resolve, reject) => {
    fetch("/api/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd, before, after }),
    }).then((resp) => {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let acc = "", result = null;
      const pump = () => reader.read().then(({ value, done }) => {
        if (done) { resolve(result); return; }
        acc += decoder.decode(value, { stream: true });
        const parts = acc.split("\n\n");
        acc = parts.pop();
        for (const part of parts) {
          const line = part.replace(/^data:\s*/, "").trim();
          if (!line || line === "[DONE]") continue;
          let ev;
          try { ev = JSON.parse(line); } catch (e) { continue; }
          if (ev.type === "result") result = ev;
          else if (ev.type === "error") { reject(new Error(ev.message)); return; }
        }
        pump();
      }).catch(reject);
      pump();
    }).catch(reject);
  });
}

// 合并渲染：评分对比卡 + 文字诊断
function renderCompareWithScore(compareText, scoreResult) {
  let scoreHtml = "";
  if (scoreResult && scoreResult.mode === "compare") {
    const diff = (scoreResult.after ? scoreResult.after.total : 0) - (scoreResult.before ? scoreResult.before.total : 0);
    const diffHtml = `<div class="score-compare-bar">
      <span>优化前 ${scoreResult.before.total} → 优化后 ${scoreResult.after.total}</span>
      <span class="score-diff ${diff >= 0 ? "up" : "down"}">${diff >= 0 ? "+" : ""}${diff} 分</span>
    </div>
    ${scoreResult.improvement ? `<div class="score-improve">${escapeHtml(scoreResult.improvement)}</div>` : ""}`;
    scoreHtml = `<div class="compare-section">
      <div class="compare-section-title">优化评分对比</div>
      ${diffHtml}
      <div class="compare-score-cards">${scoreCardHtml(scoreResult.before, "优化前")}${scoreCardHtml(scoreResult.after, "优化后")}</div>
    </div>`;
  } else if (scoreResult) {
    scoreHtml = `<div class="compare-section"><div class="compare-section-title">优化评分对比</div>${scoreCardHtml(scoreResult.before, "评分结果")}</div>`;
  }
  ui.scoreResult.innerHTML = `
    ${scoreHtml}
    <div class="compare-section">
      <div class="compare-section-title">优化对比诊断（文字版）</div>
      <div class="compare-text">${escapeHtml(compareText)}</div>
    </div>`;
}

ui.run.addEventListener("click", runOptimize);

// ---------- 标签页切换（供迭代过程实时展示复用） ----------
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
}

// ---------- JD 图片识别 ----------
function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(file);
  });
}

async function ocrJd() {
  const file = ui.jdImage.files && ui.jdImage.files[0];
  if (!file) { ui.ocrStatus.textContent = "请先选择 JD 图片"; return; }
  ui.ocrBtn.disabled = true;
  ui.ocrStatus.textContent = "正在识别图片文字…";
  try {
    const dataUrl = await readFileAsDataUrl(file);
    const resp = await fetch("/api/jd/ocr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: dataUrl }),
    });
    const json = await resp.json();
    if (!resp.ok) { ui.ocrStatus.textContent = json.error || "识别失败"; return; }
    ui.jd.value = (ui.jd.value.trim() ? ui.jd.value.trim() + "\n\n" : "") + json.text.trim();
    ui.ocrStatus.textContent = "识别完成，已填入 JD 文本框。";
  } catch (e) {
    ui.ocrStatus.textContent = "识别失败：" + e.message;
  } finally {
    ui.ocrBtn.disabled = false;
  }
}

// ---------- 智能迭代优化闭环 ----------
// autoGen：生成令牌。每次发起新请求自增，处理中只认当前令牌，
// 确保「最终结果」只在 result 事件落定一次，切换 Tab 或发起新请求都不会让已定稿数据被更新。
let autoGen = 0;
// optGen：基础优化令牌，逻辑同 autoGen，保证切换 Tab 或发起新请求时
// 旧请求流不会覆盖新 UI、也不会被中断，切回 Tab 能看到原数据。
let optGen = 0;
// 当前迭代运行的 trace/refs 引用，供「刷新可视化数据」按钮重渲染（内存优先，localStorage 兜底）。
let currentTrace = null;
let currentRefs = [];

// 已展开的折叠面板状态：键为「轮次-类型」（如 "3-think"），跨重渲染持久化，
// 避免流式事件频繁重建时间线 DOM 时把用户手动展开的面板折叠掉。
const openSections = new Set();
// 同上的 Agent 消息流面板展开状态
const openBuses = new Set();

async function runAutoOptimize(taskId) {
  if (running) return;
  // 会员专属功能门禁
  if (!(window.CVP_USER && window.CVP_USER.vip)) {
    ui.status.textContent = "智能迭代优化为会员专属功能，请前往「会员中心」开通 VIP 后使用。";
    const go = confirm("智能迭代优化为会员专属功能。\n点击「确定」前往会员中心开通 VIP。");
    if (go) window.location.href = "/vip";
    return;
  }
  const jd = ui.jd.value.trim();
  const resume = ui.resume.value.trim();
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD（或上传图片识别）"; return; }
  if (!resume) { ui.status.textContent = "请先填写简历原文"; return; }

  const myGen = ++autoGen;
  running = true;
  ui.runAuto.disabled = true;
  ui.run.disabled = true;
  ui.status.textContent = taskId ? "从断点继续优化中…" : "智能迭代优化中…";
  resetOutput();
  switchTab("trace");
  persistResume({ jd, original: resume });

  const buf = { resume: "", notes: "" };
  const trace = { iterations: [], targetScore: parseInt(ui.targetScore.value || "90", 10), finalScore: null, targetMet: false, traceId: "" };
  let refs = [];
  // 新一轮启动即清掉旧 localStorage 链路，避免与即将开始的实时落盘冲突
  clearTraceLocal();
  currentTrace = trace;
  currentRefs = refs;
  let returnedTaskId = taskId || null;

  try {
    const payload = {
      jd, resume, version: ui.version.value,
      enable_search: ui.searchToggle.checked,
      target_score: trace.targetScore,
    };
    if (taskId) payload.task_id = taskId;
    const resp = await fetch("/api/optimize/auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      const parts = acc.split("\n\n");
      acc = parts.pop();
      for (const part of parts) {
        const line = part.replace(/^data:\s*/, "").trim();
        if (!line || line === "[DONE]") continue;
        let ev;
        try { ev = JSON.parse(line); } catch (e) { continue; }
        if (myGen !== autoGen) return; // 已被新请求取代，停止更新 UI
        handleAutoEvent(ev, buf, trace, refs);
        if (ev.type === "result") {
          refs = ev.references || [];
          trace.iterations = ev.iterations || [];
          trace.finalScore = ev.final_score;
          trace.targetMet = ev.target_met;
          trace.traceId = ev.trace_id;
          returnedTaskId = ev.task_id || returnedTaskId;
        }
        // 捕获任务创建事件，记录 task_id
        if (ev.type === "progress" && ev.task_id) {
          returnedTaskId = ev.task_id;
        }
      }
    }
    if (myGen !== autoGen) return;
    // 最终结果一次性落定（此处之后不再对 resultResume 做任何写入）
    ui.resultResume.value = buf.resume || "";
    persistResume({ optimized: buf.resume || "" });
    renderTrace(trace, refs, true);
    renderBus(trace);
    saveTraceLocal(trace, refs);
    // 同步填充优化说明 / 参考来源
    const notes = (buf.notes || "").split("\n").map((s) => s.trim()).filter(Boolean);
    ui.resultNotes.innerHTML = notes.length
      ? "<ul>" + notes.map((n) => `<li>${escapeHtml(n.replace(/^[-•]\s*/, ""))}</li>`).join("") + "</ul>"
      : '<div class="empty">本次未返回结构化优化说明</div>';
    ui.resultRefs.innerHTML = refs && refs.length ? refs.map((t) => `<li>${escapeHtml(t)}</li>`).join("") : '<li class="empty">暂无参考来源</li>';
    ui.status.textContent = trace.targetMet
      ? `迭代优化完成，最终评分 ${trace.finalScore} 分（已达标 ${trace.targetScore}）。`
      : `迭代优化完成，最终评分 ${trace.finalScore} 分（未达目标 ${trace.targetScore}，可手动再优化）。`;
  } catch (e) {
    ui.status.textContent = "请求失败：" + e.message;
    logProgress("请求失败：" + e.message);
  } finally {
    running = false;
    ui.runAuto.disabled = false;
    ui.run.disabled = false;
  }
}

function handleAutoEvent(ev, buf, trace, refs) {
  if (ev.type === "progress") {
    logProgress(`[第${ev.iteration || "-"}轮] ${ev.message}`);
  } else if (ev.type === "delta") {
    // 每轮开始时清空，避免多轮简历叠加成多份
    if (ev.iteration !== buf._currentIter) {
      buf._currentIter = ev.iteration;
      buf.resume = ev.text;
    } else {
      buf.resume += ev.text;
    }
    ui.resultResume.value = buf.resume;
    ui.resultResume.scrollTop = ui.resultResume.scrollHeight;
  } else if (ev.type === "iteration_result") {
    buf.notes = ev.notes || "";
    if (ev.resume) {
      buf.resume = ev.resume;
      buf._currentIter = ev.iteration;
      upsertIteration(trace, ev.iteration, { resume: ev.resume });
    }
  } else if (ev.type === "score") {
    upsertIteration(trace, ev.iteration, { score: { total: ev.total, dimensions: ev.dimensions, summary: ev.summary } });
    scheduleRender(trace, refs);
  } else if (ev.type === "think") {
    upsertIteration(trace, ev.iteration, { weak: ev.weak, issues: ev.issues, guidance: ev.guidance, projectExplanations: ev.project_explanations });
    scheduleRender(trace, refs);
  } else if (ev.type === "supervise") {
    upsertIteration(trace, ev.iteration, { decision: ev.decision, safe: ev.safe, reason: ev.reason, risk: ev.risk });
    scheduleRender(trace, refs);
  } else if (ev.type === "node_metrics") {
    // 节点状态：时长、token 消耗、成功/失败
    if (!trace.nodeMetrics) trace.nodeMetrics = {};
    const key = ev.name;
    if (!trace.nodeMetrics[key]) trace.nodeMetrics[key] = { name: key, calls: [], total_ok: 0, total_error: 0, total_duration_ms: 0, total_tokens: 0 };
    const nm = trace.nodeMetrics[key];
    nm.calls.push(ev);
    if (ev.status === "ok") nm.total_ok++; else nm.total_error++;
    nm.total_duration_ms += ev.duration_ms || 0;
    nm.total_tokens += (ev.tokens && ev.tokens.total_tokens) || 0;
    scheduleRender(trace, refs);
  } else if (ev.type === "baseline_score") {
    // 原始简历基线评分：在迭代过程下方展示
    trace.baselineScore = { total: ev.total, dimensions: ev.dimensions || [], summary: ev.summary || "" };
    scheduleRender(trace, refs);
  } else if (ev.type === "error") {
    ui.status.textContent = ev.message;
    logProgress(ev.message);
    // 错误时也渲染当前已有的迭代过程
    scheduleRender(trace, refs);
  }
}

function upsertIteration(trace, it, patch) {
  let entry = trace.iterations.find((x) => x.iteration === it);
  if (!entry) { entry = { iteration: it }; trace.iterations.push(entry); }
  Object.assign(entry, patch);
}

// 量化可视化：汇总卡 + 分数曲线（SVG）+ 每轮时间线
function renderTrace(trace, refs, autoOpenLatest) {
  const iters = trace.iterations || [];
  // 终轮渲染时自动展开最新一轮的三个面板，让用户直接进入即可看到迭代/优化信息
  if (autoOpenLatest && iters.length) {
    const lastIt = iters[iters.length - 1].iteration;
    ["think", "guide", "proj"].forEach((k) => openSections.add(lastIt + "-" + k));
  }
  const last = iters.length ? iters[iters.length - 1] : null;
  const finalScore = trace.finalScore != null ? trace.finalScore : (last && last.score ? last.score.total : "--");
  ui.traceSummary.innerHTML = `
    <div class="ts-item"><span>目标分</span><b>${trace.targetScore || "--"}</b></div>
    <div class="ts-item"><span>最终分</span><b class="${trace.targetMet ? "ok" : "warn"}">${finalScore}</b></div>
    <div class="ts-item"><span>迭代轮次</span><b>${iters.length}</b></div>
    <div class="ts-item"><span>是否达标</span><b class="${trace.targetMet ? "ok" : "warn"}">${trace.targetMet ? "是" : "否"}</b></div>
    ${trace.traceId ? `<div class="ts-item"><span>链路ID</span><b class="mono" title="可在 LangSmith 中按此 ID 检索本次链路日志">${escapeHtml(trace.traceId)}</b></div>` : ""}
  `;
  const refsHtml = refs && refs.length
    ? `<div class="trace-refs"><div class="trace-refs-title">参考来源</div><ul>${refs.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul></div>`
    : "";
  // 原始简历评分卡（展示在迭代过程下方，作为起点对照）
  let baselineHtml = "";
  if (trace.baselineScore && (trace.baselineScore.total || (trace.baselineScore.dimensions && trace.baselineScore.dimensions.length))) {
    baselineHtml =
      '<div class="trace-baseline">' +
      '<div class="trace-baseline-title">原始简历评分（迭代起点）</div>' +
      scoreCardHtml(trace.baselineScore) +
      '</div>';
  }

  ui.traceView.innerHTML = buildNodeStats(trace.nodeMetrics) +
    buildScoreChart(iters, trace.targetScore) +
    refsHtml +
    `<div class="trace-timeline">${[...iters].reverse().map(buildIterationCard).join("") || '<div class="empty">暂无迭代数据</div>'}</div>` +
    baselineHtml;
}

function buildScoreChart(iters, target) {
  const totals = iters.map((it) => (it.score ? it.score.total : 0));
  if (!totals.length) return '<div class="empty">暂无评分数据</div>';
  const W = 640, H = 240, padL = 54, padR = 28, padT = 40, padB = 44;
  const n = totals.length;
  const x = (i) => padL + (W - padL - padR) * (n === 1 ? 0.5 : i / (n - 1));
  const y = (v) => H - padB - (H - padT - padB) * (v / 100);

  // Y轴网格线与标签
  const gridVals = [0, 25, 50, 75, 100];
  const gridLines = gridVals.map((v) => {
    const gy = y(v);
    return `<line x1="${padL}" y1="${gy.toFixed(1)}" x2="${W - padR}" y2="${gy.toFixed(1)}" class="grid-line"/>` +
           `<text x="${padL - 10}" y="${gy.toFixed(1)}" class="grid-lbl" text-anchor="end" dy="3">${v}</text>`;
  }).join("");

  const pts = totals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const targetY = y(target || 90);

  // 数据点与分数标签
  const circles = totals.map((v, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="5" class="dot"/>
  <text x="${x(i).toFixed(1)}" y="${(y(v) - 14).toFixed(1)}" class="lbl" text-anchor="middle">${v}</text>`).join("");

  const xlabels = totals.map((v, i) => `<text x="${x(i).toFixed(1)}" y="${H - padB + 22}" class="xlab" text-anchor="middle">第${i + 1}轮</text>`).join("");

  return `<div class="chart-box"><svg viewBox="0 0 ${W} ${H}" class="score-chart" preserveAspectRatio="xMidYMid meet">
    ${gridLines}
    <line x1="${padL}" y1="${targetY.toFixed(1)}" x2="${W - padR}" y2="${targetY.toFixed(1)}" class="target-line"/>
    <rect x="${padL}" y="${(targetY - 22).toFixed(1)}" width="62" height="20" rx="4" class="target-bg"/>
    <text x="${padL + 6}" y="${(targetY - 8).toFixed(1)}" class="target-lbl">目标 ${target || 90}</text>
    <polyline points="${pts}" class="score-poly"/>
    ${circles}${xlabels}
  </svg></div>`;
}

// 节点状态面板：各 Agent 节点耗时条形图 + token 消耗 + 成功率
function buildNodeStats(nodeMetrics) {
  if (!nodeMetrics) return "";
  const nodes = Object.values(nodeMetrics);
  if (!nodes.length) return "";

  // 按调用顺序排序
  const order = ["optimize", "refine", "score", "think", "supervise"];
  nodes.sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));

  // SVG 耗时条形图
  const barH = 24, barGap = 8, padT = 8, padB = 20, padL = 80, padR = 24;
  const maxDur = Math.max(...nodes.map((n) => n.total_duration_ms), 1);
  const svgW = 520, svgH = padT + nodes.length * (barH + barGap) + padB;

  const bars = nodes.map((n, i) => {
    const y = padT + i * (barH + barGap);
    const w = Math.max(4, (n.total_duration_ms / maxDur) * (svgW - padL - padR));
    const rate = n.calls.length ? ((n.total_ok / n.calls.length) * 100).toFixed(0) : "--";
    const tok = n.total_tokens > 1000 ? (n.total_tokens / 1000).toFixed(1) + "k" : n.total_tokens;
    return `<text x="${padL - 8}" y="${(y + barH * 0.65).toFixed(1)}" class="ns-lbl" text-anchor="end">${n.name}</text>
    <rect x="${padL}" y="${y}" width="${w.toFixed(1)}" height="${barH}" rx="4" class="ns-bar ns-${n.total_error ? "warn" : "ok"}"/>
    <text x="${(padL + w + 6).toFixed(1)}" y="${(y + barH * 0.65).toFixed(1)}" class="ns-val">${(n.total_duration_ms / 1000).toFixed(1)}s</text>`;
  }).join("");

  // 汇总表
  const rows = nodes.map((n) => {
    const calls = n.calls.length;
    const rate = calls ? ((n.total_ok / calls) * 100).toFixed(0) : "--";
    const tok = n.total_tokens;
    const dur = n.total_duration_ms;
    const cls = n.total_error ? "ns-err" : "ns-ok";
    return `<tr><td class="ns-name">${n.name}</td>
      <td>${calls}</td>
      <td class="${cls}">${n.total_ok}/${n.total_error || 0}</td>
      <td>${rate}%</td>
      <td>${tok > 1000 ? (tok / 1000).toFixed(1) + "K" : tok}</td>
      <td>${(dur / 1000).toFixed(1)}s</td></tr>`;
  }).join("");

  const total = { calls: 0, ok: 0, err: 0, tok: 0, dur: 0 };
  nodes.forEach((n) => {
    total.calls += n.calls.length;
    total.ok += n.total_ok;
    total.err += n.total_error;
    total.tok += n.total_tokens;
    total.dur += n.total_duration_ms;
  });
  const rateAll = total.calls ? ((total.ok / total.calls) * 100).toFixed(0) : "--";

  return `<div class="node-stats">
    <div class="ns-hd">节点状态</div>
    <div class="ns-chart"><svg viewBox="0 0 ${svgW} ${svgH}" class="ns-svg">${bars}</svg></div>
    <table class="ns-table">
      <thead><tr><th>节点</th><th>调用</th><th>成功/失败</th><th>成功率</th><th>Token</th><th>总耗时</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot><tr>
        <td>合计</td><td>${total.calls}</td><td>${total.ok}/${total.err}</td>
        <td>${rateAll}%</td>
        <td>${total.tok > 1000 ? (total.tok / 1000).toFixed(1) + "K" : total.tok}</td>
        <td>${(total.dur / 1000).toFixed(1)}s</td>
      </tr></tfoot>
    </table>
  </div>`;
}

function buildIterationCard(it) {
  const sc = it.score;
  const dims = sc && sc.dimensions ? sc.dimensions.map((d) => `<span class="dim-chip">${escapeHtml(d.name)} ${d.score}</span>`).join("") : "";
  const weak = (it.weak || []).map((w) => `<span class="weak-chip">${escapeHtml(w)}</span>`).join("");
  const issues = (it.issues || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
  const guidance = it.guidance || "";
  const proj = it.projectExplanations || "";
  const decision = it.decision === "stop" ? `<span class="badge stop">终止</span>` : `<span class="badge go">继续</span>`;
  const safe = it.safe === false ? `<span class="badge unsafe">安全未通过</span>` : `<span class="badge safe">安全</span>`;
  const reason = it.reason ? `<div class="ti-reason"><b>监督终裁：</b>${escapeHtml(it.reason)}</div>` : "";
  const risk = it.risk ? `<div class="ti-risk">风险：${escapeHtml(it.risk)}</div>` : "";
  const total = sc ? sc.total : "--";
  const sum = sc && sc.summary ? `<div class="ti-summary">${escapeHtml(sc.summary)}</div>` : "";

  const itNum = it.iteration;
  const secOpen = (kind) => openSections.has(itNum + "-" + kind);
  const secCls = (kind) => `ti-section ${secOpen(kind) ? "open" : ""}`;
  const secLbl = (kind) => secOpen(kind) ? "收起" : "展开";

  // 思考诊断：每轮都渲染；本轮无薄弱项时给占位提示，避免后续轮数“看起来没信息”
  const thinkBody = (weak || issues)
    ? `${weak ? `<div class="ti-weak">薄弱维度：${weak}</div>` : ""}
       ${issues ? `<div class="ti-issues"><ul>${issues}</ul></div>` : ""}`
    : `<div class="ti-empty">本轮评分较高，暂无明显薄弱维度。</div>`;
  const thinkSection = `
    <div class="${secCls("think")}" data-it="${itNum}" data-kind="think">
      <div class="ti-section-hd" onclick="toggleTraceSection(this)">
        <b>思考诊断</b><span class="ti-toggle-lbl">${secLbl("think")}</span>
      </div>
      <div class="ti-section-bd">${thinkBody}</div>
    </div>`;

  // 优化指引 / 优化策略：每轮都渲染
  const guideSection = `
    <div class="${secCls("guide")}" data-it="${itNum}" data-kind="guide">
      <div class="ti-section-hd" onclick="toggleTraceSection(this)">
        <b>优化指引 / 优化策略</b><span class="ti-toggle-lbl">${secLbl("guide")}</span>
      </div>
      <div class="ti-section-bd">${guidance
        ? `<div class="ti-guidance">${escapeHtml(guidance).replace(/\n/g, "<br>")}</div>`
        : `<div class="ti-empty">本轮无需额外优化指引。</div>`}</div>
    </div>`;

  // 项目经历详解：每轮都渲染
  const projSection = `
    <div class="${secCls("proj")}" data-it="${itNum}" data-kind="proj">
      <div class="ti-section-hd accent" onclick="toggleTraceSection(this)">
        <b>项目经历详解</b><span class="ti-toggle-lbl">${secLbl("proj")}</span>
      </div>
      <div class="ti-section-bd">${proj
        ? `<div class="ti-proj">${escapeHtml(proj).replace(/\n/g, "<br>")}</div>`
        : `<div class="ti-empty">本轮未输出项目经历详解。</div>`}</div>
    </div>`;

  return `<div class="ti-card">
    <div class="ti-head">
      <span class="ti-no">第 ${it.iteration} 轮</span>
      <span class="ti-total">${total} 分</span>
      ${decision}${safe}
    </div>
    <div class="ti-dims">${dims}</div>
    ${sum}
    ${thinkSection}
    ${guideSection}
    ${projSection}
    ${reason}${risk}
  </div>`;
}

// 时间线面板展开/收起（展开状态持久化在 openSections，跨流式重渲染保留）
window.toggleTraceSection = function(el) {
  const sec = el.closest(".ti-section");
  if (!sec) return;
  const key = sec.dataset.it + "-" + sec.dataset.kind;
  const open = sec.classList.toggle("open");
  if (open) openSections.add(key); else openSections.delete(key);
  const lbl = el.querySelector(".ti-toggle-lbl");
  if (lbl) lbl.textContent = open ? "收起" : "展开";
};

// Agent 消息流：把每轮各 Agent 的收发消息以对话流形式展示
function renderBus(trace) {
  const iters = trace.iterations || [];
  if (!iters.length) {
    ui.busView.innerHTML = '<div class="empty">运行「智能迭代优化」后，这里以对话流形式展示各 Agent 之间的消息传递（谁发给谁、发了什么）。</div>';
    return;
  }
  ui.busView.innerHTML = `<div class="agent-bus">${[...iters].reverse().map(buildBusRound).join("")}</div>`;
}

// ---------- 渲染节流 + 本地持久化 ----------
// 流式迭代过程中 score/think/supervise/node_metrics 事件高频触发，若每次都全量重建
// 时间线（含 SVG 图表 + 全部卡片），轮次多时严重卡顿。用 rAF 把同帧内的多次渲染请求
// 合并为「每帧最多一次全量渲染」，消除卡顿根因（而非加各种 onerror/重试兜底）。
let _renderQueued = false;
function scheduleRender(trace, refs) {
  if (_renderQueued) return;
  _renderQueued = true;
  // 进行中实时落盘：切到面试模拟页（整页跳转 /interview）断开 SSE、内存清空后，
  // 回来能恢复最新那条链路而非被旧数据覆盖。每帧存一次，配合 rAF 节流不会过频。
  saveTraceLocal(trace, refs, true);
  requestAnimationFrame(() => {
    _renderQueued = false;
    renderTrace(trace, refs || []);
    renderBus(trace);
  });
}

const TRACE_KEY = "cvp_auto_trace_v1";
// 迭代过程实时把 trace 持久化到 localStorage（无论进行中还是已完成），解决整页跳转
// /interview 断开 SSE、内存清空后回来被旧链路覆盖的问题——进行中数据也落盘，恢复时
// 优先取最新的那次运行（running 优先，其次 savedAt 最新），从根因消除「切页丢新数据」。
function saveTraceLocal(trace, refs, running) {
  try {
    if (!trace || !trace.iterations || !trace.iterations.length) return;
    localStorage.setItem(TRACE_KEY, JSON.stringify({
      trace, refs: refs || [], running: !!running, savedAt: Date.now(),
    }));
  } catch (e) { /* localStorage 不可用时静默 */ }
}
function restoreTraceLocal() {
  try {
    const raw = localStorage.getItem(TRACE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj.trace || !obj.trace.iterations || !obj.trace.iterations.length) return null;
    return obj;
  } catch (e) { return null; }
}
function clearTraceLocal() {
  try { localStorage.removeItem(TRACE_KEY); } catch (e) {}
}

// 手动刷新流程可视化数据：内存中的 trace 优先，没有则从 localStorage 恢复，确保
// 跑完一轮（或恢复断点）后点一下就能看到最新量化链路与对话流，不依赖重跑。
function refreshTraceView() {
  let t = currentTrace;
  let r = currentRefs;
  if (!t || !t.iterations || !t.iterations.length) {
    const saved = restoreTraceLocal();
    if (saved) { t = saved.trace; r = saved.refs; }
  }
  if (!t || !t.iterations || !t.iterations.length) {
    ui.traceView.innerHTML = '<div class="empty">暂无可视化数据，请先运行「智能迭代优化」。</div>';
    ui.traceSummary.innerHTML = "";
    return;
  }
  renderTrace(t, r || []);
  renderBus(t);
}
ui.refreshTrace.addEventListener("click", refreshTraceView);
ui.refreshBus.addEventListener("click", refreshTraceView);

function buildBusRound(it) {
  const isFirst = it.iteration === 1;
  const optName = isFirst ? "优化 Agent" : "细节优化 Agent";
  const optFrom = isFirst ? "用户原始简历 + JD + 参考素材" : "上轮精修简历 + JD";
  const optLabel = isFirst ? "首轮优化后的简历" : "本轮精修后的简历";
  const sc = it.score;
  const dimsTxt = sc && sc.dimensions ? sc.dimensions.map((d) => `${d.name} ${d.score}`).join(" · ") : "";
  const scoreMsg = sc
    ? `各维度评分：${dimsTxt}\n总分：${sc.total}\n总评：${sc.summary || ""}`
    : "（评分未返回）";
  const thinkMsg = [
    it.weak && it.weak.length ? "薄弱维度：" + it.weak.join("、") : "",
    it.issues && it.issues.length ? "思考诊断：\n" + it.issues.map((s) => "· " + s).join("\n") : "",
    it.guidance ? "优化指引：\n" + it.guidance : "",
    it.projectExplanations ? "项目经历详解：\n" + it.projectExplanations : "",
  ].filter(Boolean).join("\n\n");
  const decTxt = it.decision === "stop" ? "终止迭代" : "继续迭代";
  const safeTxt = it.safe === false ? "未通过" : "通过";
  const supMsg = `监督判定：${decTxt}\n安全检查：${safeTxt}` +
    (it.reason ? `\n理由：${it.reason}` : "") +
    (it.risk ? `\n风险：${it.risk}` : "");

  return `<div class="bus-round">
    <div class="bus-round-hd">第 ${it.iteration} 轮</div>
    ${busBubble(it.iteration, "opt", optName, optFrom, "编排器", it.resume || "（简历未捕获）", optLabel)}
    ${busBubble(it.iteration, "score", "评分 Agent", "优化/细节优化 Agent 传来的简历 + JD", "编排器", scoreMsg, "评分结果")}
    ${busBubble(it.iteration, "think", "思考诊断 Agent", "评分结果 + 简历 + JD", "细节优化 Agent", thinkMsg, "诊断与优化指引")}
    ${busBubble(it.iteration, "sup", "监督 Agent", "评分结果 + 思考指引", "编排器（决定是否继续）", supMsg, "终止判定")}
  </div>`;
}

function busBubble(it, cls, name, from, to, msg, label) {
  const key = it + "-" + cls;
  const open = openBuses.has(key);
  return `<div class="bus-msg ${cls} ${open ? "open" : ""}" data-it="${it}" data-cls="${cls}">
    <div class="bus-msg-hd">
      <span class="bus-dot"></span>
      <b class="bus-name">${name}</b>
      <span class="bus-from">来自 ${escapeHtml(from)}</span>
      <button class="bus-toggle" onclick="toggleBusMsg(this)">${open ? "收起消息" : "展开消息"}</button>
    </div>
    <div class="bus-msg-bd">
      <div class="bus-label">${escapeHtml(label)} → ${escapeHtml(to)}</div>
      <div class="bus-text">${escapeHtml(msg).replace(/\n/g, "<br>")}</div>
    </div>
  </div>`;
}

window.toggleBusMsg = function(el) {
  const card = el.closest(".bus-msg");
  if (!card) return;
  const key = card.dataset.it + "-" + card.dataset.cls;
  const open = card.classList.toggle("open");
  if (open) openBuses.add(key); else openBuses.delete(key);
  el.textContent = open ? "收起消息" : "展开消息";
};

ui.ocrBtn.addEventListener("click", ocrJd);
ui.runAuto.addEventListener("click", runAutoOptimize);

// ---------- 初始化：回填上次数据 + 切换页面时携带 ----------
(function initIndex() {
  // 载入当前登录用户已保存的 JD / 简历（服务端按用户隔离）。
  // 切换页面再回到首页时，回填已保存的内容，保证简历原文、优化结果、JD 都继续存在。
  loadResumeStore().then(() => {
    if (resumeStore.jd && !ui.jd.value.trim()) ui.jd.value = resumeStore.jd;
    if (resumeStore.original && !ui.resume.value.trim()) ui.resume.value = resumeStore.original;
    else if (!ui.resume.value.trim()) ui.resume.value = DEFAULT_RESUME;
    if (resumeStore.optimized && !ui.resultResume.value.trim()) ui.resultResume.value = resumeStore.optimized;
    else if (!ui.resultResume.value.trim()) ui.resultResume.value = DEFAULT_RESUME;
    // 切页/刷新后恢复迭代可视化数据：由于进行中已实时落盘（scheduleRender 内
    // saveTraceLocal(trace, refs, true)），localStorage 里始终是「最新一次运行」，
    // 因此切到面试模拟页（整页跳转 /interview）断开 SSE、内存清空后再回来，
    // 恢复的也是新链路而非旧链路 23361...，从根因消除「切页丢新数据」。
    if (autoGen === 0) {
      const saved = restoreTraceLocal();
      if (saved) {
        renderTrace(saved.trace, saved.refs || [], true);
        renderBus(saved.trace);
        // 若恢复的是进行中链路（上次切页被打断 SSE），提示用户任务未跑完。
        if (saved.running) {
          ui.status.textContent = "检测到上一次迭代任务被中断（切页/刷新导致），可视化数据已恢复至中断前进度，可重新运行「智能迭代优化」继续。";
        }
      }
    }
  });
  // 经顶部导航切换到其它页面时，先保存当前 JD / 简历，确保数据被携带
  document.querySelectorAll(".nav-link").forEach((a) => {
    a.addEventListener("click", () => { saveContext(); });
  });
  // Skill 中心：绑定搜索与筛选
  if (ui.skillSearch) {
    ui.skillSearch.addEventListener("input", () => {
      skillFilter === "online" ? loadOnlineSkills() : loadSkills();
    });
    document.querySelectorAll(".skill-filter .chip").forEach((c) => {
      c.addEventListener("click", () => {
        document.querySelectorAll(".skill-filter .chip").forEach((x) => x.classList.remove("active"));
        c.classList.add("active");
        skillFilter = c.dataset.filter;
        skillFilter === "online" ? loadOnlineSkills() : loadSkills();
      });
    });
  }
  // 检查未完成任务，显示断点续传入口
  checkUnfinishedTasks();
})();

// ---------- 断点续传 ----------
async function checkUnfinishedTasks() {
  try {
    const resp = await fetch("/api/task/list");
    const tasks = await resp.json();
    if (!Array.isArray(tasks) || !tasks.length) return;
    // 取最近一个未完成任务
    const t = tasks[0];
    if (!t.checkpoint || !t.checkpoint.iteration) return;
    const banner = document.getElementById("resumeBanner");
    if (!banner) return;
    banner.innerHTML = `
      <span>检测到中断任务（${t.updated_at} 第 ${t.checkpoint.iteration} 轮，${t.checkpoint.phase} 阶段）</span>
      <button class="btn-mini accent" id="resumeTaskBtn" data-task="${t.task_id}">从断点继续</button>
      <button class="btn-mini" id="dismissBannerBtn">忽略</button>`;
    banner.style.display = "flex";
    document.getElementById("resumeTaskBtn").addEventListener("click", () => {
      banner.style.display = "none";
      runAutoOptimize(t.task_id);
    });
    document.getElementById("dismissBannerBtn").addEventListener("click", () => {
      banner.style.display = "none";
    });
  } catch (e) { /* 静默忽略 */ }
}

// ---------- Skill 中心：发现 / 查询 / 安装 ----------
let skillCache = [];
let onlineCache = [];
let skillFilter = "all";

// 前端查询缓存：切 Tab / 切页重复进入 Skill 中心时，命中缓存（TTL 内）直接渲染，
// 不重复打后端（尤其 /api/skills/online 要查外部 registry，慢且浪费），从根因消除「流转慢」。
const _queryCache = {};
function cacheFetch(url, ttl) {
  const hit = _queryCache[url];
  if (hit && (Date.now() - hit.t) < (ttl || 60000)) return Promise.resolve(hit.data);
  return fetch(url).then((r) => r.json()).then((data) => {
    _queryCache[url] = { t: Date.now(), data };
    return data;
  });
}
// 安装/卸载/刷新后清缓存，确保下次进入拿到最新状态
function invalidateSkillCache() {
  Object.keys(_queryCache).forEach((k) => { if (k.startsWith("/api/skills")) delete _queryCache[k]; });
}

function loadSkills() {
  const q = (ui.skillSearch.value || "").trim();
  const url = "/api/skills" + (q ? "?q=" + encodeURIComponent(q) : "");
  cacheFetch(url, 60000)
    .then((data) => {
      skillCache = data.skills || [];
      renderSkills();
    })
    .catch(() => { ui.skillGrid.innerHTML = '<div class="empty">加载 Skill 失败，请刷新重试。</div>'; });
}

function loadOnlineSkills() {
  const q = (ui.skillSearch.value || "").trim();
  const url = "/api/skills/online" + (q ? "?q=" + encodeURIComponent(q) : "");
  cacheFetch(url, 60000)
    .then((data) => {
      onlineCache = data.skills || [];
      if (data.error) ui.skillEmpty.textContent = data.error;
      renderSkills();
    })
    .catch(() => { ui.skillGrid.innerHTML = '<div class="empty">联网查询失败，请检查 registry 配置。</div>'; });
}

function renderSkills() {
  if (skillFilter === "online") return renderOnlineSkills();
  let list = skillCache;
  if (skillFilter === "installed") list = list.filter((s) => s.installed);
  else if (skillFilter === "market") list = list.filter((s) => !s.installed);
  if (!list.length) {
    ui.skillGrid.innerHTML = "";
    ui.skillEmpty.style.display = "block";
    ui.skillEmpty.textContent = "没有匹配的 Skill。";
    return;
  }
  ui.skillEmpty.style.display = "none";
  ui.skillGrid.innerHTML = list.map(skillCard).join("");
}

function skillCard(s) {
  const badge = s.builtin
    ? '<span class="skill-badge builtin">内置</span>'
    : (s.installed ? '<span class="skill-badge installed">已安装</span>' : '<span class="skill-badge market">可安装</span>');
  let action;
  if (s.builtin) {
    action = '<button class="skill-btn" onclick="openSkillDetail(\'' + s.id + '\')">查看详情</button>';
  } else if (s.installed) {
    action = '<button class="skill-btn ghost" onclick="uninstallSkill(\'' + s.id + '\')">卸载</button>' +
             '<button class="skill-btn" onclick="openSkillDetail(\'' + s.id + '\')">详情</button>';
  } else {
    action = '<button class="skill-btn accent" onclick="installSkill(\'' + s.id + '\')">安装</button>' +
             '<button class="skill-btn" onclick="openSkillDetail(\'' + s.id + '\')">详情</button>';
  }
  const tags = (s.tags || []).map((t) => '<span class="skill-tag">#' + escapeHtml(t) + "</span>").join("");
  return `<div class="skill-card">
    <div class="skill-card-hd"><span class="skill-cat">${escapeHtml(s.category || "")}</span>${badge}</div>
    <div class="skill-name">${escapeHtml(s.name)}</div>
    <div class="skill-desc">${escapeHtml(s.desc || "")}</div>
    <div class="skill-tags">${tags}</div>
    <div class="skill-card-ft">${action}</div>
  </div>`;
}

function onlineCard(s) {
  const badge = s.installed
    ? '<span class="skill-badge installed">已导入</span>'
    : '<span class="skill-badge market">可导入</span>';
  const action = s.installed
    ? '<button class="skill-btn" onclick="openOnlineDetail(\'' + s.id + '\')">查看</button>'
    : '<button class="skill-btn accent" onclick="importSkillOnline(\'' + s.id + '\')">一键导入</button>' +
      '<button class="skill-btn" onclick="openOnlineDetail(\'' + s.id + '\')">详情</button>';
  const tags = (s.tags || []).map((t) => '<span class="skill-tag">#' + escapeHtml(t) + "</span>").join("");
  return `<div class="skill-card">
    <div class="skill-card-hd"><span class="skill-cat">${escapeHtml(s.category || "")}</span>${badge}</div>
    <div class="skill-name">${escapeHtml(s.name)}</div>
    <div class="skill-desc">${escapeHtml(s.desc || "")}</div>
    <div class="skill-tags">${tags}</div>
    <div class="skill-card-ft">${action}</div>
  </div>`;
}

function renderOnlineSkills() {
  if (!onlineCache.length) {
    ui.skillGrid.innerHTML = "";
    ui.skillEmpty.style.display = "block";
    if (!ui.skillEmpty.textContent) ui.skillEmpty.textContent = "没有匹配的在线技能。";
    return;
  }
  ui.skillEmpty.style.display = "none";
  ui.skillGrid.innerHTML = onlineCache.map(onlineCard).join("");
}

function skillDetailHtml(s, isOnline) {
  const badge = s.builtin ? "内置能力" : (s.installed ? "已安装" : "未安装");
  const tags = (s.tags || []).map((t) => '<span class="skill-tag">#' + escapeHtml(t) + "</span>").join("");
  let action;
  if (isOnline && !s.installed) {
    action = '<button class="skill-btn accent" onclick="importSkillOnline(\'' + s.id + '\')">一键导入</button>';
  } else if (s.builtin) {
    action = '<button class="skill-btn" onclick="closeSkillModal()">知道了</button>';
  } else if (s.installed) {
    action = '<button class="skill-btn ghost" onclick="uninstallSkill(\'' + s.id + '\')">卸载</button>';
  } else {
    action = '<button class="skill-btn accent" onclick="installSkill(\'' + s.id + '\')">确认安装</button>';
  }
  const usage = s.usage
    ? `<div class="skill-detail-usage"><b>何时使用 / 使用说明：</b><div class="skill-detail-usage-body">${escapeHtml(s.usage).replace(/\n/g, "<br>")}</div></div>`
    : "";
  return `
    <div class="skill-detail-hd"><span class="skill-cat">${escapeHtml(s.category || "")}</span><span class="skill-badge">${badge}</span></div>
    <h3 class="skill-detail-name">${escapeHtml(s.name)}</h3>
    <div class="skill-detail-meta">版本 ${escapeHtml(s.version || "")} · 来源 ${escapeHtml(s.source || "")}</div>
    <div class="skill-detail-tags">${tags}</div>
    <p class="skill-detail-desc">${escapeHtml(s.desc || "")}</p>
    ${usage}
    <div class="skill-detail-body">${escapeHtml(s.detail || "").replace(/\n/g, "<br>")}</div>
    <div class="skill-detail-ft">${action}</div>
  `;
}

window.openSkillDetail = async function(id) {
  try {
    const resp = await fetch("/api/skills/" + encodeURIComponent(id));
    const s = await resp.json();
    if (s.error) { alert(s.error); return; }
    ui.skillModalBody.innerHTML = skillDetailHtml(s, false);
    ui.skillModal.style.display = "flex";
  } catch (e) {
    alert("查询失败：" + e.message);
  }
};

window.openOnlineDetail = function(id) {
  const s = onlineCache.find((x) => x.id === id);
  if (!s) return;
  ui.skillModalBody.innerHTML = skillDetailHtml(s, true);
  ui.skillModal.style.display = "flex";
};

window.closeSkillModal = function() {
  ui.skillModal.style.display = "none";
};

window.installSkill = async function(id) {
  try {
    const resp = await fetch("/api/skills/" + encodeURIComponent(id) + "/install", { method: "POST" });
    const json = await resp.json();
    if (!resp.ok) { alert(json.error || "安装失败"); return; }
    closeSkillModal();
    invalidateSkillCache();
    loadSkills();
  } catch (e) {
    alert("安装失败：" + e.message);
  }
};

window.importSkillOnline = async function(id) {
  const s = onlineCache.find((x) => x.id === id);
  if (!s) return;
  try {
    const resp = await fetch("/api/skills/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill: s }),
    });
    const json = await resp.json();
    if (!resp.ok) { alert(json.error || "导入失败"); return; }
    closeSkillModal();
    invalidateSkillCache();
    loadOnlineSkills();
    loadSkills();
  } catch (e) {
    alert("导入失败：" + e.message);
  }
};

window.uninstallSkill = async function(id) {
  if (!confirm("确定卸载该 Skill 吗？")) return;
  try {
    const resp = await fetch("/api/skills/" + encodeURIComponent(id) + "/uninstall", { method: "POST" });
    const json = await resp.json();
    if (!resp.ok) { alert(json.error || "卸载失败"); return; }
    closeSkillModal();
    invalidateSkillCache();
    loadSkills();
  } catch (e) {
    alert("卸载失败：" + e.message);
  }
};
