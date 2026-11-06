"""V2 导入解析层的新规则测试：IP 型 SN、父资产列、向后兼容。"""

import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import Workbook

from .management.commands.import_legacy_inventory import Command as LegacyImportCommand
from .models import (
    Asset, AssetCodeSequence, AssetTypeCode, ImportBatch, InventoryDomain,
    ProductModelCode, StockItem,
)
from .code_services import apply_asset_identity
from .v2_import_services import (
    HEADERS,
    OPTIONAL_HEADERS,
    SHEET_NAME,
    build_asset_v2_template,
    parse_asset_v2_workbook,
    validate_asset_v2_records,
)


def _workbook(rows, headers=None):
    """构造一个最小的 V2 导入工作簿。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    used = headers or HEADERS
    sheet.append(used)
    for values in rows:
        base = {header: '' for header in used}
        base.update(values)
        sheet.append([base[header] for header in used])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _base_row(**overrides):
    row = {
        '导入动作*': '新增',
        '资产归属*': 'RND',
        '编码方式*': 'pending_admin',
        '资产名称*': '测试设备',
        '物品类型代码*': 'ASSET_RND',
        'SN状态*': 'provided',
        '仓库代码*': 'RND',
    }
    row.update(overrides)
    return row


class SourceLedgerClassificationTest(TestCase):
    """11.2 原始台账必须由正式资产编码驱动分类。"""

    def setUp(self):
        self.user = get_user_model().objects.create_user('ledger_importer', password='test-pass')

    def _source_workbook(self):
        workbook = Workbook()
        market = workbook.active
        market.title = '市场库存'
        market.append([
            '产品名称', '采购个数', '到货个数', '库存剩余', '序列号', '资产编号',
            '厂家', '型号', '到货日期', '状态', '客户名称', '销售出库日期',
            '物流单号', '备注', 'OA采购订单审批号',
        ])
        market.append(['宇树G1', '2台', 2, 1, '', '', '', '', '', '', '', '', '', '', ''])
        market.append(['', 1, '', '', 'SN-G1-1', 'SC-G1-YS-0031', '宇树', 'G1', '', '在库空闲', '', '', '', '', ''])
        market.append(['', 2, '', '', 'SN-G1-2', '', '宇树', 'G1', '', '出库借用', '', '', '', '', ''])
        market.append(['麦克风', '8台', 8, 5, '', '', '', '', '', '', '', '', '', '', ''])
        market.append(['', 1, '', '', 'MIC-1', '', '猛犸', 'LARK', '', '在库空闲', '', '', '', '', ''])

        rnd = workbook.create_sheet('研发库存')
        rnd.append([
            '产品名称', '采购数量', '库存数量', '序列号', '八维通SN', '厂家', '型号',
            '到货日期', '状态', '出货日期', '出货类型', '领用人', '备注',
            '归还日期', 'ERP同步情况', '用途', '',
        ])
        rnd.append(['测试设备', '2台', 0, '', '', '', '', '', '', '', '', '', '', '', '', '', ''])
        rnd.append(['', 1, '', 'SN-R-1', 'DZSB-D7-31-0002-600001\nDZSB-D7-31-0002-600002', '厂家', '型号', '', '已出库', '', '', '', '', '', '', '', ''])

        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return buffer

    def test_codes_are_preserved_and_uncoded_group_becomes_stock(self):
        assets, stocks, issues, summary = LegacyImportCommand()._parse_workbook(self._source_workbook())

        self.assertEqual([row['asset_code'] for row in assets], [
            'SC-G1-YS-0031',
            'DZSB-D7-31-0002-600001',
            'DZSB-D7-31-0002-600002',
        ])
        self.assertEqual([(row['name'], row['quantity']) for row in stocks], [('麦克风', 5)])
        self.assertEqual(summary['recognized_sc_count'], 1)
        self.assertEqual(summary['recognized_dzsb_count'], 2)
        self.assertTrue(any(issue['code'] == 'uncoded_rows_in_asset_group' for issue in issues))

    def test_apply_keeps_source_codes_and_seeds_next_sc_number(self):
        command = LegacyImportCommand()
        assets, stocks, _, _ = command._parse_workbook(self._source_workbook())
        batch = ImportBatch.objects.create(source_filename='11.2-test.xlsx', created_by=self.user)

        imported, stock_count, _, apply_issues = command._apply_records(batch, assets, stocks)

        self.assertEqual((imported, stock_count, apply_issues), (3, 1, []))
        self.assertEqual(
            set(Asset.objects.values_list('asset_code', flat=True)),
            {'SC-G1-YS-0031', 'DZSB-D7-31-0002-600001', 'DZSB-D7-31-0002-600002'},
        )
        self.assertEqual(StockItem.objects.get().quantity, 5)
        sequence = AssetCodeSequence.objects.get(prefix='SC-G1-YS')
        self.assertEqual((sequence.last_number, sequence.width), (31, 4))

    def test_sc_generation_uses_full_source_prefix_in_rnd_domain(self):
        asset_type = AssetTypeCode.objects.get(code='JQR')
        model = ProductModelCode.objects.get(asset_type_code=asset_type, code='G1EDU')
        model.sc_prefix = 'SC-G1-YS'
        model.sc_serial_width = 4
        model.save(update_fields=['sc_prefix', 'sc_serial_width', 'updated_at'])
        AssetCodeSequence.objects.create(
            scheme=AssetCodeSequence.Scheme.MARKET,
            prefix='SC-G1-YS', width=4, last_number=31,
        )
        asset = apply_asset_identity(Asset(
            domain=InventoryDomain.objects.get(code='RND'),
            code_source=Asset.CodeSource.MARKET_GENERATED,
            asset_type_code=asset_type,
            product_model_code=model,
            name='研发库无 DZSB 资产',
            item_type=command_item_type(),
            sn_status=Asset.SnStatus.NO_SN,
        ), actor=self.user, reason='测试源表格式续号')

        self.assertEqual(asset.asset_code, 'SC-G1-YS-0032')


def command_item_type():
    from .models import Category, ItemType

    category, _ = Category.objects.get_or_create(code='SOURCE-CODE', defaults={'name': '源表编码测试'})
    return ItemType.objects.get_or_create(
        code='SOURCE-CODE-ASSET',
        defaults={'name': '源表编码测试资产', 'category': category, 'is_serialized': True},
    )[0]


class IpSerialParsingTest(TestCase):
    """IP 地址误填进 SN 列时，应转成无 SN 并把 IP 收进备注。"""

    def test_ip_serial_becomes_no_sn_and_moves_to_remarks(self):
        content = _workbook([_base_row(**{
            '外部行号': 'RND-241',
            '生产厂家SN': '192.168.10.86',
            '资产名称*': '浪潮LZJ008A2',
        })])

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['serial_number'], '')
        self.assertEqual(record['sn_status'], Asset.SnStatus.NO_SN)
        self.assertIn('192.168.10.86', record['remarks'])
        self.assertIn('RND-241', record['remarks'])

    def test_ip_serial_keeps_existing_remarks(self):
        content = _workbook([_base_row(**{
            '生产厂家SN': '10.0.0.1',
            '备注': '滨江展厅',
        })])

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['serial_number'], '')
        self.assertTrue(record['remarks'].startswith('滨江展厅'))
        self.assertIn('10.0.0.1', record['remarks'])

    def test_real_serial_is_untouched(self):
        content = _workbook([_base_row(**{
            '生产厂家SN': 'E21D6000Q3OAM3G7',
            'SN状态*': 'provided',
        })])

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['serial_number'], 'E21D6000Q3OAM3G7')
        self.assertEqual(record['sn_status'], Asset.SnStatus.PROVIDED)
        self.assertEqual(record['remarks'], '')

    def test_non_ip_dotted_value_is_not_rewritten(self):
        """型号里带点号不算 IP，不能被改写。"""
        content = _workbook([_base_row(**{
            '生产厂家SN': '1.2.3',
            'SN状态*': 'provided',
        })])

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['serial_number'], '1.2.3')


class ParentAssetColumnTest(TestCase):
    """父资产列用于表达“同一套设备的多个部件”。"""

    def test_template_exposes_parent_column(self):
        workbook = parse_asset_v2_workbook(build_asset_v2_template().getvalue())

        self.assertTrue(workbook)
        headers = build_asset_v2_template()
        from openpyxl import load_workbook
        sheet = load_workbook(headers, data_only=True)[SHEET_NAME]
        header_row = [cell.value for cell in sheet[1]]
        self.assertIn('父资产永久编号', header_row)

    def test_parent_column_is_optional(self):
        self.assertIn('父资产永久编号', OPTIONAL_HEADERS)

    def test_parent_asset_no_is_parsed(self):
        content = _workbook([_base_row(**{
            '父资产永久编号': 'ast-00000402',
        })])

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['parent_asset_no'], 'AST-00000402')

    def test_legacy_workbook_without_parent_column_still_parses(self):
        """已发出的旧模板没有父资产列，必须仍能导入。"""
        legacy = [header for header in HEADERS if header != '父资产永久编号']
        content = _workbook([_base_row()], headers=legacy)

        record = parse_asset_v2_workbook(content)[0]

        self.assertEqual(record['parent_asset_no'], '')
        self.assertEqual(record['name'], '测试设备')


class BatchDuplicateSerialTest(TestCase):
    """批次内部重复 SN 必须能在预检阶段暴露出来。

    `validate_sn()` 只比对已入库资产，同批文件里两行写同一个 SN 它发现不了，
    所以这条自检必须独立成立。
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Category, InventoryDomain, ItemType, Warehouse

        self.user = get_user_model().objects.create_user('dup_user', password='test-pass')
        category, _ = Category.objects.get_or_create(code='DUP', defaults={'name': '重复测试'})
        ItemType.objects.get_or_create(
            code='ASSET_RND',
            defaults={'name': '研发资产', 'is_serialized': True, 'category': category},
        )
        Warehouse.objects.get_or_create(code='RND', defaults={'name': '研发库存'})
        self.domain = InventoryDomain.objects.get(code='RND')

    def _record(self, serial, name='设备', row=2, mfg='', model=''):
        return {
            'sheet_name': SHEET_NAME, 'row_number': row, 'external_row_no': f'RND-{row}',
            'source': {}, 'operation': '新增', 'system_asset_no': '',
            'domain_code': 'RND', 'code_source': 'pending_admin', 'asset_code': '',
            'asset_type_code': '', 'product_model_code': model, 'name': name,
            'item_type_code': 'ASSET_RND', 'manufacturer_code': mfg, 'manufacturer_name': mfg,
            'model': '', 'sn_status': 'provided', 'serial_number': serial,
            'manufacturer_barcode': '', 'supplier_code': '', 'supplier_name': '',
            'warehouse_code': 'RND', 'location_code': '', 'project_code': '',
            'work_order_code': '', 'asset_status': 'in_stock', 'asset_status_supplied': True,
            'received_date': None, 'purchase_order_no': '', 'purchase_amount': None,
            'warranty_until': None, 'search_aliases': '', 'remarks': '',
            'parent_asset_no': '',
        }

    def _codes(self, records):
        return [issue['code'] for issue in validate_asset_v2_records(records)]

    def test_duplicate_serial_in_same_batch_is_reported(self):
        codes = self._codes([
            self._record('H2-1063', name='宇树H2 EDU', row=2),
            self._record('H2-1063', name='宇树H2 EDU', row=3),
        ])

        self.assertTrue(
            any(code.startswith('duplicate_sn_in_batch') for code in codes),
            f'同批次重复 SN 未报出：{codes}',
        )

    def test_duplicate_with_same_model_is_marked_high(self):
        codes = self._codes([
            self._record('研发001', name='云台', row=2, mfg='DH', model='DH1'),
            self._record('研发001', name='云台', row=3, mfg='DH', model='DH1'),
        ])

        self.assertIn('duplicate_sn_in_batch_high', codes)

    def test_duplicate_across_products_is_only_warning(self):
        codes = self._codes([
            self._record('研发001', name='云台', row=2, mfg='DH', model='DH1'),
            self._record('研发001', name='相机', row=3, mfg='SY', model='SY1'),
        ])

        self.assertIn('duplicate_sn_in_batch', codes)
        self.assertNotIn('duplicate_sn_in_batch_high', codes)

    def test_unique_serials_produce_no_duplicate_issue(self):
        codes = self._codes([
            self._record('SN-A', row=2),
            self._record('SN-B', row=3),
        ])

        self.assertFalse([code for code in codes if 'duplicate_sn_in_batch' in code])

    def test_blank_serials_are_not_treated_as_duplicates(self):
        """无 SN 的资产可以有多件，不能互相告警。"""
        codes = self._codes([
            self._record('', row=2),
            self._record('', row=3),
            self._record('', row=4),
        ])

        self.assertFalse([code for code in codes if 'duplicate_sn_in_batch' in code])

    def test_serial_comparison_ignores_case(self):
        codes = self._codes([
            self._record('h2-1063', row=2),
            self._record('H2-1063', row=3),
        ])

        self.assertTrue(any('duplicate_sn_in_batch' in code for code in codes))
