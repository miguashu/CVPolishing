// -*- coding: utf-8 -*-
// 登录页逻辑：提交账号密码到 /api/login，成功后跳转首页。
(function () {
  var form = document.getElementById("loginForm");
  var err = document.getElementById("loginErr");
  var btn = document.getElementById("loginBtn");
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.textContent = "";
    var username = document.getElementById("username").value.trim();
    var password = document.getElementById("password").value;
    if (!username || !password) {
      err.textContent = "请输入账号和密码";
      return;
    }
    btn.disabled = true;
    btn.textContent = "登录中...";
    fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ username: username, password: password }),
    }).then(function (r) {
      if (r.ok) { window.location.href = "/"; return; }
      return r.json().then(function (d) { err.textContent = (d && d.error) || "登录失败"; });
    }).catch(function () { err.textContent = "网络错误，请重试"; })
      .then(function () { btn.disabled = false; btn.textContent = "登 录"; });
  });
})();
