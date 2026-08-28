// JD 生成工作经历前端：JD 图片 OCR / SSE 流式生成经历卡片 / 编辑 / 复制 / 存入简历 / 评估匹配度
const $ = (sel) => document.querySelector(sel);

const ui = {
  jd: $("#jd"),
  jdImage: $("#jdImage"),
  ocrBtn: $("#ocrBtn"),
  ocrStatus: $("#ocrStatus"),
  useResume: $("#useResume"),
  refResume: $("#refResume"),
  count: $("#count"),
  targetRole: $("#targetRole"),
  gen: $("#gen"),
  status: $("#status"),
  progress: $("#progress"),
  jdKw: $("#jdKw"),
  jdKwRole: $("#jdKwRole"),
  jdKwChips: $("#jdKwChips"),
  expList: $("#expList"),
  copyAll: $("#copyAll"),
  undoSave: $("#undoSave"),
};

let experiences = [];        // 当前生成的经历列表
let generating = false;
const BACKUP_KEY = "jd_resume_backup";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[c]));
}

function expText(exp) {
  const company = exp.company.endsWith("公司") ? exp.company : exp.company + " 公司";
  const head = company + (exp.role ? " | " + exp.role : "") + (exp.period ? " | " + exp.period : "");
  const bullets = (exp.bullets || []).map((b) => "- " + b).join("\n");
  return head + (bullets ? "\n" + bullets : "");
}

// ---------- 初始化：加载参考简历 ----------
async function loadRefResume() {
  if (!ui.useResume.checked) return;
  try {
    const resp = await fetch("/api/resume");
    if (resp.ok) {
      const r = await resp.json();
      const text = (r.optimized || "").trim() || (r.original || "").trim();
      if (text) ui.refResume.value = text;
    }
  } catch (e) { /* 静默失败，不阻塞页面 */ }
}

// ---------- JD 图片识别（复用 /api/jd/ocr） ----------
function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
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

// ---------- 渲染 ----------
function renderKeywords(keywords, role) {
  ui.jdKw.hidden = false;
  ui.jdKwRole.textContent = role ? "匹配岗位：" + role : "";
  ui.jdKwChips.innerHTML = (keywords || []).map((k) => `<span class="chip">${esc(k)}</span>`).join("");
}

function renderExp(exp, i) {
  const card = document.createElement("div");
  card.className = "exp-card";
  card.id = "exp_" + i;
  card.innerHTML = `
    <div class="exp-head">
      <span class="exp-title">${esc(exp.company)} 公司 · ${esc(exp.role)} · ${esc(exp.period)}</span>
    </div>
    <ul class="exp-bullets">${(exp.bullets || []).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
    <div class="exp-actions">
      <button class="btn-mini" data-act="copy">复制本条</button>
      <button class="btn-mini" data-act="edit">编辑</button>
      <button class="btn-mini" data-act="score">评估匹配度</button>
      <button class="btn-mini accent" data-act="save">存入简历</button>
      <span class="exp-score" data-score></span>
    </div>`;
  ui.expList.appendChild(card);
  return card;
}

