from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Asset


User = get_user_model()


class InventoryTypeSummaryTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.admin = self.make_user('summary-admin', is_superuser=True)
        self.category = self.make_category(name='汇总分类', code='SUMMARY-CAT')
        self.item_type = self.make_item_type(name='汇总设备', code='SUMMARY-TYPE', category=self.category)
        self.item_type.safety_stock = 2
        self.item_type.save(update_fields=['safety_stock'])
        self.warehouse = self.make_warehouse(name='汇总仓', code='SUMMARY-WH')
        self.make_asset(name='汇总设备一', item_type=self.item_type, warehouse=self.warehouse, asset_code='SC-SUM-0001')
        self.make_asset(name='汇总设备二', item_type=self.item_type, warehouse=self.warehouse, asset_code='SC-SUM-0002', status=Asset.Status.MAINTENANCE)
        self.client.force_login(self.admin)

    def test_type_mode_shows_counts_and_single_asset_link(self):
        response = self.client.get('/warehouse/', {'view': 'types'})
        self.assertEqual(response.status_code, 200)
        group = next(group for group in response.context['asset_type_groups'] if group.id == self.item_type.id)
        self.assertEqual(group.total_count, 2)
        self.assertEqual(group.in_stock_count, 1)
        self.assertEqual(group.available_count, 1)
        self.assertEqual(group.safety_stock, 2)
        self.assertEqual(group.history_action_count, 0)
        self.assertContains(response, '按物品类型汇总')
        self.assertContains(response, '低于安全库存')

    def test_type_mode_respects_asset_filter(self):
        response = self.client.get('/warehouse/', {'view': 'types', 'item_type': str(self.item_type.id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['asset_type_groups']), 1)
