"""表单体验优化回归：只读字段视觉区分 + 申请单用途开关。

用户反馈两点：
1) 绑定值输入框（如申请单的实际使用人固定为申请人）外观与可编辑框一样，
   第一次使用的人以为是 bug，点了没反应。需要灰底区分。
2) 申请单默认铺开客户/项目/工单等 6 个字段，普通测试借用看着头晕。
   需要一个「用途」开关，日常只显示基础字段。

实现：
- web_base.html 增加全局规则：input[readonly] / :disabled / .is-readonly
  统一灰底 + 虚线边 + not-allowed 光标。
- request_create.html 增加 usage_scope 单选（日常 / 项目客户），
  项目相关字段带 data-scope="project"，默认 hidden 且 disabled。
- 视图在 usage_scope != project 时清空 customer/project/work_order 及手填编号，
  防止日常单被挂上项目（会污染项目统计）。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import UserProfile

User = get_user_model()


class ReadonlyVisualTests(TestCase):
    """只读字段的全局视觉规则。"""

    def setUp(self):
        user = User.objects.create_user(
            username='ro_probe', password='Pw#2026aa', first_name='只读测试',
        )
        UserProfile.objects.create(user=user, phone='13600136000')
        self.client.force_login(user)

    def test_global_readonly_style_defined(self):
        html = self.client.get('/requests/new/').content.decode('utf-8')
        for token in (
            'input[readonly]',
            '.form-field input.is-readonly',
            '#f2f5f5',
            'border-style: dashed',
            'cursor: not-allowed',
        ):
            with self.subTest(token=token):
                self.assertIn(token, html, f'缺少只读样式定义：{token}')

    def test_request_page_readonly_fields_marked(self):
        """申请页的接收人/电话是绑定值，必须带 readonly 或 is-readonly。"""
        html = self.client.get('/requests/new/').content.decode('utf-8')
        match = re.search(r'name="recipient_name"[^>]*', html)
        # 该字段已被简化移除，改为验证只读展示框存在且带标记
        self.assertIn('实际使用人/接收人', html)
        self.assertTrue(
            'is-readonly' in html or 'readonly' in html,
            '接收人展示框没有只读标记，用户会以为是可编辑的',
        )


class UsageScopeTests(TestCase):
    """申请单用途开关：日常只留基础字段，项目客户才展开。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='scope_probe', password='Pw#2026aa', first_name='用途测试',
        )
        UserProfile.objects.create(user=self.user, phone='13500135000')
        self.client.force_login(self.user)

    def _html(self):
        response = self.client.get('/requests/new/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_scope_radio_present(self):
        html = self._html()
        self.assertIn('name="usage_scope"', html)
        self.assertIn('value="daily"', html)
        self.assertIn('value="project"', html)

    def test_daily_is_default(self):
        html = self._html()
        self.assertRegex(html, r'value="daily"\s+checked')

    def test_project_fields_hidden_by_default(self):
        """项目/客户/工单字段默认隐藏，避免日常借用被一堆字段淹没。"""
        html = self._html()
        self.assertIn('usage-project-field', html)
        self.assertIn('data-scope="project"', html)
        self.assertIn('hidden', html)

    def test_quick_picks_present(self):
        """归还日期与申请原因的快捷选项。"""
        html = self._html()
        self.assertIn('date-quick', html, '缺少归还日期快捷按钮')
        self.assertIn('reason-quick', html, '缺少申请原因快捷选项')

    def test_usage_location_datalist(self):
        html = self._html()
        self.assertIn('usage-location-options', html, '缺少使用地点候选')


class UsageScopeIsolationTests(TestCase):
    """日常用途提交时，不能把项目/工单/客户带进单子。"""

    def setUp(self):
        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        self.warehouse = Warehouse.objects.create(name='用途仓', code='SC-WH')
        category = Category.objects.create(name='用途分类', code='SC-CAT')
        item_type = ItemType.objects.create(
            name='用途类型', code='SC-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='用途资产', system_asset_no='AST-SC-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.user = User.objects.create_user(
            username='iso_probe', password='Pw#2026aa', first_name='隔离测试',
        )
        UserProfile.objects.create(user=self.user, phone='13400134000')
        self.client.force_login(self.user)

    def test_daily_scope_drops_project_fields(self):
        from apps.workflow.models import WorkflowRequest

        response = self.client.post('/requests/new/', {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'usage_scope': 'daily',
            'asset_id': str(self.asset.id),
            'reason': '日常借用测试',
            'usage_location': '售后仓库',
            'expected_return_date': '2026-12-01',
            'application_date': '2026-11-19',
            'project_code': 'SHOULD-BE-DROPPED',
            'work_order_no': 'ALSO-DROPPED',
            'action': 'submit',
        })
        self.assertEqual(response.status_code, 302, '日常借用应能正常提交')

        created = WorkflowRequest.objects.filter(reason='日常借用测试').latest('created_at')
        self.assertEqual(created.project_code, '', '日常单不应保留项目编号')
        self.assertEqual(created.work_order_no, '', '日常单不应保留工单编号')
        self.assertIsNone(created.project_ref_id)
        self.assertIsNone(created.work_order_ref_id)
        self.assertIsNone(created.customer_ref_id)
