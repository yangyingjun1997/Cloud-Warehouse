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


class FinanceWebVisibilityTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.asset = self.make_asset(warehouse=self.warehouse, purchase_amount=1200)
        self.reports_user = self.make_user('reports-only', groups=('warehouse_reports',))
        self.finance_user = self.make_user(
            'reports-finance', groups=('warehouse_reports', 'warehouse_finance'),
        )

    def test_report_hides_cost_without_finance_permission(self):
        self.client.force_login(self.reports_user)
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '库存成本价值')
        self.assertNotContains(response, '1200.00')

    def test_report_shows_cost_with_finance_permission(self):
        self.client.force_login(self.finance_user)
        response = self.client.get('/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '库存成本价值')
        self.assertContains(response, '1200.00')

    def test_cost_metric_request_falls_back_to_count(self):
        self.client.force_login(self.reports_user)
        response = self.client.get('/reports/?metric=cost')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '库存动作趋势')
        self.assertNotContains(response, '库存成本价值趋势')

    def test_supplier_finance_reports_require_finance_permission(self):
        self.client.force_login(self.reports_user)
        for path in ('/reports/supplier-prices/', '/reports/supplier-reconciliation/'):
            self.assertEqual(self.client.get(path).status_code, 302, path)
        self.client.force_login(self.finance_user)
        for path in ('/reports/supplier-prices/', '/reports/supplier-reconciliation/'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_transaction_export_hides_cost_column_without_finance(self):
        self.client.force_login(self.reports_user)
        content = self.client.get('/reports/transactions.csv').content.decode('utf-8')
        self.assertNotIn('库存成本价值', content)
        self.client.force_login(self.finance_user)
        content = self.client.get('/reports/transactions.csv').content.decode('utf-8')
        self.assertIn('库存成本价值', content)