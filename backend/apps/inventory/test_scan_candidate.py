from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture


class CandidateScanNormalizationTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.asset = self.make_asset(warehouse=self.warehouse)
        self.user = self.make_employee('scan-employee')

    def test_full_width_qr_colon_still_matches(self):
        self.client.force_login(self.user)
        payload = f'WMS2：A：{self.asset.system_asset_no}'
        response = self.client.get(
            '/requests/candidates/scan/',
            {'value': payload, 'request_type': 'borrow'},
        )
        self.assertEqual(response.status_code, 200, response.content[:200])

    def test_trailing_newline_still_matches(self):
        self.client.force_login(self.user)
        response = self.client.get(
            '/requests/candidates/scan/',
            {'value': f'{self.asset.qr_value}\r\n', 'request_type': 'borrow'},
        )
        self.assertEqual(response.status_code, 200, response.content[:200])