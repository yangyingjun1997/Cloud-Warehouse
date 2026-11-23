"""主数据删除语义回归。

覆盖：
- 命中 PROTECT 外键时返回 4xx 而不是 500，且不留下孤儿审计行；
- 成功删除时审计写入删除前快照。
"""

from django.test import TestCase
from django.contrib import admin
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import ValidationError
from django.db.models.deletion import PROTECT, ProtectedError
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.common.models import OperationAuditLog
from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Asset, InventoryCheckTask, Location, StockItem, Warehouse
from apps.workflow.models import InventoryTransaction, WorkflowRequest


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
    """P1-01 回归：仓库仍被资产引用时不应被删除。
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

    def test_delete_location_in_use_is_refused(self):
        warehouse = self.make_warehouse(name='库位保护仓', code='LOC-WH')
        location = Location.objects.create(warehouse=warehouse, name='C-01', code='C-01')
        self.make_asset(warehouse=warehouse, location=location, asset_code='SC-LOC-0001')

        response = self.client.delete(f'/api/inventory/locations/{location.id}/')

        self.assertIn(response.status_code, {400, 409}, response.content)
        self.assertTrue(Location.objects.filter(pk=location.pk).exists())

    def test_direct_orm_delete_is_protected_for_assets_and_locations(self):
        warehouse = self.make_warehouse(name='ORM保护仓', code='ORM-WH')
        location = Location.objects.create(warehouse=warehouse, name='D-01', code='D-01')
        asset = self.make_asset(warehouse=warehouse, location=location, asset_code='SC-ORM-0001')

        with self.assertRaises(ProtectedError):
            warehouse.delete()
        with self.assertRaises(ProtectedError):
            location.delete()

        asset.refresh_from_db()
        self.assertEqual(asset.warehouse_id, warehouse.id)
        self.assertEqual(asset.location_id, location.id)

    def test_workflow_history_also_protects_warehouse(self):
        warehouse = self.make_warehouse(name='历史保护仓', code='HISTORY-WH')
        WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.admin,
            target_warehouse=warehouse,
        )

        with self.assertRaises(ProtectedError):
            warehouse.delete()


class WarehouseAdminDeleteTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.admin_user = self.make_user('admin-delete', groups=('warehouse_admin',))
        self.factory = RequestFactory()
        self.model_admin = admin.site._registry[Warehouse]

    def _request(self, method='post'):
        request = getattr(self.factory, method)('/admin/inventory/warehouse/')
        request.user = self.admin_user
        return request

    def test_single_delete_refuses_in_use_object_without_audit(self):
        warehouse = self.make_warehouse(name='Admin保护仓', code='ADMIN-WH')
        location = Location.objects.create(warehouse=warehouse, name='E-01', code='E-01')
        self.make_asset(warehouse=warehouse, location=location, asset_code='SC-ADMIN-0001')

        with self.assertRaises(ValidationError):
            self.model_admin.delete_model(self._request(), warehouse)

        self.assertTrue(Warehouse.objects.filter(pk=warehouse.pk).exists())
        self.assertFalse(OperationAuditLog.objects.filter(action='warehouse_admin_deleted').exists())

    def test_single_delete_records_complete_snapshot_after_success(self):
        warehouse = self.make_warehouse(name='Admin可删仓', code='ADMIN-EMPTY')
        warehouse_id = str(warehouse.pk)

        self.model_admin.delete_model(self._request(), warehouse)

        self.assertFalse(Warehouse.objects.filter(pk=warehouse.pk).exists())
        log = OperationAuditLog.objects.get(action='warehouse_admin_deleted')
        self.assertEqual(log.before_data['id'], warehouse_id)
        self.assertEqual(str(log.target_id), warehouse_id)
        self.assertEqual(log.before_data['code'], 'ADMIN-EMPTY')

    def test_batch_delete_rolls_back_when_one_object_is_protected(self):
        empty = self.make_warehouse(name='Admin批量空仓', code='ADMIN-BATCH-EMPTY')
        protected = self.make_warehouse(name='Admin批量在用仓', code='ADMIN-BATCH-USED')
        location = Location.objects.create(warehouse=protected, name='F-01', code='F-01')
        self.make_asset(warehouse=protected, location=location, asset_code='SC-BATCH-0001')
        request = self._request()
        request._messages = CookieStorage(request)

        self.model_admin.delete_queryset(
            request,
            Warehouse.objects.filter(pk__in=[empty.pk, protected.pk]),
        )

        self.assertTrue(Warehouse.objects.filter(pk=empty.pk).exists())
        self.assertTrue(Warehouse.objects.filter(pk=protected.pk).exists())
        self.assertFalse(OperationAuditLog.objects.filter(action='warehouse_admin_deleted').exists())


class MasterDataDeleteMetadataTests(TestCase):
    def test_master_data_references_use_protect(self):
        protected_fields = {
            (Asset, 'warehouse'),
            (Asset, 'location'),
            (StockItem, 'warehouse'),
            (StockItem, 'location'),
            (Location, 'warehouse'),
            (InventoryTransaction, 'source_warehouse'),
            (InventoryTransaction, 'target_warehouse'),
            (WorkflowRequest, 'target_warehouse'),
            (WorkflowRequest, 'target_location'),
        }
        for model, field_name in protected_fields:
            with self.subTest(model=model.__name__, field=field_name):
                self.assertIs(
                    model._meta.get_field(field_name).remote_field.on_delete,
                    PROTECT,
                )
