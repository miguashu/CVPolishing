// -*- coding: utf-8 -*-
// 面试模拟前端：生成面试题 / 编辑答案 / 保存到长期记忆 / 追问答疑 / 导出 / 自由提问

const $ = (sel) => document.querySelector(sel);

// 简历与 JD 由服务端按登录用户存储（/api/resume），不再使用浏览器本地存储，避免不同用户串数据。

const ui = {
  jd: $("#jd"),
  resume: $("#resume"),
  count: $("#count"),
  autoSave: $("#autoSave"),
  genSearch: $("#genSearch"),
  gen: $("#gen"),
  more: $("#more"),
  status: $("#status"),
  progress: $("#progress"),
  qaList: $("#qaList"),
  // 记忆
  memList: $("#memList"),
  refreshMem: $("#refreshMem"),
  exportJson: $("#exportJson"),
  exportMd: $("#exportMd"),
  clearMem: $("#clearMem"),
  memSearch: $("#memSearch"),
  importJsonBtn: $("#importJsonBtn"),
  importJson: $("#importJson"),
  importMdBtn: $("#importMdBtn"),
  importMd: $("#importMd"),
  memModal: $("#memModal"),
  memModalTitle: $("#memModalTitle"),
  memModalText: $("#memModalText"),
  memModalExtra: $("#memModalExtra"),
  memModalClose: $("#memModalClose"),
  memModalSave: $("#memModalSave"),
  // 问我
  askLog: $("#askLog"),
  askText: $("#askText"),
  askSend: $("#askSend"),
  askSave: $("#askSave"),
  // 简历优化（页内统一入口）
  optVersion: $("#optVersion"),
  optRun: $("#optRun"),
  optStatus: $("#optStatus"),
  // TXT 文件导入长期记忆
  fileInput: $("#fileInput"),
  filePick: $("#filePick"),
  fileName: $("#fileName"),
  fileParse: $("#fileParse"),
  fileStatus: $("#fileStatus"),
  // 手动添加
  addQ: $("#addQ"),
  addA: $("#addA"),
  addBtn: $("#addBtn"),
  genAnsBtn: $("#genAnsBtn"),
  // 长期记忆新增问答
  memNewQ: $("#memNewQ"),
  memNewA: $("#memNewA"),
  memGenAns: $("#memGenAns"),
  memAddSave: $("#memAddSave"),
  memNewStatus: $("#memNewStatus"),
};

let running = false;
let questions = [];      // [{question, answer, extra, saved}]
let memory = {};         // key -> {answer, extra}
// 原始简历（优化基准）：init 时从 /api/resume 的 original 取得，优化始终基于原文，保证幂等
let originalResume = "";

// ---------- 标签页 ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tabpanel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("#tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "memory") renderMemory();
  });
});

