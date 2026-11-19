"""大屏自适应回归：容器铺满 + 网格 auto-fit。

用户反馈：27 寸等大屏上内容挤在中间、左右与底部大片空白。
根因是 .content 有 max-width: 1440px，且多个网格固定列数（4 列 / 2 列），
容器放宽后固定列数会把单个卡片拉得过宽，所以两者必须一起改。

策略：容器不设固定上限（width: 100%），可读性交给内层 auto-fit；
指标卡与资产摘要条额外加 max-width 上限，避免 4K 下碎成十几列。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class ResponsiveLayoutTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='rsp_probe', password='Pw#2026aa')
        user.is_staff = True
        user.is_superuser = True
        for group in ('warehouse_admin', 'warehouse_state_correction'):
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(user)

    def _html(self, url='/'):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f'{url} 未正常渲染')
        return response.content.decode('utf-8')

    def _rule(self, html, selector):
        match = re.search(re.escape(selector) + r' \{[^}]*\}', html)
        self.assertIsNotNone(match, f'未找到 {selector} 规则')
        return match.group(0)

    def test_content_has_no_fixed_max_width(self):
        """内容区不再限制 1440px，否则大屏左右留白。"""
        rule = self._rule(self._html(), '.content')
        self.assertNotIn('max-width: 1440px', rule)
        self.assertIn('width: 100%', rule)

    def test_list_grids_use_auto_fit(self):
        """列表类网格必须自适应列数。"""
        html = self._html()
        expected = {
            '.metric-grid': '200px',
            '.workflow-grid': '380px',
            '.detail-list': '300px',
            '.permission-guide-grid': '210px',
            '.choice-card-grid': '320px',
            '.compact-choice-list': '230px',
            '.report-dimension-grid': '300px',
            '.account-dialog-grid': '240px',
            '.asset-lifecycle-summary': '150px',
        }
        for selector, min_px in expected.items():
            with self.subTest(selector=selector):
                rule = self._rule(html, selector)
                self.assertIn('auto-fit', rule, f'{selector} 仍在用固定列数')
                self.assertIn(min_px, rule, f'{selector} 的 minmax 下限不是 {min_px}')

    def test_panel_grid_keeps_two_columns(self):
        """主内容 + 侧栏是有意设计，不能改成 auto-fit。"""
        rule = self._rule(self._html(), '.panel-grid')
        self.assertNotIn('auto-fit', rule)
        self.assertIn('minmax(0, 1.6fr)', rule)

    def test_wide_screen_caps(self):
        """指标卡与摘要条要有上限，避免 4K 下碎成十几列。"""
        html = self._html()
        self.assertIn('max-width: 1800px', self._rule(html, '.metric-grid'))
        self.assertIn('max-width: 1400px', self._rule(html, '.asset-lifecycle-summary'))

    def test_mobile_single_column_still_works(self):
        """小屏仍要回落到单列，不能因为改自适应而破坏移动端。"""
        html = self._html()
        self.assertIn('@media (max-width: 760px)', html)
        match = re.search(
            r'\.panel-grid, \.form-grid[^{]*\{[^}]*grid-template-columns: 1fr',
            html,
        )
        self.assertIsNotNone(match, '移动端强制单列的规则丢失')

    def test_key_pages_render(self):
        """改了全局布局后，主要页面都要能正常渲染。"""
        pages = [
            '/', '/warehouse/', '/requests/', '/requests/new/',
            '/approvals/', '/reports/', '/permissions/',
        ]
        for url in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (200, 302), f'{url} 异常')
