"""新建申请页的接收人字段回归。

设计变更（2026-11-19 与用户确认）：
员工自助申请只允许给自己提，接收人恒为申请人。原本的「接收人下拉 + 外部人员
勾选框 + 手工姓名框」三件套已移除，替换为只读展示。

此文件早期版本验证的是「勾选框控制姓名框显隐」，那套逻辑随简化一并删除。
现在改为验证：控件确实消失、页面仍展示本人信息、并给出改走快速出入库的提示。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import UserProfile

User = get_user_model()


class RecipientFieldRemovedTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user(
            username='recipient_probe', password='Pw#2026aa', first_name='员工乙',
        )
        UserProfile.objects.create(user=self.employee, phone='13900139000')
        self.client.force_login(self.employee)

    def _page(self):
        response = self.client.get('/requests/new/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_recipient_controls_removed(self):
        """下拉框、外部勾选框、手工姓名框都必须不再出现。"""
        html = self._page()
        for element_id in (
            'id="recipient_select"',
            'id="recipient_external"',
            'id="recipient-name-field"',
            'id="recipient_name"',
        ):
            with self.subTest(element=element_id):
                self.assertNotIn(element_id, html)

    def test_shows_applicant_as_recipient_readonly(self):
        """仍要展示「实际使用人/接收人」，但固定为申请人本人且只读。"""
        html = self._page()
        self.assertIn('实际使用人/接收人', html)
        self.assertIn('员工乙', html)

    def test_shows_phone_from_profile(self):
        """电话取自 UserProfile，不是 User。"""
        html = self._page()
        self.assertIn('13900139000', html)

    def test_hints_quick_process_for_others(self):
        """需要替他人或客户办理时，页面要指向快速出入库。"""
        html = self._page()
        self.assertIn('快速出入库', html)

    def test_no_stale_recipient_js(self):
        """清理后不应残留指向已删除元素的脚本引用。"""
        html = self._page()
        for token in (
            'recipientExternal',
            'recipientNameField',
            'warehouse:recipient-changed',
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, html)


class RecipientModelStillSupportedTests(TestCase):
    """模型层保留 recipient 字段：快速出入库与历史数据仍在使用。"""

    def test_model_keeps_recipient_fields(self):
        from apps.workflow.models import WorkflowRequest

        for field in ('recipient', 'recipient_name', 'recipient_phone',
                      'recipient_company', 'is_external_recipient'):
            with self.subTest(field=field):
                self.assertTrue(
                    any(f.name == field for f in WorkflowRequest._meta.get_fields()),
                    f'模型应保留 {field} 字段（快速出入库依赖）',
                )