// ---------- 简历优化（页内统一入口，复用首页 /api/optimize 引擎）----------
async function optimizeResumeInplace() {
  if (running) { alert("正在优化中，请稍候…"); return; }
  const jd = ui.jd.value.trim();
  // 优化基准：始终基于原始简历，保证幂等；首次进入无 original 时以当前框内容为准
  const base = (originalResume && originalResume.length > 30) ? originalResume
            : (ui.resume.value.trim() || "");
  if (!base) { alert("请先在左侧粘贴原始简历（或去首页做一次优化）。"); return; }

  running = true;
  ui.optRun.disabled = true;
  ui.optRun.textContent = "优化中…";
  ui.optStatus.style.color = "";
  ui.optStatus.textContent = "正在联网检索并优化简历，请稍候…";
  ui.resume.value = "";
  let buffer = "";

  try {
    const resp = await fetch("/api/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume: base, jd: jd, version: ui.optVersion.value, enable_search: true }),
    });
    if (!resp.ok) { throw new Error("HTTP " + resp.status); }
    if (!resp.body || !resp.body.getReader) { throw new Error("当前浏览器不支持流式响应"); }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let partial = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      partial += decoder.decode(value, { stream: true });
      const lines = partial.split("\n");
      partial = lines.pop();
      for (const line of lines) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        let ev;
        try { ev = JSON.parse(t.slice(5).trim()); } catch (e) { continue; }
        if (ev.type === "progress") {
          ui.optStatus.textContent = ev.message || "处理中…";
        } else if (ev.type === "delta") {
          buffer += ev.text || "";
          ui.resume.value = buffer;
          ui.optStatus.textContent = "正在生成优化后简历…";
        } else if (ev.type === "error") {
          ui.optStatus.style.color = "#c0392b";
          ui.optStatus.textContent = ev.message || "优化失败";
          if (ev.payUrl) {
            if (confirm((ev.message || "需要开通会员") + "\n\n点击「确定」前往会员中心。")) {
              window.open(ev.payUrl, "_blank");
            }
          }
          running = false; ui.optRun.disabled = false; ui.optRun.textContent = "优化简历";
          return;
        } else if (ev.type === "result") {
          const resume = (ev.resume || "").trim();
          if (resume) {
            ui.resume.value = resume;
            // 写回后端：optimized 更新为本次结果，original 保持原基准不变
            await fetch("/api/resume", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ optimized: resume, original: base }),
            });
            // 锁定优化基准为本次原文，保证反复点击「优化简历」始终基于同一原始简历（幂等）
            originalResume = base;
            ui.optStatus.textContent = "优化完成，已自动更新简历并写回后端；可直接点「生成面试题」或去「问我」追问。";
          } else {
            ui.optStatus.textContent = "优化完成，但返回内容为空，请重试。";
          }
        }
      }
    }
  } catch (e) {
    ui.optStatus.style.color = "#c0392b";
    ui.optStatus.textContent = "优化失败：" + e.message;
  } finally {
    running = false;
    ui.optRun.disabled = false;
    ui.optRun.textContent = "优化简历";
  }
}

// ---------- 工具 ----------
function logProgress(msg) {
  const line = document.createElement("div");
  line.className = "line";
  line.textContent = msg;
  ui.progress.appendChild(line);
  ui.progress.scrollTop = ui.progress.scrollHeight;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function getContext() {
  return {
    resume: ui.resume.value.trim(),
    jd: ui.jd.value.trim(),
  };
}

// ---------- 面试题生成 ----------
async function generateInterview(append) {
  if (running) return;
  const { resume, jd } = getContext();
  if (!resume) { ui.status.textContent = "请先填写简历（可回到「简历优化」页保存后自动加载）。"; return; }
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD。"; return; }

  running = true;
  ui.gen.disabled = true;
  ui.more.disabled = true;
  ui.status.textContent = "生成中…";
  ui.progress.innerHTML = "";
  if (!append) { questions = []; }

  const count = parseInt(ui.count.value, 10) || 8;
  const enableSearch = ui.genSearch ? ui.genSearch.checked : true;
  const autoSave = ui.autoSave ? ui.autoSave.checked : true;
  let full = "";
  let reusedCount = 0;

  try {
    const resp = await fetch("/api/interview/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume, jd, count, enable_search: enableSearch, auto_save: autoSave }),
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
          logProgress(ev.message);
        } else if (ev.type === "delta") {
          full += ev.text;
        } else if (ev.type === "reused") {
          reusedCount++;
        } else if (ev.type === "saved") {
          loadMemory();
        } else if (ev.type === "error") {
          ui.status.textContent = ev.message;
          logProgress(ev.message);
        } else if (ev.type === "result") {
          const newQs = ev.questions || [];
          if (append) questions = questions.concat(newQs);
          else questions = newQs;
          // 后端已按 auto_save 逐条入库，前端无需再 saveMemory
          reusedCount = (ev.reused || []).length || reusedCount;
          renderQuestions();
        }
      }
    }
    const gen = questions.length;
    ui.status.textContent = `生成完成：新生成 ${gen} 道，复用长期记忆 ${reusedCount} 道（已自动剔除相似问题）。`;
  } catch (e) {
    ui.status.textContent = "请求失败：" + e.message;
    logProgress("请求失败：" + e.message);
  } finally {
    running = false;
    ui.gen.disabled = false;
    ui.more.disabled = false;
  }
}

// ---------- 渲染面试题 ----------
function normalizeQ(s) {
  return (s || "").trim().replace(/\s+/g, "").toLowerCase();
}

