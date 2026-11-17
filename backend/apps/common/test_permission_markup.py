import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class PermissionDialogMarkupTests(TestCase):
    """回归：编辑按钮的 data-dialog 必须能对上 <dialog> 的 id，否则点击无反应。

    2026-11-17 起编辑区从「表格内展开」改为原生 <dialog> 弹窗，
    避免展开权限胶囊时把表格行撑高。
    """

    def setUp(self):
        self.admin = User.objects.create_user(username='toggle-admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.target = User.objects.create_user(username='toggle-target', password='pw')
        self.target.groups.add(Group.objects.get_or_create(name='warehouse_entry')[0])
        self.client.force_login(self.admin)

    def test_dialog_id_matches_button_target(self):
        html = self.client.get('/permissions/').content.decode('utf-8')

        targets = re.findall(r'data-dialog="([^"]+)"', html)
        self.assertTrue(targets, '没有渲染出编辑按钮')

        dialog_ids = set(re.findall(r'<dialog[^>]*id="([^"]+)"', html))
        for target in targets:
            self.assertIn(
                target, dialog_ids,
                f'按钮 data-dialog={target} 找不到对应弹窗，点击会无反应',
            )

    def test_dialog_contains_permissions_and_dingtalk(self):
        html = self.client.get('/permissions/').content.decode('utf-8')
        self.assertIn('<dialog', html)
        self.assertIn('name="permissions"', html)
        self.assertIn('name="dingtalk_user_id"', html)
        self.assertIn('permission-chip', html)

    def test_old_inline_toggle_no_longer_rendered(self):
        """旧的表格内展开区必须彻底移除，否则页面又会被拉高。"""
        html = self.client.get('/permissions/').content.decode('utf-8')
        self.assertNotIn('account-edit-body', html)
        self.assertNotIn('data-target="role-form-', html)

    def test_employee_no_not_exposed_in_ui(self):
        """公司暂无工号体系：界面不再展示工号输入，但保留隐藏字段避免丢数据。"""
        html = self.client.get('/permissions/').content.decode('utf-8')
        self.assertNotIn('员工工号（选填）', html)
        self.assertIn('name="employee_no"', html)
