"""用途开关按类型隐藏（第 2 项修复）的回归。

背景：两个表单的「用途：日常使用 / 项目·客户」开关原来无条件显示。
但只有「借用/领用」才真正需要二选一：
- 归还、调修、报损等不涉及项目，让用户选是多余负担
- 出售必然挂客户，选择「日常」反而会让客户字段被禁用、customer_ref 关联不上

修复：用途区块加 data-types 限制；出售在后端强制视为 project，
避免开关隐藏导致 customer_ref 被清空。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import Asset, Category, Customer, ItemType, Warehouse
from apps.workflow.models import WorkflowRequest

User = get_user_model()


class UsageScopeVisibilityTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='开关仓', code='US-WH')
        category = Category.objects.create(name='开关分类', code='US-CAT')
        self.item_type = ItemType.objects.create(
            name='开关类型', code='US-IT', category=category,
        )
        self.customer = Customer.objects.create(
            code='US-CUST', name='开关客户', is_active=True,
        )
        self.operator = User.objects.create_user(
            username='us_op', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def test_quick_process_scope_limited_to_outbound_and_sale(self):
        response = self.client.get('/warehouse/quick-process/')
        content = response.content.decode('utf-8')
        self.assertIn('data-types="borrow,issue,sale"', content,
                      '快速出入库的用途开关应只对借用/领用/出售显示')

    def test_request_create_scope_limited_to_borrow_issue(self):
        response = self.client.get('/requests/new/?request_type=borrow')
        content = response.content.decode('utf-8')
        self.assertIn('data-types="borrow,issue"', content,
                      '申请页的用途开关应只对借用/领用显示（该页无出售类型）')

    def _make_asset(self, code):
        return Asset.objects.create(
            name=f'设备{code}', system_asset_no=code, item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def test_sale_forces_project_scope_without_usage_scope_field(self):
        """出售时开关隐藏、usage_scope 未提交，客户关联仍须保留。"""
        asset = self._make_asset('AST-US-1')
        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.SALE,
            'asset_ids': [str(asset.id)],
            'recipient_name': '张三',
            'recipient_company': '开关客户',
            'recipient_phone': '13800008111',
            'usage_location': '客户现场',
            'customer_ref': str(self.customer.id),
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '出售开关验证',
            'action': 'queue_quick_process',
            # 刻意不传 usage_scope
        })
        self.assertEqual(response.status_code, 302, '出售应能成功建单')
        latest = WorkflowRequest.objects.filter(
            request_type=WorkflowRequest.RequestType.SALE,
        ).order_by('-created_at').first()
        self.assertIsNotNone(latest)
        self.assertEqual(
            latest.customer_ref_id, self.customer.pk,
            '出售必须保留客户关联，不能被「日常」逻辑清空',
        )

    def test_borrow_without_scope_clears_customer(self):
        """借用未传用途时按日常处理，客户应被清空。"""
        asset = self._make_asset('AST-US-2')
        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'asset_ids': [str(asset.id)],
            'recipient_name': '李四',
            'recipient_phone': '13800008222',
            'usage_location': '售后仓库',
            'customer_ref': str(self.customer.id),
            'expected_return_date': (
                timezone.localdate() + timezone.timedelta(days=7)
            ).isoformat(),
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '借用开关验证',
            'action': 'queue_quick_process',
        })
        self.assertEqual(response.status_code, 302)
        latest = WorkflowRequest.objects.filter(
            request_type=WorkflowRequest.RequestType.BORROW,
        ).order_by('-created_at').first()
        self.assertIsNone(
            latest.customer_ref_id,
            '借用未选项目时应清空客户，符合「日常」语义',
        )

    def test_sale_scope_forced_in_source(self):
        """静态确认：出售分支不再依赖 POST 里的 usage_scope。"""
        import inspect

        from apps.common.views import legacy

        source = inspect.getsource(legacy.quick_inventory_process)
        self.assertIn('WorkflowRequest.RequestType.SALE', source)
        self.assertIn('_is_project_scope = True', source,
                      '出售应强制 _is_project_scope=True')
