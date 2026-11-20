"""持有人登记回归：非在库资产可补录持有人，在库资产拒绝。

背景（2026-11-18 与用户确认）：
历史台账导入的资产状态为「已借出/已领用」但没有持有人记录，
导致「我的借用」看不到、也无法追责。需要受控的补录入口。
在库资产不该有持有人——那与库存事实矛盾。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.inventory.holder_forms import AssetHolderRegistrationForm
from apps.inventory.holder_services import (
    HOLDER_REGISTRABLE_STATUSES,
    AssetHolderRegistrationError,
    register_asset_holder,
)
from apps.inventory.models import (
    Asset, AssetLifecycleEvent, Category, ItemType, Warehouse,
)
from apps.common.models import OperationAuditLog

User = get_user_model()


class HolderRegistrableStatusTests(TestCase):
    def test_in_stock_not_registrable(self):
        """在库资产不该有持有人。"""
        self.assertNotIn(Asset.Status.IN_STOCK, HOLDER_REGISTRABLE_STATUSES)

    def test_pending_inspection_not_registrable(self):
        """待质检说明东西还在仓。"""
        self.assertNotIn(Asset.Status.PENDING_INSPECTION, HOLDER_REGISTRABLE_STATUSES)

    def test_scrapped_not_registrable(self):
        """已报废物品已不存在。"""
        self.assertNotIn(Asset.Status.SCRAPPED, HOLDER_REGISTRABLE_STATUSES)

    def test_out_of_warehouse_statuses_registrable(self):
        for status in (
            Asset.Status.BORROWED,
            Asset.Status.ISSUED,
            Asset.Status.REPAIRING,
            Asset.Status.SHIPPED_OUT,
        ):
            with self.subTest(status=status):
                self.assertIn(status, HOLDER_REGISTRABLE_STATUSES)

    def test_sold_and_returned_registrable(self):
        """已售出、已退供应商同样离开了仓库，必须能记录现在在谁手上。

        用户明确指出过：只有「在库」才不该有持有人。
        """
        for status in (
            Asset.Status.SOLD,
            Asset.Status.RETURNED_TO_VENDOR,
            Asset.Status.LOST,
            Asset.Status.DAMAGED,
        ):
            with self.subTest(status=status):
                self.assertIn(status, HOLDER_REGISTRABLE_STATUSES)

    def test_only_three_statuses_excluded(self):
        """排除法：只应有 3 个状态不可登记，避免以后又收窄成白名单。"""
        from apps.inventory.holder_services import HOLDER_NOT_APPLICABLE_STATUSES

        self.assertEqual(len(HOLDER_NOT_APPLICABLE_STATUSES), 3)


class RegisterAssetHolderTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='登记仓', code='REG-WH')
        category = Category.objects.create(name='登记分类', code='REG-CAT')
        self.item_type = ItemType.objects.create(
            name='登记类型', code='REG-IT', category=category,
        )
        self.borrowed = Asset.objects.create(
            name='已借出', system_asset_no='AST-REG-B1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )
        self.in_stock = Asset.objects.create(
            name='在库', system_asset_no='AST-REG-S1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.actor = User.objects.create_user(username='reg_actor', password='Pw#2026aa')
        self.actor.is_staff = True
        self.actor.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.person = User.objects.create_user(username='reg_person', password='Pw#2026aa')
        self.person.first_name = '张三'
        self.person.save()

    def test_can_register_internal_holder(self):
        asset = register_asset_holder(
            asset=self.borrowed, holder=self.person,
            reason='历史台账补录', actor=self.actor,
        )
        self.assertEqual(asset.current_holder_id, self.person.pk)
        self.assertEqual(asset.external_holder_name, '')

    def test_can_register_external_holder(self):
        asset = register_asset_holder(
            asset=self.borrowed, external_name='李四',
            external_company='某公司', external_phone='13800138000',
            reason='外部客户现场使用', actor=self.actor,
        )
        self.assertIsNone(asset.current_holder_id)
        self.assertEqual(asset.external_holder_name, '李四')
        self.assertEqual(asset.external_holder_company, '某公司')

    def test_rejects_in_stock_asset(self):
        with self.assertRaises(AssetHolderRegistrationError) as ctx:
            register_asset_holder(
                asset=self.in_stock, holder=self.person,
                reason='测试', actor=self.actor,
            )
        self.assertIn('在库', str(ctx.exception))

    def test_requires_reason(self):
        with self.assertRaises(AssetHolderRegistrationError):
            register_asset_holder(
                asset=self.borrowed, holder=self.person, reason='', actor=self.actor,
            )

    def test_requires_holder_or_external_name(self):
        with self.assertRaises(AssetHolderRegistrationError):
            register_asset_holder(
                asset=self.borrowed, reason='测试', actor=self.actor,
            )

    def test_rejects_both_holder_and_external(self):
        with self.assertRaises(AssetHolderRegistrationError):
            register_asset_holder(
                asset=self.borrowed, holder=self.person, external_name='李四',
                reason='测试', actor=self.actor,
            )

    def test_writes_audit_and_lifecycle(self):
        before_audit = OperationAuditLog.objects.count()
        before_events = AssetLifecycleEvent.objects.filter(asset=self.borrowed).count()

        register_asset_holder(
            asset=self.borrowed, holder=self.person,
            reason='审计验证', actor=self.actor,
        )

        self.assertEqual(OperationAuditLog.objects.count(), before_audit + 1)
        self.assertEqual(
            AssetLifecycleEvent.objects.filter(asset=self.borrowed).count(),
            before_events + 1,
        )
        latest = OperationAuditLog.objects.order_by('-created_at').first()
        self.assertEqual(latest.action, 'asset_holder_registered')

    def test_optimistic_lock_on_stale_version(self):
        stale = self.borrowed.updated_at
        # 模拟并发修改
        self.borrowed.remarks = '被别人改过'
        self.borrowed.save(update_fields=['remarks', 'updated_at'])
        with self.assertRaises(AssetHolderRegistrationError):
            register_asset_holder(
                asset=self.borrowed, holder=self.person,
                reason='并发测试', actor=self.actor,
                expected_updated_at=stale,
            )


class HolderFormTests(TestCase):
    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='表单仓', code='FRM-WH')
        category = Category.objects.create(name='表单分类', code='FRM-CAT')
        item_type = ItemType.objects.create(name='表单类型', code='FRM-IT', category=category)
        self.borrowed = Asset.objects.create(
            name='借出', system_asset_no='AST-FRM-B1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )
        self.in_stock = Asset.objects.create(
            name='在库', system_asset_no='AST-FRM-S1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def test_form_requires_one_of_holder_or_name(self):
        form = AssetHolderRegistrationForm(
            data={'holder-expected_asset_updated_at': self.borrowed.updated_at.isoformat(),
                  'holder-reason': '测试'},
            asset=self.borrowed,
        )
        self.assertFalse(form.is_valid())

    def test_form_rejects_in_stock_asset(self):
        form = AssetHolderRegistrationForm(
            data={'holder-expected_asset_updated_at': self.in_stock.updated_at.isoformat(),
                  'holder-external_holder_name': '李四',
                  'holder-reason': '测试'},
            asset=self.in_stock,
        )
        self.assertFalse(form.is_valid())


class HolderPageRenderingTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='page_actor', password='Pw#2026aa')
        user.is_staff = True
        # 「登记当前持有人」是历史台账补录，属于台账校正类操作。
        # 2026-11-20 收紧后 can_correct_asset_state 只认 warehouse_state_correction，
        # 不再因 warehouse_admin 自动放行，所以这里显式授予该组。
        user.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        user.groups.add(Group.objects.get_or_create(name='warehouse_state_correction')[0])
        self.client.force_login(user)

        self.warehouse = Warehouse.objects.create(name='页面仓', code='PAGE-WH')
        category = Category.objects.create(name='页面分类', code='PAGE-CAT')
        item_type = ItemType.objects.create(name='页面类型', code='PAGE-IT', category=category)
        self.borrowed = Asset.objects.create(
            name='借出', system_asset_no='AST-PAGE-B1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )
        self.in_stock = Asset.objects.create(
            name='在库', system_asset_no='AST-PAGE-S1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def test_form_shown_for_borrowed_asset(self):
        html = self.client.get(f'/warehouse/assets/{self.borrowed.id}/lifecycle/').content.decode('utf-8')
        self.assertIn('holder-register-box', html)
        self.assertIn('登记当前持有人', html)

    def test_form_hidden_for_in_stock_asset(self):
        html = self.client.get(f'/warehouse/assets/{self.in_stock.id}/lifecycle/').content.decode('utf-8')
        self.assertNotIn('holder-register-box', html)


class AssetEditPageStatusCardTests(TestCase):
    """编辑资产页必须一眼看到状态与持有人，并能就地登记。

    用户反馈：编辑页全是静态属性（编码/型号/采购单号），最需要看的
    实时状态和持有人反而只在生命周期页，信息架构是反的。
    """

    def setUp(self):
        user = User.objects.create_user(username='card_actor', password='Pw#2026aa')
        user.is_staff = True
        for group in ('warehouse_admin', 'warehouse_state_correction'):
            user.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(user)

        self.warehouse = Warehouse.objects.create(name='卡片仓', code='CARD-WH')
        category = Category.objects.create(name='卡片分类', code='CARD-CAT')
        item_type = ItemType.objects.create(name='卡片类型', code='CARD-IT', category=category)
        self.borrowed = Asset.objects.create(
            name='借出', system_asset_no='AST-CARD-B1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )
        self.sold = Asset.objects.create(
            name='售出', system_asset_no='AST-CARD-S1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.SOLD,
        )
        self.in_stock = Asset.objects.create(
            name='在库', system_asset_no='AST-CARD-I1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def _html(self, asset):
        response = self.client.get(f'/warehouse/assets/{asset.id}/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_edit_page_shows_status_card(self):
        for asset in (self.borrowed, self.sold, self.in_stock):
            with self.subTest(asset=asset.system_asset_no):
                self.assertIn('asset-status-card', self._html(asset))

    def test_holder_button_shown_for_out_of_warehouse(self):
        for asset in (self.borrowed, self.sold):
            with self.subTest(asset=asset.system_asset_no):
                self.assertIn('id="holder-toggle"', self._html(asset))

    def test_holder_button_hidden_for_in_stock(self):
        html = self._html(self.in_stock)
        self.assertNotIn('id="holder-toggle"', html)
        # 应给出明确提示，而不是让用户找不到入口
        self.assertIn('资产在库，不应有持有人', html)
