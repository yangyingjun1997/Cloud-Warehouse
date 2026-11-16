from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.workflow.models import InventoryTransaction, WorkflowRequest

from .models import StockItem


class FinancialApiFieldPermissionTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.client = APIClient()
        self.warehouse = self.make_warehouse()
        self.stock_item = StockItem.objects.create(
            name='权限测试耗材',
            code='FINANCE-STOCK-001',
            quantity=5,
            unit_cost='19.90',
            warehouse=self.warehouse,
        )
        self.request_obj = WorkflowRequest.objects.create(
            applicant=self.make_user('finance-applicant'),
            request_type=WorkflowRequest.RequestType.ISSUE,
            status=WorkflowRequest.Status.DONE,
        )
        self.transaction = InventoryTransaction.objects.create(
            request=self.request_obj,
            stock_item=self.stock_item,
            actor=self.request_obj.applicant,
            action=WorkflowRequest.RequestType.ISSUE,
            quantity=1,
            cost_amount='19.90',
        )
        self.reports_user = self.make_user(
            'api-reports-only', groups=('warehouse_reports',),
        )
        self.finance_user = self.make_user(
            'api-finance', groups=('warehouse_finance',),
        )

    def _get(self, user, path):
        self.client.force_authenticate(user=user)
        response = self.client.get(path)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_non_finance_user_cannot_see_cost_fields(self):
        stock_data = self._get(
            self.reports_user,
            f'/api/inventory/stock-items/{self.stock_item.id}/',
        )
        transaction_data = self._get(
            self.reports_user,
            f'/api/workflow/inventory-transactions/{self.transaction.id}/',
        )

        self.assertNotIn('unit_cost', stock_data)
        self.assertNotIn('cost_amount', transaction_data)

    def test_finance_user_can_see_cost_fields(self):
        stock_data = self._get(
            self.finance_user,
            f'/api/inventory/stock-items/{self.stock_item.id}/',
        )
        transaction_data = self._get(
            self.finance_user,
            f'/api/workflow/inventory-transactions/{self.transaction.id}/',
        )

        self.assertEqual(str(stock_data['unit_cost']), '19.90')
        self.assertEqual(str(transaction_data['cost_amount']), '19.90')


class AssetPhotoPermissionTests(WarehouseTestFixture, TestCase):
    def test_reports_only_user_cannot_upload_asset_photo(self):
        user = self.make_user('photo-reports-only', groups=('warehouse_reports',))
        asset = self.make_asset(asset_code='PHOTO-PERMISSION-001')
        client = APIClient()
        client.force_authenticate(user=user)
        photo = SimpleUploadedFile('asset.png', b'not-read-because-forbidden', 'image/png')

        response = client.post(
            f'/api/inventory/assets/{asset.id}/upload-photo/',
            {'photo': photo},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        asset.refresh_from_db()
        self.assertFalse(asset.photo_url)
