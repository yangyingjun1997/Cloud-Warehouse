"""标签渲染与打印作业测试。"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APIClient

from apps.common.models import OperationAuditLog

from .label_services import (
    build_asset_label,
    build_asset_labels,
    labels_to_print_payload,
    resolve_label_codes,
)
from .models import Asset, AssetLabelPrintJob, AssetTypeCode, InventoryDomain, ItemType, Category, Manufacturer, ProductModelCode


class LabelRenderTest(TestCase):
    """标签内容必须能支撑人工识别，且二维码协议正确。"""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(code='LBLCAT', name='标签测试类')
        cls.item_type = ItemType.objects.create(
            code='LBLTYPE', name='标签测试类型', is_serialized=True, category=cls.category,
        )
        cls.domain = InventoryDomain.objects.get(code='MARKET')

    def _asset(self, **overrides):
        defaults = {
            'domain': self.domain,
            'name': '测试资产',
            'item_type': self.item_type,
            'system_asset_no': 'AST-00000099',
            'asset_code': 'SC-JQR-G1EDU-0001',
            'serial_number': 'SN-TEST-001',
        }
        defaults.update(overrides)
        return Asset.objects.create(**defaults)

    def test_label_contains_qr_and_both_codes(self):
        asset = self._asset()
        label = build_asset_label(asset)
        self.assertTrue(label.qr_image_base64)
        self.assertEqual(label.primary_code, 'SC-JQR-G1EDU-0001')
        self.assertEqual(label.manufacture_sn, 'SN-TEST-001')
        self.assertEqual(label.permanent_no, 'AST-00000099')
        self.assertFalse(label.pending_code)

    def test_asset_without_primary_code_falls_back_to_permanent_no(self):
        asset = self._asset(asset_code='')
        label = build_asset_label(asset)
        self.assertTrue(label.pending_code)
        self.assertIn('AST-00000099', label.primary_code)
        self.assertTrue(label.warnings)

    def test_qr_payload_follows_protocol(self):
        asset = self._asset()
        label = build_asset_label(asset)
        self.assertTrue(label.qr_payload.startswith('WMS2:A:'))


class LabelPrintJobApiTest(TestCase):
    """打印作业的创建、领取、上报闭环。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('label_user', password='pw-label')
        cls.user.groups.add(Group.objects.get_or_create(name='warehouse_label_print')[0])
        cls.category = Category.objects.create(code='JOB', name='作业测试类')
        cls.item_type = ItemType.objects.create(
            code='JOBTYPE', name='作业测试类型', is_serialized=True, category=cls.category,
        )
        cls.domain = InventoryDomain.objects.get(code='MARKET')
        cls.asset = Asset.objects.create(
            domain=cls.domain, name='作业资产', item_type=cls.item_type,
            system_asset_no='AST-00000088', asset_code='SC-JQR-B2-0001',
            serial_number='SN-JOB-1',
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_claim_finish_round_trip(self):
        created = self.client.post(
            '/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        job_id = created.data['job_id']
        self.assertEqual(created.data['asset_count'], 1)

        claimed = self.client.post(
            '/api/inventory/label-jobs/claim/', {'client_name': 'printer-pc'}, format='json',
        )
        self.assertEqual(claimed.status_code, 200)
        job = claimed.data['job']
        self.assertEqual(job['id'], job_id)
        self.assertEqual(len(job['payload']['labels']), 1)
        self.assertTrue(job['claim_token'])

        finished = self.client.post(
            f'/api/inventory/label-jobs/{job_id}/finish/',
            {'success': True, 'claim_token': job['claim_token']},
            format='json',
        )
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(AssetLabelPrintJob.objects.get(id=job_id).status, 'printed')

    def test_claim_marks_job_claimed_and_second_claim_returns_none(self):
        self.client.post('/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json')
        first = self.client.post('/api/inventory/label-jobs/claim/', {'client_name': 'pc-1'}, format='json')
        self.assertIsNotNone(first.data['job'])
        second = self.client.post('/api/inventory/label-jobs/claim/', {'client_name': 'pc-2'}, format='json')
        self.assertIsNone(second.data['job'])

    def test_failure_report_records_error_message(self):
        created = self.client.post(
            '/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json',
        )
        job_id = created.data['job_id']
        claimed = self.client.post(
            '/api/inventory/label-jobs/claim/', {'client_name': 'pc-1'}, format='json',
        ).data['job']
        response = self.client.post(
            f'/api/inventory/label-jobs/{job_id}/finish/',
            {'success': False, 'message': '打印机脱机', 'claim_token': claimed['claim_token']},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        job = AssetLabelPrintJob.objects.get(id=job_id)
        self.assertEqual(job.status, 'failed')
        self.assertIn('打印机脱机', job.error_message)

    def test_empty_asset_list_is_rejected(self):
        response = self.client.post('/api/inventory/label-jobs/', {'asset_ids': []}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_print_job_is_audited(self):
        before = OperationAuditLog.objects.count()
        self.client.post('/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json')
        self.assertGreater(OperationAuditLog.objects.count(), before)

    def test_user_without_print_permission_is_rejected(self):
        other = get_user_model().objects.create_user('no_print', password='pw')
        self.client.force_authenticate(user=other)
        response = self.client.post(
            '/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_finish_requires_matching_claim_token_and_user(self):
        created = self.client.post(
            '/api/inventory/label-jobs/', {'asset_ids': [str(self.asset.id)]}, format='json',
        )
        claimed = self.client.post(
            '/api/inventory/label-jobs/claim/', {'client_name': 'pc-1'}, format='json',
        ).data['job']
        wrong_token = self.client.post(
            f'/api/inventory/label-jobs/{created.data["job_id"]}/finish/',
            {'success': True, 'claim_token': 'wrong'}, format='json',
        )
        self.assertEqual(wrong_token.status_code, 403)

        other = get_user_model().objects.create_user('other_printer', password='pw')
        other.groups.add(Group.objects.get(name='warehouse_label_print'))
        self.client.force_authenticate(user=other)
        wrong_user = self.client.post(
            f'/api/inventory/label-jobs/{created.data["job_id"]}/finish/',
            {'success': True, 'claim_token': claimed['claim_token']}, format='json',
        )
        self.assertEqual(wrong_user.status_code, 403)

    def test_missing_asset_aborts_entire_job(self):
        response = self.client.post(
            '/api/inventory/label-jobs/',
            {'asset_ids': [str(self.asset.id), '00000000-0000-0000-0000-000000000001']},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AssetLabelPrintJob.objects.count(), 0)
