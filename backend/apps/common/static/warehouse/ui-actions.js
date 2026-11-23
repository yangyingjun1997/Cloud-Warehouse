/* 页面交互委托（2026-11-23，P2-14 CSP nonce 改造配套）。
 *
 * 背景：去掉 CSP 的 'unsafe-inline' 后，浏览器不再执行内联的
 * onclick / onsubmit 属性。过去这些属性承担了大量确认弹窗与简单动作，
 * 直接删除会让「删除/报废/批量操作」等破坏性操作失去二次确认，
 * 这是比 CSP 更严重的可用性与安全问题。
 *
 * 本脚本用事件委托统一接管，模板一侧改为声明式 data 属性：
 *   data-confirm="文案"        点击/提交前弹确认；取消则阻止本次操作
 *   data-href="URL"            点击后跳转（原 location.href='...'）
 *   data-action="print"        点击后 window.print()
 *   data-action="select-all-assets"  勾选/取消全部 .asset-selector
 *   data-action="toggle-history"     切换 #history-links 显示
 *   data-auto-submit           变化后自动提交所在表单（原 onchange）
 *
 * 使用捕获阶段监听，保证在 htmx 等库绑定前就能拦截并阻止默认行为。
 */
(() => {
  const findActionElement = (node, attr) => {
    let cursor = node;
    while (cursor && cursor !== document) {
      if (cursor.nodeType === 1 && cursor.hasAttribute(attr)) return cursor;
      cursor = cursor.parentElement;
    }
    return null;
  };

  const confirmThen = (element, event) => {
    const message = element.getAttribute('data-confirm');
    if (!message) return true;
    if (!window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return false;
    }
    return true;
  };

  const runAction = (element) => {
    const action = element.getAttribute('data-action');
    if (action === 'print') {
      window.print();
      return;
    }
    if (action === 'select-all-assets') {
      document.querySelectorAll('.asset-selector').forEach((item) => {
        item.checked = element.checked;
      });
      return;
    }
    if (action === 'toggle-history') {
      const target = document.getElementById('history-links');
      if (!target) return;
      target.style.display = target.style.display === 'none' ? '' : 'none';
    }
  };

  /* 点击类：确认 -> 动作 -> 跳转 */
  document.addEventListener('click', (event) => {
    const el = findActionElement(event.target, 'data-confirm')
      || findActionElement(event.target, 'data-action')
      || findActionElement(event.target, 'data-href');
    if (!el) return;

    if (!confirmThen(el, event)) return;

    const href = el.getAttribute('data-href');
    if (href) {
      event.preventDefault();
      window.location.href = href;
      return;
    }
    runAction(el);
  }, true);

  /* 表单提交类：带 data-confirm 的表单或提交按钮需要二次确认 */
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    /* 提交按钮上的 data-confirm 优先于表单上的 */
    const submitter = event.submitter;
    const holder = (submitter && submitter.getAttribute('data-confirm'))
      ? submitter
      : (form.getAttribute('data-confirm') ? form : null);
    if (!holder) return;
    confirmThen(holder, event);
  }, true);

  /* 自动提交类：原 onchange="this.form.submit()" */
  document.addEventListener('change', (event) => {
    const el = findActionElement(event.target, 'data-auto-submit')
      || findActionElement(event.target, 'data-action');
    if (!el) return;
    if (el.hasAttribute('data-auto-submit')) {
      const form = el.form || el.closest('form');
      if (form) form.submit();
      return;
    }
    /* data-action 在 change 上只处理勾选类动作 */
    runAction(el);
  }, true);
})();
