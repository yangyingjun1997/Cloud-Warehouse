"""P1 修复回归：导出公式注入、盘点归一化、耗材歧义保护。"""
from decimal import Decimal

from django.test import TestCase

from apps.common.utils import sanitize_csv_cell, sanitize_csv_row
from apps.inventory.check_services import InventoryCheckError, _resolve_asset, _resolve_stock
from apps.inventory.models import Asset, Category, ItemType, StockItem, Warehouse
from apps.inventory.search import normalize_scan_value


class CsvFormulaInjectionTests(TestCase):
    """导出单元格不得被 Excel/WPS 当作公式执行。"""

    def test_dangerous_prefixes_are_escaped(self):
        for payload in (
            '=cmd|\'/c calc\'!A1',
            '+1+1',
            '-2+3',
            '@SUM(A1:A9)',
            '\t=1+1',
            '\r=1+1',
        ):
            with self.subTest(payload=payload):
                self.assertTrue(
                    sanitize_csv_cell(payload).startswith("'"),
                    f'{payload!r} 未被转义',
                )

    def test_safe_text_is_unchanged(self):
        for payload in ('普通资产', 'AST-0001', '', 'A=B'):
            with self.subTest(payload=payload):
                self.assertEqual(sanitize_csv_cell(payload), payload)

    def test_non_string_values_pass_through(self):
        self.assertEqual(sanitize_csv_cell(123), 123)
        self.assertEqual(sanitize_csv_cell(Decimal('1.50')), Decimal('1.50'))
        self.assertEqual(sanitize_csv_cell(None), '')

    def test_row_helper_covers_every_cell(self):
        row = sanitize_csv_row(['正常', '=坏', 42, None, '-负'])
        self.assertEqual(row, ['正常', "'=坏", 42, '', "'-负"])


class ScanNormalizationTests(TestCase):
    """全角标点与尾随换行不得导致「看起来一样却查不到」。"""

    def test_normalize_folds_fullwidth_and_strips(self):
        self.assertEqual(normalize_scan_value('WMS2：A：AST-00000238'), 'WMS2:A:AST-00000238')
        self.assertEqual(normalize_scan_value('AST-0001\r\n'), 'AST-0001')
        self.assertEqual(normalize_scan_value('  AST-0001  '), 'AST-0001')
        self.assertEqual(normalize_scan_value(''), '')
        self.assertEqual(normalize_scan_value(None), '')

    def test_resolve_asset_accepts_fullwidth_colon(self):
        warehouse = Warehouse.objects.create(name='测试仓', code='T-WH')
        category = Category.objects.create(name='测试分类')
        item_type = ItemType.objects.create(name='测试类型', category=category)
        asset = Asset.objects.create(
            name='归一化测试资产',
            system_asset_no='AST-TEST-NORM-1',
            asset_code='ZC-NORM-1',
            item_type=item_type,
            warehouse=warehouse,
            status=Asset.Status.IN_STOCK,
        )
        # 半角、全角写法都应解析到同一件资产
        for value in ('AST-TEST-NORM-1', 'ＡＳＴ－ＴＥＳＴ－ＮＯＲＭ－１', 'AST-TEST-NORM-1\r\n'):
            with self.subTest(value=value):
                resolved = _resolve_asset(value)
                self.assertIsNotNone(resolved, f'{value!r} 未解析到资产')
                self.assertEqual(resolved.pk, asset.pk)

    def test_resolve_asset_returns_none_for_unknown(self):
        self.assertIsNone(_resolve_asset('不存在的编码-XYZ'))


class StockResolutionTests(TestCase):
    """耗材名称与编码撞车时，必须按编码优先，不得静默取错。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='耗材仓', code='T-STOCK-WH')
        self.category = Category.objects.create(name='耗材分类')
        self.item_type = ItemType.objects.create(name='耗材类型', category=self.category)

    def _stock(self, name, code):
        return StockItem.objects.create(
            name=name, code=code, item_type=self.item_type, warehouse=self.warehouse,
        )

    def test_code_takes_priority_over_name(self):
        # A 的编码恰好等于 B 的名称：编码命中必须优先，应返回 A 而不是 B
        a = self._stock('耗材甲', 'CODE-A')
        b = self._stock('CODE-A', 'CODE-B')
        resolved = _resolve_stock('CODE-A')
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.pk, a.pk, '编码命中应优先于名称命中，不得返回名称相同的 B')
        self.assertNotEqual(resolved.pk, b.pk)

    def test_unique_name_still_resolves(self):
        item = self._stock('独有耗材名', 'CODE-UNIQ')
        resolved = _resolve_stock('独有耗材名')
        self.assertEqual(resolved.pk, item.pk)

    def test_ambiguous_name_raises_instead_of_guessing(self):
        self._stock('重名耗材', 'CODE-D1')
        self._stock('重名耗材', 'CODE-D2')
        with self.assertRaises(InventoryCheckError):
            _resolve_stock('重名耗材')

    def test_unknown_value_returns_none(self):
        self.assertIsNone(_resolve_stock('完全不存在的耗材-ZZZ'))