function renderQuestions() {
  if (!questions.length) {
    ui.qaList.innerHTML = '<div class="empty">点击「生成面试题」开始，题目会显示在这里。</div>';
    return;
  }
  // 重复检测：列表内重复 + 已存在于长期记忆
  const seenInList = {};
  const memKeys = Object.keys(memory).map(normalizeQ);
  questions.forEach((q) => {
    const n = normalizeQ(q.question);
    q._dupInList = seenInList[n] !== undefined;
    seenInList[n] = (seenInList[n] || 0) + 1;
    q._inMem = memKeys.includes(n);
  });
  ui.qaList.innerHTML = "";
  questions.forEach((q, i) => {
    const card = document.createElement("div");
    card.className = "qa-card" + (q._dupInList || q._inMem ? " dup" : "");
    const badge = (q._inMem || q._dupInList)
      ? `<div class="qa-badge">重复题目 · ${q._inMem ? "已存于长期记忆" : "列表内重复"}</div>`
      : "";
    const saveLabel = q._inMem ? "更新记忆" : "保存到长期记忆";
    card.innerHTML = `
      <div class="qa-no">第 ${i + 1} 题</div>
      ${badge}
      <div class="qa-q"><textarea class="qa-q-edit" data-i="${i}">${escapeHtml(q.question)}</textarea></div>
      <div class="qa-a-label">参考答案（可直接修改）</div>
      <div class="qa-a"><textarea class="qa-a-edit" data-i="${i}">${escapeHtml(q.answer)}</textarea></div>
      <div class="qa-extra" data-i="${i}"></div>
      <div class="qa-ask" data-i="${i}" style="display:none">
        <input type="text" class="qa-ask-input" placeholder="对这道题追问，例如：能不能换个更简单的说法？">
        <button class="btn-mini accent qa-ask-send">发送追问</button>
      </div>
      <div class="qa-actions">
        <button class="btn-mini qa-save">${saveLabel}</button>
        <button class="btn-mini qa-toggle-ask">追问</button>
      </div>`;
    ui.qaList.appendChild(card);
  });
  bindQuestionEvents();
  renderExtra();
}

function renderExtra() {
  questions.forEach((q, i) => {
    const box = ui.qaList.querySelector(`.qa-extra[data-i="${i}"]`);
    if (!box) return;
    if (q.extra && q.extra.length) {
      box.innerHTML = '<div class="qa-extra-label">答疑补充</div>' +
        q.extra.map((e) => `<div class="qa-extra-item">${escapeHtml(e.q)}<br><span>${escapeHtml(e.a)}</span></div>`).join("");
    } else {
      box.innerHTML = "";
    }
  });
}

function bindQuestionEvents() {
  ui.qaList.querySelectorAll(".qa-q-edit").forEach((el) => {
    el.addEventListener("input", () => { questions[el.dataset.i].question = el.value; });
  });
  ui.qaList.querySelectorAll(".qa-a-edit").forEach((el) => {
    el.addEventListener("input", () => { questions[el.dataset.i].answer = el.value; });
  });
  // 按钮统一用事件委托处理
  ui.qaList.onclick = (e) => {
    const card = e.target.closest(".qa-card");
    if (!card) return;
    const i = parseInt(card.querySelector(".qa-q-edit").dataset.i, 10);
    if (e.target.classList.contains("qa-save")) {
      const q = questions[i];
      const wasInMem = q._inMem;
      const isDup = q._dupInList;
      saveMemory(q.question, q.answer, q.extra, false).then(() => {
        questions[i].saved = true;
        questions[i]._inMem = true;
        e.target.textContent = "已保存";
        if (wasInMem) ui.status.textContent = "该题已存在于长期记忆，已更新内容。";
        else if (isDup) ui.status.textContent = "该题在列表中重复，已保存到长期记忆。";
        else ui.status.textContent = "已保存到长期记忆。";
      });
    } else if (e.target.classList.contains("qa-toggle-ask")) {
      const box = card.querySelector(".qa-ask");
      box.style.display = box.style.display === "none" ? "flex" : "none";
    } else if (e.target.classList.contains("qa-ask-send")) {
      const input = card.querySelector(".qa-ask-input");
      const text = input.value.trim();
      if (!text) return;
      askAboutQuestion(i, text, card);
    }
  };
}