// ---------- 生成（SSE 流式） ----------
async function generate() {
  const jd = ui.jd.value.trim();
  if (!jd) { ui.status.textContent = "请先填写目标岗位 JD。"; return; }
  if (generating) return;
  generating = true;
  ui.gen.disabled = true;
  ui.expList.classList.add("locked");
  ui.status.textContent = "正在分析 JD 关键词…";
  ui.progress.textContent = "";
  ui.jdKw.hidden = true;
  ui.expList.innerHTML = '<div class="empty">正在生成…</div>';
  experiences = [];

  const body = {
    jd,
    resume: ui.useResume.checked ? ui.refResume.value.trim() : "",
    count: parseInt(ui.count.value, 10) || 3,
    target_role: ui.targetRole.value.trim(),
  };

  try {
    const resp = await fetch("/api/jd/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      let payload = {};
      try { payload = await resp.json(); } catch (e) {}
      if (payload.need_vip) {
        ui.status.textContent = payload.error || "免费生成次数已用完，请开通 VIP。";
        const go = confirm((payload.error || "免费生成次数已用完。") + "\n点击「确定」前往会员中心开通 VIP。");
        if (go) window.location.href = "/vip";
      } else {
        ui.status.textContent = payload.error || ("生成失败：" + resp.status);
      }
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let acc = "";
    let firstCard = true;
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
        if (ev.type === "status") {
          ui.status.textContent = ev.message;
        } else if (ev.type === "jd_keywords") {
          renderKeywords(ev.keywords, ev.role);
        } else if (ev.type === "experience") {
          if (firstCard) { ui.expList.innerHTML = ""; firstCard = false; }
          renderExp(ev, ev.index);
          experiences.push({ company: ev.company, role: ev.role, period: ev.period, bullets: ev.bullets || [] });
        } else if (ev.type === "result") {
          ui.status.textContent = "已生成 " + ev.total + " 条工作经历。";
        } else if (ev.type === "error") {
          ui.status.textContent = ev.message;
        }
      }
    }
  } catch (e) {
    ui.status.textContent = "请求失败：" + e.message;
  } finally {
    generating = false;
    ui.gen.disabled = false;
    ui.expList.classList.remove("locked");
    if (!experiences.length) {
      ui.expList.innerHTML = '<div class="empty">未生成任何条目，请重试。</div>';
    }
  }
}

// ---------- 卡片操作 ----------
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e2) {}
    document.body.removeChild(ta);
  }
}

function enterEdit(card) {
  const idx = indexOfCard(card);
  const exp = experiences[idx] || { company: "xxx", role: "", period: "", bullets: [] };
  const head = card.querySelector(".exp-head");
  const bullets = card.querySelector(".exp-bullets");
  const wrap = document.createElement("div");
  wrap.className = "exp-edit";
  wrap.innerHTML = `
    <div class="exp-edit-row">
      <input class="exp-edit-company" value="${esc(exp.company)}">
      <input class="exp-edit-role" value="${esc(exp.role)}" placeholder="岗位名">
      <input class="exp-edit-period" value="${esc(exp.period)}" placeholder="时间段">
    </div>
    <textarea class="exp-edit-bullets" placeholder="每行一条工作内容">${esc((exp.bullets || []).join("\n"))}</textarea>
    <div class="exp-edit-actions">
      <button class="btn-mini accent" data-act="save-edit">保存</button>
      <button class="btn-mini" data-act="cancel-edit">取消</button>
    </div>`;
  if (head) head.style.display = "none";
  if (bullets) bullets.style.display = "none";
  card.insertBefore(wrap, card.querySelector(".exp-actions"));
}

function exitEdit(card) {
  const wrap = card.querySelector(".exp-edit");
  if (wrap) wrap.remove();
  const head = card.querySelector(".exp-head");
  const bullets = card.querySelector(".exp-bullets");
  if (head) head.style.display = "";
  if (bullets) bullets.style.display = "";
}

function readExpFromCard(card) {
  const wrap = card.querySelector(".exp-edit");
  if (!wrap) return null;
  return {
    company: wrap.querySelector(".exp-edit-company").value.trim() || "xxx",
    role: wrap.querySelector(".exp-edit-role").value.trim(),
    period: wrap.querySelector(".exp-edit-period").value.trim(),
    bullets: wrap.querySelector(".exp-edit-bullets").value.split("\n").map((s) => s.trim()).filter(Boolean),
  };
}

function applyExpToCard(card, exp) {
  const title = card.querySelector(".exp-title");
  if (title) title.textContent = exp.company + " 公司 · " + exp.role + " · " + exp.period;
  const ul = card.querySelector(".exp-bullets");
  if (ul) ul.innerHTML = (exp.bullets || []).map((b) => `<li>${esc(b)}</li>`).join("");
}

function indexOfCard(card) {
  const m = (card.id || "").match(/^exp_(\d+)$/);
  return m ? parseInt(m[1], 10) : -1;
}

