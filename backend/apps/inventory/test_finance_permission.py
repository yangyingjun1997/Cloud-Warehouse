from django.test import TestCase
from rest_framework.test import APIClient

from apps.common.testing.fixtures import WarehouseTestFixture

from .models import Asset


class FinanceFieldPermissionTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.asset = self.make_asset(warehouse=self.warehouse, purchase_amount=1200)
        self.clerk = self.make_operator('finance-clerk')
        self.finance = self.make_user('finance-viewer', groups=('warehouse_finance',))
        self.admin = self.make_user('finance-admin', groups=('warehouse_admin',))

    def _get(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(f'/api/inventory/assets/{self.asset.id}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_clerk_cannot_see_purchase_amount(self):
        self.assertNotIn('purchase_amount', self._get(self.clerk))

    def test_finance_and_admin_can_see_purchase_amount(self):
        self.assertEqual(str(self._get(self.finance)['purchase_amount']), '1200.00')
        self.assertEqual(str(self._get(self.admin)['purchase_amount']), '1200.00')