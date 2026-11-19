"""申请单简化与全局列表回归。

背景（2026-11-19 与用户确认）：
员工自助申请只允许给自己提，接收人恒为申请人。原「代他人申请」引入三处不一致：
1) 出库时 current_holder 记 applicant，接收人填 B 结果资产登记在 A 名下；
2) 外部接收人的判定依赖 is_quick_process，普通申请填了外部姓名也走不进去；
3) 申请列表只按 applicant 过滤，接收人 B 看不到别人替他提的单。
转手场景改为「库管先归还再借出」，需要指定他人时统一走快速出入库。

同时新增管理员「全部申请」视图（?scope=all），仅供 warehouse_admin 使用。
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import UserProfile
from apps.inventory.models import Asset, Category, ItemType, Warehouse
from apps.workflow.models import WorkflowRequest

User = get_user_model()


class RequestCreateSimplifiedTests(TestCase):
    """新建申请页：接收人必须是申请人本人，不再提供选择。"""

    def setUp(self):
        self.employee = User.objects.create_user(
            username='simple_emp', password='Pw#2026aa', first_name='员工甲',
        )
        UserProfile.objects.create(user=self.employee, phone='13800138000')
        self.client.force_login(self.employee)

    def _page(self):
        response = self.client.get('/requests/new/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_no_recipient_selector(self):
        """接收人下拉框、外部勾选框、姓名输入框都必须移除。"""
        html = self._page()
        for element_id in (
            'id="recipient_select"',
            'id="recipient_external"',
            'id="recipient_name"',
        ):
            with self.subTest(element=element_id):
                self.assertNotIn(element_id, html)

    def test_shows_applicant_as_recipient(self):
        html = self._page()
        self.assertIn('实际使用人/接收人', html)
        self.assertIn('员工甲', html)
        self.assertIn('快速出入库', html, '应提示替他/客户办理走快速出入库')

    def test_shows_phone_from_profile(self):
        """电话取自 UserProfile，不是 User。"""
        html = self._page()
        self.assertIn('13800138000', html)

    def test_missing_profile_phone_shows_hint(self):
        no_phone = User.objects.create_user(
            username='simple_nophone', password='Pw#2026aa', first_name='无电话',
        )
        UserProfile.objects.create(user=no_phone, phone='')
        client = self.client_class()
        client.force_login(no_phone)
        html = client.get('/requests/new/').content.decode('utf-8')
        self.assertIn('未登记手机号', html)


class RequestListScopeTests(TestCase):
    """申请列表：普通员工只看自己，管理员可切换全部。"""

    def setUp(self):
        self.employee = User.objects.create_user(
            username='scope_emp', password='Pw#2026aa',
        )
        self.other = User.objects.create_user(
            username='scope_other', password='Pw#2026aa',
        )
        self.admin = User.objects.create_user(
            username='scope_admin', password='Pw#2026aa',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])

        # 两张不同的申请单
        self.mine = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.BORROW,
            reason='我的申请',
            application_date=date(2026, 11, 19),
        )
        self.theirs = WorkflowRequest.objects.create(
            applicant=self.other,
            request_type=WorkflowRequest.RequestType.BORROW,
            reason='别人的申请',
            application_date=date(2026, 11, 19),
        )

    def test_employee_sees_only_own(self):
        self.client.force_login(self.employee)
        html = self.client.get('/requests/').content.decode('utf-8')
        self.assertIn('我的申请', html)
        self.assertNotIn('查看全部申请', html, '普通员工不应有全量入口')

    def test_employee_cannot_escalate_via_scope_param(self):
        """非管理员带 ?scope=all 也不能看到别人的单。"""
        self.client.force_login(self.employee)
        response = self.client.get('/requests/?scope=all')
        html = response.content.decode('utf-8')
        self.assertIn('我的申请', html)
        self.assertNotIn('全部申请', html)
        self.assertNotContains(response, self.theirs.request_no)

    def test_admin_can_switch_to_all(self):
        self.client.force_login(self.admin)
        html = self.client.get('/requests/').content.decode('utf-8')
        self.assertIn('查看全部申请', html)

        html_all = self.client.get('/requests/?scope=all').content.decode('utf-8')
        self.assertIn('全部申请', html_all)
        self.assertIn('只看我的', html_all)
        # 筛选表单要保留 scope，否则点「筛选」会退回我的视图
        self.assertIn('name="scope" value="all"', html_all)

    def test_admin_sees_others_requests_in_all_scope(self):
        self.client.force_login(self.admin)
        response = self.client.get('/requests/?scope=all')
        self.assertContains(response, self.theirs.request_no)

    def test_recipient_also_sees_request(self):
        """作为接收人被指定的单，接收人也能看到（跨人代提时可追溯）。"""
        self.theirs.recipient = self.employee
        self.theirs.save(update_fields=['recipient'])
        self.client.force_login(self.employee)
        response = self.client.get('/requests/')
        self.assertContains(response, self.theirs.request_no)


class RepairRequiresInStockTests(TestCase):
    """维修申请要求资产已归还入库，避免状态矛盾。"""

    def setUp(self):
        from apps.workflow.models import WorkflowRequestLine

        self.warehouse = Warehouse.objects.create(name='维修仓', code='REP-WH')
        category = Category.objects.create(name='维修分类', code='REP-CAT')
        item_type = ItemType.objects.create(
            name='维修类型', code='REP-IT', category=category,
        )
        self.borrowed = Asset.objects.create(
            name='借出中', system_asset_no='AST-REP-B1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.BORROWED,
        )
        self.in_stock = Asset.objects.create(
            name='在库', system_asset_no='AST-REP-S1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.employee = User.objects.create_user(
            username='rep_emp', password='Pw#2026aa', first_name='维修申请',
        )
        UserProfile.objects.create(user=self.employee, phone='13800138001')
        self.client.force_login(self.employee)

        from apps.workflow.services import WorkflowError, submit_request

        self.submit_request = submit_request
        self.WorkflowError = WorkflowError
        self.RequestLine = WorkflowRequestLine

    def _build(self, asset):
        request_obj = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.REPAIR,
            reason='设备故障送修',
            application_date=date(2026, 11, 19),
        )
        self.RequestLine.objects.create(request=request_obj, asset=asset, quantity=1)
        return request_obj

    def test_borrowed_asset_rejected(self):
        request_obj = self._build(self.borrowed)
        with self.assertRaises(self.WorkflowError) as ctx:
            self.submit_request(request_obj, actor=self.employee)
        self.assertIn('已归还入库', str(ctx.exception))

    def test_in_stock_asset_accepted(self):
        request_obj = self._build(self.in_stock)
        self.submit_request(request_obj, actor=self.employee)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, WorkflowRequest.Status.PENDING)
