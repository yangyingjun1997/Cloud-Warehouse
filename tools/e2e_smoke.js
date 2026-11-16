'use strict';

const { chromium } = require('playwright');

const baseUrl = process.env.QA_BASE_URL || 'http://127.0.0.1:8765';
const username = process.env.QA_TEST_USER || 'qa_admin';
const password = process.env.QA_TEST_PASSWORD || '';
const browserPath = process.env.QA_BROWSER_PATH || '';
if (!password) throw new Error('QA_TEST_PASSWORD is required.');

async function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function indexedDbChecks(page) {
  await page.goto(`${baseUrl}/warehouse/quick-process/`);
  await page.waitForFunction(() => Boolean(window.WarehouseOffline));
  const result = await page.evaluate(async () => {
    const owner = String(document.body.dataset.userId || '');
    const open = () => new Promise((resolve, reject) => {
      const request = indexedDB.open('warehouse-offline', 3);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const add = async item => {
      const db = await open();
      await new Promise((resolve, reject) => {
        const request = db.transaction('queue', 'readwrite').objectStore('queue').add(item);
        request.onsuccess = resolve;
        request.onerror = () => reject(request.error);
      });
    };
    const base = {
      status: 'pending', createdAt: new Date().toISOString(), fields: [],
      title: 'E2E local record', autoSync: false,
    };
    await add({...base, ownerUserId: owner, expiresAt: new Date(Date.now() - 1000).toISOString()});
    await add({...base, ownerUserId: owner, expiresAt: new Date(Date.now() + 60000).toISOString()});
    await add({...base, ownerUserId: 'another-user', expiresAt: new Date(Date.now() + 60000).toISOString()});
    const removedExpired = await window.WarehouseOffline.pruneExpired();
    const visibleBeforeClear = await window.WarehouseOffline.readAll();
    const removedOwner = await window.WarehouseOffline.clearCurrentUser();
    const visibleAfterClear = await window.WarehouseOffline.readAll();
    const db = await open();
    const all = await new Promise((resolve, reject) => {
      const request = db.transaction('queue', 'readonly').objectStore('queue').getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error);
    });
    return {
      removedExpired,
      visibleBeforeClear: visibleBeforeClear.length,
      removedOwner,
      visibleAfterClear: visibleAfterClear.length,
      otherOwnerRemaining: all.filter(item => item.ownerUserId === 'another-user').length,
    };
  });
  await assert(result.removedExpired >= 1, 'Expired offline record was not pruned.');
  await assert(result.visibleBeforeClear === 1, 'Offline records were not isolated by account.');
  await assert(result.removedOwner === 1, 'Current-account offline records were not cleared.');
  await assert(result.visibleAfterClear === 0, 'Current-account queue is not empty after cleanup.');
  await assert(result.otherOwnerRemaining === 1, 'Cleanup removed another account\'s record.');
}

async function main() {
  const launchOptions = {headless: true};
  if (browserPath) launchOptions.executablePath = browserPath;
  const browser = await chromium.launch(launchOptions);
  try {
    const context = await browser.newContext({viewport: {width: 1280, height: 900}});
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('dialog', dialog => dialog.accept());

  const loginResponse = await page.goto(`${baseUrl}/login/`);
  await assert(loginResponse.ok(), `Login page returned ${loginResponse.status()}.`);
  const csp = loginResponse.headers()['content-security-policy'] || '';
  await assert(csp.includes("default-src 'self'"), 'CSP header is missing.');
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', password);
  await Promise.all([
    page.waitForURL(url => !url.pathname.includes('/login/')),
    page.click('button[type="submit"], input[type="submit"]'),
  ]);

  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 900});
    for (const path of ['/', '/requests/new/', '/approvals/', '/warehouse/', '/transactions/']) {
      const response = await page.goto(`${baseUrl}${path}`);
      await assert(response.ok(), `${path} returned ${response.status()} at width ${width}.`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      await assert(!overflow, `${path} has horizontal overflow at width ${width}.`);
    }
  }

  await page.setViewportSize({width: 1280, height: 900});
  await page.goto(`${baseUrl}/warehouse/quick-process/`);
  await page.waitForFunction(() => Boolean(window.WarehouseScanner));
  await page.locator('#item-query').evaluate(element => {
    element.dispatchEvent(new CustomEvent('warehouse:hid-scan', {
      detail: {value: 'SC-E2E-0001'},
    }));
  });
  await page.waitForFunction(() => document.querySelectorAll('#selected-items .selected-line').length === 1);
  await page.fill('#recipient_name', 'E2E 接收人');
  await page.fill('#recipient_phone', '13800000000');
  await page.fill('#usage_location', 'E2E 测试地点');
  await page.fill('#reason', 'E2E 浏览器快速借出回归');
  await Promise.all([
    page.waitForURL(url => /\/workflow\/[0-9a-f-]+\//.test(url.pathname)),
    page.click('button[name="action"][value="queue_quick_process"]'),
  ]);
  await assert((await page.locator('body').innerText()).includes('已完成'), 'Quick inventory request did not complete.');

    await indexedDbChecks(page);
    await assert(pageErrors.length === 0, `Browser page errors: ${pageErrors.join(' | ')}`);
    console.log('E2E smoke passed: login, responsive pages, scan, quick borrow, CSP, offline retention/isolation.');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
