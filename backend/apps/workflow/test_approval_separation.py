from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.common.models import OperationAuditLog
from apps.inventory.models import Category, InventoryDomain, ItemType, Warehouse
from apps.workflow.models import WorkflowRequest
from apps.workflow.services import WorkflowError, approve_request, submit_request


User = get_user_model()


class ApprovalDutySeparationTests(TestCase):
    """职责分离：有其他人可审批时，不允许自批。"""

    def setUp(self):
        for name in ('warehouse_approval', 'warehouse_admin'):
            Group.objects.get_or_create(name=name)
        self.category = Category.objects.create(name='审批分类', code='APP-CAT')
        self.domain, _ = InventoryDomain.objects.get_or_create(
            code='MARKET', defaults={'name': '市场'},
        )
        self.warehouse = Warehouse.objects.create(name='审批仓', code='APP-WH')
        self.item_type = ItemType.objects.create(
            name='审批设备', code='APP-TYPE', category=self.category, is_serialized=True,
        )
        # 申请人同时拥有审批权限
        self.applicant = User.objects.create_user(username='both-roles', password='pw')
        self.applicant.groups.add(Group.objects.get(name='warehouse_approval'))

    def _make_pending_request(self):
        request = WorkflowRequest.objects.create(
            applicant=self.applicant,
            request_type=WorkflowRequest.RequestType.BORROW,
            reason='自批测试',
            status=WorkflowRequest.Status.PENDING,
            recipient_name='张三',
        )
        from apps.workflow.models import ApprovalTask
        ApprovalTask.objects.create(
            request=request, status=ApprovalTask.Status.PENDING,
        )
        return request

    def test_self_approval_blocked_when_other_approver_exists(self):
        other = User.objects.create_user(username='other-approver', password='pw')
        other.groups.add(Group.objects.get(name='warehouse_approval'))
        request = self._make_pending_request()

        with self.assertRaises(WorkflowError) as ctx:
            approve_request(request, self.applicant, '自己批自己')

        self.assertIn('不能审批自己', str(ctx.exception))
        request.refresh_from_db()
        self.assertEqual(request.status, WorkflowRequest.Status.PENDING)

    def test_self_approval_allowed_when_sole_approver_with_audit(self):
        # 没有其他审批人
        request = self._make_pending_request()

        approve_request(request, self.applicant, '只有我一个人')

        request.refresh_from_db()
        self.assertEqual(request.status, WorkflowRequest.Status.WAITING_WAREHOUSE)
        self.assertTrue(OperationAuditLog.objects.filter(
            action='request_self_approved',
        ).exists())

    def test_other_approver_can_always_approve(self):
        other = User.objects.create_user(username='colleague', password='pw')
        other.groups.add(Group.objects.get(name='warehouse_approval'))
        request = self._make_pending_request()

        approve_request(request, other, '同事审批')

        request.refresh_from_db()
        self.assertEqual(request.status, WorkflowRequest.Status.WAITING_WAREHOUSE)
        self.assertFalse(OperationAuditLog.objects.filter(
            action='request_self_approved',
        ).exists())


class RecipientSelectionTests(TestCase):
    """接收人：可选系统账号，也可标记为非系统用户。"""

    def setUp(self):
        self.category = Category.objects.create(name='接收分类', code='REC-CAT')
        self.warehouse = Warehouse.objects.create(name='接收仓', code='REC-WH')
        self.item_type = ItemType.objects.create(
            name='接收设备', code='REC-TYPE', category=self.category, is_serialized=True,
        )
        self.employee = User.objects.create_user(username='recipient-applicant', password='pw')
        self.employee.first_name = '申请员工'
        self.employee.save(update_fields=['first_name'])
        self.colleague = User.objects.create_user(username='zhangsan', password='pw')
        self.colleague.first_name = '张三'
        self.colleague.save(update_fields=['first_name'])
        self.client.force_login(self.employee)

    def test_request_page_fixes_recipient_to_applicant(self):
        """新建申请只允许给自己提，不再提供接收人选择（2026-11-19 简化）。

        原实现提供「接收人下拉 + 外部人员勾选框」，但出库时 current_holder
        始终记 applicant，接收人填他人会造成资产登记错人、且对方看不到单据。
        现在统一为「接收人=申请人」，需要指定他人时走快速出入库。
        """
        response = self.client.get('/requests/new/')
        self.assertEqual(response.status_code, 200)
        # 接收人选择控件必须消失
        self.assertNotContains(response, 'id="recipient_select"')
        self.assertNotContains(response, 'id="recipient_external"')
        # 但仍要展示「实际使用人/接收人」并固定为本人
        self.assertContains(response, '实际使用人/接收人')
        self.assertContains(response, '申请员工')
        # 模型层仍保留 recipient 字段（历史数据与快速出入库仍在使用），
        # 这里只验证页面不再暴露选择入口。
        self.assertTrue(hasattr(WorkflowRequest, 'recipient'))

    def test_model_supports_external_recipient_flag(self):
        request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.ISSUE,
            reason='外部人员',
            recipient_name='外来工程师',
            is_external_recipient=True,
        )
        self.assertTrue(request.is_external_recipient)
        self.assertIsNone(request.recipient_id)

    def test_model_supports_linked_recipient(self):
        request = WorkflowRequest.objects.create(
            applicant=self.employee,
            request_type=WorkflowRequest.RequestType.ISSUE,
            reason='内部借用',
            recipient=self.colleague,
            recipient_name='张三',
        )
        self.assertEqual(request.recipient_id, self.colleague.pk)
        self.assertFalse(request.is_external_recipient)
