"""主数据删除语义回归（P2-03 / P1-02 前置）。

覆盖：
- 命中 PROTECT 外键时返回 4xx 而不是 500，且不留下孤儿审计行；
- 成功删除时审计写入删除前快照。
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.common.models import OperationAuditLog
from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import InventoryCheckTask, Location, Warehouse


class MasterDataDeleteTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self.make_user('delete-admin', groups=('warehouse_admin',))
        self.client.force_authenticate(user=self.admin)
        self.warehouse = self.make_warehouse()

    def test_protected_delete_returns_4xx_and_leaves_no_orphan_audit(self):
        location = Location.objects.create(warehouse=self.warehouse, name='A-01', code='A-01')
        InventoryCheckTask.objects.create(warehouse=self.warehouse, location=location)

        response = self.client.delete(f'/api/inventory/locations/{location.id}/')

        self.assertIn(response.status_code, {400, 409}, response.content)
        self.assertTrue(Location.objects.filter(pk=location.pk).exists())
        self.assertFalse(
            OperationAuditLog.objects.filter(action='location_deleted').exists(),
            '删除失败时不应写入删除审计（孤儿审计行会破坏审计可信度）',
        )

    def test_successful_delete_records_snapshot(self):
        category = self.make_category(name='可删分类', code='DEL-CAT')
        response = self.client.delete(f'/api/inventory/categories/{category.id}/')

        self.assertEqual(response.status_code, 204, response.content)
        log = OperationAuditLog.objects.get(action='category_deleted')
        self.assertEqual(log.target_id, category.pk)
        self.assertIn('code', log.before_data, '删除审计应保留删除前字段快照')
        self.assertEqual(log.before_data['code'], 'DEL-CAT')

    def test_non_admin_cannot_delete(self):
        employee = self.make_employee('delete-employee')
        self.client.force_authenticate(user=employee)
        category = self.make_category(name='受保护分类', code='KEEP-CAT')
        response = self.client.delete(f'/api/inventory/categories/{category.id}/')
        self.assertIn(response.status_code, {401, 403})
        self.assertTrue(category.__class__.objects.filter(pk=category.pk).exists())


class WarehouseDeleteCascadeTests(WarehouseTestFixture, TestCase):
    """P1-02 回归：仓库仍被资产引用时不应被删除。

    当前实现会 SET_NULL 资产并 CASCADE 删除库位，属于静默数据损坏，
    先以此用例锁定期望行为；修复 P1-02 后该用例应保持通过。
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = self.make_user('cascade-admin', groups=('warehouse_admin',))
        self.client.force_authenticate(user=self.admin)

    def test_delete_warehouse_in_use_is_refused(self):
        warehouse = self.make_warehouse(name='在用仓库', code='IN-USE')
        location = Location.objects.create(warehouse=warehouse, name='B-01', code='B-01')
        asset = self.make_asset(warehouse=warehouse, location=location)

        response = self.client.delete(f'/api/inventory/warehouses/{warehouse.id}/')

        self.assertIn(response.status_code, {400, 409}, response.content)
        asset.refresh_from_db()
        self.assertEqual(asset.warehouse_id, warehouse.id, '资产不应被静默脱仓')
        self.assertEqual(asset.location_id, location.id, '资产库位不应被清空')
        self.assertTrue(Location.objects.filter(pk=location.pk).exists(), '库位不应被级联删除')
