"""表单布局一致性回归：宽度、列数、字段对齐。

背景（2026-11-18 用户反馈）：
1) 「当前状态」卡比「资产身份标签」「归属与编码」大 —— 根因是宽度上限有三档
   （.form-card 860px、.management-form 980px、.asset-qr-card 980px、
   .asset-status-card 未设上限），同一页面三块宽度参差。
2) 一行两列上下留白太多 —— 想要更紧凑的多列布局。
3) 「资产归属」框上下和左侧不一致 —— .form-field 缺 align-content，
   同一行里较高的字段把较矮的字段拉伸。

修复方式：
- 引入 --form-max-width 变量（1180px），所有「撑满父容器 + 上限」的块引用它；
- .form-grid 与 .lifecycle-form-grid 改用 repeat(auto-fit, minmax(...))，
  窄屏 1 列、中屏 2 列、宽屏 3 列，字段少时自动收缩不留空；
- .form-field 加 align-content: start，内容从顶部排列不被拉伸。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class FormLayoutConsistencyTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='layout_probe', password='Pw#2026aa')
        user.is_staff = True
        user.is_superuser = True
        for group in ('warehouse_admin', 'warehouse_state_correction'):
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(user)

        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        self.warehouse = Warehouse.objects.create(name='布局仓', code='LAYOUT-WH')
        category = Category.objects.create(name='布局分类', code='LAYOUT-CAT')
        item_type = ItemType.objects.create(
            name='布局类型', code='LAYOUT-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='布局资产', system_asset_no='AST-LAYOUT-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )

    def _page(self, url=''):
        target = url or f'/warehouse/assets/{self.asset.id}/'
        response = self.client.get(target)
        self.assertEqual(response.status_code, 200, f'{target} 未正常渲染')
        return response.content.decode('utf-8')

    def test_form_max_width_variable_defined(self):
        html = self._page()
        self.assertIn('--form-max-width: 1180px', html)

    def test_all_cards_share_same_max_width(self):
        """四块容器必须引用同一变量，不能再各写数字。"""
        html = self._page()
        for selector in (
            '.form-card', '.management-form', '.asset-qr-card', '.asset-status-card',
        ):
            with self.subTest(selector=selector):
                match = re.search(
                    re.escape(selector) + r' \{[^}]*?max-width:\s*([^;]+);', html, re.S,
                )
                self.assertIsNotNone(match, f'{selector} 未定义 max-width')
                self.assertIn(
                    'var(--form-max-width)', match.group(1),
                    f'{selector} 仍在使用硬编码宽度：{match.group(1).strip()}',
                )

    def test_no_legacy_hardcoded_widths(self):
        """860px / 980px 是历史遗留的两套数字，必须全部清除。"""
        html = self._page()
        self.assertIsNone(re.search(r'max-width:\s*860px', html), '仍有 860px 残留')
        self.assertIsNone(re.search(r'max-width:\s*980px', html), '仍有 980px 残留')

    def test_form_grid_is_auto_fit(self):
        """列数自适应，避免硬编码 2 列导致宽屏留白。"""
        html = self._page()
        match = re.search(r'\.form-grid \{[^}]*\}', html, re.S)
        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertIn('auto-fit', body)
        self.assertIn('minmax(240px', body)

    def test_lifecycle_form_grid_is_auto_fit(self):
        html = self._page()
        match = re.search(r'\.lifecycle-form-grid \{[^}]*\}', html, re.S)
        self.assertIsNotNone(match)
        self.assertIn('auto-fit', match.group(0))

    def test_form_field_aligns_to_top(self):
        """缺 align-content 时同一行较高的字段会拉伸相邻字段。"""
        html = self._page()
        match = re.search(r'\.form-field \{[^}]*\}', html)
        self.assertIsNotNone(match)
        self.assertIn('align-content: start', match.group(0))

    def test_all_form_card_pages_render(self):
        """7 个使用 .form-card 的页面都要能正常渲染并引用统一宽度。"""
        from apps.inventory.models import StockItem

        stock = StockItem.objects.create(
            name='布局耗材', code='STK-LAYOUT-1', item_type=self.asset.item_type,
            warehouse=self.warehouse, quantity=5,
        )
        pages = [
            f'/warehouse/assets/{self.asset.id}/',
            '/warehouse/assets/new/',
            f'/warehouse/stock-items/{stock.id}/',
            f'/warehouse/assets/{self.asset.id}/domain-change/',
            '/warehouse/conversions/',
            '/warehouse/composite-units/',
            '/warehouse/setup/locations/',
        ]
        for url in pages:
            with self.subTest(url=url):
                html = self._page(url)
                self.assertIn('var(--form-max-width)', html)
