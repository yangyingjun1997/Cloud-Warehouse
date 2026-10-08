(function () {
  'use strict';

  const DB_NAME = 'warehouse-offline';
  const DB_VERSION = 2;
  const STORE = 'queue';

  function currentUserId() {
    return String(document.body?.dataset.userId || '');
  }

  function deviceId() {
    const key = 'warehouse-device-id';
    let value = '';
    try { value = window.localStorage.getItem(key) || ''; } catch (_) {}
    if (!value) {
      value = operationId();
      try { window.localStorage.setItem(key, value); } catch (_) {}
    }
    return value;
  }

  function openDb() {
    return new Promise((resolve, reject) => {
      if (!('indexedDB' in window)) {
        reject(new Error('当前浏览器不支持本地离线存储。'));
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, {keyPath: 'id', autoIncrement: true});
        }
        const store = request.transaction.objectStore(STORE);
        const cursorRequest = store.openCursor();
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (!cursor) return;
          const item = cursor.value;
          if (!item.ownerUserId) {
            item.status = 'orphaned';
            item.lastError = '旧版本离线记录未绑定账号，已停止自动同步。';
            cursor.update(item);
          }
          cursor.continue();
        };
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('无法打开本地离线存储。'));
    });
  }

  function readAll() {
    return openDb().then(db => new Promise((resolve, reject) => {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
      request.onsuccess = () => resolve((request.result || []).filter(item => item.ownerUserId === currentUserId()));
      request.onerror = () => reject(request.error);
    }));
  }

  function put(item) {
    if (!currentUserId() || item.ownerUserId !== currentUserId()) {
      return Promise.reject(new Error('当前登录账号未绑定离线记录，不能保存或修改本机队列。'));
    }
    return openDb().then(db => new Promise((resolve, reject) => {
      const request = db.transaction(STORE, 'readwrite').objectStore(STORE).put(item);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    }));
  }

  function get(id) {
    return openDb().then(db => new Promise((resolve, reject) => {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(id);
      request.onsuccess = () => {
        const item = request.result;
        resolve(item && item.ownerUserId === currentUserId() ? item : null);
      };
      request.onerror = () => reject(request.error);
    }));
  }

  function remove(id) {
    return get(id).then(item => {
      if (!item) return;
      return openDb().then(db => new Promise((resolve, reject) => {
        const request = db.transaction(STORE, 'readwrite').objectStore(STORE).delete(id);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
      }));
    });
  }

  function operationId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, char => {
      const random = Math.random() * 16 | 0;
      return (char === 'x' ? random : (random & 0x3 | 0x8)).toString(16);
    });
  }

  function serializeForm(form) {
    return [...new FormData(form)].map(([name, value]) => ({name, value: String(value)}));
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function showStatus(message, tone) {
    let node = document.getElementById('offline-status-message');
    if (!node) {
      node = document.createElement('div');
      node.id = 'offline-status-message';
      node.className = 'notice';
      document.querySelector('.content')?.prepend(node);
    }
    node.textContent = message;
    node.className = `notice ${tone || ''}`;
    node.hidden = false;
    window.setTimeout(() => { node.hidden = true; }, 7000);
  }

  async function updateIndicator() {
    try {
      const items = await readAll();
      const count = items.filter(item => item.status === 'pending').length;
      document.querySelectorAll('[data-offline-queue-count]').forEach(node => {
        node.textContent = count ? `待同步 ${count}` : '已同步';
        node.hidden = !count && navigator.onLine;
      });
      return count;
    } catch (_) {
      return 0;
    }
  }

  async function saveOfflineForm(form, action) {
    if (!currentUserId()) throw new Error('当前登录状态无法使用离线队列，请重新登录后重试。');
    const fields = serializeForm(form);
    if (!fields.some(field => field.name === 'action')) fields.push({name: 'action', value: action});
    const item = {
      status: 'pending',
      ownerUserId: currentUserId(),
      deviceId: deviceId(),
      createdAt: new Date().toISOString(),
      url: form.action || window.location.href,
      pageUrl: window.location.href,
      title: form.dataset.offlineTitle || document.title,
      action,
      fields,
      autoSync: form.dataset.offlineAutoSync === 'true',
      syncUrl: form.dataset.offlineSyncUrl || form.action || window.location.href,
    };
    if (form.dataset.offlineMode === 'quick_inventory') {
      item.clientOperationId = operationId();
      fields.push({name: 'client_operation_id', value: item.clientOperationId});
      fields.push({name: 'client_created_at', value: item.createdAt});
    }
    await put(item);
    await updateIndicator();
    showStatus(
      item.autoSync ? '当前网络不可用，记录已保存到本机，联网后会自动同步。' : '当前网络不可用，内容已保存到本机；联网后请重新打开页面提交。',
      'success'
    );
  }

  async function syncQueue(targetId) {
    if (!navigator.onLine || !currentUserId()) return;
    const items = (await readAll()).filter(item => (
      item.status === 'pending' && item.autoSync && (!targetId || item.id === targetId)
    ));
    for (const item of items) {
      const body = new URLSearchParams();
      item.fields.forEach(field => body.append(field.name, field.value));
      body.append('offline_sync', '1');
      try {
        const response = await fetch(item.syncUrl || item.url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'X-CSRFToken': csrfToken(),
            'X-Offline-Sync': '1',
          },
          body,
        });
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error(response.redirected ? '登录已失效，请重新登录后同步。' : '服务器未返回有效同步结果。');
        }
        const result = await response.json();
        if (!response.ok || (result && result.ok === false)) {
          throw new Error(result?.message || `服务器返回 ${response.status}`);
        }
        item.status = 'synced';
        item.syncedAt = new Date().toISOString();
        item.serverStatus = result?.status || '';
        item.serverOperationId = result?.operation_id || '';
        item.lastError = '';
        await put(item);
      } catch (error) {
        item.lastError = error.message || '同步失败';
        item.lastAttemptAt = new Date().toISOString();
        await put(item);
        if (targetId) throw error;
        break;
      }
    }
    await updateIndicator();
    window.dispatchEvent(new CustomEvent('warehouse-offline-updated'));
  }

  async function retryItem(id) {
    const item = await get(id);
    if (!item) return;
    item.status = 'pending';
    item.lastError = '';
    await put(item);
    await syncQueue(id);
  }

  async function removeItem(id) {
    await remove(id);
    await updateIndicator();
    window.dispatchEvent(new CustomEvent('warehouse-offline-updated'));
  }

  function registerForms() {
    document.addEventListener('submit', async event => {
      const form = event.target;
      if (!form.matches('form[data-offline-mode]') || event.defaultPrevented) return;
      const action = event.submitter?.value || form.querySelector('[name="action"]')?.value || '';
      const allowed = (form.dataset.offlineActions || '').split(',').filter(Boolean);
      if (form.dataset.offlineMode === 'quick_inventory' && navigator.onLine && allowed.includes(action)) {
        event.preventDefault();
        try {
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 4000);
          const response = await fetch('/health/', {cache: 'no-store', credentials: 'same-origin', signal: controller.signal});
          window.clearTimeout(timeout);
          if (!response.ok) throw new Error('服务器健康检查失败。');
          HTMLFormElement.prototype.submit.call(form);
        } catch (_) {
          await saveOfflineForm(form, action).catch(error => showStatus(error.message, 'error'));
        }
        return;
      }
      if (navigator.onLine) return;
      if (!allowed.includes(action)) {
        event.preventDefault();
        showStatus('当前网络不可用，正式提交不会离线执行，请恢复网络后再提交。', 'error');
        return;
      }
      event.preventDefault();
      saveOfflineForm(form, action).catch(error => showStatus(error.message, 'error'));
    });
  }

  function init() {
    const status = document.getElementById('network-status');
    const updateNetwork = () => {
      if (status) {
        status.textContent = navigator.onLine ? '网络正常' : '离线模式';
        status.classList.toggle('offline', !navigator.onLine);
      }
      updateIndicator();
      if (navigator.onLine) syncQueue();
    };
    window.addEventListener('online', updateNetwork);
    window.addEventListener('offline', updateNetwork);
    registerForms();
    updateNetwork();
  }

  window.WarehouseOffline = {readAll, updateIndicator, syncQueue, retryItem, removeItem, currentUserId};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
