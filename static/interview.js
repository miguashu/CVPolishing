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
  // 语音对话
  voiceBar: $("#voiceBar"),
  voiceToggle: $("#voiceToggle"),
  voiceStatus: $("#voiceStatus"),
  voiceInterim: null,  // 运行时动态创建，见 5.2
  // 简历优化（页内统一入口）
  optVersion: $("#optVersion"),
  optRun: $("#optRun"),
  optStatus: $("#optStatus"),
  // 简历文件上传解析
  resumeFile: $("#resumeFile"),
  resumeFilePick: $("#resumeFilePick"),
  resumeFileName: $("#resumeFileName"),
  resumeFileParse: $("#resumeFileParse"),
  resumeFileStatus: $("#resumeFileStatus"),
  // 按需修改简历
  applyReq: $("#applyReq"),
  applyRun: $("#applyRun"),
  applyReset: $("#applyReset"),
  applyStatus: $("#applyStatus"),
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

// 全局互斥锁：仅用于「重流程」（简历优化 + 面试题生成）互相排斥、以及防止自身重入。
// 单题追问 / 自由提问 / AI 生成答案 各自用独立锁，避免被生成流程的 running 误伤而完全无法发送。
let running = false;
let asking = false;        // 单题追问（问我）锁
let freeAsking = false;    // 自由提问锁
let genAnsing = false;     // AI 生成答案锁
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
    const tabName = btn.dataset.tab;
    if (tabName === "memory") renderMemory();
    // 语音对话：离开「问我」Tab 时暂停监听，切回时自动恢复
    if (tabName !== "ask" && voiceChat && voiceChat.active) {
      voiceChat._stopListening();
    }
    if (tabName === "ask" && voiceChat && voiceChat.active) {
      setTimeout(() => voiceChat._startListening(), 200);
    }
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
  const origQuestions = append ? [...questions] : [];
  if (!append) { questions = []; }

  const count = parseInt(ui.count.value, 10) || 8;
  const enableSearch = ui.genSearch ? ui.genSearch.checked : true;
  const autoSave = ui.autoSave ? ui.autoSave.checked : true;
  // 补充模式：把已渲染的题目（含本次新生成与从记忆复用的）传给后端，模型直接避开历史
  const existing = append ? questions.map((q) => q.question) : [];
  let full = "";
  let reusedCount = 0;

  try {
    const resp = await fetch("/api/interview/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume, jd, count, enable_search: enableSearch,
        auto_save: autoSave, append: append, existing: existing }),
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
        } else if (ev.type === "questions_batch") {
          // 每批题生成完毕立即展示，不用等到全部完成
          const batch = ev.questions || [];
          if (batch.length) {
            questions = questions.concat(batch);
            renderQuestions();
          }
        } else if (ev.type === "result") {
          const newQs = ev.questions || [];
          // 复用的长期记忆题也并进列表：保证「要 8 条就显示 8 条」
          // （newQs 是本次新生成，reused 是命中长期记忆直接复用，合计 = count）
          const reusedQs = (ev.reused || []).map((r) => ({
            question: r.key,
            answer: r.answer || "",
            _fromMemory: true,
          }));
          if (append) questions = [...origQuestions, ...newQs, ...reusedQs];
          else questions = newQs.concat(reusedQs);
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
  // 停止正在进行的语音输入（避免 DOM 重建后状态丢失）
  if (voiceInput && voiceInput.listening) voiceInput.stop();
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
    // 复用题：直接命中长期记忆，语义是「来自长期记忆」而非「重复」
    const badge = q._fromMemory
      ? `<div class="qa-badge">来自长期记忆</div>`
      : (q._inMem || q._dupInList)
        ? `<div class="qa-badge">重复题目 · ${q._inMem ? "已存于长期记忆" : "列表内重复"}</div>`
        : "";
    const saveLabel = q._inMem ? "更新记忆" : "保存到长期记忆";
    card.innerHTML = `
      <div class="qa-no">第 ${i + 1} 题</div>
      ${badge}
      <div class="qa-q"><textarea class="qa-q-edit" data-i="${i}">${escapeHtml(q.question)}</textarea></div>
      <div class="qa-a-label">参考答案（可直接修改）<button class="voice-ans-btn" data-i="${i}" title="语音输入答案">语音输入</button></div>
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
    if (e.target.classList.contains("voice-ans-btn")) {
      // 语音输入按钮：切换当前题目的语音识别
      if (!voiceInput) return;
      e.preventDefault();
      voiceInput.toggle(i);
      return;
    }
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
  if (asking) return;
  asking = true;
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
    asking = false;
    sendBtn.disabled = false;
    input.disabled = false;
    input.value = "";
  }
}

// ---------- 长期记忆追问 ----------
async function askAboutMemory(k, text, row) {
  const v = memory[k];
  if (!v) return;

  const sendBtn = row.querySelector(".mem-ask-send");
  const input = row.querySelector(".mem-ask-input");
  sendBtn.disabled = true;
  input.disabled = true;

  const itemsArea = row.querySelector(".mem-extra-items");
  const tmp = document.createElement("div");
  tmp.className = "mem-ask-item streaming";
  tmp.textContent = "AI 答疑中…";
  itemsArea.appendChild(tmp);

  try {
    const resp = await fetch("/api/interview/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: k, original_answer: v.answer || "", user_question: text, query: text }),
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
        if (ev.type === "delta") {
          full += ev.text;
          tmp.textContent = "问：" + text + "\n答：" + full;
        } else if (ev.type === "error") {
          tmp.textContent = "错误：" + ev.message;
        } else if (ev.type === "result") {
          full = ev.answer || full;
        }
      }
    }
    tmp.className = "mem-ask-item";
    tmp.textContent = "问：" + text + "\n答：" + full;

    // 追加到记忆的 extra 并持久化
    let extra = v.extra || "";
    if (extra) extra += "\n\n";
    extra += "问：" + text + "\n答：" + full;
    v.extra = extra;
    await fetch("/api/memory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: k, answer: v.answer, extra }),
    });
  } catch (e) {
    tmp.textContent = "请求失败：" + e.message;
    tmp.className = "mem-ask-item error";
  } finally {
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
    const seq = (v.seq != null && v.seq !== 0) ? v.seq : (i + 1);
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
        <div class="mem-extra-items"></div>
        <div class="mem-ask" data-k="${escapeHtml(k)}" style="display:none">
          <input type="text" class="mem-ask-input" placeholder="对这条记忆追问，例如：能不能换一种更简单的说法？">
          <button class="btn-mini accent mem-ask-send" data-k="${escapeHtml(k)}">发送追问</button>
        </div>
      </div>
      <div class="mem-actions">
        <button class="btn-mini mem-update" data-k="${escapeHtml(k)}">更新</button>
        <button class="btn-mini mem-toggle-ask" data-k="${escapeHtml(k)}">追问</button>
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
  ui.memList.querySelectorAll(".mem-toggle-ask").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();  // 不触发行点击弹窗
      const k = btn.dataset.k;
      const row = btn.closest(".mem-row");
      const askBox = row.querySelector(".mem-ask");
      askBox.style.display = askBox.style.display === "none" ? "flex" : "none";
      if (askBox.style.display !== "none") {
        row.querySelector(".mem-ask-input").focus();
      }
    });
  });
  ui.memList.querySelectorAll(".mem-ask-send").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const k = btn.dataset.k;
      const row = btn.closest(".mem-row");
      const input = row.querySelector(".mem-ask-input");
      const text = input.value.trim();
      if (!text) return;
      askAboutMemory(k, text, row);
    });
  });
  // 追问输入框回车发送
  ui.memList.querySelectorAll(".mem-ask-input").forEach((inp) => {
    inp.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.stopPropagation();
      const k = inp.closest(".mem-ask").dataset.k;
      const row = inp.closest(".mem-row");
      const text = inp.value.trim();
      if (!text) return;
      askAboutMemory(k, text, row);
    });
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
  // 按 seq 倒序（最新在前）：后端 get_all 已 ORDER BY seq DESC，这里兜底再排一次
  const keys = Object.keys(memory).sort((a, b) => (memory[b].seq || 0) - (memory[a].seq || 0));
  renderMemoryList(keys, null);
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
  if (genAnsing) return;
  const q = (question || "").trim();
  if (!q) { if (statusEl) statusEl.textContent = "请先输入问题再生成答案。"; return; }
  const { resume, jd } = getContext();
  genAnsing = true;
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
    genAnsing = false;
    if (btn) btn.disabled = false;
  }
}

// ========== 语音对话模块（Web Speech API） ==========
// 零依赖、纯浏览器内置 API。SpeechRecognition 采集用户语音 → 转文本 → 送入现有 askFree 流式问答管道
// → SSE delta 流 → SpeechSynthesis 分段朗读。支持打断（用户再说话自动停止当前朗读）。

class VoiceChat {
  constructor() {
    this.active = false;           // 语音模式开关
    this.listening = false;        // 是否正在监听
    this.speaking = false;        // 是否正在朗读 AI 回答
    this.recognition = null;      // SpeechRecognition 实例
    this.pendingSSE = null;       // 当前 SSE 读流的 AbortController（用于打断时中断网络请求）
    this.utteranceQueue = [];     // TTS 朗读队列
    this.currentUtterance = null; // 当前正在播放的 utterance
    this.speechSupported = false; // 浏览器是否支持
  }

  /** 检测浏览器是否支持 Web Speech API */
  checkSupport() {
    const hasSR = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    const hasSS = !!window.speechSynthesis;
    this.speechSupported = hasSR && hasSS;
    return { recognition: hasSR, synthesis: hasSS };
  }

  /** 切换语音模式开关。正在朗读时点击 = 打断并恢复聆听 */
  toggle() {
    if (!this.speechSupported) {
      this._setStatus('浏览器不支持语音功能，请使用 Chrome 或 Edge', 'error');
      return;
    }
    if (this.active) {
      if (this.speaking) {
        // 正在朗读中，点击打断并恢复聆听（用户要插话）
        this._cancelAllSpeech();
        this._resumeRecognition();
        this._setStatus('正在聆听...', 'listening');
        return;
      }
      this.stop();
    } else {
      this.start();
    }
  }

  /** 开启语音模式 */
  start() {
    const sup = this.checkSupport();
    if (!sup.recognition) {
      this._setStatus('当前浏览器不支持语音识别，请使用 Chrome 或 Edge', 'error');
      return;
    }
    if (!sup.synthesis) {
      this._setStatus('当前浏览器不支持语音合成，请使用 Chrome 或 Edge', 'error');
      return;
    }
    this.active = true;
    ui.voiceToggle.classList.add('active');
    ui.voiceToggle.querySelector('.voice-label').textContent = '关闭语音';
    this._setStatus('正在初始化麦克风...', 'listening');
    this._startListening();
  }

  /** 关闭语音模式 */
  stop() {
    this.active = false;
    this._stopListening();
    this._cancelSSE();
    this._cancelAllSpeech();
    ui.voiceToggle.classList.remove('active', 'listening', 'speaking');
    ui.voiceToggle.querySelector('.voice-label').textContent = '语音对话';
    this._setStatus('语音对话已关闭', '');
  }

  // ---------- 语音识别（ASR）----------

  _initRecognition() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = 'zh-CN';               // 中文识别
    rec.interimResults = true;        // 开启中间结果（边说边显示）
    rec.continuous = true;            // 持续监听，不因一次停顿就结束
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) {
          final += r[0].transcript;
        } else {
          interim += r[0].transcript;
        }
      }
      // 实时显示中间结果
      this._showInterim(interim);
      // 有定稿文本时：打断当前朗读 → 发送到 AI
      if (final.trim()) {
        this._onSpeechFinal(final.trim());
      }
    };

    rec.onerror = (event) => {
      // no-speech / audio-capture / not-allowed 等
      if (event.error === 'no-speech') {
        // 用户没说话，静默忽略，不重置 —— continuous 模式下自动继续监听
        return;
      }
      if (event.error === 'aborted') {
        // 手动 stop 触发的，正常行为
        return;
      }
      if (event.error === 'not-allowed') {
        this._setStatus('麦克风权限被拒绝，请在浏览器设置中允许', 'error');
        this._stopListening();
        return;
      }
      console.warn('[VoiceChat] SpeechRecognition error:', event.error, event.message);
      // 其他错误：短暂等待后自动重试
      setTimeout(() => {
        if (this.active && !this.listening) {
          this._startListening();
        }
      }, 800);
    };

    rec.onend = () => {
      this.listening = false;
      ui.voiceToggle.classList.remove('listening');
      // TTS 播放期间不自动重启识别（防止把 AI 声音也识别进去）
      if (this.active && !this.listening && !this.speaking) {
        setTimeout(() => {
          if (this.active && !this.listening && !this.speaking) {
            this._startListening();
          }
        }, 300);
      }
    };

    return rec;
  }

  _startListening() {
    if (this.listening) return;
    try {
      if (!this.recognition) {
        this.recognition = this._initRecognition();
      }
      this.recognition.start();
      this.listening = true;
      ui.voiceToggle.classList.add('listening');
      this._setStatus('正在聆听...', 'listening');
      this._ensureInterimEl();
    } catch (e) {
      console.warn('[VoiceChat] start failed:', e);
      this.listening = false;
      setTimeout(() => {
        if (this.active && !this.listening) this._startListening();
      }, 1000);
    }
  }

  _stopListening() {
    this.listening = false;
    if (this.recognition) {
      try { this.recognition.stop(); } catch (e) { /* 忽略 stop 异常 */ }
    }
    this._removeInterimEl();
  }

  /** 暂停语音识别（TTS 播放期间防止把 AI 声音也识别进去） */
  _pauseRecognition() {
    if (!this.listening) return;
    this.listening = false;
    if (this.recognition) {
      try { this.recognition.stop(); } catch (e) { /* ignore */ }
    }
    ui.voiceToggle.classList.remove('listening');
  }

  /** 恢复语音识别（TTS 播完后重新开始聆听） */
  _resumeRecognition() {
    if (!this.active) return;
    if (this.listening) return;
    if (this.speaking) return; // 仍在朗读中，不恢复
    this._startListening();
  }

  /** 语音定稿回调：打断当前朗读并送入 AI 流式问答管道 */
  _onSpeechFinal(text) {
    this._showInterim(''); // 清空中间结果
    // 打断：停止当前 AI 回答的朗读（用户要听新的回答了）
    this._cancelAllSpeech();
    // 发送文本到现有 askFree 管道（复用后端 /api/interview/ask SSE）
    this._sendVoiceQuery(text);
  }

  /** 实时显示语音识别的中间结果 */
  _showInterim(text) {
    this._ensureInterimEl();
    if (ui.voiceInterim) {
      ui.voiceInterim.textContent = text || '';
      ui.voiceInterim.style.display = text ? 'block' : 'none';
    }
  }

  _ensureInterimEl() {
    if (ui.voiceInterim) return;
    ui.voiceInterim = document.createElement('div');
    ui.voiceInterim.className = 'voice-interim';
    ui.voiceBar.after(ui.voiceInterim);
  }

  _removeInterimEl() {
    if (ui.voiceInterim) {
      ui.voiceInterim.remove();
      ui.voiceInterim = null;
    }
  }

  // ---------- 发送语音文本到 AI ----------

  async _sendVoiceQuery(text) {
    if (freeAsking) {
      // 上一轮追问还在进行中，先中断再发新请求
      this._cancelSSE();
      // 等一小段让 freeAsking 释放
      await new Promise(r => setTimeout(r, 100));
    }
    // 直接复用 askFree 的完整流程：
    //   1) 把文本填入 #askText 输入框
    //   2) 触发现有 send 逻辑（appendAskMessage + SSE 流式读取）
    //   3) SSE delta 到达时同时送入 TTS 朗读队列
    ui.askText.value = text;
    this._voiceAskFree(text);
  }

  /** 语音版 askFree：与原 askFree 逻辑一致，但增补 TTS 朗读分支 */
  async _voiceAskFree(text) {
    if (freeAsking) return;
    const { resume, jd } = getContext();
    appendAskMessage('你', text, false);
    ui.askText.value = '';

    freeAsking = true;
    ui.askSend.disabled = true;
    const ansEl = appendAskMessage('AI', '思考中...', true);
    this._setStatus('AI 正在生成回答...', 'speaking');
    ui.voiceToggle.classList.add('speaking');

    // 创建 AbortController 用于打断时中断网络请求
    const controller = new AbortController();
    this.pendingSSE = controller;

    try {
      const resp = await fetch('/api/interview/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: `我的简历如下：\n${resume}\n\n目标岗位：\n${jd}`,
          original_answer: '',
          user_question: text,
          query: text,
          auto_memory: ui.askSave.checked,
        }),
        signal: controller.signal,
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let acc = '', full = '';
      // SSE 增量文本缓冲区（用于 TTS 分段切割）
      let speechBuffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        acc += decoder.decode(value, { stream: true });
        const parts = acc.split('\n\n');
        acc = parts.pop();
        for (const part of parts) {
          const line = part.replace(/^data:\s*/, '').trim();
          if (!line || line === '[DONE]') continue;
          let ev;
          try { ev = JSON.parse(line); } catch (e) { continue; }
          if (ev.type === 'delta') {
            full += ev.text;
            ansEl.textContent = full;
            ui.askLog.scrollTop = ui.askLog.scrollHeight;
            // → 累积到 TTS 缓冲区，达到切割条件时分段入队朗读
            speechBuffer += ev.text;
            speechBuffer = this._flushSpeechBuffer(speechBuffer);
          } else if (ev.type === 'error') {
            ansEl.textContent = '错误：' + ev.message;
          } else if (ev.type === 'result') {
            full = ev.answer || full;
            ansEl.textContent = full;
          } else if (ev.type === 'saved') {
            ui.status.textContent = '已自动提炼并存入长期记忆：' + ev.key;
            loadMemory();
          }
        }
      }
      // SSE 流结束，把缓冲区剩余文本全部送入 TTS
      if (speechBuffer.trim()) {
        this._enqueueSpeech(speechBuffer.trim());
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        ansEl.textContent = '请求失败：' + e.message;
      }
    } finally {
      freeAsking = false;
      ui.askSend.disabled = false;
      ui.askLog.scrollTop = ui.askLog.scrollHeight;
      ui.voiceToggle.classList.remove('speaking');
      this.pendingSSE = null;
      // 朗读完成后恢复"正在聆听"状态
      if (this.active) {
        this._setStatus('正在聆听...', 'listening');
      }
    }
  }

  /** 从文本缓冲区中切出完整句子送入 TTS 队列，返回剩余文本 */
  _flushSpeechBuffer(buf) {
    // 切割标记：句号、问号、感叹号、换行；保留标点以便朗读自然停顿
    const re = /([^。！？\n]+[。！？\n])/g;
    let m;
    let lastIdx = 0;
    while ((m = re.exec(buf)) !== null) {
      const chunk = m[0].trim();
      if (chunk.length >= 4) {
        // 至少 4 个字符才入队朗读（过滤太短的碎片）
        this._enqueueSpeech(chunk);
      }
      lastIdx = m.index + m[0].length;
    }
    return buf.slice(lastIdx);
  }

  // ---------- 语音合成（TTS）----------

  _enqueueSpeech(text) {
    if (!this.active) return; // 语音模式已关闭，不朗读
    this.utteranceQueue.push(text);
    this._playNextInQueue();
  }

  _playNextInQueue() {
    if (this.currentUtterance || this.utteranceQueue.length === 0) return;
    const text = this.utteranceQueue.shift();
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = 'zh-CN';
    utt.rate = 1.05;    // 稍快，接近自然对话语速
    utt.pitch = 1.0;
    utt.volume = 1.0;
    // 选取中文女声（各浏览器实现不同，优先匹配包含 "Chinese" 或 "Zhiyu" 的语音）
    utt.voice = this._pickVoice();

    utt.onstart = () => {
      this.speaking = true;
      this._pauseRecognition();  // 暂停识别，防止把 AI 声音也识别进去
      this._setStatus('AI 正在朗读回答...', 'speaking');
    };
    utt.onend = () => {
      this.currentUtterance = null;
      if (this.utteranceQueue.length === 0) {
        this.speaking = false;
        this._resumeRecognition();
      } else {
        this._playNextInQueue();
      }
    };
    utt.onerror = (e) => {
      if (e.error === 'canceled' || e.error === 'interrupted') {
        // 用户打断或手动取消，不清空队列（cancelAllSpeech 已处理）
        this.currentUtterance = null;
        return;
      }
      // 其他错误：跳过当前句，继续播下一句
      console.warn('[VoiceChat] TTS error:', e.error);
      this.currentUtterance = null;
      if (this.utteranceQueue.length === 0) {
        this.speaking = false;
        this._resumeRecognition();
      } else {
        this._playNextInQueue();
      }
    };

    this.currentUtterance = utt;
    window.speechSynthesis.speak(utt);
  }

  _pickVoice() {
    const voices = window.speechSynthesis.getVoices();
    // 优先：包含 "Zhiyu"（Edge/Windows 中文女声）或 "Chinese" 且 female
    let best = voices.find(v => v.lang.startsWith('zh') && v.name.includes('Zhiyu'));
    if (!best) best = voices.find(v => v.lang.startsWith('zh-CN') && v.name.toLowerCase().includes('female'));
    if (!best) best = voices.find(v => v.lang.startsWith('zh-CN'));
    if (!best) best = voices.find(v => v.lang.startsWith('zh'));
    return best || voices[0];
  }

  // ---------- 打断 ----------

  /** 中断当前 SSE 网络请求 */
  _cancelSSE() {
    if (this.pendingSSE) {
      this.pendingSSE.abort();
      this.pendingSSE = null;
    }
  }

  /** 取消所有正在播放和排队的 TTS */
  _cancelAllSpeech() {
    window.speechSynthesis.cancel();
    this.utteranceQueue = [];
    this.currentUtterance = null;
    this.speaking = false;
    ui.voiceToggle.classList.remove('speaking');
  }

  // ---------- 状态显示 ----------

  _setStatus(msg, cls) {
    ui.voiceStatus.textContent = msg;
    ui.voiceStatus.className = 'voice-status ' + (cls || '');
  }
}

// ---------- 面试答题语音输入（纯 ASR，无 TTS）----------
class VoiceInput {
  constructor() {
    this.recognition = null;
    this.listening = false;
    this.targetIdx = -1;
    this.supported = false;
    this._accumulated = '';
    this._silenceTimer = null;
    this._init();
  }

  _init() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.supported = !!SR;
  }

  /** 对指定题号的答案文本框开始语音输入 */
  start(idx) {
    if (!this.supported) { alert('浏览器不支持语音识别，请使用 Chrome 或 Edge'); return; }
    if (this.listening) this.stop();
    if (voiceChat && voiceChat.active) voiceChat.stop(); // 关闭对话模式避免冲突

    this.targetIdx = idx;
    const targetEl = document.querySelector('.qa-a-edit[data-i="' + idx + '"]');
    const btnEl = document.querySelector('.voice-ans-btn[data-i="' + idx + '"]');
    this._accumulated = targetEl ? targetEl.value : '';

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.interimResults = true;
    rec.continuous = true;
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      let interim = '';
      let finalText = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) { finalText += r[0].transcript; }
        else { interim += r[0].transcript; }
      }
      if (finalText) this._accumulated += finalText;
      const display = this._accumulated + (interim ? ' ' + interim : '');
      const ta = document.querySelector('.qa-a-edit[data-i="' + this.targetIdx + '"]');
      if (ta) {
        ta.value = display;
        // 同步到 questions 数组
        if (typeof questions !== 'undefined' && questions[this.targetIdx]) {
          questions[this.targetIdx].answer = display;
        }
      }
      this._showInterim(interim);
    };

    rec.onspeechstart = () => { clearTimeout(this._silenceTimer); };
    rec.onspeechend = () => {
      // 用户停止说话 2 秒后自动结束录音
      clearTimeout(this._silenceTimer);
      this._silenceTimer = setTimeout(() => this.stop(), 2000);
    };

    rec.onerror = (event) => {
      if (event.error === 'no-speech') return;
      if (event.error === 'aborted') return;
      if (event.error === 'not-allowed') {
        alert('麦克风权限被拒绝，请在浏览器设置中允许');
        this._cleanup();
        return;
      }
      console.warn('[VoiceInput] error:', event.error);
    };

    rec.onend = () => {
      // 非主动 stop 导致的 onend：清理状态但不自动重启
      this.listening = false;
      this._updateBtn(false);
      this._hideInterim();
    };

    try {
      rec.start();
      this.listening = true;
      this.recognition = rec;
      this._updateBtn(true);
    } catch (e) {
      console.warn('[VoiceInput] start failed:', e);
    }
  }

  stop() {
    clearTimeout(this._silenceTimer);
    this.listening = false;
    if (this.recognition) {
      try { this.recognition.stop(); } catch (e) {}
      this.recognition = null;
    }
    this._updateBtn(false);
    this._hideInterim();
    this.targetIdx = -1;
  }

  toggle(idx) {
    if (this.listening && this.targetIdx === idx) {
      this.stop();
    } else {
      this.start(idx);
    }
  }

  _cleanup() {
    clearTimeout(this._silenceTimer);
    this.listening = false;
    if (this.recognition) {
      try { this.recognition.stop(); } catch (e) {}
      this.recognition = null;
    }
    this._updateBtn(false);
    this._hideInterim();
  }

  _updateBtn(active) {
    const btnEl = document.querySelector('.voice-ans-btn[data-i="' + this.targetIdx + '"]');
    if (!btnEl) return;
    if (active) {
      btnEl.classList.add('listening');
      btnEl.textContent = '聆听中...';
    } else {
      btnEl.classList.remove('listening');
      btnEl.textContent = '语音输入';
    }
  }

  _showInterim(text) {
    if (!text) { this._hideInterim(); return; }
    let el = document.querySelector('.voice-ans-interim[data-i="' + this.targetIdx + '"]');
    if (!el) {
      el = document.createElement('div');
      el.className = 'voice-ans-interim';
      el.dataset.i = this.targetIdx;
      const answerArea = document.querySelector('.qa-a[data-i-ans="' + this.targetIdx + '"]');
      if (answerArea) answerArea.appendChild(el);
      else {
        // 回退：放到 .qa-a 下面
        const ta = document.querySelector('.qa-a-edit[data-i="' + this.targetIdx + '"]');
        if (ta && ta.parentElement) ta.parentElement.appendChild(el);
      }
    }
    el.textContent = '正在聆听: ' + text;
    el.style.display = 'block';
  }

  _hideInterim() {
    const el = document.querySelector('.voice-ans-interim[data-i="' + this.targetIdx + '"]');
    if (el) el.style.display = 'none';
  }
}

// 全局单例
let voiceChat = null;
let voiceInput = null;

// ---------- 自由提问 ----------
async function askFree() {
  if (freeAsking) return;
  const text = ui.askText.value.trim();
  if (!text) return;
  const { resume, jd } = getContext();
  appendAskMessage("你", text, false);
  ui.askText.value = "";

  freeAsking = true;
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
    freeAsking = false;
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

// ---------- 语音对话初始化 ----------
function initVoiceChat() {
  voiceChat = new VoiceChat();
  voiceInput = new VoiceInput();
  const sup = voiceChat.checkSupport();
  if (!sup.recognition || !sup.synthesis) {
    // 浏览器不支持，语音按钮变灰并提示
    ui.voiceToggle.disabled = true;
    ui.voiceToggle.title = '当前浏览器不支持语音功能（需 Chrome 或 Edge）';
    voiceChat._setStatus('浏览器不支持语音功能', 'error');
    // voiceInput 仅需 SpeechRecognition，synthesis 不支持时仍可用
    if (!voiceInput.supported) {
      document.querySelectorAll('.voice-ans-btn').forEach(b => b.style.display = 'none');
    }
    return;
  }
  // 预加载语音列表（某些浏览器异步加载）
  window.speechSynthesis.onvoiceschanged = () => {
    window.speechSynthesis.getVoices();
  };
  window.speechSynthesis.getVoices();

  ui.voiceToggle.addEventListener('click', () => voiceChat.toggle());

  // 键盘快捷键：Ctrl+Shift+V 切换语音对话
  document.addEventListener('keydown', (e) => {
    if (e.key === 'v' && e.ctrlKey && e.shiftKey) {
      e.preventDefault();
      voiceChat.toggle();
    }
  });
}

// 页面加载后初始化
initVoiceChat();

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

// ---------- 简历文件上传解析（PDF / Word / TXT / Markdown）----------
let pendingResumeFile = null;   // 待解析的 File 对象

function setResumeFileStatus(msg, isErr) {
  ui.resumeFileStatus.textContent = msg;
  ui.resumeFileStatus.classList.toggle("err", !!isErr);
}

ui.resumeFilePick.addEventListener("click", () => ui.resumeFile.click());
ui.resumeFile.addEventListener("change", () => {
  const file = ui.resumeFile.files[0];
  if (!file) return;
  pendingResumeFile = file;
  ui.resumeFileName.textContent = file.name;
  ui.resumeFileName.dataset.name = file.name;
  ui.resumeFileParse.disabled = false;
  setResumeFileStatus(`已选择文件（${(file.size / 1024).toFixed(0)} KB），点击「解析并填入简历」。`, false);
});

ui.resumeFileParse.addEventListener("click", async () => {
  if (!pendingResumeFile) { setResumeFileStatus("请先选择文件。", true); return; }
  const file = pendingResumeFile;
  ui.resumeFileParse.disabled = true;
  ui.resumeFileParse.textContent = "解析中…";
  setResumeFileStatus("正在解析文件…", false);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/resume/parse", { method: "POST", body: fd });
    const r = await resp.json();
    if (!resp.ok) throw new Error(r.error || ("HTTP " + resp.status));
    ui.resume.value = r.text;
    originalResume = r.text;   // 以解析结果作为后续优化基准
    setResumeFileStatus(`解析完成，已填入简历框（${r.text.length} 字）。可继续优化、按需修改或生成面试题。`, false);
    await fetch("/api/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ original: r.text }),
    });
  } catch (e) {
    setResumeFileStatus("解析失败：" + e.message, true);
  } finally {
    ui.resumeFileParse.disabled = false;
    ui.resumeFileParse.textContent = "解析并填入简历";
  }
});

// ---------- 按需修改简历（SSE 流式，复用 /api/resume/apply-change）----------
let applying = false;          // 按需修改锁
let lastApplyResume = "";      // 最近一次修改结果，供「回填」使用

ui.applyRun.addEventListener("click", applyResumeChange);
ui.applyReset.addEventListener("click", () => {
  if (!lastApplyResume) return;
  ui.resume.value = lastApplyResume;
  ui.applyReset.hidden = true;
  setApplyStatus("已将修改结果回填到简历框。", false);
});

function setApplyStatus(msg, isErr) {
  ui.applyStatus.textContent = msg;
  ui.applyStatus.style.color = isErr ? "#c0392b" : "";
}

async function applyResumeChange() {
  if (applying) { alert("正在修改中，请稍候…"); return; }
  const resume = ui.resume.value.trim();
  const requirement = ui.applyReq.value.trim();
  if (!resume) { alert("请先填写或上传简历。"); return; }
  if (!requirement) { alert("请填写修改需求，例如：突出项目管理能力。"); return; }

  applying = true;
  ui.applyRun.disabled = true;
  ui.applyRun.textContent = "修改中…";
  setApplyStatus("正在按需求修改简历…");
  ui.applyReset.hidden = true;
  let buffer = "";

  try {
    const resp = await fetch("/api/resume/apply-change", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume, requirement, jd: ui.jd.value.trim() }),
    });
    if (!resp.ok) {
      const r = await resp.json().catch(() => ({}));
      throw new Error(r.error || ("HTTP " + resp.status));
    }
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
          setApplyStatus(ev.message || "处理中…");
        } else if (ev.type === "delta") {
          buffer += ev.text || "";
        } else if (ev.type === "error") {
          setApplyStatus(ev.message || "修改失败", true);
          applying = false; ui.applyRun.disabled = false; ui.applyRun.textContent = "按需求修改";
          return;
        } else if (ev.type === "result") {
          lastApplyResume = (ev.resume || "").trim();
          if (lastApplyResume) {
            ui.resume.value = lastApplyResume;
            ui.applyReset.hidden = false;
            const noteText = (ev.notes || "").trim()
              ? "　修改说明：" + ev.notes.replace(/\n+/g, "；")
              : "";
            setApplyStatus("修改完成，已预览修改结果。如满意请点「回填修改结果」覆盖简历框；或直接生成面试题。" + noteText);
          } else {
            setApplyStatus("修改完成但内容为空，请重试。", true);
          }
        }
      }
    }
    if (!buffer.trim()) setApplyStatus("未收到修改结果，请重试。", true);
  } catch (e) {
    setApplyStatus("修改失败：" + e.message, true);
  } finally {
    applying = false;
    ui.applyRun.disabled = false;
    ui.applyRun.textContent = "按需求修改";
  }
}

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
