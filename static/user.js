// -*- coding: utf-8 -*-
// 用户态（基于服务端会话 cookie）：未登录跳转登录页、展示当前用户、退出登录。
// 鉴权由服务端会话 cookie 自动携带完成，不再需要前端注入 X-User-Id。
(function () {
  function byId(id) { return document.getElementById(id); }

  function ensureUserBar(user) {
    var bar = byId("cvpUserBar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "cvpUserBar";
      bar.className = "user-bar";
      var header = document.querySelector(".topbar");
      if (header) header.appendChild(bar);
    }
    var vip = user.vip || {};
    var vipBadge = vip.active
      ? '<span class="vip-badge" id="cvpVip">VIP</span>'
      : (window.CVP_VIP_ENABLED ? '<a class="vip-upgrade" href="/vip">开通会员</a>' : '');
    bar.innerHTML =
      '<span class="user-id"></span>' + vipBadge +
      '<button id="cvpLogout" class="btn-mini">退出登录</button>';
    bar.querySelector(".user-id").textContent = "当前用户: " + (user.username || "");
    var btn = byId("cvpLogout");
    if (btn) btn.addEventListener("click", logout);
  }

  function logout() {
    fetch("/api/logout", { method: "POST", credentials: "same-origin" })
      .then(function () { window.location.href = "/login"; })
      .catch(function () { window.location.href = "/login"; });
  }

  window.CVP_USER = { logout: logout };

  document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/me", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.user) { window.location.href = "/login"; return; }
        window.CVP_VIP_ENABLED = d.vip_enabled;
        ensureUserBar(d.user);
        // 暴露给各业务页用于 VIP 门禁判断
        window.CVP_USER.vip = (d.user.vip && d.user.vip.active) || false;
        window.CVP_USER.is_admin = !!d.user.is_admin;
        window.CVP_USER.benefits = d.vip_benefits || [];
        // 提示词配置仅管理员可见：非管理员隐藏入口（含非管理员非VIP、非管理员VIP）
        if (!window.CVP_USER.is_admin) {
          document.querySelectorAll('a[href="/prompts"]').forEach(function (el) {
            el.style.display = "none";
          });
          // 后台系统仅管理员可见（admin / liwenhao）：非管理员隐藏入口
          var navAdmin = document.getElementById("navAdmin");
          if (navAdmin) navAdmin.style.display = "none";
        }
      })
      .catch(function () { window.location.href = "/login"; });
  });
})();
