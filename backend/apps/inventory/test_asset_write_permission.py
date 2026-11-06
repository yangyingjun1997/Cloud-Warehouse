from django.test import TestCase
from django.urls import reverse

from apps.common.testing.fixtures import WarehouseTestFixture

from .models import Asset


class AssetWritePermissionTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse()
        self.asset = self.make_asset(warehouse=self.warehouse)
        self.clerk = self.make_operator('write-clerk')
        self.admin = self.make_user('write-admin', groups=('warehouse_admin',))

    def test_clerk_can_view_but_cannot_post(self):
        self.client.force_login(self.clerk)
        url = reverse('warehouse_asset_edit', args=[self.asset.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'name': '改名尝试'})
        self.assertEqual(response.status_code, 403)
        self.asset.refresh_from_db()
        self.assertNotEqual(self.asset.name, '改名尝试')

    def test_clerk_cannot_open_create_page(self):
        self.client.force_login(self.clerk)
        response = self.client.get(reverse('warehouse_asset_create'))
        self.assertEqual(response.status_code, 302)

    def test_admin_can_open_create_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('warehouse_asset_create'))
        self.assertEqual(response.status_code, 200)