// -*- coding: utf-8 -*-
// 注册页逻辑：提交账号密码到 /api/register，成功后自动登录并跳转首页。
(function () {
  var form = document.getElementById("registerForm");
  var err = document.getElementById("registerErr");
  var btn = document.getElementById("registerBtn");
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    var username = document.getElementById("username").value.trim();
    var password = document.getElementById("password").value;
    var confirm = document.getElementById("confirm").value;
    if (!username || !password) {
      err.textContent = "请输入账号和密码";
      return;
    }
    if (password !== confirm) {
      err.textContent = "两次输入的密码不一致";
      return;
    }
    btn.disabled = true;
    btn.textContent = "注册中...";
    fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username: username, password: password }),
    }).then(function (r) {
      if (r.ok) { window.location.href = "/"; return; }
      return r.json().then(function (d) { err.textContent = (d && d.error) || "注册失败"; });
    }).catch(function () { err.textContent = "网络错误，请重试"; })
      .then(function () { btn.disabled = false; btn.textContent = "注 册"; });
  });
})();