// ---------- 单题追问 ----------
async function askAboutQuestion(i, text, card) {
  const q = questions[i];
  if (running) return;
  running = true;
  const sendBtn = card.querySelector(".qa-ask-send");
  const input = card.querySelector(".qa-ask-input");
  sendBtn.disabled = true;
  input.disabled = true;
  const extraLabel = card.querySelector(".qa-extra");
  const tmp = document.createElement("div");
  tmp.className = "qa-extra-item streaming";
  tmp.textContent = "AI 答疑中…";
  extraLabel.appendChild(tmp);

  try {
    const resp = await fetch("/api/interview/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q.question, original_answer: q.answer, user_question: text, query: text }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "", full = "";
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
        try { ev = JSON.parse(line); } catch (err) { continue; }
        if (ev.type === "delta") { full += ev.text; tmp.textContent = "你：" + text + "\nAI：" + full; }
        else if (ev.type === "error") { tmp.textContent = "错误：" + ev.message; }
        else if (ev.type === "result") { full = ev.answer || full; }
      }
    }
    if (!q.extra) q.extra = [];
    q.extra.push({ q: text, a: full });
    if (q.saved) saveMemory(q.question, q.answer, q.extra, false);
    renderExtra();
  } catch (e) {
    tmp.textContent = "请求失败：" + e.message;
  } finally {
    running = false;
    sendBtn.disabled = false;
    input.disabled = false;
    input.value = "";
  }
}

// ---------- 长期记忆 ----------
let currentMemKey = null;

function openMemModal(k) {
  if (!memory[k]) return;
  currentMemKey = k;
  ui.memModalTitle.textContent = k;
  ui.memModalText.value = memory[k].answer || "";
  ui.memModalExtra.value = memory[k].extra || "";
  ui.memModal.hidden = false;
  ui.memModalText.focus();
}
function closeMemModal() {
  ui.memModal.hidden = true;
  currentMemKey = null;
}
async function saveMemModal() {
  if (!currentMemKey) return;
  const k = currentMemKey;
  const ans = ui.memModalText.value;
  const extra = ui.memModalExtra.value;
  try {
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: k, answer: ans, extra }),
    });
    memory[k].answer = ans;
    memory[k].extra = extra;
    ui.status.textContent = "已更新记忆。";
  } catch (e) {
    ui.status.textContent = "保存失败：" + e.message;
  }
  closeMemModal();
  renderMemory();
}

async function saveMemory(key, answer, extra, notify) {
  const extraStr = Array.isArray(extra)
    ? extra.map((e) => `问：${e.q}\n答：${e.a}`).join("\n\n")
    : (extra || "");
  try {
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, answer, extra: extraStr }),
    });
    if (notify) ui.status.textContent = "已保存到长期记忆。";
  } catch (e) {
    if (notify) ui.status.textContent = "保存失败：" + e.message;
  }
}

async function loadMemory() {
  try {
    const resp = await fetch("/api/memory");
    memory = await resp.json();
  } catch (e) {
    memory = {};
  }
  renderMemory();
}

// 渲染记忆列表：keys 为展示顺序（默认全量倒序，检索时按相关度），hits 为检索命中信息（key -> {kw_score, score}）
function renderMemoryList(keys, hits) {
  if (!keys.length) {
    ui.memList.innerHTML = hits
      ? '<div class="empty">没有检索到相关记忆，换个关键词试试。</div>'
      : '<div class="empty">暂无长期记忆。在「面试题」页点击「保存到长期记忆」即可沉淀。</div>';
    return;
  }
  ui.memList.innerHTML = "";
  keys.forEach((k, i) => {
    const v = memory[k];
    if (!v) return;
    const seq = (v._seq != null && v._seq !== 0) ? v._seq : (i + 1);
    const hit = hits && hits[k];
    const hitBadge = hit
      ? `<span class="mem-hit">${hit.kw_score ? "关键词命中 " + hit.kw_score : ""}${hit.kw_score && hit.score ? " · " : ""}${hit.score ? "语义 " + hit.score.toFixed(2) : ""}</span>`
      : "";
    const row = document.createElement("div");
    row.className = "mem-row";
    row.innerHTML = `
      <div class="mem-key"><span class="mem-seq" style="color:#8a94a6;font-weight:600;margin-right:6px;">#${seq}</span>${escapeHtml(k)}${hitBadge}</div>
      <div class="mem-val">
        <div class="mem-ans-label">答案</div>
        <div class="mem-ans-preview" data-k="${escapeHtml(k)}">${escapeHtml(v.answer || "")}</div>
        ${v.extra ? `<div class="mem-extra-label">答疑补充</div><div class="mem-extra">${escapeHtml(v.extra)}</div>` : ""}
      </div>
      <div class="mem-actions">
        <button class="btn-mini mem-update" data-k="${escapeHtml(k)}">更新</button>
        <button class="btn-mini danger mem-del" data-k="${escapeHtml(k)}">删除</button>
      </div>`;
    // 整行点击（按钮除外）→ 弹窗展示该条全部数据
    row.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openMemModal(k);
    });
    ui.memList.appendChild(row);
  });

  ui.memList.querySelectorAll(".mem-update").forEach((btn) => {
    btn.addEventListener("click", () => openMemModal(btn.dataset.k));
  });
  ui.memList.querySelectorAll(".mem-del").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const k = btn.dataset.k;
      await fetch("/api/memory?key=" + encodeURIComponent(k), { method: "DELETE" });
      delete memory[k];
      renderMemory();
    });
  });
}

