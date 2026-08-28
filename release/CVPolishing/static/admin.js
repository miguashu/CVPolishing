// -*- coding: utf-8 -*-
// 后台用户管理：仅管理员可访问。列出用户、新增、删除、重置密码。
(function () {
  function byId(id) { return document.getElementById(id); }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    return fetch(path, opts).then(function (r) {
      if (r.status === 403) { window.location.href = "/"; throw new Error("无权限"); }
      if (!r.ok) { return r.json().then(function (d) { throw new Error((d && d.error) || "请求失败"); }); }
      return r.json();
    });
  }

  function loadUsers() {
    api("/api/admin/users").then(function (users) {
      var tbody = byId("userRows");
      tbody.innerHTML = "";
      users.forEach(function (u) {
        var vip = u.vip || {};
        var vipLabel = vip.active
          ? '<span class="vip-badge">VIP</span>' + (vip.expire ? '<br><span class="muted" style="font-size:11px;">' + vip.expire + "</span>" : "")
          : '<span class="muted" style="font-size:12px;">普通</span>';
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" + u.id + "</td>" +
          "<td>" + escapeHtml(u.username) + "</td>" +
          "<td>" + (u.role === "admin" ? "管理员" : "普通用户") + "</td>" +
          "<td>" + vipLabel + "</td>" +
          "<td>" + (u.created_at || "") + "</td>" +
          "<td>" +
            '<button class="btn-mini" data-act="vip" data-u="' + escapeHtml(u.username) + '">' + (vip.active ? "取消会员" : "设为会员") + "</button> " +
            '<button class="btn-mini" data-act="pwd" data-u="' + escapeHtml(u.username) + '">重置密码</button> ' +
            '<button class="btn-mini" data-act="del" data-u="' + escapeHtml(u.username) + '">删除</button>' +
          "</td>";
        tbody.appendChild(tr);
      });
    }).catch(function (e) { /* 已处理跳转 */ });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    var u = btn.getAttribute("data-u");
    if (act === "del") {
      if (!confirm("确认删除用户 " + u + "？其简历与长期记忆将一并清除。")) return;
      api("/api/admin/users/" + encodeURIComponent(u), { method: "DELETE" })
        .then(function () { loadUsers(); })
        .catch(function (err) { alert(err.message); });
    } else if (act === "pwd") {
      var pwd = prompt("为 " + u + " 设置新密码：");
      if (!pwd) return;
      api("/api/admin/users/" + encodeURIComponent(u) + "/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwd }),
      }).then(function () { alert("密码已重置"); }).catch(function (err) { alert(err.message); });
    } else if (act === "vip") {
      var make = btn.textContent.indexOf("设为") === 0;
      var days = make ? prompt("会员有效期天数（留空=永久）：", "") : null;
      var payload = { active: make };
      if (make && days && days.trim()) payload.days = parseInt(days.trim(), 10);
      api("/api/admin/users/" + encodeURIComponent(u) + "/vip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function () { loadUsers(); }).catch(function (err) { alert(err.message); });
    }
  });

  byId("addUserForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var msg = byId("addMsg");
    msg.textContent = "";
    var username = byId("au_username").value.trim();
    var password = byId("au_password").value;
    var role = byId("au_role").value;
    api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password, role: role }),
    }).then(function () {
      byId("au_username").value = ""; byId("au_password").value = "";
      msg.textContent = "已新增"; loadUsers();
    }).catch(function (err) { msg.textContent = err.message; });
  });

  // ---------- 专属邀请码 ----------
  function loadInvites() {
    api("/api/admin/invite").then(function (d) {
      var tbody = byId("inviteRows");
      if (!tbody) return;
      tbody.innerHTML = "";
      (d.invites || []).forEach(function (it) {
        var tr = document.createElement("tr");
        tr.innerHTML =
          "<td><span class='code-box'>" + escapeHtml(it.code) + "</span> " +
            "<span class='copy-btn' data-code='" + escapeHtml(it.code) + "'>[复制]</span></td>" +
          "<td>" + (it.target_user ? escapeHtml(it.target_user) : "任意") + "</td>" +
          "<td>" + (it.vip_days ? it.vip_days + " 天" : "永久") + "</td>" +
          "<td>" + (it.expires_at || "不过期") + "</td>" +
          "<td>" + (it.used_by ? escapeHtml(it.used_by) + "<br><span class='muted' style='font-size:11px;'>" + (it.used_at || "") + "</span>" : "<span class='muted'>未使用</span>") + "</td>" +
          "<td>" + (it.used_by ? "<span class='muted'>已失效</span>" : "<span class='del-btn' data-del='" + escapeHtml(it.code) + "'>撤销</span>") + "</td>";
        tbody.appendChild(tr);
      });
    }).catch(function () {});
  }

  byId("inviteForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var msg = byId("inviteMsg");
    msg.textContent = "";
    var payload = {
      target_user: byId("iv_target").value.trim() || undefined,
      vip_days: parseInt(byId("iv_days").value, 10) || 0,
      expires_hours: parseInt(byId("iv_hours").value, 10) || 0,
    };
    api("/api/admin/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (d) {
      msg.textContent = "已生成：" + d.code + "（请复制分发给用户）";
      byId("iv_target").value = ""; byId("iv_days").value = "0"; byId("iv_hours").value = "0";
      loadInvites();
    }).catch(function (err) { msg.textContent = err.message; });
  });

  document.addEventListener("click", function (e) {
    var copy = e.target.closest(".copy-btn");
    if (copy) {
      var code = copy.getAttribute("data-code");
      if (navigator.clipboard) navigator.clipboard.writeText(code);
      else { var t = document.createElement("textarea"); t.value = code; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
      var old = copy.textContent; copy.textContent = "[已复制]";
      setTimeout(function () { copy.textContent = old; }, 1200);
      return;
    }
    var del = e.target.closest(".del-btn");
    if (del) {
      if (!confirm("确认撤销邀请码 " + del.getAttribute("data-del") + "？")) return;
      api("/api/admin/invite/" + encodeURIComponent(del.getAttribute("data-del")), { method: "DELETE" })
        .then(function () { loadInvites(); })
        .catch(function (err) { alert(err.message); });
    }
  });

  // 鉴权：以服务端返回的 is_admin 为准（role 字段可能未及时同步，不可靠）
  fetch("/api/me", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.user || !d.user.is_admin) { window.location.href = "/"; return; }
    loadUsers();
    loadInvites();
  }).catch(function () { window.location.href = "/"; });
})();
