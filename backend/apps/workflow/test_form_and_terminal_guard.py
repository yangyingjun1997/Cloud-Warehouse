"""四项表单与权限修复的回归。

1) 「使用部门/客户单位」改名为「接收人单位」（一个字段表达「对方是哪家公司」）
2) 出售时整个用途区块隐藏（此前只禁用「日常」，区块仍显示）
3) 退货给供应商必须有供应商下拉（此前无任何专属字段）
4) 终态校正仅 superuser 可用（此前 warehouse_admin 组即可直接报废）
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.common.permissions import can_correct_asset_state
from apps.inventory.forms import AssetStateCorrectionForm
from apps.inventory.models import (
    Asset, Category, Customer, ItemType, Location, Supplier, Warehouse,
)
from apps.inventory.state_services import (
    ADMIN_CORRECTION_TARGET_STATUSES,
    CORRECTION_TARGET_STATUSES,
    SUPERADMIN_CORRECTION_TARGET_STATUSES,
    TERMINAL_CORRECTION_STATUSES,
    AssetStateCorrectionError,
    correct_asset_states,
)
from apps.workflow.models import WorkflowRequest

User = get_user_model()


class TerminalStatusSetsTests(TestCase):
    """终态集合的三层划分必须正确。"""

    def test_terminal_statuses_not_in_admin_set(self):
        overlap = TERMINAL_CORRECTION_STATUSES & ADMIN_CORRECTION_TARGET_STATUSES
        self.assertEqual(
            overlap, frozenset(),
            '终态不应出现在仓库管理员的校正范围内',
        )

    def test_superadmin_set_contains_terminal(self):
        self.assertTrue(
            TERMINAL_CORRECTION_STATUSES <= SUPERADMIN_CORRECTION_TARGET_STATUSES,
            '超级管理员范围应包含终态',
        )

    def test_terminal_contains_three_statuses(self):
        expected = {Asset.Status.SOLD, Asset.Status.RETURNED_TO_VENDOR, Asset.Status.SCRAPPED}
        self.assertEqual(TERMINAL_CORRECTION_STATUSES, frozenset(expected))

    def test_normal_role_limited_to_two(self):
        expected = {Asset.Status.IN_STOCK, Asset.Status.PENDING_INSPECTION}
        self.assertEqual(CORRECTION_TARGET_STATUSES, frozenset(expected))


class CorrectionPermissionTests(TestCase):
    """入口权限与可改状态的三层矩阵。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='终态仓', code='TS-WH')
        self.location = Location.objects.create(
            warehouse=self.warehouse, code='TS-LOC', name='终态库位',
        )
        category = Category.objects.create(name='终态分类', code='TS-CAT')
        self.item_type = ItemType.objects.create(
            name='终态类型', code='TS-IT', category=category,
        )

    def _user(self, username, groups=(), is_super=False):
        user = User.objects.create_user(username=username, password='Pw#2026aa')
        user.is_superuser = is_super
        user.save()
        for group in groups:
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        return user

    def _asset(self, code):
        return Asset.objects.create(
            name=f'设备{code}', system_asset_no=code, item_type=self.item_type,
            warehouse=self.warehouse, location=self.location,
            status=Asset.Status.IN_STOCK,
        )

    def test_admin_group_alone_cannot_enter_correction(self):
        user = self._user('ts_admin_only', ['warehouse_admin'])
        self.assertFalse(can_correct_asset_state(user),
                         '仅 warehouse_admin 不应获得台账校正入口')

    def test_state_correction_group_can_enter(self):
        user = self._user('ts_corr', ['warehouse_state_correction'])
        self.assertTrue(can_correct_asset_state(user))

    def test_superuser_can_enter(self):
        user = self._user('ts_super', [], is_super=True)
        self.assertTrue(can_correct_asset_state(user))

    def test_admin_form_hides_terminal_options(self):
        user = self._user('ts_admin_form', ['warehouse_admin', 'warehouse_state_correction'])
        form = AssetStateCorrectionForm(user=user)
        choices = {choice[0] for choice in form.fields['to_status'].choices}
        self.assertEqual(choices & TERMINAL_CORRECTION_STATUSES, set(),
                         '管理员表单不应出现终态选项')

    def test_superadmin_form_shows_terminal_options(self):
        user = self._user('ts_super_form', [], is_super=True)
        form = AssetStateCorrectionForm(user=user)
        choices = {choice[0] for choice in form.fields['to_status'].choices}
        self.assertTrue(TERMINAL_CORRECTION_STATUSES <= choices,
                        '	superuser 表单应包含终态选项')

    def test_admin_cannot_scrap_asset(self):
        user = self._user('ts_scrap_block', ['warehouse_admin', 'warehouse_state_correction'])
        asset = self._asset('AST-TS-1')
        with self.assertRaises(AssetStateCorrectionError) as ctx:
            correct_asset_states(
                assets=[asset], to_status=Asset.Status.SCRAPPED,
                reason='尝试报废', actor=user,
                warehouse=self.warehouse, location=self.location,
            )
        self.assertIn('最终状态', str(ctx.exception))

    def test_superadmin_can_scrap_asset(self):
        user = self._user('ts_scrap_allow', [], is_super=True)
        asset = self._asset('AST-TS-2')
        correct_asset_states(
            assets=[asset], to_status=Asset.Status.SCRAPPED,
            reason='历史补录', actor=user,
            warehouse=self.warehouse, location=self.location,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.SCRAPPED)

    def test_admin_can_set_damaged(self):
        """损坏不是终态，管理员应可校正。"""
        user = self._user('ts_damaged', ['warehouse_admin', 'warehouse_state_correction'])
        asset = self._asset('AST-TS-3')
        correct_asset_states(
            assets=[asset], to_status=Asset.Status.DAMAGED,
            reason='外观损坏', actor=user,
            warehouse=self.warehouse, location=self.location,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.DAMAGED)


