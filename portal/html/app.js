/* Vega VPN Portal · 通用脚本
 * - 从 URL ?u=token 取订阅 token，存入 localStorage
 * - 设备检测自动高亮
 * - 复制按钮
 * - 生成二维码（基于 CDN qrcode.js）
 */
(function () {
  'use strict';

  const SUB_BASE = 'https://vpn.example.com/sub/';
  const TOKEN_KEY = 'vega_vpn_token';

  /* ---------- token 处理 ---------- */
  function readTokenFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const u = params.get('u') || params.get('token');
    if (u && /^[A-Za-z0-9_\-]{4,128}$/.test(u)) {
      localStorage.setItem(TOKEN_KEY, u);
      // 清理 URL（不刷新）
      const clean = window.location.pathname + window.location.hash;
      window.history.replaceState({}, '', clean);
      return u;
    }
    return localStorage.getItem(TOKEN_KEY) || '';
  }

  function getSubUrl() {
    const t = readTokenFromUrl();
    return t ? (SUB_BASE + t) : '';
  }

  function getClashUrl() {
    const u = getSubUrl();
    return u ? (u + '?type=clash') : '';
  }

  function getSingboxUrl() {
    const u = getSubUrl();
    return u ? (u + '?type=singbox') : '';
  }

  /* ---------- 设备检测 ---------- */
  function detectDevice() {
    const ua = navigator.userAgent.toLowerCase();
    if (/iphone|ipod/.test(ua)) return 'iphone';
    if (/ipad/.test(ua)) return 'iphone';
    if (/android.*tv|googletv|smart-tv|smarttv|appletv|hbbtv/.test(ua)) return 'androidtv';
    if (/android/.test(ua)) return 'android';
    if (/macintosh|mac os x/.test(ua)) return 'mac';
    if (/windows/.test(ua)) return 'windows';
    if (/linux/.test(ua)) return 'linux';
    return null;
  }

  /* ---------- 注入订阅 URL ---------- */
  function fillSubBoxes() {
    const url = getSubUrl();
    document.querySelectorAll('[data-sub-url]').forEach(el => {
      const variant = el.getAttribute('data-sub-url');
      let u = url;
      if (variant === 'clash') u = getClashUrl();
      else if (variant === 'singbox') u = getSingboxUrl();
      if (u) {
        el.textContent = u;
        el.classList.remove('empty');
      } else {
        el.textContent = '请用你所收到的邀请码到 /signup.html 注册以拿到订阅链接';
        el.classList.add('empty');
      }
    });
  }

  /* ---------- 复制按钮 ---------- */
  function bindCopyButtons() {
    // 字面量复制：data-copy-text="brew install sing-box"
    document.querySelectorAll('[data-copy-text]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const text = btn.getAttribute('data-copy-text') || '';
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          flash(btn, '✅ 已复制!', '#6fba2c');
        } catch (e) {
          const ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta);
          ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
          flash(btn, '✅ 已复制!', '#6fba2c');
        }
      });
    });
    document.querySelectorAll('[data-copy]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const target = btn.getAttribute('data-copy');
        let text = '';
        if (target === 'sub') text = getSubUrl();
        else if (target === 'clash') text = getClashUrl();
        else if (target === 'singbox') text = getSingboxUrl();
        else text = target;
        if (!text) {
          flash(btn, '⚠️ 没有 token', '#e05a5a');
          return;
        }
        try {
          await navigator.clipboard.writeText(text);
          flash(btn, '✅ 已复制!', '#6fba2c');
        } catch (e) {
          // fallback
          const ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta);
          ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
          flash(btn, '✅ 已复制!', '#6fba2c');
        }
      });
    });
  }
  function flash(btn, msg, color) {
    const oldText = btn.textContent;
    const oldBg = btn.style.background;
    btn.textContent = msg;
    if (color) btn.style.background = color;
    setTimeout(() => {
      btn.textContent = oldText;
      btn.style.background = oldBg;
    }, 1600);
  }

  /* ---------- 二维码 ---------- */
  function renderQRCodes() {
    if (typeof QRCode === 'undefined') return;
    document.querySelectorAll('[data-qr]').forEach(el => {
      const variant = el.getAttribute('data-qr');
      let url = getSubUrl();
      if (variant === 'clash') url = getClashUrl();
      else if (variant === 'singbox') url = getSingboxUrl();
      el.innerHTML = '';
      if (!url) {
        const empty = document.createElement('div');
        empty.className = 'qr-empty';
        empty.textContent = '没有专属链接\n请先获取 ?u=xxx';
        empty.style.whiteSpace = 'pre-line';
        el.appendChild(empty);
        return;
      }
      try {
        new QRCode(el, {
          text: url,
          width: 180, height: 180,
          colorDark: '#794f27',
          colorLight: '#ffffff',
          correctLevel: QRCode.CorrectLevel.M
        });
      } catch (e) {
        el.textContent = '二维码生成失败: ' + e.message;
      }
    });
  }

  /* ---------- 高亮检测到的设备 ---------- */
  function highlightDetectedDevice() {
    const d = detectDevice();
    if (!d) return;
    const card = document.querySelector(`.device-card[data-device="${d}"]`);
    if (card) card.classList.add('detected');
  }

  /* ---------- 状态条 ---------- */
  function updateTokenBadge() {
    const badge = document.getElementById('token-badge');
    if (!badge) return;
    const t = localStorage.getItem(TOKEN_KEY);
    if (t) {
      badge.textContent = '🎫 已绑定 token: ' + t.substring(0, 6) + '…';
      badge.style.background = '#e9f7d8';
      badge.style.borderColor = '#6fba2c';
      badge.style.color = '#4d7e1a';
    } else {
      badge.textContent = '👻 未绑定 token（点 ?u=xxx 链接绑定）';
      badge.style.background = '#fff3cf';
      badge.style.borderColor = '#f5c31c';
      badge.style.color = '#8a6c0b';
    }
  }

  /* ---------- 初始化 ---------- */
  document.addEventListener('DOMContentLoaded', () => {
    readTokenFromUrl();
    fillSubBoxes();
    bindCopyButtons();
    renderQRCodes();
    highlightDetectedDevice();
    updateTokenBadge();
  });

  // 暴露给页面用
  window.VegaPortal = { getSubUrl, getClashUrl, getSingboxUrl };
})();
