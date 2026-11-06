from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook

from apps.common.models import OperationAuditLog

from .code_services import (
    AssetCodeError,
    apply_asset_identity,
    change_asset_domain,
    parse_asset_qr_payload,
    validate_sn,
)
from .models import (
    Asset, AssetAssemblyLink, AssetCodeSequence, AssetDomainChange, AssetIdentifier,
    AssetTypeCode, Category, ImportBatch, InventoryDomain, ItemType, Location,
    Manufacturer, ProductModelCode, Warehouse,
)
from .v2_import_services import (
    HEADERS,
    SHEET_NAME,
    AssetV2ImportError,
    apply_asset_v2_import,
    build_asset_v2_template,
    parse_asset_v2_workbook,
    validate_asset_v2_records,
)
from .search import asset_search_query


class AssetIdentityV2Test(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('identity_admin', password='test-pass')
        category = Category.objects.create(name='默认分类', code='DEFAULT')
        self.item_type = ItemType.objects.create(
            name='机器人', code='ROBOT', category=category, is_serialized=True,
        )
        self.market_warehouse = Warehouse.objects.create(name='市场库存', code='MARKET')
        self.rnd_warehouse = Warehouse.objects.create(name='研发库存', code='RND')
        self.market_location = Location.objects.create(warehouse=self.market_warehouse, name='A01', code='A01')
        self.rnd_location = Location.objects.create(warehouse=self.rnd_warehouse, name='R01', code='R01')
        self.market_domain = InventoryDomain.objects.get(code='MARKET')
        self.rnd_domain = InventoryDomain.objects.get(code='RND')
        # 0024 迁移已从真实台账预置基础资料，这里复用而不是重复创建。
        self.manufacturer, _ = Manufacturer.objects.get_or_create(
            code='HIK', defaults={'name': '海康威视'},
        )
        self.asset_type, _ = AssetTypeCode.objects.get_or_create(
            code='YT', defaults={'name': '云台吊舱', 'created_by': self.user},
        )
        self.product_model, _ = ProductModelCode.objects.get_or_create(
            asset_type_code=self.asset_type,
            code='HK15W',
            defaults={
                'manufacturer': self.manufacturer,
                'name': '海康 15W',
                'created_by': self.user,
            },
        )
        self.product_model.sc_prefix = 'SC-YT-HK15W'
        self.product_model.sc_serial_width = 4
        self.product_model.save(update_fields=['sc_prefix', 'sc_serial_width', 'updated_at'])

    def make_market_asset(self, serial_number='SN-001'):
        return apply_asset_identity(Asset(
            domain=self.market_domain,
            code_source=Asset.CodeSource.MARKET_GENERATED,
            asset_type_code=self.asset_type,
            product_model_code=self.product_model,
            name='海康云台', item_type=self.item_type,
            manufacturer_ref=self.manufacturer,
            serial_number=serial_number,
            sn_status=Asset.SnStatus.PROVIDED,
            warehouse=self.market_warehouse,
            location=self.market_location,
        ), actor=self.user, reason='测试录入')

    def test_market_asset_gets_permanent_market_and_qr_codes(self):
        asset = self.make_market_asset()
        self.assertRegex(asset.system_asset_no, r'^AST-\d{8}$')
        self.assertEqual(asset.asset_code, 'SC-YT-HK15W-0001')
        self.assertEqual(asset.qr_value, f'WMS2:A:{asset.system_asset_no}')
        self.assertEqual(parse_asset_qr_payload(asset.qr_value)['identifier'], asset.system_asset_no)
        self.assertTrue(asset.identifiers.filter(identifier_type='system').exists())
        self.assertTrue(asset.identifiers.filter(identifier_type='market', is_primary=True).exists())

    def test_market_sequence_is_independent_per_prefix(self):
        first = self.make_market_asset('SN-001')
        second = self.make_market_asset('SN-002')
        other_model = ProductModelCode.objects.create(
            asset_type_code=self.asset_type, code='DH2', name='大华 2',
            sc_prefix='SC-YT-DH2', created_by=self.user,
        )
        third = apply_asset_identity(Asset(
            domain=self.market_domain, code_source=Asset.CodeSource.MARKET_GENERATED,
            asset_type_code=self.asset_type, product_model_code=other_model,
            name='另一型号', item_type=self.item_type, sn_status=Asset.SnStatus.NO_SN,
            warehouse=self.market_warehouse,
        ), actor=self.user)
        self.assertTrue(first.asset_code.endswith('0001'))
        self.assertTrue(second.asset_code.endswith('0002'))
        self.assertEqual(third.asset_code, 'SC-YT-DH2-0001')

    def test_rnd_pending_admin_uses_only_system_code(self):
        asset = apply_asset_identity(Asset(
            domain=self.rnd_domain, code_source=Asset.CodeSource.PENDING_ADMIN,
            name='研发样机', item_type=self.item_type, sn_status=Asset.SnStatus.NO_SN,
            warehouse=self.rnd_warehouse,
        ), actor=self.user)
        self.assertIsNone(asset.asset_code)
        self.assertRegex(asset.system_asset_no, r'^AST-\d{8}$')
        self.assertFalse(asset.identifiers.filter(is_primary=True).exists())

    def test_rnd_asset_cannot_use_market_code(self):
        asset = Asset(
            domain=self.rnd_domain, code_source=Asset.CodeSource.ADMIN_ASSIGNED,
            asset_code='SC-YT-HK15W-9999', name='错误编码', item_type=self.item_type,
            sn_status=Asset.SnStatus.NO_SN, warehouse=self.rnd_warehouse,
        )
        with self.assertRaises(AssetCodeError):
            apply_asset_identity(asset, actor=self.user)

    def test_duplicate_sn_is_warning_not_blocking(self):
        self.make_market_asset('DUP-SN')
        candidate = Asset(
            manufacturer_ref=self.manufacturer, product_model_code=self.product_model,
            serial_number='DUP-SN', sn_status=Asset.SnStatus.PROVIDED,
        )
        warnings = validate_sn(candidate)
        self.assertEqual(warnings[0]['severity'], 'high')

    def test_domain_change_keeps_current_identifier(self):
        asset = self.make_market_asset()
        old_code = asset.asset_code
        change = change_asset_domain(
            asset=asset, to_domain=self.rnd_domain, actor=self.user, reason='转为研发验证设备',
        )
        asset.refresh_from_db()
        self.assertEqual(asset.asset_code, old_code)
        self.assertEqual(change.from_domain, self.market_domain)
        self.assertTrue(AssetIdentifier.objects.filter(asset=asset, value=old_code, is_active=True).exists())
        self.assertTrue(Asset.objects.filter(asset_search_query(old_code)).distinct().exists())
        self.assertTrue(AssetDomainChange.objects.filter(asset=asset).exists())
        self.assertTrue(OperationAuditLog.objects.filter(action='asset_domain_changed', target_id=asset.id).exists())

    def test_rnd_asset_can_change_to_market_with_selected_codes(self):
        asset = apply_asset_identity(Asset(
            domain=self.rnd_domain, code_source=Asset.CodeSource.PENDING_ADMIN,
            name='研发转市场设备', item_type=self.item_type, sn_status=Asset.SnStatus.NO_SN,
            warehouse=self.rnd_warehouse,
        ), actor=self.user)
        system_code = asset.system_asset_no
        change_asset_domain(
            asset=asset, to_domain=self.market_domain, actor=self.user, reason='转为市场销售资产',
            asset_type_code=self.asset_type, product_model_code=self.product_model,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.system_asset_no, system_code)
        self.assertEqual(asset.asset_code, 'SC-YT-HK15W-0001')
        self.assertEqual(asset.domain, self.market_domain)

    def test_generated_template_headers_and_samples_align(self):
        """模板表头必须与 HEADERS 一致，示例行列数必须对齐，否则用户一旦
        填写就会因列错位而整批报错。"""
        sheet = load_workbook(build_asset_v2_template())[SHEET_NAME]
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header, HEADERS)
        self.assertGreaterEqual(sheet.max_row, 2, '模板应至少保留一行示例')
        for row in range(2, sheet.max_row + 1):
            values = [cell.value for cell in sheet[row]]
            self.assertEqual(len(values), len(HEADERS), f'第 {row} 行列数与表头不一致')
            self.assertIn(values[1], {'新增', '更新'}, f'第 {row} 行“导入动作”取值非法')

    def _workbook_with_row(self, **overrides):
        """按表头构建单行导入工作簿，避免测试依赖列顺序。"""
        workbook = load_workbook(build_asset_v2_template())
        sheet = workbook[SHEET_NAME]
        sheet.delete_rows(2, sheet.max_row - 1)
        values = {
            '外部行号': 'E-1', '导入动作': '新增', '系统永久编号': '', '资产归属': 'MARKET',
            '编码方式': 'market_generated', '主资产编码': '', '设备类型代码': 'YT',
            '型号代码': 'HK15W', '资产名称': '海康云台', '物品类型代码': 'ROBOT',
            '生产厂家代码': 'HIK', '生产厂家名称': '海康威视', '型号名称': '海康 15W',
            'SN状态': 'provided', '生产厂家SN': 'IMPORT-SN-001', '生产厂家条码': '',
            '供应商代码': '', '供应商名称': '', '仓库代码': 'MARKET', '库位代码': 'A01',
            '项目代码': '', '工单编号': '', '资产状态': '', '到货日期': '', '采购订单号': '',
            '采购金额': '', '质保到期日': '', '别名标签': '', '备注': '',
        }
        values.update(overrides)
        headers = [cell.value for cell in sheet[1]]
        sheet.append([values.get(str(header or '').rstrip('*'), '') for header in headers])
        content = BytesIO()
        workbook.save(content)
        return content.getvalue()

    def test_v2_template_preview_does_not_allocate_sequence(self):
        records = parse_asset_v2_workbook(self._workbook_with_row())
        before = AssetCodeSequence.objects.filter(scheme='market').count()
        issues = validate_asset_v2_records(records)
        self.assertFalse([issue for issue in issues if issue['severity'] == 'error'])
        self.assertEqual(AssetCodeSequence.objects.filter(scheme='market').count(), before)

    def test_v2_import_applies_after_clean_preview(self):
        records = parse_asset_v2_workbook(self._workbook_with_row(**{'生产厂家SN': 'IMPORT-SN-002'}))
        issues = validate_asset_v2_records(records)
        batch = ImportBatch.objects.create(
            source_filename='v2.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        self.assertFalse(issues)
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        asset = Asset.objects.get(serial_number='IMPORT-SN-002')
        self.assertEqual(asset.asset_code, 'SC-YT-HK15W-0001')
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)

    def test_import_applies_asset_status_and_syncs_dimensions(self):
        """导入“维修中”必须落到 status，并同步四维，而不是默认在库。"""
        records = parse_asset_v2_workbook(self._workbook_with_row(**{
            '生产厂家SN': 'IMPORT-SN-003', '资产状态': '维修中',
        }))
        self.assertFalse(validate_asset_v2_records(records))
        batch = ImportBatch.objects.create(
            source_filename='v2-status.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        asset = Asset.objects.get(serial_number='IMPORT-SN-003')
        self.assertEqual(asset.status, Asset.Status.REPAIRING)
        self.assertEqual(asset.location_state, Asset.LocationState.EXTERNAL_REPAIR)
        self.assertEqual(asset.availability_state, Asset.AvailabilityState.UNAVAILABLE)
        self.assertEqual(asset.quality_state, Asset.QualityState.REPAIRING)
        self.assertEqual(asset.disposition_state, Asset.DispositionState.INTERNAL)

    def test_import_accepts_english_status_value(self):
        records = parse_asset_v2_workbook(self._workbook_with_row(**{
            '生产厂家SN': 'IMPORT-SN-004', '资产状态': 'borrowed',
        }))
        self.assertFalse(validate_asset_v2_records(records))
        batch = ImportBatch.objects.create(
            source_filename='v2-status-en.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        asset = Asset.objects.get(serial_number='IMPORT-SN-004')
        self.assertEqual(asset.status, Asset.Status.BORROWED)
        self.assertEqual(asset.location_state, Asset.LocationState.OUT_ON_LOAN)

    def test_import_blank_status_defaults_to_in_stock(self):
        records = parse_asset_v2_workbook(self._workbook_with_row(**{
            '生产厂家SN': 'IMPORT-SN-005', '资产状态': '',
        }))
        self.assertFalse(validate_asset_v2_records(records))
        batch = ImportBatch.objects.create(
            source_filename='v2-status-blank.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        asset = Asset.objects.get(serial_number='IMPORT-SN-005')
        self.assertEqual(asset.status, Asset.Status.IN_STOCK)

    def test_import_rejects_disabled_status(self):
        with self.assertRaises(AssetV2ImportError):
            parse_asset_v2_workbook(self._workbook_with_row(**{'资产状态': '已停用'}))

    def test_import_rejects_unknown_status(self):
        with self.assertRaises(AssetV2ImportError):
            parse_asset_v2_workbook(self._workbook_with_row(**{'资产状态': '随便写写'}))

    def test_import_does_not_change_status_when_column_absent(self):
        """旧版模板没有“资产状态”列时，更新不应把已有状态改回在库。"""
        asset = self.make_market_asset(serial_number='IMPORT-SN-006')
        asset.status = Asset.Status.SCRAPPED
        asset.save()

        workbook = load_workbook(build_asset_v2_template())
        sheet = workbook[SHEET_NAME]
        sheet.delete_rows(2, sheet.max_row - 1)
        headers = [str(cell.value or '').rstrip('*') for cell in sheet[1]]

        # 旧版模板里该列名为“操作”，这里同时覆盖旧列名兼容。
        values = {
            '操作': '更新', '系统永久编号': asset.system_asset_no, '资产归属': 'MARKET',
            '编码方式': 'market_generated', '主资产编码': asset.asset_code,
            '设备类型代码': 'YT', '型号代码': 'HK15W', '资产名称': '海康云台',
            '物品类型代码': 'ROBOT', '生产厂家代码': 'HIK', '生产厂家名称': '海康威视',
            '型号名称': '海康 15W', 'SN状态': 'provided', '生产厂家SN': 'IMPORT-SN-006',
            '仓库代码': 'MARKET', '库位代码': 'A01',
        }
        # 表头此刻已是“导入动作”，但值字典按旧名“操作”提供，逐个映射。
        row_values = []
        for header in headers:
            key = '操作' if header == '导入动作' else header
            row_values.append(values.get(key, ''))
        sheet.append(row_values)
        # 还原成真正的旧版模板：删掉新增的两列，并把“导入动作”改回“操作”。
        operation_column = headers.index('导入动作') + 1
        sheet.cell(1, operation_column).value = '操作*'
        for position in reversed([
            index + 1 for index, header in enumerate(headers)
            if header in ('外部行号', '资产状态')
        ]):
            sheet.delete_cols(position)

        content = BytesIO()
        workbook.save(content)
        records = parse_asset_v2_workbook(content.getvalue())
        self.assertFalse(validate_asset_v2_records(records))
        batch = ImportBatch.objects.create(
            source_filename='v2-legacy-template.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.SCRAPPED)


class SeededCodeReferenceTest(TestCase):
    """0024 迁移从 11.2 真实台账预置的基础资料必须可用。"""

    def test_device_types_are_seeded(self):
        codes = set(AssetTypeCode.objects.values_list('code', flat=True))
        for expected in ('JQR', 'LD', 'XJ', 'YT', 'SZ', 'ZJ'):
            self.assertIn(expected, codes)

    def test_manufacturers_are_seeded_and_aliased(self):
        self.assertTrue(Manufacturer.objects.filter(code='UNIT', name='宇树科技').exists())
        self.assertTrue(Manufacturer.objects.filter(code='HIK', name='海康威视').exists())
        # 别名必须归并，不能同时存在“宇树”和“宇树科技”两条
        self.assertFalse(Manufacturer.objects.filter(code='宇树').exists())

    def test_every_seeded_model_belongs_to_existing_type(self):
        for model in ProductModelCode.objects.select_related('asset_type_code'):
            self.assertIsNotNone(model.asset_type_code, f'{model.code} 缺少设备类型')
            self.assertTrue(model.code, f'{model.id} 型号代码为空')

    def test_model_code_unique_within_type(self):
        seen = set()
        for type_id, code in ProductModelCode.objects.values_list('asset_type_code_id', 'code'):
            key = (type_id, code.upper())
            self.assertNotIn(key, seen, f'型号代码重复：{code}')
            seen.add(key)

    def test_seeded_models_can_drive_real_market_code_generation(self):
        """用预置资料真实生成一个市场编码，确认种子数据自洽可用。"""
        user = get_user_model().objects.create_user('seed_user', password='test-pass')
        category, _ = Category.objects.get_or_create(code='ROBOT', defaults={'name': '机器人'})
        item_type, _ = ItemType.objects.get_or_create(
            code='ROBOT',
            defaults={'name': '机器人', 'is_serialized': True, 'category': category},
        )
        warehouse, _ = Warehouse.objects.get_or_create(code='MARKET', defaults={'name': '市场库存'})
        asset_type = AssetTypeCode.objects.get(code='JQR')
        model = ProductModelCode.objects.filter(
            asset_type_code=asset_type, code='G1EDU',
        ).first()
        self.assertIsNotNone(model, '预置型号 G1EDU 应存在')

        asset = apply_asset_identity(Asset(
            domain=InventoryDomain.objects.get(code='MARKET'),
            code_source=Asset.CodeSource.MARKET_GENERATED,
            asset_type_code=asset_type,
            product_model_code=model,
            name='宇树 G1 EDU',
            item_type=item_type,
            sn_status=Asset.SnStatus.PROVIDED,
            serial_number='SEED-SN-001',
            warehouse=warehouse,
        ), actor=user, reason='种子数据联调')
        self.assertEqual(asset.asset_code, 'SC-G1-YS-0001')

    def test_112_ledger_manufacturers_are_seeded(self):
        """11.2 台账新出现的 5 个厂家必须预置，否则导入会被拦下。"""
        expected = {
            'XDF': '祥达丰', 'TR': '图瑞', 'HUAWEI': '华为',
            'MLD': '长沙迈乐迪', 'BWT': '八维通',
        }
        for code, name in expected.items():
            self.assertTrue(
                Manufacturer.objects.filter(code=code, name=name).exists(),
                f'缺少厂家 {code}/{name}',
            )

    def test_112_ledger_models_are_seeded(self):
        """台账里型号为空或写描述性文字的产品，必须补出型号才能生成 SC 编码。"""
        expected = [
            ('SZ', 'GLOVE'), ('SB', 'PAD'), ('DZ', 'GAS4'), ('DZ', 'GAS6'),
        ]
        for type_code, model_code in expected:
            self.assertTrue(
                ProductModelCode.objects.filter(
                    asset_type_code__code=type_code, code=model_code,
                ).exists(),
                f'缺少型号 {type_code}/{model_code}',
            )


class ParentAssetImportTest(TestCase):
    """父资产列用于一次性导入“同一套设备的多个部件”。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user('assembly_user', password='test-pass')
        category, _ = Category.objects.get_or_create(code='ROBOT', defaults={'name': '机器人'})
        self.item_type, _ = ItemType.objects.get_or_create(
            code='ASSET_RND',
            defaults={'name': '研发资产', 'is_serialized': True, 'category': category},
        )
        Warehouse.objects.get_or_create(code='RND', defaults={'name': '研发库存'})
        self.rnd_domain = InventoryDomain.objects.get(code='RND')

    def _record(self, name, parent_no='', system_no=''):
        return {
            'sheet_name': SHEET_NAME, 'row_number': 2, 'external_row_no': '',
            'source': {}, 'operation': '新增', 'system_asset_no': system_no,
            'domain_code': 'RND', 'code_source': 'pending_admin', 'asset_code': '',
            'asset_type_code': '', 'product_model_code': '', 'name': name,
            'item_type_code': 'ASSET_RND', 'manufacturer_code': '', 'manufacturer_name': '',
            'model': '', 'sn_status': 'no_sn', 'serial_number': '',
            'manufacturer_barcode': '', 'supplier_code': '', 'supplier_name': '',
            'warehouse_code': 'RND', 'location_code': '', 'project_code': '',
            'work_order_code': '', 'asset_status': 'in_stock', 'asset_status_supplied': True,
            'received_date': None, 'purchase_order_no': '', 'purchase_amount': None,
            'warranty_until': None, 'search_aliases': '', 'remarks': '',
            'parent_asset_no': parent_no,
        }

    def _apply(self, records):
        batch = ImportBatch.objects.create(
            source_filename='assembly.xlsx', mode=ImportBatch.Mode.INCREMENTAL,
            summary={'template_version': 2}, created_by=self.user,
        )
        apply_asset_v2_import(actor=self.user, batch=batch, records=records)
        return batch

    def test_parent_column_creates_assembly_link(self):
        parent = self._record('整机')
        self._apply([parent])
        parent_asset = Asset.objects.get(name='整机')

        child = self._record('云台部件', parent_no=parent_asset.system_asset_no)
        self._apply([child])

        link = AssetAssemblyLink.objects.get(child_asset__name='云台部件')
        self.assertEqual(link.parent_asset_id, parent_asset.id)
        self.assertIsNone(link.removed_at)

    def test_parent_missing_is_rejected(self):
        records = [self._record('孤儿部件', parent_no='AST-99999999')]
        issues = validate_asset_v2_records(records)
        self.assertTrue(
            any(issue['code'] == 'parent_asset_missing' for issue in issues),
            '父资产不存在时必须报错',
        )

    def test_parent_pointing_to_self_is_rejected(self):
        self._apply([self._record('自引用')])
        asset = Asset.objects.get(name='自引用')
        record = self._record('自引用', parent_no=asset.system_asset_no,
                              system_no=asset.system_asset_no)
        record['operation'] = '更新'
        issues = validate_asset_v2_records([record])
        self.assertTrue(any(issue['code'] == 'parent_asset_self' for issue in issues))

    def test_parent_format_is_validated(self):
        issues = validate_asset_v2_records([self._record('坏编号', parent_no='XYZ-1')])
        self.assertTrue(any(issue['code'] == 'parent_asset_no_invalid' for issue in issues))


class WarehouseAssignedCodeTest(TestCase):
    """仓管既有编码必须原样保留，且与行政 DZSB 编码互斥。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user('wh_admin', password='test-pass')
        self.market_warehouse = Warehouse.objects.create(name='市场库存', code='MARKET')
        self.rnd_warehouse = Warehouse.objects.create(name='研发库存', code='RND')
        self.market_domain = InventoryDomain.objects.get(code='MARKET')
        self.rnd_domain = InventoryDomain.objects.get(code='RND')
        category = Category.objects.create(name='默认分类', code='DEFAULT')
        self.item_type, _ = ItemType.objects.get_or_create(
            code='ASSET_MARKET',
            defaults={'name': '市场资产', 'category': category, 'is_serialized': True},
        )
        self.manufacturer, _ = Manufacturer.objects.get_or_create(
            code='UNIT', defaults={'name': '宇树科技'},
        )

    def _asset(self, domain, code, code_source=Asset.CodeSource.WAREHOUSE_ASSIGNED):
        return Asset(
            domain=domain,
            code_source=code_source,
            asset_code=code,
            name='仓管编码资产',
            item_type=self.item_type,
            manufacturer_ref=self.manufacturer,
            sn_status=Asset.SnStatus.NO_SN,
            warehouse=self.market_warehouse if domain == self.market_domain else self.rnd_warehouse,
        )

    def test_market_keeps_warehouse_assigned_sc_code(self):
        asset = apply_asset_identity(
            self._asset(self.market_domain, 'SC-G1-YS-0001'), actor=self.user,
        )
        self.assertEqual(asset.asset_code, 'SC-G1-YS-0001')
        # 系统永久编号仍要分配，但不能为 SC 生成流水号。
        self.assertFalse(
            AssetCodeSequence.objects.filter(scheme=AssetCodeSequence.Scheme.MARKET).exists(),
            '仓管既有编码不应消耗 SC 编码流水',
        )
        self.assertTrue(
            asset.identifiers.filter(identifier_type='market', is_primary=True).exists()
        )

    def test_market_keeps_warehouse_assigned_dzsb_code_as_admin_identifier(self):
        asset = apply_asset_identity(
            self._asset(self.market_domain, 'DZSB-D7-22-0004-600001'), actor=self.user,
        )
        self.assertEqual(asset.asset_code, 'DZSB-D7-22-0004-600001')
        self.assertTrue(
            asset.identifiers.filter(identifier_type='admin', is_primary=True).exists(),
            'DZSB 编码应归档为行政编码标识',
        )

    def test_warehouse_assigned_requires_code(self):
        with self.assertRaises(AssetCodeError):
            apply_asset_identity(
                self._asset(self.market_domain, ''), actor=self.user,
            )

    def test_warehouse_assigned_rejects_illegal_characters(self):
        with self.assertRaises(AssetCodeError):
            apply_asset_identity(
                self._asset(self.market_domain, 'SC-G1 YS-0001'), actor=self.user,
            )

    def test_rnd_keeps_sc_code(self):
        asset = apply_asset_identity(
            self._asset(self.rnd_domain, 'SC-G1-YS-0001'), actor=self.user,
        )
        self.assertEqual(asset.asset_code, 'SC-G1-YS-0001')

    def test_rnd_keeps_dzsb_code(self):
        asset = apply_asset_identity(
            self._asset(self.rnd_domain, 'DZSB-D7-21-0002-600007'), actor=self.user,
        )
        self.assertEqual(asset.asset_code, 'DZSB-D7-21-0002-600007')

    def test_market_pending_admin_keeps_code_empty(self):
        asset = apply_asset_identity(
            self._asset(self.market_domain, '', Asset.CodeSource.PENDING_ADMIN),
            actor=self.user,
        )
        self.assertIsNone(asset.asset_code)
        self.assertFalse(
            AssetCodeSequence.objects.filter(scheme=AssetCodeSequence.Scheme.MARKET).exists(),
            '待赋码不应生成 SC 编码',
        )


class SourceLedgerRowRetentionTest(TestCase):
    """源台账每一行代表一件实物，重复 SN 只能告警，绝不能丢行。

    2026-11 的真实事故：按 SN 去重把 7 行合并掉了，其中两条是仓管把 SN
    填成位置代号“2-4”、三条是把内网 IP 填成 SN，导致实物少于台账。
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user('row_admin', password='test-pass')
        self.rnd_warehouse = Warehouse.objects.create(name='研发库存', code='RND')
        self.market_warehouse = Warehouse.objects.create(name='市场库存', code='MARKET')
        self.category, _ = Category.objects.get_or_create(name='研发', defaults={'code': 'RNDCAT'})
        self.rnd_item_type, _ = ItemType.objects.get_or_create(
            code='ASSET_RND',
            defaults={'name': '研发资产', 'category': self.category, 'is_serialized': True},
        )

    def _record(self, name, sn='', code='', code_source='pending_admin',
                domain='RND', row_number=2, warehouse_code='RND', item_type_code='ASSET_RND'):
        return {
            'sheet_name': '资产导入V2', 'row_number': row_number, 'external_row_no': '',
            'operation': '新增', 'system_asset_no': '',
            'domain_code': domain, 'code_source': code_source, 'asset_code': code,
            'asset_type_code': '', 'product_model_code': '',
            'name': name, 'item_type_code': item_type_code,
            'manufacturer_code': '', 'manufacturer_name': '',
            'model': '', 'sn_status': 'provided' if sn else 'no_sn', 'serial_number': sn,
            'manufacturer_barcode': '', 'supplier_code': '', 'supplier_name': '',
            'warehouse_code': warehouse_code, 'location_code': '', 'project_code': '',
            'work_order_code': '', 'asset_status': '', 'asset_status_supplied': False,
            'received_date': None, 'purchase_order_no': '', 'purchase_amount': None,
            'warranty_until': None, 'search_aliases': '', 'remarks': '',
            'parent_asset_no': '', 'source': {},
        }

    def _errors(self, records):
        return [x for x in validate_asset_v2_records(records) if x.get('severity') == 'error']

    def test_same_sn_rows_are_warnings_not_errors(self):
        """两个不同实物填了相同 SN（位置代号）时都必须能导入。"""
        records = [
            self._record('中车盒子A', sn='2-4', row_number=2),
            self._record('中车盒子B', sn='2-4', row_number=3),
        ]
        self.assertEqual(self._errors(records), [], '重复 SN 不能阻止导入，否则会丢实物')

    def test_same_ip_as_sn_does_not_block_import(self):
        """多条记录把内网 IP 填成 SN 时，每行仍要各自入库。"""
        records = [
            self._record(f'LZJ{i}', sn='192.168.10.82', row_number=10 + i)
            for i in range(3)
        ]
        self.assertEqual(self._errors(records), [])

    def test_cross_domain_duplicate_sn_keeps_both_rows(self):
        """市场与研发台账各登一次同一 SN，两条都要保留。"""
        self.market_item_type, _ = ItemType.objects.get_or_create(
            code='ASSET_MARKET',
            defaults={'name': '市场资产', 'category': self.category, 'is_serialized': True},
        )
        records = [
            self._record('市场设备', sn='H2-1219', code='SC-TEST-H2-9001',
                         code_source='warehouse_assigned', domain='MARKET',
                         row_number=2, warehouse_code='MARKET',
                         item_type_code='ASSET_MARKET'),
            self._record('研发设备', sn='H2-1219', row_number=3),
        ]
        self.assertEqual(self._errors(records), [])

    def test_rnd_domain_accepts_existing_sc_code(self):
        records = [self._record('研发件', code='SC-TEST-G1-9002',
                                code_source='warehouse_assigned')]
        self.assertEqual(self._errors(records), [])

    def test_pending_admin_must_leave_code_empty(self):
        records = [self._record('待赋码件', code='SC-TEST-X-9003',
                                code_source='pending_admin', domain='MARKET',
                                warehouse_code='MARKET', item_type_code='ASSET_MARKET')]
        codes = {x['code'] for x in self._errors(records)}
        self.assertIn('pending_code_present', codes)