function renderMemory() {
  const kw = (ui.memSearch.value || "").trim();
  if (kw) { searchMemory(kw); return; }
  // 后端已按加入顺序倒序返回（最新在前），直接按 key 顺序渲染
  renderMemoryList(Object.keys(memory), null);
}

// 后端倒排索引 + 语义混合检索
let memSearchSeq = 0;
async function searchMemory(kw) {
  const seq = ++memSearchSeq;
  try {
    const resp = await fetch("/api/memory/search?q=" + encodeURIComponent(kw) + "&k=50");
    const results = await resp.json();
    if (seq !== memSearchSeq) return; // 已有更新的检索请求，丢弃过期结果
    const hits = {};
    const keys = [];
    (Array.isArray(results) ? results : []).forEach((r) => {
      keys.push(r.key);
      hits[r.key] = { kw_score: r.kw_score || 0, score: r.score || 0 };
    });
    renderMemoryList(keys, hits);
  } catch (e) {
    if (seq === memSearchSeq) renderMemoryList([], {});
  }
}

function exportMemory(asMd) {
  const keys = Object.keys(memory);
  if (!keys.length) { ui.status.textContent = "没有可导出的记忆。"; return; }
  let content, filename, type;
  if (asMd) {
    content = "# 面试长期记忆\n\n" + keys.map((k, i) => {
      const v = memory[k];
      let s = `## ${i + 1}. ${k}\n\n**答案**\n${v.answer || ""}`;
      if (v.extra) s += `\n\n**答疑补充**\n${v.extra}`;
      return s;
    }).join("\n\n");
    filename = "面试记忆.md";
    type = "text/markdown;charset=utf-8";
  } else {
    content = JSON.stringify(memory, null, 2);
    filename = "面试记忆.json";
    type = "application/json;charset=utf-8";
  }
  const blob = new Blob(["﻿" + content], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------- AI 生成答案（新增面试题 / 新增记忆问答共用） ----------
async function genAnswer(question, targetEl, statusEl, btn) {
  if (running) return;
  const q = (question || "").trim();
  if (!q) { if (statusEl) statusEl.textContent = "请先输入问题再生成答案。"; return; }
  const { resume, jd } = getContext();
  running = true;
  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = "AI 正在生成答案…";
  if (targetEl) targetEl.value = "";
  let full = "";
  try {
    const resp = await fetch("/api/generate-answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, resume, jd }),
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
        if (ev.type === "delta") { full += ev.text; if (targetEl) targetEl.value = full; }
        else if (ev.type === "error") { if (statusEl) statusEl.textContent = "生成失败：" + ev.message; if (targetEl) targetEl.value = ""; }
        else if (ev.type === "result") { full = ev.answer || full; if (targetEl) targetEl.value = full; }
      }
    }
    if (statusEl) statusEl.textContent = "已生成，可编辑后保存。";
  } catch (e) {
    if (statusEl) statusEl.textContent = "请求失败：" + e.message;
  } finally {
    running = false;
    if (btn) btn.disabled = false;
  }
}

// ---------- 自由提问 ----------
async function askFree() {
  if (running) return;
  const text = ui.askText.value.trim();
  if (!text) return;
  const { resume, jd } = getContext();
  appendAskMessage("你", text, false);
  ui.askText.value = "";

  running = true;
  ui.askSend.disabled = true;
  const ansEl = appendAskMessage("AI", "思考中…", true);

  try {
    const resp = await fetch("/api/interview/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: `我的简历如下：\n${resume}\n\n目标岗位：\n${jd}`,
        original_answer: "",
        user_question: text,
        query: text,
        auto_memory: ui.askSave.checked,
      }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "", full = "";
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
        if (ev.type === "delta") { full += ev.text; ansEl.textContent = full; ui.askLog.scrollTop = ui.askLog.scrollHeight; }
        else if (ev.type === "error") { ansEl.textContent = "错误：" + ev.message; }
        else if (ev.type === "result") { full = ev.answer || full; ansEl.textContent = full; }
        else if (ev.type === "saved") {
          ui.status.textContent = "已自动提炼并存入长期记忆：" + ev.key;
          loadMemory();
        }
      }
    }
  } catch (e) {
    ansEl.textContent = "请求失败：" + e.message;
  } finally {
    running = false;
    ui.askSend.disabled = false;
    ui.askLog.scrollTop = ui.askLog.scrollHeight;
  }
}

