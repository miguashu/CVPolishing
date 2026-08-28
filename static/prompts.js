// -*- coding: utf-8 -*-
// Agent 提示词配置页前端逻辑：加载 / 编辑 / 保存（单个或批量）

const $ = (sel) => document.querySelector(sel);

const ui = {
  list: $("#promptList"),
  saveAll: $("#saveAll"),
  resetAll: $("#resetAll"),
  status: $("#saveStatus"),
};

let state = {
  prompts: {},   // 当前生效
  defaults: {},  // 内置默认
  meta: {},      // 展示元信息
};

// ---------- 加载 ----------
async function loadPrompts() {
  ui.list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const resp = await fetch("/api/prompts");
    const data = await resp.json();
    state.prompts = data.prompts || {};
    state.defaults = data.defaults || {};
    state.meta = data.meta || {};
    renderList();
  } catch (e) {
    ui.list.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderList() {
  const keys = Object.keys(state.prompts);
  if (!keys.length) {
    ui.list.innerHTML = '<div class="empty">暂无提示词</div>';
    return;
  }
  ui.list.innerHTML = keys.map((key) => {
    const meta = state.meta[key] || { title: key, desc: "" };
    return `<div class="prompt-card" data-key="${escapeHtml(key)}">
      <div class="prompt-card-head">
        <div class="prompt-card-titlewrap">
          <div class="prompt-card-title">${escapeHtml(meta.title)}</div>
          <div class="prompt-card-desc">${escapeHtml(meta.desc)}</div>
        </div>
        <div class="prompt-card-btns">
          <button class="btn-mini save-one" data-key="${escapeHtml(key)}">保存</button>
          <button class="btn-mini reset-one" data-key="${escapeHtml(key)}">恢复默认</button>
        </div>
      </div>
      <textarea class="prompt-text" data-key="${escapeHtml(key)}" spellcheck="false">${escapeHtml(state.prompts[key] || "")}</textarea>
    </div>`;
  }).join("");

  ui.list.querySelectorAll(".save-one").forEach((b) =>
    b.addEventListener("click", () => saveOne(b.dataset.key)));
  ui.list.querySelectorAll(".reset-one").forEach((b) =>
    b.addEventListener("click", () => resetOne(b.dataset.key)));
}

// ---------- 保存 ----------
function collectOne(key) {
  const ta = ui.list.querySelector(`textarea.prompt-text[data-key="${cssEsc(key)}"]`);
  return ta ? ta.value : "";
}

async function saveOne(key) {
  const body = { [key]: collectOne(key) };
  await postSave(body, `「${state.meta[key] ? state.meta[key].title : key}」已保存`);
}

async function saveAll() {
  const body = {};
  ui.list.querySelectorAll("textarea.prompt-text").forEach((ta) => {
    body[ta.dataset.key] = ta.value;
  });
  await postSave(body, "已全部保存");
}

async function postSave(body, okMsg) {
  ui.status.textContent = "保存中…";
  try {
    const resp = await fetch("/api/prompts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok || data.error) {
      ui.status.textContent = "保存失败：" + (data.error || resp.status);
      return;
    }
    // 同步本地缓存，确保「恢复默认」前状态正确
    Object.assign(state.prompts, body);
    ui.status.textContent = okMsg + "，已立即生效。";
  } catch (e) {
    ui.status.textContent = "保存失败：" + e.message;
  }
}

// ---------- 恢复默认 ----------
function resetOne(key) {
  const def = state.defaults[key];
  if (def == null) return;
  const ta = ui.list.querySelector(`textarea.prompt-text[data-key="${cssEsc(key)}"]`);
  if (ta) ta.value = def;
  // 立即保存默认
  postSave({ [key]: def }, `「${state.meta[key] ? state.meta[key].title : key}」已恢复默认并保存`);
}

function resetAll() {
  const body = {};
  ui.list.querySelectorAll("textarea.prompt-text").forEach((ta) => {
    const def = state.defaults[ta.dataset.key];
    if (def != null) {
      ta.value = def;
      body[ta.dataset.key] = def;
    }
  });
  postSave(body, "已全部恢复默认并保存");
}

function cssEsc(s) {
  return String(s).replace(/"/g, '\\"');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- 绑定 ----------
ui.saveAll.addEventListener("click", saveAll);
ui.resetAll.addEventListener("click", resetAll);

// 启动前先校验管理员权限：非管理员隐藏配置页并显示无权限遮罩
function bootstrap() {
  fetch("/api/me", { credentials: "same-origin" })
    .then((r) => r.json())
    .then((d) => {
      const admin = !!(d && d.user && d.user.is_admin);
      if (!admin) showDenied();
      else loadPrompts();
    })
    .catch(() => showDenied());
}

function showDenied() {
  const layout = document.getElementById("promptLayout");
  const denied = document.getElementById("deniedBox");
  if (layout) layout.style.display = "none";
  if (denied) denied.style.display = "flex";
}

bootstrap();
