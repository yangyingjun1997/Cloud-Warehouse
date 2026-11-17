from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook

from apps.common.models import OperationAuditLog

from .import_batch_services import create_import_batch
from .models import (
    Asset, AssetNetworkEndpoint, Category, ImportBatch, ImportMutation,
    InventoryDomain, ItemType, Location, StockItem, Warehouse,
)
from .import_recovery_services import (
    ControlledAssetDeletionError, ImportRecoveryError,
    controlled_delete_test_asset, rollback_import_batch,
)
from .v2_import_services import HEADERS as ASSET_HEADERS
from .v3_import_services import (
    ASSET_SHEET_NAME, STOCK_HEADERS, STOCK_SHEET_NAME, InventoryV3ImportError,
    apply_inventory_v3_import, build_inventory_v3_template,
    export_inventory_v3_workbook, parse_inventory_v3_workbook,
    validate_inventory_v3_records,
)


User = get_user_model()


class InventoryV3ImportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('v3-admin', '', 'password123')
        self.category = Category.objects.create(name='V3 测试分类', code='V3-CAT')
        self.asset_type = ItemType.objects.create(
            name='V3 单件资产', code='V3-ASSET', category=self.category,
            is_serialized=True, unit='台',
        )
        self.stock_type = ItemType.objects.create(
            name='V3 耗材配件', code='V3-STOCK', category=self.category,
            is_serialized=False, unit='件',
        )
        self.warehouse = Warehouse.objects.create(name='V3 测试仓', code='V3-WH')
        self.location = Location.objects.create(
            warehouse=self.warehouse, name='V3 测试库位', code='A-01',
        )
        InventoryDomain.objects.update_or_create(
            code='RND', defaults={
                'name': '研发资产',
                'code_scheme': InventoryDomain.CodeScheme.ADMIN_ASSIGNED,
                'is_active': True,
            },
        )

    def _content(self, *, asset_rows=(), stock_rows=()):
        workbook = load_workbook(build_inventory_v3_template())
        asset_sheet = workbook[ASSET_SHEET_NAME]
        stock_sheet = workbook[STOCK_SHEET_NAME]
        asset_sheet.delete_rows(2, asset_sheet.max_row - 1)
        stock_sheet.delete_rows(2, stock_sheet.max_row - 1)
        for values in asset_rows:
            row = {header.rstrip('*'): '' for header in ASSET_HEADERS}
            row.update(values)
            asset_sheet.append([row[header.rstrip('*')] for header in ASSET_HEADERS])
        for values in stock_rows:
            row = {header.rstrip('*'): '' for header in STOCK_HEADERS}
            row.update(values)
            stock_sheet.append([row[header.rstrip('*')] for header in STOCK_HEADERS])
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()

    def _asset_row(self, **overrides):
        row = {
            '导入动作': '新增', '资产归属': 'RND', '编码方式': 'pending_admin',
            '资产名称': 'V3 新资产', '物品类型代码': self.asset_type.code,
            'SN状态': 'no_sn', '仓库代码': self.warehouse.code,
            '库位代码': self.location.code,
        }
        row.update(overrides)
        return row

    def _stock_row(self, **overrides):
        row = {
            '导入动作': '新增', '物品名称': 'V3 新耗材',
            '物品类型代码': self.stock_type.code, '仓库代码': self.warehouse.code,
            '库位代码': self.location.code, '库存数量': 12, '单位': '件',
            '安全库存': 3,
        }
        row.update(overrides)
        return row

    def _preview(self, content):
        records, issues, summary = parse_inventory_v3_workbook(content)
        issues.extend(validate_inventory_v3_records(records))
        summary = {
            **summary,
            'error_count': sum(issue['severity'] == 'error' for issue in issues),
            'warning_count': sum(issue['severity'] == 'warning' for issue in issues),
        }
        batch = create_import_batch(
            actor=self.admin, filename='v3.xlsx', content=content,
            mode=ImportBatch.Mode.INCREMENTAL, records=records,
            issues=issues, summary=summary,
        )
        return records, issues, batch

    def test_template_and_export_use_two_business_sheets(self):
        workbook = load_workbook(build_inventory_v3_template(), data_only=True)
        self.assertEqual(workbook.sheetnames[:2], [ASSET_SHEET_NAME, STOCK_SHEET_NAME])
        self.assertEqual([cell.value for cell in workbook[ASSET_SHEET_NAME][1]], ASSET_HEADERS)
        self.assertEqual([cell.value for cell in workbook[STOCK_SHEET_NAME][1]], STOCK_HEADERS)

        exported = load_workbook(export_inventory_v3_workbook(), data_only=True)
        self.assertEqual(exported[ASSET_SHEET_NAME].max_row, 1)
        self.assertEqual(exported[STOCK_SHEET_NAME].max_row, 1)

    def test_combined_import_creates_asset_and_stock_in_one_batch(self):
        content = self._content(
            asset_rows=[self._asset_row()],
            stock_rows=[self._stock_row(耗材配件编码='V3-STOCK-001')],
        )
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])

        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        asset = Asset.objects.get(name='V3 新资产')
        stock = StockItem.objects.get(code='V3-STOCK-001')
        batch.refresh_from_db()
        self.assertRegex(asset.system_asset_no, r'^AST-\d{8}$')
        self.assertEqual((stock.quantity, stock.safety_stock), (12, 3))
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        self.assertEqual((batch.imported_asset_count, batch.imported_stock_item_count), (1, 1))
        self.assertEqual(batch.mutations.count(), 2)
        self.assertEqual(
            set(batch.mutations.values_list('operation', flat=True)),
            {ImportMutation.Operation.CREATE},
        )
        self.assertTrue(OperationAuditLog.objects.filter(action='inventory_v3_import_applied').exists())

    def test_exported_stock_can_be_updated(self):
        stock = StockItem.objects.create(
            name='已有耗材', code='V3-EXISTING', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=5, unit='件',
        )
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=stock.code,
            物品名称=stock.name, 库存数量=19,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])

        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 19)
        self.assertEqual(StockItem.objects.filter(code=stock.code).count(), 1)

    def test_failure_rolls_back_asset_and_stock_together(self):
        content = self._content(
            asset_rows=[self._asset_row(资产名称='事务回滚资产')],
            stock_rows=[self._stock_row(耗材配件编码='V3-ROLLBACK')],
        )
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        records['stocks'][0]['warehouse_code'] = 'MISSING-WH'

        with self.assertRaises(Warehouse.DoesNotExist):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        self.assertFalse(Asset.objects.filter(name='事务回滚资产').exists())
        self.assertFalse(StockItem.objects.filter(code='V3-ROLLBACK').exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_update_requires_stock_code(self):
        content = self._content(stock_rows=[self._stock_row(导入动作='更新')])
        _, issues, _ = self._preview(content)
        self.assertTrue(any(issue['code'] == 'update_code_required' for issue in issues))

    def test_rollback_removes_created_asset_and_stock(self):
        content = self._content(
            asset_rows=[self._asset_row(资产名称='可撤销资产')],
            stock_rows=[self._stock_row(耗材配件编码='V3-ROLLBACK-CREATED')],
        )
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        rollback_import_batch(
            actor=self.admin, batch=batch, confirmation=str(batch.id), reason='测试整批撤销',
        )

        self.assertFalse(Asset.objects.filter(name='可撤销资产').exists())
        self.assertFalse(StockItem.objects.filter(code='V3-ROLLBACK-CREATED').exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.ROLLED_BACK)
        self.assertEqual(batch.mutations.filter(rolled_back_at__isnull=False).count(), 2)
        self.assertTrue(OperationAuditLog.objects.filter(action='inventory_import_batch_rolled_back').exists())

    def test_rollback_restores_updated_stock(self):
        stock = StockItem.objects.create(
            name='撤销更新耗材', code='V3-ROLLBACK-UPDATE', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=4, unit='件',
        )
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=stock.code,
            物品名称=stock.name, 库存数量=17,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        rollback_import_batch(
            actor=self.admin, batch=batch, confirmation=str(batch.id), reason='恢复导入前数量',
        )

        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 4)
        self.assertIsNone(stock.source_import_batch_id)

    def test_rollback_restores_updated_asset(self):
        domain = InventoryDomain.objects.get(code='RND')
        asset = Asset.objects.create(
            name='更新前资产名称', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
            domain=domain, code_source=Asset.CodeSource.PENDING_ADMIN,
            sn_status=Asset.SnStatus.NO_SN,
        )
        content = self._content(asset_rows=[self._asset_row(
            导入动作='更新', 系统永久编号=asset.system_asset_no,
            资产名称='更新后资产名称',
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        rollback_import_batch(
            actor=self.admin, batch=batch, confirmation=str(batch.id), reason='恢复资产资料',
        )

        asset.refresh_from_db()
        self.assertEqual(asset.name, '更新前资产名称')
        self.assertIsNone(asset.source_import_batch_id)

    def test_rollback_is_blocked_when_object_changed_after_import(self):
        stock = StockItem.objects.create(
            name='后续修改耗材', code='V3-AFTER-CHANGE', item_type=self.stock_type,
            warehouse=self.warehouse, quantity=2, unit='件',
        )
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=stock.code,
            物品名称=stock.name, 库位代码='', 库存数量=7,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)
        stock.refresh_from_db()
        stock.quantity = 8
        stock.save(update_fields=['quantity', 'updated_at'])

        with self.assertRaisesMessage(ImportRecoveryError, '又被修改'):
            rollback_import_batch(
                actor=self.admin, batch=batch,
                confirmation=str(batch.id), reason='不应覆盖后续修改',
            )
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, 8)

    def test_rollback_restores_baseline_zeroed_stock(self):
        included = StockItem.objects.create(
            name='基准保留耗材', code='V3-BASELINE-KEEP', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=5, unit='件',
        )
        missing = StockItem.objects.create(
            name='基准缺失耗材', code='V3-BASELINE-MISSING', item_type=self.stock_type,
            warehouse=self.warehouse, quantity=9, unit='件',
        )
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=included.code,
            物品名称=included.name, 库存数量=5,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)
        missing.refresh_from_db()
        self.assertEqual(missing.quantity, 0)
        self.assertTrue(batch.mutations.filter(
            object_id=missing.id, operation=ImportMutation.Operation.BASELINE,
        ).exists())

        rollback_import_batch(
            actor=self.admin, batch=batch, confirmation=str(batch.id), reason='撤销全量基准',
        )

        missing.refresh_from_db()
        self.assertEqual(missing.quantity, 9)

    def test_baseline_blocks_missing_asset_with_active_status_before_any_write(self):
        missing = Asset.objects.create(
            name='基准缺失借出资产', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
            status=Asset.Status.BORROWED,
        )
        included = Asset.objects.create(
            name='基准保留资产', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
        )
        content = self._content(asset_rows=[self._asset_row(
            导入动作='更新', 系统永久编号=included.system_asset_no,
            资产名称='不应被部分更新',
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])

        with self.assertRaisesMessage(InventoryV3ImportError, '仍有业务关系'):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        missing.refresh_from_db()
        included.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(missing.status, Asset.Status.BORROWED)
        self.assertEqual(included.name, '基准保留资产')
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_baseline_blocks_missing_asset_with_active_reservation(self):
        from apps.workflow.models import InventoryReservation, WorkflowRequest, WorkflowRequestLine

        missing = Asset.objects.create(
            name='基准缺失预占资产', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
        )
        included = Asset.objects.create(
            name='基准保留资产二', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
        )
        request_obj = WorkflowRequest.objects.create(
            applicant=self.admin, request_type=WorkflowRequest.RequestType.BORROW,
        )
        line = WorkflowRequestLine.objects.create(request=request_obj, asset=missing)
        InventoryReservation.objects.create(
            request_line=line, asset=missing, quantity=1,
        )
        content = self._content(asset_rows=[self._asset_row(
            导入动作='更新', 系统永久编号=included.system_asset_no,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])

        with self.assertRaisesMessage(InventoryV3ImportError, '库存预占'):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        self.assertEqual(batch.mutations.count(), 0)
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_baseline_blocks_missing_asset_with_open_maintenance_record(self):
        from apps.inventory.models import MaintenanceRecord

        missing = Asset.objects.create(
            name='基准缺失维修资产', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
        )
        included = Asset.objects.create(
            name='基准保留资产三', item_type=self.asset_type,
            warehouse=self.warehouse, location=self.location,
        )
        MaintenanceRecord.objects.create(
            asset=missing, fault_description='待处理故障',
            status=MaintenanceRecord.Status.OPEN,
        )
        content = self._content(asset_rows=[self._asset_row(
            导入动作='更新', 系统永久编号=included.system_asset_no,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])

        with self.assertRaisesMessage(InventoryV3ImportError, '未完成维修记录'):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_baseline_blocks_missing_stock_with_holding_before_asset_write(self):
        from apps.inventory.models import StockItemHolding

        missing = StockItem.objects.create(
            name='基准缺失借用耗材', code='V3-BASELINE-HOLDING', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=9, unit='件',
        )
        included = StockItem.objects.create(
            name='基准保留耗材二', code='V3-BASELINE-HOLDING-KEEP', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=5, unit='件',
        )
        StockItemHolding.objects.create(stock_item=missing, holder=self.admin, quantity=2)
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=included.code, 物品名称='不应被部分更新',
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])

        with self.assertRaisesMessage(InventoryV3ImportError, '个人借用余额'):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        missing.refresh_from_db()
        included.refresh_from_db()
        self.assertEqual(missing.quantity, 9)
        self.assertEqual(included.name, '基准保留耗材二')
        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_baseline_blocks_missing_stock_with_active_loan(self):
        from datetime import date

        from apps.workflow.models import StockItemLoan, WorkflowRequest, WorkflowRequestLine

        missing = StockItem.objects.create(
            name='基准缺失未归还耗材', code='V3-BASELINE-LOAN', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=9, unit='件',
        )
        included = StockItem.objects.create(
            name='基准保留耗材三', code='V3-BASELINE-LOAN-KEEP', item_type=self.stock_type,
            warehouse=self.warehouse, location=self.location, quantity=5, unit='件',
        )
        request_obj = WorkflowRequest.objects.create(
            applicant=self.admin, request_type=WorkflowRequest.RequestType.BORROW,
        )
        line = WorkflowRequestLine.objects.create(
            request=request_obj, stock_item=missing, quantity=2,
        )
        StockItemLoan.objects.create(
            borrower=self.admin, stock_item=missing, source_line=line,
            original_quantity=2, outstanding_quantity=2,
            expected_return_date=date.today(),
        )
        content = self._content(stock_rows=[self._stock_row(
            导入动作='更新', 耗材配件编码=included.code,
        )])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        batch.mode = ImportBatch.Mode.BASELINE
        batch.save(update_fields=['mode', 'updated_at'])

        with self.assertRaisesMessage(InventoryV3ImportError, '未归还借用'):
            apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)

        self.assertEqual(batch.status, ImportBatch.Status.PREVIEWED)

    def test_rollback_is_blocked_after_business_history(self):
        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine

        content = self._content(asset_rows=[self._asset_row(资产名称='已有业务历史资产')])
        records, issues, batch = self._preview(content)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)
        asset = Asset.objects.get(name='已有业务历史资产')
        request = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.admin,
        )
        WorkflowRequestLine.objects.create(request=request, asset=asset)

        with self.assertRaisesMessage(ImportRecoveryError, '申请单明细'):
            rollback_import_batch(
                actor=self.admin, batch=batch,
                confirmation=str(batch.id), reason='不应成功',
            )

        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)

    def test_rollback_requires_superuser_and_exact_confirmation(self):
        content = self._content(stock_rows=[self._stock_row(耗材配件编码='V3-GUARD')])
        records, _, batch = self._preview(content)
        apply_inventory_v3_import(actor=self.admin, batch=batch, records=records)
        employee = User.objects.create_user('v3-employee', password='password123')

        with self.assertRaisesMessage(ImportRecoveryError, '超级管理员'):
            rollback_import_batch(
                actor=employee, batch=batch, confirmation=str(batch.id), reason='越权',
            )
        with self.assertRaisesMessage(ImportRecoveryError, '确认文字'):
            rollback_import_batch(
                actor=self.admin, batch=batch, confirmation='wrong', reason='错误确认',
            )

    def test_import_page_exposes_only_v3_daily_actions(self):
        self.client.force_login(self.admin)
        response = self.client.get('/warehouse/import/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '下载 V3 填写模板')
        self.assertContains(response, '导出当前库存')
        self.assertNotContains(response, '下载资产 V2 模板')
        self.assertNotContains(response, '下载旧版耗材模板')


class InventoryV3ErrorTranslationTests(TestCase):
    def test_missing_business_sheets_has_clear_error(self):
        output = BytesIO()
        workbook = load_workbook(build_inventory_v3_template())
        del workbook[STOCK_SHEET_NAME]
        workbook.save(output)
        with self.assertRaisesMessage(InventoryV3ImportError, '必须同时包含'):
            parse_inventory_v3_workbook(output.getvalue())


class ControlledTestAssetDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('delete-admin', '', 'password123')
        self.employee = User.objects.create_user('delete-employee', password='password123')
        category = Category.objects.create(name='删除测试分类', code='DELETE-CAT')
        self.item_type = ItemType.objects.create(
            name='删除测试资产', code='DELETE-ASSET', category=category, is_serialized=True,
        )
        self.warehouse = Warehouse.objects.create(name='删除测试仓', code='DELETE-WH')

    def _asset(self, name='待删除测试资产'):
        return Asset.objects.create(
            name=name, item_type=self.item_type, warehouse=self.warehouse,
        )

    def test_superuser_can_delete_asset_without_business_history(self):
        asset = self._asset()
        endpoint = AssetNetworkEndpoint.objects.create(
            asset=asset,
            interface_name='eth0',
            ip_address='192.0.2.10',
            subnet_mask='255.255.255.0',
            gateway='192.0.2.1',
            mac_address='00:11:22:33:44:55',
            hostname='delete-test',
            vlan_id=120,
            is_primary=True,
            notes='完整快照测试',
        )
        asset_id = asset.id
        label = controlled_delete_test_asset(
            actor=self.admin, asset=asset,
            confirmation=asset.system_asset_no, reason='调试阶段重复录入',
        )

        self.assertEqual(label, asset.system_asset_no)
        self.assertFalse(Asset.objects.filter(pk=asset_id).exists())
        audit = OperationAuditLog.objects.get(
            action='controlled_test_asset_deleted', target_id=asset_id,
        )
        self.assertEqual(audit.before_data['reason'], '调试阶段重复录入')
        endpoint_snapshot = audit.before_data['network_endpoints'][0]
        self.assertEqual(endpoint_snapshot['id'], str(endpoint.id))
        self.assertEqual(endpoint_snapshot['asset_id'], str(asset_id))
        self.assertEqual(endpoint_snapshot['subnet_mask'], '255.255.255.0')
        self.assertEqual(endpoint_snapshot['gateway'], '192.0.2.1')
        self.assertEqual(endpoint_snapshot['vlan_id'], 120)
        self.assertTrue(endpoint_snapshot['is_primary'])
        self.assertEqual(endpoint_snapshot['notes'], '完整快照测试')
        self.assertIn('created_at', endpoint_snapshot)
        self.assertIn('updated_at', endpoint_snapshot)
        self.assertTrue(audit.after_data['deleted'])

    def test_delete_is_blocked_by_request_history(self):
        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine

        asset = self._asset('有申请历史资产')
        request = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.admin,
        )
        WorkflowRequestLine.objects.create(request=request, asset=asset)

        with self.assertRaisesMessage(ControlledAssetDeletionError, '申请单明细'):
            controlled_delete_test_asset(
                actor=self.admin, asset=asset,
                confirmation=asset.system_asset_no, reason='不应删除',
            )
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())

    def test_delete_requires_superuser_confirmation_and_reason(self):
        asset = self._asset()
        with self.assertRaisesMessage(ControlledAssetDeletionError, '超级管理员'):
            controlled_delete_test_asset(
                actor=self.employee, asset=asset,
                confirmation=asset.system_asset_no, reason='越权',
            )
        with self.assertRaisesMessage(ControlledAssetDeletionError, '确认文字'):
            controlled_delete_test_asset(
                actor=self.admin, asset=asset,
                confirmation='wrong', reason='错误确认',
            )
        with self.assertRaisesMessage(ControlledAssetDeletionError, '删除原因'):
            controlled_delete_test_asset(
                actor=self.admin, asset=asset,
                confirmation=asset.system_asset_no, reason='',
            )