// ---------- TXT 文件解析导入长期记忆 ----------
let pendingFileText = "";

function setFileStatus(msg, isErr) {
  ui.fileStatus.textContent = msg;
  ui.fileStatus.classList.toggle("err", !!isErr);
}

async function parseFile() {
  if (running) return;
  if (!pendingFileText) { setFileStatus("请先选择 TXT 文件。", true); return; }
  running = true;
  ui.fileParse.disabled = true;
  ui.filePick.disabled = true;
  setFileStatus("AI 正在解析文件内容…", false);

  try {
    const resp = await fetch("/api/interview/parse-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: pendingFileText, filename: ui.fileName.dataset.name || "" }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "", full = "", saved = 0, total = 0;
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
        if (ev.type === "progress") { setFileStatus(ev.message, false); }
        else if (ev.type === "delta") { full += ev.text; }
        else if (ev.type === "error") { setFileStatus("解析失败：" + ev.message, true); }
        else if (ev.type === "result") { total = (ev.entries || []).length; setFileStatus(`解析完成，共 ${total} 条，正在入库…`, false); }
        else if (ev.type === "saved") { saved = ev.index; setFileStatus(`已导入 ${ev.index}/${ev.total}：${ev.key}`, false); }
        else if (ev.type === "done") { saved = ev.saved; total = ev.total; }
      }
    }
    if (total > 0) {
      setFileStatus(`导入完成：成功写入 ${saved}/${total} 条到长期记忆。`, false);
      loadMemory();
    } else if (!ui.fileStatus.classList.contains("err")) {
      setFileStatus("未解析出可导入的问答条目。", false);
    }
  } catch (e) {
    setFileStatus("请求失败：" + e.message, true);
  } finally {
    running = false;
    ui.fileParse.disabled = !pendingFileText;
    ui.filePick.disabled = false;
  }
}

function appendAskMessage(role, text, isAi) {
  const el = document.createElement("div");
  el.className = "ask-msg " + (isAi ? "ai" : "me");
  el.innerHTML = `<div class="ask-role">${role}</div><div class="ask-body">${escapeHtml(text)}</div>`;
  ui.askLog.appendChild(el);
  ui.askLog.scrollTop = ui.askLog.scrollHeight;
  return el.querySelector(".ask-body");
}

// ---------- 事件绑定 ----------
ui.gen.addEventListener("click", () => generateInterview(false));
ui.more.addEventListener("click", () => generateInterview(true));

// 手动添加面试题
ui.addBtn.addEventListener("click", () => {
  const q = ui.addQ.value.trim();
  if (!q) { ui.status.textContent = "请先输入要添加的面试题。"; return; }
  const a = ui.addA.value.trim();
  questions.unshift({ question: q, answer: a, extra: [], saved: false });
  ui.addQ.value = "";
  ui.addA.value = "";
  renderQuestions();
  ui.status.textContent = "已添加面试题（置顶显示）。";
});

// 手动添加面试题：用 AI 生成答案
ui.genAnsBtn.addEventListener("click", () => {
  genAnswer(ui.addQ.value, ui.addA, ui.status, ui.genAnsBtn);
});

