from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
import re


User = get_user_model()


class PermissionToggleMarkupTests(TestCase):
    """回归：编辑权限按钮的 data-target 必须能对上表单 id。"""

    def setUp(self):
        self.admin = User.objects.create_user(username='toggle-admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.target = User.objects.create_user(username='toggle-target', password='pw')
        self.target.groups.add(Group.objects.get_or_create(name='warehouse_entry')[0])
        self.client.force_login(self.admin)

    def test_toggle_target_matches_form_id(self):
        response = self.client.get('/permissions/')
        html = response.content.decode('utf-8')

        targets = re.findall(r'data-target="([^"]+)"', html)
        self.assertTrue(targets, '没有渲染出编辑权限按钮')

        form_ids = set(re.findall(r'<form method="post" id="([^"]+)"', html))
        for target in targets:
            self.assertIn(
                target, form_ids,
                f'按钮 data-target={target} 找不到对应表单，点击会无反应',
            )

    def test_edit_body_is_hidden_by_default(self):
        response = self.client.get('/permissions/')
        html = response.content.decode('utf-8')
        self.assertIn('class="account-edit-body" hidden', html)

    def test_permission_checkboxes_exist_inside_form(self):
        response = self.client.get('/permissions/')
        html = response.content.decode('utf-8')
        self.assertIn('name="permissions"', html)
        self.assertIn('permission-chip', html)
