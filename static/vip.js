// -*- coding: utf-8 -*-
// 会员中心：展示权益、激活码开通、VIP 状态刷新。
(function () {
  function byId(id) { return document.getElementById(id); }

  function renderStatus(info) {
    var el = byId("vipStatus");
    if (!el) return;
    if (info && info.active) {
      var expire = info.expire ? "（有效期至 " + info.expire + "）" : "（永久有效）";
      el.innerHTML = '<span class="on">当前为 VIP 会员</span> ' + expire;
      var box = byId("activateBox");
      if (box) box.style.display = "none";
    } else {
      el.innerHTML = '<span class="off">当前为普通用户，开通会员即可解锁全部权益</span>';
    }
  }

  function renderBenefits(list) {
    var el = byId("benefitList");
    if (!el) return;
    var items = list && list.length ? list : ["会员专属功能持续上新"];
    el.innerHTML = items.map(function (b) {
      return '<div class="benefit"><span class="dot">◆</span><span>' + b + "</span></div>";
    }).join("");
  }

  function loadVip() {
    fetch("/api/vip/info", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        renderStatus(d.vip);
        renderBenefits(d.benefits);
      })
      .catch(function () {});
  }

  function activate() {
    var code = byId("vipCode").value.trim();
    var msg = byId("vipMsg");
    var btn = byId("vipActivateBtn");
    if (!code) { msg.className = "vip-msg err"; msg.textContent = "请输入激活码"; return; }
    btn.disabled = true; btn.textContent = "开通中..."; msg.textContent = "";
    fetch("/api/vip/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ code: code }),
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (res.ok) {
        msg.className = "vip-msg ok";
        msg.textContent = "开通成功，欢迎成为 VIP 会员！";
        renderStatus(res.d.vip);
      } else {
        msg.className = "vip-msg err";
        msg.textContent = (res.d && res.d.error) || "开通失败";
      }
    }).catch(function () {
      msg.className = "vip-msg err"; msg.textContent = "网络错误，请重试";
    }).then(function () {
      btn.disabled = false; btn.textContent = "立即开通";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadVip();
    var btn = byId("vipActivateBtn");
    if (btn) btn.addEventListener("click", activate);
    var input = byId("vipCode");
    if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") activate(); });

    var buyBtn = byId("buyBtn");
    if (buyBtn) buyBtn.addEventListener("click", openPayModal);
    var closeBtn = byId("payClose");
    if (closeBtn) closeBtn.addEventListener("click", closePayModal);
  });

  // ---------- 支付宝 / 聚合支付购买 ----------
  var _payTimer = null;
  var _currentOrder = null;

  function openPayModal() {
    var modal = byId("payModal");
    var area = byId("payArea");
    var list = byId("payProducts");
    var msg = byId("buyMsg");
    if (msg) { msg.textContent = ""; msg.className = "vip-msg"; }
    if (area) { area.style.display = "none"; }
    if (modal) { modal.style.display = "flex"; }
    if (!list) return;
    list.innerHTML = '<div class="vip-msg">加载中…</div>';
    fetch("/api/pay/products", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ready) {
          list.innerHTML = '<div class="vip-msg err">支付通道未配置，请联系管理员</div>';
          return;
        }
        if (!d.products || !d.products.length) {
          list.innerHTML = '<div class="vip-msg err">暂无可售套餐</div>';
          return;
        }
        list.innerHTML = "";
        d.products.forEach(function (p) {
          var el = document.createElement("div");
          el.className = "pay-product";
          el.innerHTML =
            '<div><div class="pname">' + p.name + '</div>' +
            '<div class="pdays">有效期 ' + p.vip_days + ' 天</div></div>' +
            '<div class="pprice">¥' + p.price + '</div>';
          el.addEventListener("click", function () { doCreatePay(p.key); });
          list.appendChild(el);
        });
      })
      .catch(function () {
        list.innerHTML = '<div class="vip-msg err">加载失败，请重试</div>';
      });
  }

  function closePayModal() {
    var modal = byId("payModal");
    if (modal) modal.style.display = "none";
    if (_payTimer) { clearInterval(_payTimer); _payTimer = null; }
    _currentOrder = null;
  }

  function doCreatePay(productKey) {
    var area = byId("payArea");
    var urlEl = byId("payUrl");
    var statusEl = byId("payStatus");
    if (area) area.style.display = "block";
    if (statusEl) statusEl.textContent = "正在创建订单…";
    fetch("/api/pay/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ product_key: productKey }),
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.ok) {
        if (statusEl) statusEl.className = "vip-msg err";
        if (statusEl) statusEl.textContent = (res.d && res.d.error) || "创建订单失败";
        return;
      }
      _currentOrder = res.d.out_trade_no;
      if (urlEl) urlEl.href = res.d.pay_url;
      if (statusEl) { statusEl.className = "vip-msg ok"; statusEl.textContent = "支付状态：等待付款（订单已创建）"; }
      if (_payTimer) clearInterval(_payTimer);
      _payTimer = setInterval(pollOrder, 2000);
    }).catch(function () {
      if (statusEl) { statusEl.className = "vip-msg err"; statusEl.textContent = "网络错误，请重试"; }
    });
  }

  function pollOrder() {
    if (!_currentOrder) return;
    fetch("/api/pay/order/" + _currentOrder, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.paid) {
          if (_payTimer) { clearInterval(_payTimer); _payTimer = null; }
          var statusEl = byId("payStatus");
          if (statusEl) { statusEl.className = "vip-msg ok"; statusEl.textContent = "支付成功，会员已开通！"; }
          loadVip();
          setTimeout(closePayModal, 1500);
        }
      })
      .catch(function () {});
  }
})();
