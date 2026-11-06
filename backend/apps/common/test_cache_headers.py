from django.test import TestCase
from django.contrib.staticfiles import finders

from apps.common.testing.fixtures import WarehouseTestFixture


class HtmlNoStoreTests(WarehouseTestFixture, TestCase):
    def test_html_pages_are_not_cached(self):
        user = self.make_operator('cache-check')
        self.client.force_login(user)
        response = self.client.get('/warehouse/quick-process/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        content = response.content.decode()
        self.assertNotIn('id="network-status"', content)
        self.assertIn('id="scanner-status"', content)
        self.assertIn('设备与扫码状态', content)

    def test_scanner_toggle_exists_on_item_entry_pages(self):
        user = self.make_operator('scanner-page')
        self.client.force_login(user)
        for url in ('/warehouse/quick-process/', '/requests/new/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '设备与扫码状态')
                self.assertContains(response, "扫码监听：<b>' + (supported ? (enabled ? '开' : '关') : '本页不适用')")

    def test_scanner_status_does_not_show_usb_presence(self):
        user = self.make_operator('scanner-status-copy')
        self.client.force_login(user)
        response = self.client.get('/requests/')
        self.assertNotContains(response, 'USB 连接状态')
        self.assertNotContains(response, '浏览器无法判断')
        self.assertNotContains(response, '扫码枪已连接')

    def test_item_entry_pages_queue_scans_and_clear_input_before_request(self):
        user = self.make_operator('scanner-queue')
        self.client.force_login(user)
        for url in ('/warehouse/quick-process/', '/requests/new/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'let scanQueue = Promise.resolve()')
                self.assertContains(response, 'const scannedValue = queryInput.value')
                self.assertContains(response, "queryInput.value = ''")
                self.assertContains(response, 'await enqueueScan(scannedValue, true)')
                self.assertContains(response, "queryInput.addEventListener('warehouse:hid-scan'")

    def test_global_hid_scan_uses_dedicated_event_without_focusing_search(self):
        user = self.make_operator('scanner-event')
        self.client.force_login(user)
        response = self.client.get('/warehouse/quick-process/')
        content = response.content.decode()
        self.assertIn("new CustomEvent('warehouse:hid-scan'", content)
        event_branch = content.split("if (candidateInput.id === 'item-query')", 1)[1].split('return;', 1)[0]
        self.assertNotIn('candidateInput.focus()', event_branch)

    def test_global_hid_scan_captures_focused_item_query(self):
        user = self.make_operator('scanner-focused-input')
        self.client.force_login(user)
        response = self.client.get('/warehouse/quick-process/')
        content = response.content.decode()

        self.assertIn("captureEditable: (element) => element?.id === 'item-query'", content)
        self.assertIn('/static/warehouse/scanner.js?v=20261105-3', content)
        scanner_path = finders.find('warehouse/scanner.js')
        self.assertIsNotNone(scanner_path)
        with open(scanner_path, encoding='utf-8') as scanner_file:
            scanner_source = scanner_file.read()
        self.assertIn('event.stopImmediatePropagation()', scanner_source)
        self.assertIn('!captureEditable(event.target)', scanner_source)

    def test_service_worker_fetches_changing_scripts_network_first(self):
        worker = self.client.get('/sw.js')

        self.assertContains(worker, 'warehouse-static-v5')
        self.assertContains(worker, 'FRESH_STATIC_PATHS')
        self.assertContains(worker, "fetch(request, {cache: 'no-store'})")
        self.assertNotContains(worker, "'/static/warehouse/scanner.js',\n  '/static/warehouse/offline.js',\n  '/static/warehouse/vendor")
    def test_first_html_response_clears_old_cache(self):
        user = self.make_operator('cache-reset')
        self.client.force_login(user)
        response = self.client.get('/warehouse/quick-process/')
        self.assertEqual(response['Clear-Site-Data'], '"cache"')
        self.assertIn('warehouse-cache-reset', response.cookies)

        self.client.cookies['warehouse-cache-reset'] = '3'
        response = self.client.get('/warehouse/quick-process/')
        self.assertNotIn('Clear-Site-Data', response)
