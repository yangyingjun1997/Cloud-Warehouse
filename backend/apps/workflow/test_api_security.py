from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.testing.fixtures import WarehouseTestFixture

from .models import ApprovalLog, WorkflowRequest, WorkflowRequestLine
from .serializers import WorkflowRequestSerializer


class WorkflowRequestApiSecurityTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.applicant = self.make_user('request-applicant')
        self.operator = self.make_operator('request-operator')
        self.asset = self.make_asset(asset_code='SECURITY-ASSET-001')

    def _make_request(self, *, status_value=WorkflowRequest.Status.DRAFT):
        request_obj = WorkflowRequest.objects.create(
            applicant=self.applicant,
            request_type=WorkflowRequest.RequestType.BORROW,
            status=status_value,
            reason='API 删除保护测试',
        )
        line = WorkflowRequestLine.objects.create(request=request_obj, asset=self.asset)
        log = ApprovalLog.objects.create(
            request=request_obj,
            actor=self.applicant,
            action='submit',
            comment='保留审计记录',
        )
        return request_obj, line, log

    def _assert_delete_is_disabled(self, user, request_obj, line, log):
        self.client.force_authenticate(user=user)
        response = self.client.delete(f'/api/workflow/requests/{request_obj.id}/')

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(WorkflowRequest.objects.filter(pk=request_obj.pk).exists())
        self.assertTrue(WorkflowRequestLine.objects.filter(pk=line.pk).exists())
        self.assertTrue(ApprovalLog.objects.filter(pk=log.pk).exists())

    def test_applicant_cannot_hard_delete_own_request(self):
        self._assert_delete_is_disabled(self.applicant, *self._make_request())

    def test_operator_cannot_hard_delete_visible_request(self):
        request_data = self._make_request(
            status_value=WorkflowRequest.Status.WAITING_WAREHOUSE,
        )
        self._assert_delete_is_disabled(self.operator, *request_data)


class WorkflowRequestCreationAtomicityTests(WarehouseTestFixture, TestCase):
    def test_line_creation_failure_rolls_back_request(self):
        applicant = self.make_user('atomic-applicant')
        asset = self.make_asset(asset_code='ATOMIC-ASSET-001')
        serializer = WorkflowRequestSerializer(data={
            'request_type': WorkflowRequest.RequestType.BORROW,
            'reason': '事务回滚测试',
            'lines': [{'asset': str(asset.id), 'quantity': 1}],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with patch.object(
            WorkflowRequestLine.objects,
            'bulk_create',
            side_effect=RuntimeError('simulated line insert failure'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'simulated line insert failure'):
                serializer.save(applicant=applicant)

        self.assertFalse(WorkflowRequest.objects.filter(applicant=applicant).exists())
        self.assertFalse(WorkflowRequestLine.objects.filter(asset=asset).exists())

    def test_client_cannot_override_nested_line_request(self):
        applicant = self.make_user('nested-request-applicant')
        other_request = WorkflowRequest.objects.create(
            applicant=applicant,
            request_type=WorkflowRequest.RequestType.BORROW,
        )
        asset = self.make_asset(asset_code='NESTED-REQUEST-ASSET-001')
        serializer = WorkflowRequestSerializer(data={
            'request_type': WorkflowRequest.RequestType.BORROW,
            'reason': '禁止客户端改写所属申请单',
            'lines': [{
                'request': str(other_request.id),
                'asset': str(asset.id),
                'quantity': 1,
            }],
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        created = serializer.save(applicant=applicant)

        line = created.lines.get()
        self.assertEqual(line.request_id, created.id)
        self.assertNotEqual(line.request_id, other_request.id)

    def test_nested_lines_update_is_rejected_until_explicitly_supported(self):
        applicant = self.make_user('nested-update-applicant')
        request_obj = WorkflowRequest.objects.create(
            applicant=applicant,
            request_type=WorkflowRequest.RequestType.BORROW,
        )
        asset = self.make_asset(asset_code='NESTED-UPDATE-ASSET-001')
        WorkflowRequestLine.objects.create(request=request_obj, asset=asset)

        serializer = WorkflowRequestSerializer(
            instance=request_obj,
            data={'lines': [{'asset': str(asset.id), 'quantity': 1}]},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('lines', serializer.errors)
