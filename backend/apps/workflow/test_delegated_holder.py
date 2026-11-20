"""代借持有人归属 + 单据场景约束回归。

缺陷（2026-11-20 实测确认）：
    services.py 旧逻辑只要 is_quick_process 且有接收人文本，就无条件
    把 current_holder 置空、把接收人存成 external_holder_* 文本。
    结果：库管代借时即使「接收人」选的是系统内账号，资产 current_holder
    也是空——实际借用人看不到设备、查不到单据，系统里无人能追溯设备去向。

修复：
    - 按「接收人是什么」分流，而不是按「是不是快速出入库」分流：
      有 recipient 外键 → 落 current_holder；只有文本 → 存 external_*；
      都没有 → 落申请人。
    - 只有真正出库到人的类型（借用/领用/出售）才设持有人。
    - my_loans 同时接受「本人申请」与「本人是接收人」两种来源。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.inventory.models import Asset, Category, ItemType, Warehouse
from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
from apps.workflow.services import process_request

User = get_user_model()


class DelegatedBorrowHolderTests(TestCase):
    """库管代借时，资产必须挂在实际借用人名下。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='代借仓', code='DB-WH')
        category = Category.objects.create(name='代借分类', code='DB-CAT')
        item_type = ItemType.objects.create(
            name='代借类型', code='DB-IT', category=category,
        )
        self.item_type = item_type

        self.keeper = User.objects.create_user(
            username='db_keeper', password='Pw#2026aa', first_name='代借库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.keeper.groups.add(Group.objects.get_or_create(name=group)[0])

        self.borrower = User.objects.create_user(
            username='db_borrower', password='Pw#2026aa', first_name='实际借用人',
        )
        UserProfile.objects.create(user=self.borrower, phone='13000130010')

    def _make_asset(self, code):
        return Asset.objects.create(
            name=f'设备{code}', system_asset_no=code, item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )

    def _run_quick(self, asset, **overrides):
        values = {
            'request_type': WorkflowRequest.RequestType.BORROW,
            'applicant': self.keeper,
            'reason': '库管代借',
            'usage_location': '售后仓库',
            'application_date': timezone.localdate(),
            'expected_return_date': timezone.localdate() + timezone.timedelta(days=7),
            'is_quick_process': True,
            'status': WorkflowRequest.Status.WAITING_WAREHOUSE,
        }
        values.update(overrides)
        request_obj = WorkflowRequest.objects.create(**values)
        WorkflowRequestLine.objects.create(
            request=request_obj, asset=asset, quantity=1,
        )
        process_request(request_obj, actor=self.keeper)
        return request_obj

    def test_internal_recipient_becomes_current_holder(self):
        asset = self._make_asset('AST-DB-1')
        self._run_quick(
            asset,
            recipient=self.borrower,
            recipient_name='实际借用人',
            recipient_phone='13000130010',
        )
        asset.refresh_from_db()
        self.assertEqual(
            asset.current_holder_id, self.borrower.pk,
            '接收人是系统账号时，current_holder 必须落外键',
        )
        self.assertEqual(asset.external_holder_name, '', '不应同时存外部文本')
        self.assertEqual(asset.status, Asset.Status.BORROWED)

    def test_external_recipient_stays_text(self):
        asset = self._make_asset('AST-DB-2')
        self._run_quick(
            asset,
            recipient=None,
            recipient_name='外部工程师',
            recipient_company='某客户',
            recipient_phone='13900001111',
        )
        asset.refresh_from_db()
        self.assertIsNone(asset.current_holder_id, '外部接收人没有账号，不应落外键')
        self.assertEqual(asset.external_holder_name, '外部工程师')
        self.assertEqual(asset.external_holder_company, '某客户')

    def test_no_recipient_falls_back_to_applicant(self):
        asset = self._make_asset('AST-DB-3')
        self._run_quick(asset, recipient=None, recipient_name='', recipient_phone='')
        asset.refresh_from_db()
        self.assertEqual(
            asset.current_holder_id, self.keeper.pk,
            '未指定接收人时应落到申请人',
        )

    def test_borrower_can_see_asset_and_request_in_my_loans(self):
        asset = self._make_asset('AST-DB-4')
        request_obj = self._run_quick(
            asset,
            recipient=self.borrower,
            recipient_name='实际借用人',
            recipient_phone='13000130010',
        )
        self.client.force_login(self.borrower)
        response = self.client.get('/my-loans/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(asset.system_asset_no, content, '借用人应能看到设备')
        self.assertIn(request_obj.request_no, content, '借用人应能看到单据号')


class NonHolderTypesTests(TestCase):
    """回收类与处置类不应给资产设持有人。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='处置仓', code='NH-WH')
        category = Category.objects.create(name='处置分类', code='NH-CAT')
        self.item_type = ItemType.objects.create(
            name='处置类型', code='NH-IT', category=category,
        )
        self.keeper = User.objects.create_user(
            username='nh_keeper', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.keeper.groups.add(Group.objects.get_or_create(name=group)[0])

    def test_scrap_does_not_set_holder(self):
        asset = Asset.objects.create(
            name='待报废', system_asset_no='AST-NH-1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.SCRAP,
            applicant=self.keeper,
            reason='老化报废',
            usage_location='售后仓库',
            application_date=timezone.localdate(),
            is_quick_process=True,
            status=WorkflowRequest.Status.WAITING_WAREHOUSE,
        )
        WorkflowRequestLine.objects.create(request=request_obj, asset=asset, quantity=1)
        process_request(request_obj, actor=self.keeper)

        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.SCRAPPED)
        self.assertIsNone(asset.current_holder_id, '报废设备不应挂到某人名下')


class SaleRequiresCompanyTests(TestCase):
    """出售必须填客户单位。"""

    def setUp(self):
        from apps.inventory.models import Warehouse as W

        self.warehouse = W.objects.create(name='出售仓', code='SC2-WH')
        category = Category.objects.create(name='出售分类', code='SC2-CAT')
        self.item_type = ItemType.objects.create(
            name='出售类型', code='SC2-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='出售设备', system_asset_no='AST-SC2-1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.keeper = User.objects.create_user(
            username='sc2_keeper', password='Pw#2026aa',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.keeper.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.keeper)

    def test_sale_without_company_is_rejected(self):
        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.SALE,
            'usage_scope': 'project',
            'asset_ids': [str(self.asset.id)],
            'recipient_name': '张三',
            'recipient_phone': '13800001111',
            'usage_location': '客户现场',
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '出售测试',
            'action': 'queue_quick_process',
        })
        self.assertEqual(response.status_code, 200, '应停留页面并提示，而非重定向')
        self.assertIn('客户单位', response.content.decode('utf-8'))

    def test_sale_with_company_passes(self):
        response = self.client.post('/warehouse/quick-process/', {
            'request_type': WorkflowRequest.RequestType.SALE,
            'usage_scope': 'project',
            'asset_ids': [str(self.asset.id)],
            'recipient_name': '张三',
            'recipient_company': '某某客户公司',
            'recipient_phone': '13800001111',
            'usage_location': '客户现场',
            'transaction_date': timezone.localdate().isoformat(),
            'contact_source': '现场',
            'reason': '出售测试',
            'action': 'queue_quick_process',
        })
        self.assertEqual(response.status_code, 302, '填了客户单位应能提交')


class AssetToRequestLinkTests(TestCase):
    """资产详情页应能跳到对应的出库单据。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='跳转仓', code='LK-WH')
        category = Category.objects.create(name='跳转分类', code='LK-CAT')
        self.item_type = ItemType.objects.create(
            name='跳转类型', code='LK-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='跳转设备', system_asset_no='AST-LK-1', item_type=self.item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.keeper = User.objects.create_user(
            username='lk_keeper', password='Pw#2026aa', first_name='跳转库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.keeper.groups.add(Group.objects.get_or_create(name=group)[0])
        self.client.force_login(self.keeper)

    def test_lifecycle_page_links_to_request(self):
        request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.keeper,
            reason='跳转测试', usage_location='售后仓库',
            application_date=timezone.localdate(),
            expected_return_date=timezone.localdate() + timezone.timedelta(days=3),
            is_quick_process=True,
            status=WorkflowRequest.Status.WAITING_WAREHOUSE,
        )
        WorkflowRequestLine.objects.create(request=request_obj, asset=self.asset, quantity=1)
        process_request(request_obj, actor=self.keeper)
        self.asset.refresh_from_db()

        response = self.client.get(f'/warehouse/assets/{self.asset.id}/lifecycle/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(request_obj.request_no, content, '详情页应显示关联单据号')
        self.assertIn(
            f'/workflow/{request_obj.id}/', content,
            '详情页应提供跳转到申请单的链接',
        )