// 长期记忆新增问答：AI 生成答案 + 保存到记忆
ui.memGenAns.addEventListener("click", () => {
  genAnswer(ui.memNewQ.value, ui.memNewA, ui.memNewStatus, ui.memGenAns);
});
ui.memAddSave.addEventListener("click", async () => {
  const k = ui.memNewQ.value.trim();
  const ans = ui.memNewA.value.trim();
  if (!k) { ui.memNewStatus.textContent = "请先输入问题。"; return; }
  try {
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: k, answer: ans, extra: "" }),
    });
    ui.memNewStatus.textContent = "已保存到长期记忆。";
    ui.memNewQ.value = "";
    ui.memNewA.value = "";
    loadMemory();
  } catch (e) {
    ui.memNewStatus.textContent = "保存失败：" + e.message;
  }
});

ui.refreshMem.addEventListener("click", loadMemory);
ui.exportJson.addEventListener("click", () => exportMemory(false));
ui.exportMd.addEventListener("click", () => exportMemory(true));
ui.clearMem.addEventListener("click", async () => {
  if (!confirm("确认清空全部长期记忆？此操作不可恢复。")) return;
  await fetch("/api/memory/clear", { method: "POST" });
  memory = {};
  renderMemory();
  ui.status.textContent = "已清空长期记忆。";
});
let memSearchTimer = null;
ui.memSearch.addEventListener("input", () => {
  clearTimeout(memSearchTimer);
  memSearchTimer = setTimeout(renderMemory, 300);
});

// 导入 JSON / MD 到长期记忆（保存即生成向量，与删除/新增共用同一套存储）
function bindImport(btn, input) {
  btn.addEventListener("click", () => input.click());
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    ui.status.textContent = "正在导入到长期记忆…";
    try {
      const resp = await fetch("/api/memory/import", { method: "POST", body: fd });
      const r = await resp.json();
      ui.status.textContent = r.ok
        ? `已导入 ${r.imported} 条到长期记忆。`
        : "导入失败：" + (r.error || "未知错误");
    } catch (e) {
      ui.status.textContent = "导入失败：" + e.message;
    } finally {
      input.value = "";
      loadMemory();
    }
  });
}
bindImport(ui.importJsonBtn, ui.importJson);
bindImport(ui.importMdBtn, ui.importMd);

// 记忆详情弹窗事件
ui.memModalClose.addEventListener("click", closeMemModal);
ui.memModalSave.addEventListener("click", saveMemModal);
ui.memModal.addEventListener("click", (e) => { if (e.target === ui.memModal) closeMemModal(); });
ui.memModalText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) saveMemModal();
  if (e.key === "Escape") closeMemModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !ui.memModal.hidden) closeMemModal();
});
ui.askSend.addEventListener("click", askFree);
ui.askText.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) askFree();
});

// TXT 文件导入：选文件 -> 读文本 -> 解析导入
ui.filePick.addEventListener("click", () => ui.fileInput.click());
ui.fileInput.addEventListener("change", () => {
  const file = ui.fileInput.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    pendingFileText = reader.result || "";
    ui.fileName.textContent = file.name;
    ui.fileName.dataset.name = file.name;
    ui.fileParse.disabled = !pendingFileText.trim();
    setFileStatus(`已读取文件（${pendingFileText.length} 字），点击「解析并导入」开始。`, false);
  };
  reader.onerror = () => setFileStatus("读取文件失败。", true);
  reader.readAsText(file, "utf-8");
});
ui.fileParse.addEventListener("click", parseFile);

// ---------- 初始化：加载当前用户在简历优化页保存的简历与 JD ----------
(async function init() {
  const resp = await fetch("/api/resume");
  if (resp.ok) {
    const r = await resp.json();
    // 优先带入优化后简历，没有则用原始简历
    const resume = (r.optimized || "").trim() || (r.original || "").trim();
    if (resume) ui.resume.value = resume;
    if (r.jd) ui.jd.value = r.jd;
    // 记住原始简历作为优化基准，保证「优化简历」幂等（始终基于原文优化）
    originalResume = (r.original || "").trim();
  }
  // 简历优化（页内统一入口）：复用首页同一引擎 /api/optimize
  ui.optRun.addEventListener("click", optimizeResumeInplace);
  loadMemory();
})();
