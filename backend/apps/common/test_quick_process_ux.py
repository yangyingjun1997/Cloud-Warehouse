"""快速出入库表单优化回归。

与新建申请保持一致的体验改造（2026-11-19）：
- 用途开关：日常只留基础字段，项目/客户才展开客户、项目、工单信息。
- 快捷输入：归还日期 +7/+15/+30、处理原因快捷短语、使用地点候选。
- 日常用途提交时不能把项目/工单/客户带进单子（会污染项目统计）。
"""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

User = get_user_model()


class QuickProcessFormTests(TestCase):
    def setUp(self):
        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        self.warehouse = Warehouse.objects.create(name='快测仓', code='QK-WH')
        category = Category.objects.create(name='快测分类', code='QK-CAT')
        item_type = ItemType.objects.create(
            name='快测类型', code='QK-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='快测资产', system_asset_no='AST-QK-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.operator = User.objects.create_user(
            username='qk_op', password='Pw#2026aa', first_name='快测库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def _html(self):
        response = self.client.get('/warehouse/quick-process/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_usage_scope_present(self):
        html = self._html()
        self.assertIn('name="usage_scope"', html)
        self.assertIn('value="daily"', html)
        self.assertIn('value="project"', html)
        self.assertRegex(html, r'value="daily"\s+checked')

    def test_project_fields_hidden_by_default(self):
        """只统计真正的 form-field 元素，不把 JS 选择器算进来。"""
        html = self._html()
        elements = re.findall(
            r'<div class="form-field usage-project-field"[^>]*>', html,
        )
        self.assertGreater(len(elements), 0, '未找到项目相关字段')
        for element in elements:
            with self.subTest(element=element[:60]):
                self.assertIn('hidden', element, '项目字段默认应隐藏')

    def test_quick_picks_present(self):
        html = self._html()
        self.assertIn('date-quick', html, '缺少归还日期快捷按钮')
        self.assertIn('reason-quick', html, '缺少原因快捷短语')
        self.assertIn('quick-usage-location-options', html, '缺少使用地点候选')

    def test_recipient_dropdown_excludes_system_accounts(self):
        admin = User.objects.create_user(
            username='admin', password='Pw#2026aa', is_superuser=True,
        )
        admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        User.objects.create_user(username='label_print', password='Pw#2026aa')

        html = self._html()
        match = re.search(r'<select id="recipient_select".*?</select>', html, re.S)
        self.assertIsNotNone(match, '未找到接收人下拉')
        options = match.group(0)
        self.assertNotIn('>admin<', options)
        self.assertNotIn('label_print', options)


class QuickProcessScopeIsolationTests(TestCase):
    """日常用途提交时不能把项目/工单/客户带进单子。"""

    def setUp(self):
        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        self.warehouse = Warehouse.objects.create(name='隔离仓', code='QI-WH')
        category = Category.objects.create(name='隔离分类', code='QI-CAT')
        item_type = ItemType.objects.create(
            name='隔离类型', code='QI-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='隔离资产', system_asset_no='AST-QI-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.operator = User.objects.create_user(
            username='qi_op', password='Pw#2026aa', first_name='隔离库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def test_daily_scope_drops_project_fields(self):
        from apps.workflow.models import WorkflowRequest

        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'usage_scope': 'daily',
            'asset_ids': [str(self.asset.id)],
            'recipient_name': '张三',
            'recipient_phone': '13800001111',
            'usage_location': '客户现场',
            'expected_return_date': '2026-12-01',
            'transaction_date': '2026-11-19',
            'contact_source': '现场',
            'reason': '现场紧急借用',
            'project_code': 'DROP-ME',
            'work_order_no': 'DROP-ME-TOO',
            'action': 'queue_quick_process',
        })
        self.assertEqual(response.status_code, 302, '日常快速出入库应能正常提交')

        created = WorkflowRequest.objects.filter(reason='现场紧急借用').latest('created_at')
        self.assertEqual(created.project_code, '', '日常单不应保留项目编号')
        self.assertEqual(created.work_order_no, '', '日常单不应保留工单编号')
        self.assertIsNone(created.project_ref_id)
        self.assertIsNone(created.work_order_ref_id)
        self.assertIsNone(created.customer_ref_id)
