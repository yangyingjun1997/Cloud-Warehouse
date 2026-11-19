"""系统账号前缀兜底与大屏高度自适应回归。

两个真实缺陷（2026-11-19 用户指出）：

1) 打印账号没被屏蔽。根因是清单写成 'label_print'（下划线），
   线上真实账号是 'label-printer'（连字符）——差一个字符就静默失效。
   同类问题还存在于 password_policy.py 与 context_processors.py。
   修复方式：统一从 system_accounts 取，并加前缀兜底
   （label_print / label-printer / labelprinter / service- / robot-）。

2) 大屏底部约 1/4 空白。根因是 .request-candidates/.request-form 用了
   clamp(440px, calc(100vh - 250px), 650px)——上限 650px 让两栏在高屏上
   停在 650px，下方留出大片空白。.lifecycle-scroll 的 620px 上限同理。
   修复方式：去掉上限，只保留最小高度，让高度随视口铺满。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.system_accounts import (
    SYSTEM_ACCOUNT_PREFIXES,
    SYSTEM_ACCOUNT_USERNAMES,
    exclude_system_accounts,
    is_system_account,
)

User = get_user_model()


class SystemAccountNamingTests(TestCase):
    """清单必须同时覆盖连字符与下划线两种写法，并有前缀兜底。"""

    def test_both_label_spellings_recognized(self):
        for username in ('label_print', 'label-printer', 'labelprinter'):
            with self.subTest(username=username):
                self.assertTrue(
                    is_system_account(type('U', (), {'username': username})()),
                    f'{username} 应被识别为维护账号',
                )

    def test_prefix_fallback(self):
        """以后新增打印/服务账号不必再改清单。"""
        for username in ('label-printer-2', 'service-backup', 'robot-sync'):
            with self.subTest(username=username):
                self.assertTrue(is_system_account(type('U', (), {'username': username})()))

    def test_real_users_not_excluded(self):
        for username in ('yangjunjie', 'zhengjiaxin', 'zhangsan'):
            with self.subTest(username=username):
                self.assertFalse(is_system_account(type('U', (), {'username': username})()))

    def test_queryset_exclusion_covers_both_spellings(self):
        User.objects.create_user(username='admin', password='Pw#2026aa')
        User.objects.create_user(username='label-printer', password='Pw#2026aa')
        User.objects.create_user(username='label_print', password='Pw#2026aa')
        real = User.objects.create_user(username='real_user_x', password='Pw#2026aa')

        names = set(
            exclude_system_accounts(User.objects.filter(is_active=True))
            .values_list('username', flat=True)
        )
        self.assertNotIn('admin', names)
        self.assertNotIn('label-printer', names)
        self.assertNotIn('label_print', names)
        self.assertIn(real.username, names)

    def test_password_policy_uses_shared_list(self):
        """密码策略不应再自带一份清单（否则又会写错）。"""
        from apps.accounts import password_policy

        self.assertIn('label-printer', password_policy.EXEMPT_USERNAMES)
        self.assertIn('admin', password_policy.EXEMPT_USERNAMES)


class QuickProcessRecipientTests(TestCase):
    def setUp(self):
        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        warehouse = Warehouse.objects.create(name='账号仓', code='SA-WH')
        category = Category.objects.create(name='账号分类', code='SA-CAT')
        item_type = ItemType.objects.create(
            name='账号类型', code='SA-IT', category=category,
        )
        Asset.objects.create(
            name='账号资产', system_asset_no='AST-SA-1', item_type=item_type,
            warehouse=warehouse, status=Asset.Status.IN_STOCK,
        )
        self.operator = User.objects.create_user(
            username='sa_op', password='Pw#2026aa', first_name='账号库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def test_recipient_dropdown_hides_underscore_and_hyphen_label_accounts(self):
        User.objects.create_user(username='admin', password='Pw#2026aa')
        User.objects.create_user(username='label-printer', password='Pw#2026aa')
        User.objects.create_user(username='label_print', password='Pw#2026aa')

        html = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        match = re.search(r'<select id="recipient_select".*?</select>', html, re.S)
        self.assertIsNotNone(match)
        options = match.group(0)
        for username in ('admin', 'label-printer', 'label_print'):
            with self.subTest(username=username):
                self.assertNotIn(f'>{username}<', options)


class LargeScreenHeightTests(TestCase):
    """大屏下两栏应铺满可用高度，不再被固定上限截断。"""

    def setUp(self):
        user = User.objects.create_user(
            username='h_probe', password='Pw#2026aa', first_name='高度测试',
        )
        from apps.accounts.models import UserProfile

        UserProfile.objects.create(user=user, phone='13900139001')
        self.client.force_login(user)

    def _html(self):
        return self.client.get('/requests/new/').content.decode('utf-8')

    def test_request_columns_have_no_fixed_height_cap(self):
        html = self._html()
        match = re.search(
            r'\.request-candidates, \.request-form \{[^}]*\}', html,
        )
        self.assertIsNotNone(match)
        rule = match.group(0)
        self.assertIn('calc(100vh', rule, '高度应随视口自适应')
        self.assertNotIn('650px', rule, '不应再有 650px 上限，否则大屏留白')
        self.assertIn('min-height', rule, '仍要保留最小高度，矮屏不至于不可用')

    def test_lifecycle_scroll_has_no_fixed_height_cap(self):
        html = self._html()
        match = re.search(r'\.lifecycle-scroll \{[^}]*\}', html)
        self.assertIsNotNone(match)
        rule = match.group(0)
        self.assertIn('calc(100vh', rule)
        self.assertNotIn('620px', rule)
