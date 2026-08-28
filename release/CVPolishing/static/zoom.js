// -*- coding: utf-8 -*-
// 通用「点击放大查看器」：让页面上所有内容框支持点击放大。
// - 展示型容器（view）：直接点击框体放大，保留原样式（克隆节点）。
// - 可编辑框（textarea）：悬停浮现「放大」按钮，点击放大为可编辑大框，关闭时回写到原框。
// 通过事件委托 + MutationObserver 自动适配静态与动态生成的内容，各页面仅需引入本脚本。

(function () {
  // 展示型容器：点击框体即放大
  const VIEW_SELECTORS = [
    '.notes', '.refs', '.score-result', '.compare-text',
    '.qa-card', '.ask-msg', '.ask-body',
    '.flow-card', '.ti-card', '.bus-msg',
    '.trace-view', '.trace-summary', '.chart-box'
  ];
  // 可编辑框：注入放大按钮
  const EDIT_SELECTORS = ['textarea'];

  let overlay, body, titleEl, copyBtn;

  function ensureOverlay() {
    if (overlay) return;
    overlay = document.createElement('div');
    overlay.className = 'zoom-viewer';
    overlay.innerHTML =
      '<div class="zoom-mask" data-zoom-close></div>' +
      '<div class="zoom-box">' +
        '<div class="zoom-head">' +
          '<div class="zoom-title"></div>' +
          '<div class="zoom-actions">' +
            '<button type="button" class="btn-mini" data-zoom-copy>复制</button>' +
            '<button type="button" class="btn-mini zoom-close" data-zoom-close>关闭</button>' +
          '</div>' +
        '</div>' +
        '<div class="zoom-body"></div>' +
      '</div>';
    document.body.appendChild(overlay);
    titleEl = overlay.querySelector('.zoom-title');
    body = overlay.querySelector('.zoom-body');
    copyBtn = overlay.querySelector('[data-zoom-copy]');
    overlay.addEventListener('click', (e) => {
      if (e.target.closest('[data-zoom-close]')) closeZoom();
    });
    copyBtn.addEventListener('click', copyContent);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && overlay.classList.contains('open')) closeZoom();
    });
  }

  function getTitle(el) {
    const t = el.getAttribute('data-zoom-title');
    if (t) return t;
    const cand =
      el.closest('.field') && el.closest('.field').querySelector('label') ||
      el.closest('.prompt-card') && el.closest('.prompt-card').querySelector('.prompt-card-title') ||
      el.closest('.qa-card') && el.closest('.qa-card').querySelector('.qa-no') ||
      el.closest('.mem-row') && el.closest('.mem-row').querySelector('.mem-key') ||
      el.closest('.flow-card') && el.closest('.flow-card').querySelector('h2') ||
      el.closest('.ti-card') && el.closest('.ti-card').querySelector('.ti-no') ||
      el.closest('.bus-msg') && el.closest('.bus-msg').querySelector('.bus-name') ||
      (el.closest('.tabpanel') && document.querySelector('.tab.active')) ||
      el.previousElementSibling;
    return cand && cand.textContent ? cand.textContent.trim().slice(0, 48) : '内容预览';
  }

  function openZoom(el, editable) {
    ensureOverlay();
    titleEl.textContent = getTitle(el);
    body.innerHTML = '';
    let view;
    if (editable) {
      view = document.createElement('textarea');
      view.className = 'zoom-text-edit';
      view.value = (el.value != null) ? el.value : (el.innerText || '');
      body.appendChild(view);
    } else {
      view = el.cloneNode(true);
      view.removeAttribute('id');
      view.removeAttribute('data-zoom-title');
      view.classList.add('zoom-clone');
      view.style.pointerEvents = 'none';
      // 克隆的 textarea 不携带运行时 value，补填以便查看
      view.querySelectorAll('textarea').forEach((t) => {
        t.textContent = t.value;
        t.classList.remove('zoom-edit-btn');
      });
      view.querySelectorAll('.zoom-edit-btn').forEach((b) => b.remove());
      body.appendChild(view);
    }
    overlay.classList.add('open');
    overlay._src = el;
    overlay._editable = !!editable;
  }

  function closeZoom() {
    if (!overlay || !overlay.classList.contains('open')) return;
    if (overlay._editable && overlay._src) {
      const view = body.querySelector('.zoom-text-edit');
      if (view && overlay._src.value != null) overlay._src.value = view.value;
    }
    overlay.classList.remove('open');
    body.innerHTML = '';
  }

  function copyContent() {
    const view = body.querySelector('.zoom-text-edit') || body.firstChild;
    const text = view && view.value != null ? view.value : (view ? view.innerText : '');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => flash('已复制'), () => {});
    }
  }
  function flash(msg) {
    const o = copyBtn.textContent;
    copyBtn.textContent = msg;
    setTimeout(() => { copyBtn.textContent = o; }, 1200);
  }

  // 展示型：事件委托，点击框体放大（跳过编辑框/按钮/链接）
  document.addEventListener('click', (e) => {
    if (e.target.closest('textarea, input, button, a, .mem-ans-preview')) return;
    const el = e.target.closest(VIEW_SELECTORS.join(','));
    if (el) openZoom(el, false);
  });

  // 可编辑框：包裹一层并注入悬停放大按钮（精确定位在文本框右下角，不遮挡其他按钮）
  function bindEditor(ta) {
    if (ta.dataset.zoomBound) return;
    ta.dataset.zoomBound = '1';
    let wrap = ta.parentElement;
    if (!wrap.classList.contains('zoom-edit-wrap')) {
      wrap = document.createElement('div');
      wrap.className = 'zoom-edit-wrap';
      ta.parentNode.insertBefore(wrap, ta);
      wrap.appendChild(ta);
      // 继承原文本框的 flex 布局（如 #resume flex:1 撑满父容器）
      const cs = getComputedStyle(ta);
      wrap.style.flexGrow = cs.flexGrow;
      wrap.style.flexShrink = cs.flexShrink;
      wrap.style.flexBasis = cs.flexBasis;
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zoom-edit-btn';
    btn.textContent = '⤢'; // 放大符号
    btn.title = '放大编辑';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      openZoom(ta, true);
    });
    wrap.appendChild(btn);
  }

  function scanEditors() {
    document.querySelectorAll(EDIT_SELECTORS.join(','))
      .forEach((ta) => {
        if (ta.closest('.zoom-viewer')) return; // 不处理放大预览内的克隆
        bindEditor(ta);
      });
  }

  // 监听动态内容，自动为新出现的 textarea 注入放大按钮
  let pending = false;
  const mo = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; scanEditors(); });
  });
  mo.observe(document.body, { childList: true, subtree: true });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scanEditors);
  } else {
    scanEditors();
  }
})();