// 评估匹配度：复用 /api/score（jd + after=单条经历文本）
async function scoreExp(card, idx) {
  const exp = experiences[idx];
  const jd = ui.jd.value.trim();
  if (!exp) return;
  if (!jd) { ui.status.textContent = "请先填写 JD 再评估匹配度。"; return; }
  const btn = card.querySelector('[data-act="score"]');
  const scoreEl = card.querySelector("[data-score]");
  if (btn) { btn.disabled = true; btn.textContent = "评分中…"; }
  try {
    const resp = await fetch("/api/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd, after: expText(exp) }),
    });
    if (!resp.ok) { ui.status.textContent = "评分失败：" + resp.status; return; }
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
        if (ev.type === "result") {
          const after = ev.after || ev.before;
          const total = after && after.total;
          if (scoreEl) scoreEl.textContent = "JD 匹配度：" + (total != null ? total + " 分" : "已评分") + "（单条经历，仅供参考）";
        }
      }
    }
  } catch (e) {
    ui.status.textContent = "评分失败：" + e.message;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "评估匹配度"; }
  }
}

// 存入简历：GET 当前简历 → 快照备份 → 服务端追加（append_work）→ POST
async function saveExp(exp) {
  try {
    ui.expList.classList.add("saving");
    const resp = await fetch("/api/resume");
    if (!resp.ok) { ui.status.textContent = "读取简历失败：" + resp.status; return; }
    const r = await resp.json();
    localStorage.setItem(BACKUP_KEY, JSON.stringify({
      original: r.original || "",
      optimized: r.optimized || "",
      jd: r.jd || "",
    }));
    const saveResp = await fetch("/api/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        original: r.original || "",
        optimized: r.optimized || "",
        jd: r.jd || "",
        append_work: [exp],
      }),
    });
    const json = await saveResp.json();
    if (!saveResp.ok) { ui.status.textContent = json.error || ("存入失败：" + saveResp.status); return; }
    ui.status.textContent = json.append_position === "end"
      ? "已存入简历（未能定位到「工作经历」章节，已追加到文末，请手动调整）。"
      : "已存入简历（追加到「工作经历」区域）。";
    ui.undoSave.disabled = false;
  } catch (e) {
    ui.status.textContent = "存入失败：" + e.message;
  } finally {
    ui.expList.classList.remove("saving");
  }
}

async function undoSave() {
  const raw = localStorage.getItem(BACKUP_KEY);
  if (!raw) return;
  try {
    const snap = JSON.parse(raw);
    const resp = await fetch("/api/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(snap),
    });
    if (!resp.ok) { ui.status.textContent = "撤销失败：" + resp.status; return; }
    localStorage.removeItem(BACKUP_KEY);
    ui.undoSave.disabled = true;
    ui.status.textContent = "已撤销上次存入，简历已还原。";
  } catch (e) {
    ui.status.textContent = "撤销失败：" + e.message;
  }
}

// ---------- 事件绑定 ----------
ui.ocrBtn.addEventListener("click", ocrJd);
ui.gen.addEventListener("click", generate);
ui.copyAll.addEventListener("click", async () => {
  if (!experiences.length) { ui.status.textContent = "暂无可复制的内容。"; return; }
  await copyText(experiences.map(expText).join("\n\n"));
  ui.status.textContent = "已复制全部经历。";
});
ui.undoSave.addEventListener("click", undoSave);
ui.useResume.addEventListener("change", () => {
  if (ui.useResume.checked) loadRefResume();
});
ui.expList.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest(".exp-card");
  if (!card) return;
  const idx = indexOfCard(card);
  const act = btn.dataset.act;
  if (act === "copy") {
    const exp = experiences[idx];
    if (exp) { await copyText(expText(exp)); ui.status.textContent = "已复制本条经历。"; }
  } else if (act === "edit") {
    enterEdit(card);
  } else if (act === "save-edit") {
    const exp = readExpFromCard(card);
    if (exp) { experiences[idx] = exp; applyExpToCard(card, exp); exitEdit(card); ui.status.textContent = "已保存编辑。"; }
  } else if (act === "cancel-edit") {
    exitEdit(card);
  } else if (act === "score") {
    await scoreExp(card, idx);
  } else if (act === "save") {
    const exp = experiences[idx];
    if (exp) await saveExp(exp);
  }
});

// ---------- 初始化 ----------
loadRefResume();
