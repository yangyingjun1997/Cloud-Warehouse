from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from .models import ApprovalTask, WorkflowRequest


User = get_user_model()


class BatchApprovalWebTests(TestCase):
    def setUp(self):
        self.approver = User.objects.create_user(username='batch-approver', password='password123')
        self.approver.groups.add(Group.objects.get_or_create(name='warehouse_approval')[0])
        self.applicant = User.objects.create_user(username='batch-applicant', password='password123')
        self.request_obj = WorkflowRequest.objects.create(
            request_no='REQ-BATCH-UUID-0001',
            request_type=WorkflowRequest.RequestType.BORROW,
            applicant=self.applicant,
            status=WorkflowRequest.Status.PENDING,
        )
        self.task = ApprovalTask.objects.create(
            request=self.request_obj,
            approver=self.approver,
            status=ApprovalTask.Status.PENDING,
        )
        self.client.force_login(self.approver)

    def test_batch_approval_accepts_real_uuid(self):
        response = self.client.post('/approvals/batch-action/', {
            'batch_action': 'approve',
            'task_ids': str(self.task.id),
            'batch_comment': '批量测试通过',
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.request_obj.refresh_from_db()
        self.assertEqual(self.task.status, ApprovalTask.Status.APPROVED)
        self.assertEqual(self.request_obj.status, WorkflowRequest.Status.WAITING_WAREHOUSE)

    def test_batch_rejection_requires_reason(self):
        response = self.client.post('/approvals/batch-action/', {
            'batch_action': 'reject',
            'task_ids': str(self.task.id),
            'batch_comment': '',
        }, follow=True)
        self.assertContains(response, '批量拒绝必须填写拒绝原因')
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, ApprovalTask.Status.PENDING)
