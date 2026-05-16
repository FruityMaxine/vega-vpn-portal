/* Vega VPN · 全站登录态识别 v0.2 · 用 class 切换避免 display 冲突 */
(function() {
  const css = document.createElement('style');
  css.textContent = '.vega-hide-by-state{display:none !important}';
  document.head.appendChild(css);

  // 初始: 所有 data-when-* 都挂 hide 类，等态确定后再放
  document.querySelectorAll('[data-when-logged-in], [data-when-logged-out], [data-when-admin]').forEach(el => el.classList.add('vega-hide-by-state'));

  async function syncState() {
    let logged = false, username = '', isAdmin = false;
    try {
      const r = await fetch('/api/account/me', { credentials: 'same-origin' });
      if (r.ok) {
        const d = await r.json();
        logged = true; username = d.account.username;
        isAdmin = (username === 'admin');
        window.__VEGA_USER = username;
        window.__VEGA_IS_ADMIN = isAdmin;
      }
    } catch(_) {}

    if (logged) {
      document.querySelectorAll('[data-when-logged-in]').forEach(el => el.classList.remove('vega-hide-by-state'));
      document.querySelectorAll('[data-when-logged-out]').forEach(el => el.classList.add('vega-hide-by-state'));
      document.querySelectorAll('[data-username-slot]').forEach(el => el.textContent = username);
      if (isAdmin) {
        document.querySelectorAll('[data-when-admin]').forEach(el => el.classList.remove('vega-hide-by-state'));
      } else {
        document.querySelectorAll('[data-when-admin]').forEach(el => el.classList.add('vega-hide-by-state'));
      }
      if (location.pathname.endsWith('/signup.html') && !location.search.includes('force=1')) {
        location.replace('/me.html');
        return;
      }
    } else {
      document.querySelectorAll('[data-when-logged-in]').forEach(el => el.classList.add('vega-hide-by-state'));
      document.querySelectorAll('[data-when-logged-out]').forEach(el => el.classList.remove('vega-hide-by-state'));
      document.querySelectorAll('[data-when-admin]').forEach(el => el.classList.add('vega-hide-by-state'));
    }
  }
  syncState();

  document.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-action="logout"]');
    if (!a) return;
    e.preventDefault();
    try { await fetch('/api/account/logout', { method: 'POST', credentials: 'same-origin' }); } catch(_) {}
    location.href = '/';
  });
})();