class RecipientCompanyLabelTests(TestCase):
    """「接收人单位」字段的语义与显示规则。"""

    def setUp(self):
        self.operator = User.objects.create_user(
            username='rc_op', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def test_label_renamed(self):
        content = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        self.assertIn('接收人单位', content)
        self.assertNotIn('使用部门/客户单位', content,
                         '旧标签应已全部替换')

    def test_field_is_recipient_block_member(self):
        """接收人单位应随接收人区块一起隐藏，而不是随项目字段。"""
        content = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        self.assertRegex(
            content,
            r'data-recipient-block[^>]*>\s*<label for="recipient_company"',
            '接收人单位应标记为接收人区块成员',
        )

    def test_request_page_label_renamed(self):
        content = self.client.get('/requests/new/?request_type=borrow').content.decode('utf-8')
        self.assertIn('接收人单位', content)
        self.assertNotIn('使用部门/单位', content)


class SaleScopeHiddenTests(TestCase):
    """出售时应隐藏整个用途区块，而非只禁用「日常」。"""

    def setUp(self):
        self.operator = User.objects.create_user(
            username='ss_op', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def test_js_hides_whole_scope_field_for_sale(self):
        content = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        self.assertIn('scopeField.hidden = mustBeProject', content,
                      '出售应隐藏整个用途区块')

    def test_sale_forces_project_scope_server_side(self):
        """即使前端隐藏，后端仍须按出售语义强制 project，保留客户关联。"""
        warehouse = Warehouse.objects.create(name='出售隐藏仓', code='SH-WH')
        location = Location.objects.create(
            warehouse=warehouse, code='SH-LOC', name='出售库位',
        )
        category = Category.objects.create(name='出售隐藏分类', code='SH-CAT')
        item_type = ItemType.objects.create(
            name='出售隐藏类型', code='SH-IT', category=category,
        )
        customer = Customer.objects.create(code='SH-CUST', name='隐藏客户', is_active=True)
        asset = Asset.objects.create(
            name='出售设备', system_asset_no='AST-SH-1', item_type=item_type,
            warehouse=warehouse, location=location, status=Asset.Status.IN_STOCK,
        )
        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.SALE,
            'asset_ids': [str(asset.id)],
            'recipient_name': '张三',
            'recipient_company': '隐藏客户',
            'recipient_phone': '13800007000',
            'usage_location': '客户现场',
            'customer_ref': str(customer.id),
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '出售验证',
            'action': 'queue_quick_process',
        })
        self.assertEqual(response.status_code, 302)
        latest = WorkflowRequest.objects.filter(
            request_type=WorkflowRequest.RequestType.SALE,
        ).order_by('-created_at').first()
        self.assertEqual(latest.customer_ref_id, customer.pk,
                         '出售必须保留客户关联')


class VendorReturnSupplierTests(TestCase):
    """退货给供应商必须有供应商，且能落库。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='退货仓', code='VR-WH')
        self.location = Location.objects.create(
            warehouse=self.warehouse, code='VR-LOC', name='退货库位',
        )
        category = Category.objects.create(name='退货分类', code='VR-CAT')
        self.item_type = ItemType.objects.create(
            name='退货类型', code='VR-IT', category=category,
        )
        self.supplier = Supplier.objects.create(
            code='VR-SUP', name='退货供应商', is_active=True,
        )
        self.operator = User.objects.create_user(
            username='vr_op', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.operator)

    def _asset(self, code):
        return Asset.objects.create(
            name=f'设备{code}', system_asset_no=code, item_type=self.item_type,
            warehouse=self.warehouse, location=self.location,
            status=Asset.Status.IN_STOCK,
        )

    def _post(self, **overrides):
        asset = self._asset(overrides.pop('asset_code', 'AST-VR-1'))
        payload = {
            'request_type': WorkflowRequest.RequestType.RETURN_TO_VENDOR,
            'asset_ids': [str(asset.id)],
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '退货测试',
            'action': 'queue_quick_process',
        }
        payload.update(overrides)
        return self.client.post('/warehouse/quick-process/', payload)

    def test_supplier_field_rendered(self):
        content = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        self.assertIn('for="supplier_ref"', content)
        self.assertIn('退货供应商', content)

    def test_missing_supplier_rejected(self):
        response = self._post(asset_code='AST-VR-2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('必须选择供应商', response.content.decode('utf-8'))

    def test_supplier_saved_to_request(self):
        response = self._post(asset_code='AST-VR-3', supplier_ref=str(self.supplier.id))
        self.assertEqual(response.status_code, 302)
        latest = WorkflowRequest.objects.filter(
            request_type=WorkflowRequest.RequestType.RETURN_TO_VENDOR,
        ).order_by('-created_at').first()
        self.assertEqual(latest.supplier_ref_id, self.supplier.pk)
        self.assertEqual(
            latest.recipient_company, self.supplier.name,
            '供应商名应落到接收人单位，便于详情页直接展示',
        )

    def test_inactive_supplier_rejected(self):
        inactive = Supplier.objects.create(code='VR-OFF', name='停用供应商', is_active=False)
        response = self._post(asset_code='AST-VR-4', supplier_ref=str(inactive.id))
        self.assertEqual(response.status_code, 200)
        self.assertIn('不存在或已停用', response.content.decode('utf-8'))
