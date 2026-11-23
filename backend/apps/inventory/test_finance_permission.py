import csv
import io
from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.workflow.models import InventoryTransaction, WorkflowRequest

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


class TransactionExportCostLeakTests(WarehouseTestFixture, TestCase):
    """回归 P1：无财务权限时，成本「数值」也不能出现在导出里。

    旧的 test_transaction_export_hides_cost_column_without_finance 只断言表头
    不含「库存成本价值」，没有创建任何流水数据，因此数据行里写死的
    cost_amount 从未被检查——这正是该缺陷长期未被发现的原因。
    这里显式造一条带成本值的流水，同时断言数值与列数。
    """

    COST = '8888.00'

    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.asset = self.make_asset(warehouse=self.warehouse)
        self.request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.ISSUE,
            status=WorkflowRequest.Status.DONE,
            reason='导出成本回归',
            application_date=date(2026, 11, 20),
            transaction_date=date(2026, 11, 20),
            target_warehouse=self.warehouse,
        )
        InventoryTransaction.objects.create(
            request=self.request_obj,
            asset=self.asset,
            action=WorkflowRequest.RequestType.ISSUE,
            business_date=date(2026, 11, 20),
            quantity=1,
            cost_amount=Decimal(self.COST),
            comment='cost-leak-regression',
        )
        self.reports_user = self.make_user('export-reports-only', groups=('warehouse_reports',))
        self.finance_user = self.make_user(
            'export-finance', groups=('warehouse_reports', 'warehouse_finance'),
        )

    def _rows(self):
        content = self.client.get('/reports/transactions.csv').content.decode('utf-8')
        return list(csv.reader(io.StringIO(content.lstrip('\ufeff'))))

    def test_non_finance_export_does_not_contain_cost_value(self):
        self.client.force_login(self.reports_user)
        rows = self._rows()
        self.assertNotIn('库存成本价值', rows[0])
        for row in rows[1:]:
            self.assertNotIn(self.COST, row, f'数据行泄露成本值：{row}')

    def test_every_row_matches_header_width(self):
        """列数错位同样有害：下游会把成本当成「项目编号」解析。"""
        for user in (self.reports_user, self.finance_user):
            self.client.force_login(user)
            rows = self._rows()
            width = len(rows[0])
            for row in rows[1:]:
                self.assertEqual(len(row), width, f'{user.username} 导出行列数与表头不一致')

    def test_finance_export_keeps_cost_value(self):
        self.client.force_login(self.finance_user)
        rows = self._rows()
        self.assertTrue(
            any('库存成本价值' in cell for cell in rows[0]), f'财务导出缺少成本列：{rows[0]}'
        )
        cost_index = rows[0].index('库存成本价值（元）')
        self.assertIn(self.COST, [row[cost_index] for row in rows[1:]])