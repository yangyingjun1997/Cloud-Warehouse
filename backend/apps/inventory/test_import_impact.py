"""导入影响预估与二次确认回归。

用户要求：导出 xlsx 大幅改动后直接导入时，必须先看到「将新增 N / 更新 M /
停用 K」，且必须勾选确认才执行；全量基准还要再勾一次二次确认。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.inventory.import_impact import describe_import_impact, estimate_import_impact
from apps.inventory.models import Asset, Category, ImportBatch, ItemType, Warehouse

User = get_user_model()


class ImpactEstimateTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='影响仓', code='IMPACT-WH')
        category = Category.objects.create(name='影响分类', code='IMPACT-CAT')
        self.item_type = ItemType.objects.create(name='影响类型', code='IMPACT-IT', category=category)
        self.existing = Asset.objects.create(
            name='已有资产', system_asset_no='AST-IMPACT-A1',
            asset_code='SC-IMPACT-A1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.orphan = Asset.objects.create(
            name='未出现在文件中', system_asset_no='AST-IMPACT-A2',
            asset_code='SC-IMPACT-A2', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def _records(self):
        return [
            {'operation': '新增', 'system_asset_no': '', 'asset_code': 'SC-NEW',
             'warehouse_code': 'IMPACT-WH'},
            {'operation': '更新', 'system_asset_no': 'AST-IMPACT-A1', 'asset_code': '',
             'warehouse_code': 'IMPACT-WH'},
        ]

    def test_incremental_does_not_disable(self):
        impact = estimate_import_impact(
            records=self._records(), mode=ImportBatch.Mode.INCREMENTAL, template_version=2,
        )
        self.assertEqual(impact['asset_create'], 1)
        self.assertEqual(impact['asset_update'], 1)
        self.assertEqual(impact['asset_disable'], 0, '增量导入不应停用任何记录')
        self.assertFalse(impact['has_destructive'])

    def test_baseline_reports_disable_count(self):
        impact = estimate_import_impact(
            records=self._records(), mode=ImportBatch.Mode.BASELINE, template_version=2,
        )
        # 文件里只出现 A1，A2 属于同一仓库且未出现 → 会被停用
        self.assertGreaterEqual(impact['asset_disable'], 1)
        self.assertTrue(impact['has_destructive'])

    def test_baseline_only_scopes_represented_warehouses(self):
        """未在 Excel 中出现的仓库，其资产不应被算作将停用。"""
        other = Warehouse.objects.create(name='别的仓', code='OTHER-WH')
        Asset.objects.create(
            name='别仓资产', system_asset_no='AST-OTHER-1', asset_code='SC-OTHER-1',
            item_type=self.item_type, warehouse=other, status=Asset.Status.IN_STOCK,
        )
        impact = estimate_import_impact(
            records=self._records(), mode=ImportBatch.Mode.BASELINE, template_version=2,
        )
        # 只算 IMPACT-WH 里的缺失项，OTHER-WH 那条不计入
        self.assertEqual(impact['asset_disable'], 1)

    def test_update_missing_counted_separately(self):
        records = [
            {'operation': '更新', 'system_asset_no': 'AST-NOT-EXIST', 'asset_code': '',
             'warehouse_code': 'IMPACT-WH'},
        ]
        impact = estimate_import_impact(
            records=records, mode=ImportBatch.Mode.INCREMENTAL, template_version=2,
        )
        self.assertEqual(impact['asset_update_missing'], 1)
        self.assertEqual(impact['asset_update'], 0)

    def test_description_is_readable(self):
        impact = estimate_import_impact(
            records=self._records(), mode=ImportBatch.Mode.BASELINE, template_version=2,
        )
        text = describe_import_impact(impact)
        self.assertIn('新增资产', text)
        self.assertIn('更新资产', text)
        self.assertIn('停用资产', text)


class ImportConfirmationGateTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='imp_admin', password='Pw#2026aa')
        admin.is_staff = True
        admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.client.force_login(admin)

    def test_page_renders_both_confirmations(self):
        html = self.client.get('/warehouse/import/').content.decode('utf-8')
        self.assertIn('impact_confirmation', html, '缺少「我已确认上述影响」勾选框')
        self.assertIn('baseline_double_confirm', html, '缺少全量基准二次确认勾选框')
        self.assertIn('impact-panel', html, '缺少影响预估面板样式')

    def test_impact_confirm_hidden_until_apply_checked(self):
        """未勾选「立即写入」时不该要求确认影响。"""
        html = self.client.get('/warehouse/import/').content.decode('utf-8')
        match = re.search(r'<div[^>]*id="impact-confirm-field"[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertIn('hidden', match.group(0))
