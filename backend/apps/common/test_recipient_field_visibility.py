"""回归：新建申请页不能同时显示「接收人下拉框」和「接收人姓名框」。

历史 bug：`recipient-name-field` 同时带 `data-types` 和 `hidden`。
`refresh()` 遍历 `[data-types]` 时无条件执行 `field.hidden = !visible`，
把「外部接收人」勾选状态覆盖掉。初始加载时 refresh 还没跑，所以看起来正常；
一旦选中物品触发 refresh，姓名框就无条件冒出来，和下拉框重复。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class RecipientFieldVisibilityTests(TestCase):
    def setUp(self):
        admin = User.objects.create_user(username='recipient_admin', password='Pw#2026aa')
        admin.is_staff = True
        admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.client.force_login(admin)

    def _page(self):
        response = self.client.get('/requests/new/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_name_field_hidden_by_default(self):
        html = self._page()
        match = re.search(r'<div[^>]*id="recipient-name-field"[^>]*>', html)
        self.assertIsNotNone(match, '未渲染接收人姓名框')
        self.assertIn('hidden', match.group(0), '姓名框模板必须默认隐藏')

    def test_single_source_of_visibility(self):
        """不能再有两份互相覆盖的 show/hide 逻辑。"""
        html = self._page()
        # 旧的末端 IIFE 用 nameField.hidden 直接控制，必须已移除
        self.assertNotIn('nameField.hidden', html,
                         '仍有独立逻辑直接改 nameField.hidden，会与 refresh() 冲突')
        # 统一由 refresh 里的 recipientNameField 判断负责
        self.assertIn('recipientNameField', html)
        self.assertIn('field === recipientNameField', html)

    def test_refresh_respects_external_checkbox(self):
        html = self._page()
        # refresh 必须先读勾选状态，再决定姓名框是否可见
        self.assertIn('wantsExternalRecipient', html)
        self.assertIn('recipientExternal', html)

    def test_checkbox_triggers_refresh(self):
        html = self._page()
        # 勾选变化必须触发统一刷新，而不是走另一套逻辑
        self.assertIn('warehouse:recipient-changed', html)
        self.assertIn("recipientExternal.addEventListener('change', refresh)", html)

    def test_mutually_exclusive_required(self):
        """外部接收人时禁用下拉框；否则禁用姓名框。两者不能同时 required。"""
        html = self._page()
        self.assertIn('recipientSelect.disabled', html)
        self.assertIn('recipientSelect.required', html)
        self.assertIn('recipientNameInput.required', html)

    def test_only_one_recipient_selector_element(self):
        """页面上只能有一个接收人下拉框和一个姓名输入框。"""
        html = self._page()
        self.assertEqual(html.count('id="recipient_select"'), 1)
        self.assertEqual(html.count('id="recipient_name"'), 1)

    def test_external_checkbox_is_own_row(self):
        """勾选框必须独立成行。

        历史问题：把 checkbox-inline 塞进带 select 的 form-field 后，该网格行
        多出一行高度，同一行的「接收人姓名」「联系电话」被一起撑大，
        表现为字段高度不一致、画幅不协调。
        """
        html = self._page()
        self.assertIn('form-field checkbox-row', html)

        # 接收人 select 所在的 form-field 内不能再出现勾选框
        match = re.search(
            r'<div class="form-field"[^>]*>(?:(?!</div>).)*?id="recipient_select"(?:(?!</div>).)*?</div>',
            html, re.S,
        )
        self.assertIsNotNone(match, '未找到接收人下拉框容器')
        self.assertNotIn('checkbox-inline', match.group(0),
                         '勾选框仍在接收人 select 的 form-field 内，会撑高该行')

    def test_checkbox_row_style_defined(self):
        """校验行样式必须存在，否则独立出来仍然是普通 form-field 高度。"""
        html = self._page()
        self.assertIn('form-field.checkbox-row', html)
