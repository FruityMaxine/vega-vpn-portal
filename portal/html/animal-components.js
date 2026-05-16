/* ===========================================================
 * Animal Components — 1:1 Animal Island 复刻
 * 提供：window.AnimalModal / window.AnimalSelect / window.AnimalCheckbox
 * 以及向后兼容：window.aiConfirm (若全局未先定义则注入)
 * =========================================================== */
(function () {
  'use strict';

  // ============ SVG clip-path 定义（一次性挂到 body） ============
  const CLIP_SVG_ID = 'animal-modal-clip-svg-host';
  function ensureClipDef() {
    if (document.getElementById(CLIP_SVG_ID)) return;
    const wrap = document.createElement('div');
    wrap.id = CLIP_SVG_ID;
    wrap.setAttribute('aria-hidden', 'true');
    wrap.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none';
    wrap.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute">' +
      '<defs><clipPath id="animal-modal-clip" clipPathUnits="objectBoundingBox">' +
      '<path d="M0.501,0.005 L0.501,0.005 L0.523,0.005 L0.549,0.006 C0.704,0.01,0.796,0.017,0.825,0.027 L0.827,0.028 C0.872,0.045,0.939,0.044,0.978,0.17 C1,0.254,1,0.365,0.99,0.505 L0.988,0.513 C0.979,0.558,0.971,0.598,0.965,0.633 C0.956,0.689,0.979,0.77,0.964,0.865 C0.953,0.928,0.921,0.966,0.869,0.979 C0.821,0.986,0.773,0.992,0.726,0.995 L0.712,0.996 L0.694,0.997 C0.648,1,0.586,1,0.507,1 L0.501,1 L0.464,1 C0.385,1,0.325,0.998,0.283,0.995 C0.234,0.992,0.184,0.987,0.133,0.979 C0.081,0.966,0.05,0.928,0.039,0.865 C0.023,0.77,0.047,0.689,0.037,0.633 C0.031,0.595,0.023,0.552,0.013,0.505 C-0.006,0.365,-0.002,0.254,0.024,0.17 C0.064,0.045,0.13,0.045,0.174,0.028 L0.175,0.028 C0.204,0.017,0.303,0.009,0.474,0.005 L0.501,0.005"/>' +
      '</clipPath></defs></svg>';
    document.body.insertBefore(wrap, document.body.firstChild);
  }

  // ============ Typewriter ============
  // 按字符逐字 reveal HTML 内容，保留嵌套元素结构。
  // 算法：先把目标节点克隆出"完整 DOM"，再逐次只显示前 N 个字符。
  function countText(node) {
    if (!node) return 0;
    if (node.nodeType === 3) return node.nodeValue.length;
    if (node.nodeType !== 1 && node.nodeType !== 11) return 0;
    let s = 0;
    for (const c of node.childNodes) s += countText(c);
    return s;
  }
  // 在 dest 内重建源节点 source 的前 remaining 个字符内容；返回剩余 remaining
  function buildTruncated(source, dest, remaining) {
    for (const child of source.childNodes) {
      if (remaining <= 0) break;
      if (child.nodeType === 3) {
        const txt = child.nodeValue;
        if (remaining >= txt.length) {
          dest.appendChild(document.createTextNode(txt));
          remaining -= txt.length;
        } else {
          dest.appendChild(document.createTextNode(txt.slice(0, remaining)));
          remaining = 0;
        }
      } else if (child.nodeType === 1) {
        const clone = child.cloneNode(false);
        dest.appendChild(clone);
        remaining = buildTruncated(child, clone, remaining);
      }
    }
    return remaining;
  }
  function typewriterReveal(targetEl, htmlString, speed) {
    speed = speed || 80;
    // 把 htmlString 解析成离屏 fragment
    const tpl = document.createElement('template');
    tpl.innerHTML = htmlString;
    const source = tpl.content;
    const total = countText(source);
    if (total === 0) { targetEl.innerHTML = htmlString; return Promise.resolve(); }
    targetEl.innerHTML = '';
    let count = 0;
    return new Promise((resolve) => {
      const tick = () => {
        count++;
        targetEl.innerHTML = '';
        buildTruncated(source, targetEl, count);
        if (count >= total) { clearInterval(timer); resolve(); }
      };
      const timer = setInterval(tick, speed);
    });
  }

  // ============ Modal ============
  let modalIdSeq = 0;
  const openModals = new Set();

  function createModalDom() {
    const mask = document.createElement('div');
    mask.className = 'am-mask';
    mask.setAttribute('role', 'presentation');
    mask.innerHTML =
      '<div class="am-modal" role="dialog" aria-modal="true">' +
        '<div class="am-modal-clipped">' +
          '<div class="am-modal-header" data-am-hdr>' +
            '<div class="am-modal-title" data-am-title></div>' +
            '<button type="button" class="am-modal-close" data-am-x aria-label="关闭">' +
              '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            '</button>' +
          '</div>' +
          '<div class="am-modal-body" data-am-body></div>' +
          '<div class="am-modal-footer" data-am-footer></div>' +
        '</div>' +
      '</div>';
    return mask;
  }

  function openModal(opts) {
    ensureClipDef();
    opts = opts || {};
    const id = ++modalIdSeq;
    const mask = createModalDom();
    const modal = mask.querySelector('.am-modal');
    const titleEl = mask.querySelector('[data-am-title]');
    const headerEl = mask.querySelector('[data-am-hdr]');
    const bodyEl = mask.querySelector('[data-am-body]');
    const footerEl = mask.querySelector('[data-am-footer]');
    const closeBtn = mask.querySelector('[data-am-x]');

    if (opts.width) modal.style.width =
      (typeof opts.width === 'number' ? (opts.width + 'px') : opts.width);

    // Title
    if (opts.title != null && opts.title !== '') {
      titleEl.innerHTML = opts.title;
    } else {
      headerEl.style.justifyContent = 'flex-end';
      titleEl.style.display = 'none';
    }

    // Footer
    const footer = opts.footer;
    if (footer === null) {
      footerEl.style.display = 'none';
    } else if (footer === undefined) {
      // 默认两按钮
      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'am-modal-btn';
      cancelBtn.innerHTML = opts.cancelText || '取消';
      const okBtn = document.createElement('button');
      okBtn.type = 'button';
      okBtn.className = 'am-modal-btn ' + (opts.okDanger ? 'am-danger' : 'am-primary');
      okBtn.innerHTML = opts.okText || '确定';
      footerEl.appendChild(cancelBtn);
      footerEl.appendChild(okBtn);
      cancelBtn.addEventListener('click', () => api.close(false));
      okBtn.addEventListener('click', () => api.close(true));
    } else if (typeof footer === 'string') {
      footerEl.innerHTML = footer;
    } else if (footer instanceof Node) {
      footerEl.appendChild(footer);
    }

    // Mask / ESC close
    const maskClosable = opts.maskClosable !== false;
    function onMaskClick(e) { if (e.target === mask && maskClosable) api.close(false); }
    function onKey(e) { if (e.key === 'Escape') api.close(false); }
    mask.addEventListener('click', onMaskClick);
    document.addEventListener('keydown', onKey);
    closeBtn.addEventListener('click', () => api.close(false));

    document.body.appendChild(mask);
    document.body.classList.add('am-modal-lock');
    // 触发动画
    requestAnimationFrame(() => mask.classList.add('am-open'));
    openModals.add(id);

    // Body content + typewriter
    const bodyHtml = (opts.body != null) ? String(opts.body) : '';
    const useTypewriter = opts.typewriter !== false;
    if (useTypewriter && bodyHtml) {
      typewriterReveal(bodyEl, bodyHtml, opts.typeSpeed || 80);
    } else {
      bodyEl.innerHTML = bodyHtml;
    }

    let resolver;
    const promise = new Promise((res) => { resolver = res; });
    const api = {
      el: mask,
      close(result) {
        if (!openModals.has(id)) return;
        openModals.delete(id);
        mask.removeEventListener('click', onMaskClick);
        document.removeEventListener('keydown', onKey);
        mask.classList.remove('am-open');
        mask.style.transition = 'opacity .18s ease';
        mask.style.opacity = '0';
        setTimeout(() => {
          if (mask.parentNode) mask.parentNode.removeChild(mask);
          if (openModals.size === 0) document.body.classList.remove('am-modal-lock');
        }, 180);
        if (result === true && typeof opts.onOk === 'function') {
          try { opts.onOk(); } catch (e) { console.error(e); }
        }
        if (result === false && typeof opts.onClose === 'function') {
          try { opts.onClose(); } catch (e) { console.error(e); }
        }
        resolver(result);
      },
    };
    return { promise, api };
  }

  const AnimalModal = {
    open(opts) {
      const { promise, api } = openModal(opts || {});
      promise.close = api.close.bind(api);
      promise.el = api.el;
      return promise;
    },
  };

  // ============ aiConfirm 兼容垫片 ============
  // 用法：aiConfirm(msgHTML, { okText, title, danger })  → Promise<boolean>
  function aiConfirm(msg, opts) {
    opts = opts || {};
    return AnimalModal.open({
      title: opts.title || '请确认',
      body: msg,
      okText: opts.okText || '确认',
      cancelText: opts.cancelText || '取消',
      okDanger: !!opts.danger,
      typewriter: opts.typewriter !== false,
      typeSpeed: opts.typeSpeed || 80,
    });
  }

  // ============ Select Enhancer ============
  // 扫 [data-ai-select] 的原生 <select>，渲染自定义面板，保持原生元素同步。
  function enhanceSelect(nativeSel) {
    if (nativeSel.__amEnhanced) return;
    nativeSel.__amEnhanced = true;

    const wrap = document.createElement('div');
    wrap.className = 'am-select';
    const trigger = document.createElement('div');
    trigger.className = 'am-select-trigger';
    trigger.setAttribute('tabindex', '0');
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.innerHTML =
      '<span class="am-select-text"></span>' +
      '<span class="am-select-arrow"><svg width="12" height="12" viewBox="0 0 12 12" fill="none">' +
      '<path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>';
    wrap.appendChild(trigger);

    nativeSel.parentNode.insertBefore(wrap, nativeSel);
    wrap.appendChild(nativeSel);
    nativeSel.classList.add('am-native-hidden');

    const txtEl = trigger.querySelector('.am-select-text');
    let dropdown = null;

    function currentLabel() {
      const o = nativeSel.options[nativeSel.selectedIndex];
      if (!o || o.value === '') {
        const ph = nativeSel.getAttribute('data-placeholder') || '请选择';
        return { label: ph, isPh: true };
      }
      return { label: o.textContent, isPh: false };
    }
    function syncTrigger() {
      const cur = currentLabel();
      txtEl.textContent = cur.label;
      txtEl.classList.toggle('am-select-placeholder', cur.isPh);
      if (nativeSel.disabled) wrap.classList.add('am-disabled');
      else wrap.classList.remove('am-disabled');
    }
    function buildDropdown() {
      dropdown = document.createElement('div');
      dropdown.className = 'am-select-dropdown';
      dropdown.setAttribute('role', 'listbox');
      Array.from(nativeSel.options).forEach((o, i) => {
        if (o.value === '' && o.disabled) return; // skip placeholder option
        const opt = document.createElement('div');
        opt.className = 'am-select-option';
        if (o.value === nativeSel.value) opt.classList.add('am-active');
        opt.textContent = o.textContent;
        opt.dataset.value = o.value;
        opt.addEventListener('mouseenter', () => {
          dropdown.querySelectorAll('.am-select-option').forEach(e => e.classList.remove('am-hovered'));
          opt.classList.add('am-hovered');
        });
        opt.addEventListener('mouseleave', () => opt.classList.remove('am-hovered'));
        opt.addEventListener('click', () => {
          nativeSel.value = o.value;
          nativeSel.dispatchEvent(new Event('change', { bubbles: true }));
          syncTrigger();
          close();
        });
        dropdown.appendChild(opt);
      });
      document.body.appendChild(dropdown);
      positionDropdown();
    }
    function positionDropdown() {
      if (!dropdown) return;
      const r = wrap.getBoundingClientRect();
      const ddH = dropdown.offsetHeight;
      const vh = window.innerHeight;
      const spaceBelow = vh - r.bottom;
      let top;
      if (spaceBelow < ddH + 12 && r.top > ddH + 12) {
        top = r.top + window.scrollY - ddH - 6;
      } else {
        top = r.bottom + window.scrollY + 6;
      }
      dropdown.style.top = top + 'px';
      dropdown.style.left = (r.left + window.scrollX) + 'px';
      dropdown.style.minWidth = r.width + 'px';
    }
    function open() {
      if (nativeSel.disabled) return;
      if (dropdown) return;
      wrap.classList.add('am-open');
      buildDropdown();
      setTimeout(() => {
        document.addEventListener('mousedown', onDocDown);
        window.addEventListener('resize', positionDropdown);
        window.addEventListener('scroll', positionDropdown, true);
      }, 0);
    }
    function close() {
      wrap.classList.remove('am-open');
      if (dropdown && dropdown.parentNode) dropdown.parentNode.removeChild(dropdown);
      dropdown = null;
      document.removeEventListener('mousedown', onDocDown);
      window.removeEventListener('resize', positionDropdown);
      window.removeEventListener('scroll', positionDropdown, true);
    }
    function onDocDown(e) {
      if (wrap.contains(e.target)) return;
      if (dropdown && dropdown.contains(e.target)) return;
      close();
    }
    trigger.addEventListener('click', () => {
      if (dropdown) close(); else open();
    });
    trigger.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (dropdown) close(); else open(); }
      else if (e.key === 'Escape') close();
    });
    // 外部代码改 select.value / 增删 option 时，自动同步
    const mo = new MutationObserver(() => {
      if (dropdown) { close(); }
      syncTrigger();
    });
    mo.observe(nativeSel, { childList: true, attributes: true, attributeFilter: ['value', 'disabled'] });
    nativeSel.addEventListener('am:refresh', () => { if (dropdown) close(); syncTrigger(); });
    syncTrigger();
  }

  function scanSelects(root) {
    (root || document).querySelectorAll('select[data-ai-select]').forEach(enhanceSelect);
  }

  // ============ Checkbox Enhancer ============
  function enhanceCheckbox(nativeInput) {
    if (nativeInput.__amEnhanced) return;
    nativeInput.__amEnhanced = true;
    const label = document.createElement('label');
    label.className = 'am-checkbox';
    const size = nativeInput.getAttribute('data-ai-size') || 'middle';
    if (size === 'small') label.classList.add('am-size-small');
    if (size === 'large') label.classList.add('am-size-large');
    const box = document.createElement('span');
    box.className = 'am-checkbox-box';
    const txt = document.createElement('span');
    txt.className = 'am-checkbox-label';
    const labelText = nativeInput.getAttribute('data-ai-label')
      || (nativeInput.nextSibling && nativeInput.nextSibling.nodeType === 3 ? nativeInput.nextSibling.nodeValue.trim() : '')
      || (nativeInput.id && document.querySelector('label[for="' + nativeInput.id + '"]')
        ? document.querySelector('label[for="' + nativeInput.id + '"]').textContent : '');
    txt.textContent = labelText;

    nativeInput.parentNode.insertBefore(label, nativeInput);
    label.appendChild(nativeInput);
    label.appendChild(box);
    if (labelText) label.appendChild(txt);
    nativeInput.style.cssText = 'position:absolute;opacity:0;pointer-events:none;width:0;height:0';

    function sync() {
      label.classList.toggle('am-checked', nativeInput.checked);
      label.classList.toggle('am-disabled', nativeInput.disabled);
      box.innerHTML = nativeInput.checked
        ? '<span class="am-checkbox-checkmark"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M2 8L6 12L14 4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'
        : '';
    }
    label.addEventListener('click', (e) => {
      if (nativeInput.disabled) return;
      if (e.target === nativeInput) return; // 原生事件
      e.preventDefault();
      nativeInput.checked = !nativeInput.checked;
      nativeInput.dispatchEvent(new Event('change', { bubbles: true }));
    });
    nativeInput.addEventListener('change', sync);
    sync();
  }
  function scanCheckboxes(root) {
    (root || document).querySelectorAll('input[type="checkbox"][data-ai-check]').forEach(enhanceCheckbox);
  }

  // ============ 初始化 ============
  function init() {
    ensureClipDef();
    scanSelects();
    scanCheckboxes();
    // 监听后续动态插入
    const mo = new MutationObserver((muts) => {
      muts.forEach(m => {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches('select[data-ai-select]')) enhanceSelect(n);
          if (n.matches && n.matches('input[type="checkbox"][data-ai-check]')) enhanceCheckbox(n);
          if (n.querySelectorAll) {
            n.querySelectorAll('select[data-ai-select]').forEach(enhanceSelect);
            n.querySelectorAll('input[type="checkbox"][data-ai-check]').forEach(enhanceCheckbox);
          }
        });
      });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // ============ 公开 API ============
  window.AnimalModal = AnimalModal;
  window.AnimalSelect = { enhance: enhanceSelect, scan: scanSelects };
  window.AnimalCheckbox = { enhance: enhanceCheckbox, scan: scanCheckboxes };
  // 仅在没人定义全局 aiConfirm 时注入（页面内部闭包定义不会影响这里）
  if (typeof window.aiConfirm === 'undefined') {
    window.aiConfirm = aiConfirm;
  }
  window.AnimalAiConfirm = aiConfirm; // 给页面内部 aiConfirm 转调用
})();
