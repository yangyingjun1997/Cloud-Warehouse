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

  /* 必填校验：data-require-field 指向同一表单内必须非空的字段名。
     替代过去 `onclick="return this.form.xxx.value.trim() ? ... : (alert(...), false)"` 的写法。 */
  const checkRequiredField = (element, event) => {
    const fieldName = element.getAttribute('data-require-field');
    if (!fieldName) return true;
    const form = element.form || element.closest('form');
    if (!form) return true;
    const field = form.elements[fieldName];
    const value = field && typeof field.value === 'string' ? field.value.trim() : '';
    if (value) return true;
    const message = element.getAttribute('data-require-message') || '请先填写必填字段。';
    window.alert(message);
    if (event) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    if (field && typeof field.focus === 'function') field.focus();
    return false;
  };

  /* 转发到页面内已定义的函数：data-call="函数名" [data-call-arg="参数"]。
     这些函数原本由内联 onclick 调用，去掉 unsafe-inline 后需经委托转发。 */
  const callPageFunction = (element) => {
    const name = element.getAttribute('data-call');
    if (!name) return false;
    const fn = window[name];
    if (typeof fn !== 'function') return false;
    const arg = element.getAttribute('data-call-arg');
    if (arg !== null) {
      fn(arg);
    } else {
      fn();
    }
    return true;
  };

  /* 点击类：必填校验 -> 确认 -> 页面函数 -> 动作 -> 跳转 */
  document.addEventListener('click', (event) => {
    const el = findActionElement(event.target, 'data-confirm')
      || findActionElement(event.target, 'data-action')
      || findActionElement(event.target, 'data-href')
      || findActionElement(event.target, 'data-call')
      || findActionElement(event.target, 'data-require-field');
    if (!el) return;

    if (!checkRequiredField(el, event)) return;
    if (!confirmThen(el, event)) return;

    if (callPageFunction(el)) return;

    const href = el.getAttribute('data-href');
    if (href) {
      event.preventDefault();
      window.location.href = href;
      return;
    }
    runAction(el);
  }, true);

  /* 表单提交类：带 data-confirm / data-require-field 的表单或提交按钮需要校验 */
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = event.submitter;
    const holder = (submitter && (submitter.getAttribute('data-confirm')
      || submitter.getAttribute('data-require-field')))
      ? submitter
      : ((form.getAttribute('data-confirm') || form.getAttribute('data-require-field')) ? form : null);
    if (!holder) return;
    if (!checkRequiredField(holder, event)) return;
    confirmThen(holder, event);
  }, true);

  /* 自动提交与 change 类 */
  document.addEventListener('change', (event) => {
    const el = findActionElement(event.target, 'data-auto-submit')
      || findActionElement(event.target, 'data-action')
      || findActionElement(event.target, 'data-call');
    if (!el) return;
    if (el.hasAttribute('data-auto-submit')) {
      const form = el.form || el.closest('form');
      if (form) form.submit();
      return;
    }
    if (el.hasAttribute('data-call')) {
      callPageFunction(el);
      return;
    }
    /* data-action 在 change 上只处理勾选类动作 */
    runAction(el);
  }, true);
})();
