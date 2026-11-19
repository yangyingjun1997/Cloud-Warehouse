"""系统账号隔离与报修流程回归。

安全背景（2026-11-19 用户指出）：新建申请的「指定审批人」下拉里出现了 admin。
这是权限模型上的大忌——既泄露账号存在，又可能让申请落到无人处理的地方。
排查发现同一份维护账号清单在密码策略、上下文处理器里已各自硬编码过一次，
本次收口为 apps/accounts/system_accounts.py 唯一来源，并覆盖所有对外用户下拉。

报修功能：员工可自助提交报修，审批通过后由库管执行，资产转为「维修中」
并自动生成维修单。
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.accounts.system_accounts import (
    SYSTEM_ACCOUNT_USERNAMES,
    exclude_system_accounts,
    is_system_account,
)
from apps.inventory.models import Asset

User = get_user_model()


class SystemAccountIsolationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='Pw#2026aa', is_superuser=True, is_staff=True,
        )
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.label = User.objects.create_user(username='label_print', password='Pw#2026aa')

        self.approver = User.objects.create_user(
            username='real_approver', password='Pw#2026aa', first_name='真审批人',
        )
        self.approver.groups.add(Group.objects.get_or_create(name='warehouse_approval')[0])
        self.approver.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])

        self.employee = User.objects.create_user(
            username='sec_emp', password='Pw#2026aa', first_name='安全员工',
        )
        UserProfile.objects.create(user=self.employee, phone='13100131000')
        self.client.force_login(self.employee)

    def test_system_accounts_constant(self):
        self.assertIn('admin', SYSTEM_ACCOUNT_USERNAMES)
        self.assertIn('label_print', SYSTEM_ACCOUNT_USERNAMES)

    def test_exclude_system_accounts_helper(self):
        qs = exclude_system_accounts(User.objects.filter(is_active=True))
        names = set(qs.values_list('username', flat=True))
        self.assertNotIn('admin', names)
        self.assertNotIn('label_print', names)
        self.assertIn('real_approver', names)

    def test_is_system_account(self):
        self.assertTrue(is_system_account(self.admin))
        self.assertFalse(is_system_account(self.approver))

    def test_approver_dropdown_excludes_system_accounts(self):
        html = self.client.get('/requests/new/').content.decode('utf-8')
        import re
        match = re.search(r'<select id="designated_approver".*?</select>', html, re.S)
        self.assertIsNotNone(match, '未找到指定审批人下拉')
        options = match.group(0)
        self.assertNotIn('>admin<', options, '审批人下拉泄露了 admin 账号')
        self.assertNotIn('label_print', options, '审批人下拉泄露了 label_print 账号')
        self.assertIn('real_approver', options, '真实审批人应保留')

    def test_quick_process_recipient_excludes_system_accounts(self):
        """快速出入库不能让库管把资产「发」给维护账号。"""
        self.client.force_login(self.admin)
        html = self.client.get('/warehouse/quick-process/').content.decode('utf-8')
        import re
        match = re.search(r'<select id="recipient_select".*?</select>', html, re.S)
        self.assertIsNotNone(match, '未找到接收人下拉')
        self.assertNotIn('>admin<', match.group(0))
        self.assertNotIn('label_print', match.group(0))


class RepairRequestTests(TestCase):
    """报修：员工自助提交 → 审批 → 库管执行 → 维修中 + 生成维修单。"""

    def setUp(self):
        from apps.inventory.models import Asset, Category, ItemType, Warehouse

        self.warehouse = Warehouse.objects.create(name='报修仓', code='RP-WH')
        category = Category.objects.create(name='报修分类', code='RP-CAT')
        item_type = ItemType.objects.create(
            name='报修类型', code='RP-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='报修资产', system_asset_no='AST-RP-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.employee = User.objects.create_user(
            username='rep_emp', password='Pw#2026aa', first_name='报修员工',
        )
        UserProfile.objects.create(user=self.employee, phone='13000130000')
        self.client.force_login(self.employee)

    def test_repair_type_listed_on_request_page(self):
        html = self.client.get('/requests/new/').content.decode('utf-8')
        self.assertIn('repair', html)
        self.assertIn('报修', html)
        self.assertIn('故障描述', html)

    def test_repair_flow_ends_in_repairing(self):
        from apps.inventory.models import MaintenanceRecord
        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
        from apps.workflow.services import approve_request, process_request, submit_request

        approver = User.objects.create_user(
            username='rep_approver', password='Pw#2026aa',
        )
        for group in ('warehouse_approval', 'warehouse_outbound'):
            approver.groups.add(Group.objects.get_or_create(name=group)[0])

        req = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.REPAIR,
            reason='开机无反应',
            usage_location='售后仓库',
            application_date=date(2026, 11, 19),
        )
        WorkflowRequestLine.objects.create(request=req, asset=self.asset, quantity=1)

        submit_request(req, actor=self.employee)
        req.refresh_from_db()
        self.asset.refresh_from_db()
        # 审批前资产仍在库、无维修单
        self.assertEqual(req.status, WorkflowRequest.Status.PENDING)
        self.assertEqual(self.asset.status, Asset.Status.IN_STOCK)
        self.assertEqual(MaintenanceRecord.objects.filter(asset=self.asset).count(), 0)

        approve_request(req, actor=approver, comment='同意送修')
        process_request(req, actor=approver)

        req.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertEqual(req.status, WorkflowRequest.Status.DONE)
        self.assertEqual(self.asset.status, Asset.Status.REPAIRING)
        records = MaintenanceRecord.objects.filter(asset=self.asset, source_request=req)
        self.assertEqual(records.count(), 1, '报修执行后应自动生成一张维修单')
        self.assertIn('开机无反应', records.first().fault_description)

    def test_repair_does_not_require_recipient(self):
        """报修设备留在原地，不应强制填接收人与电话。"""
        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
        from apps.workflow.services import submit_request

        req = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.REPAIR,
            reason='屏幕闪烁',
            usage_location='售后仓库',
            application_date=date(2026, 11, 19),
        )
        WorkflowRequestLine.objects.create(request=req, asset=self.asset, quantity=1)
        submit_request(req, actor=self.employee)
        req.refresh_from_db()
        self.assertEqual(req.status, WorkflowRequest.Status.PENDING)
