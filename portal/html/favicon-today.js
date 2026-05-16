/* 按 UTC 日期 mod 7 取今日 favicon · 客户端动态切换 · 每天 00:00 UTC 自动换 */
(function() {
  const POOL = [
    { src: '/favicons/0-fox.png',          name: '小狐狸' },
    { src: '/favicons/1-leaf.png',         name: '叶子' },
    { src: '/favicons/2-helicopter.svg',   name: '直升机' },
    { src: '/favicons/3-camera.svg',       name: '相机' },
    { src: '/favicons/4-chat.svg',         name: '聊天泡' },
    { src: '/favicons/5-map.svg',          name: '地图' },
    { src: '/favicons/6-critterpedia.svg', name: '生物图鉴' }
  ];
  const epoch = Math.floor(Date.now() / 86400000);
  const today = POOL[epoch % POOL.length];

  // 清掉现有 favicon
  document.querySelectorAll('link[rel~="icon"]').forEach(el => el.remove());
  // 注入今日 favicon
  const link = document.createElement('link');
  link.rel = 'icon';
  link.href = today.src + '?d=' + epoch;
  document.head.appendChild(link);
})();
