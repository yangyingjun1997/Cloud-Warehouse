from django.contrib.auth import get_user_model
from django.test import TestCase
from urllib.parse import parse_qs

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
        self.assertNotContains(response, '<details class="management-advanced" open>')

    def test_type_drilldown_return_drops_only_drilldown_item_type(self):
        response = self.client.get('/warehouse/', {
            'view': 'assets',
            'from_type': '1',
            'item_type': str(self.item_type.id),
            'q': '汇总',
            'status': Asset.Status.IN_STOCK,
        })
        self.assertEqual(response.status_code, 200)
        params = parse_qs(response.context['asset_type_filter_query'])
        self.assertNotIn('item_type', params)
        self.assertEqual(params['q'], ['汇总'])
        self.assertEqual(params['status'], [Asset.Status.IN_STOCK])
        self.assertEqual(params['view'], ['types'])

    def test_explicit_type_filter_is_kept_on_type_view(self):
        response = self.client.get('/warehouse/', {
            'view': 'types',
            'item_type': str(self.item_type.id),
        })
        self.assertEqual(response.status_code, 200)
        params = parse_qs(response.context['asset_type_filter_query'])
        self.assertEqual(params['item_type'], [str(self.item_type.id)])


class WarehouseResetFilterTests(WarehouseTestFixture, TestCase):
    """搜索框旁「清空」的行为：清筛选但保留视图与每页条数。"""

    def setUp(self):
        self.admin = self.make_user('reset-admin', is_superuser=True)
        self.warehouse = self.make_warehouse(name='清空仓', code='RESET-WH')
        self.client.force_login(self.admin)

    def test_reset_link_drops_all_filters_but_keeps_view_and_page_size(self):
        response = self.client.get('/warehouse/', {
            'q': '机器人',
            'status': Asset.Status.IN_STOCK,
            'warehouse': str(self.warehouse.id),
            'sort': 'name',
            'dir': 'desc',
            'page_size': '200',
            'view': 'assets',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_active_filters'])
        params = parse_qs(response.context['asset_reset_filter_query'])
        for dropped in ('q', 'status', 'warehouse', 'sort', 'dir'):
            self.assertNotIn(dropped, params)
        self.assertEqual(params['tab'], ['assets'])
        self.assertEqual(params['view'], ['assets'])
        # 每页条数是显示偏好，不随筛选清空而重置。
        self.assertNotIn('page_size', params)

    def test_reset_keeps_stock_tab_and_omits_asset_view(self):
        response = self.client.get('/warehouse/', {'tab': 'stock', 'q': '螺丝'})
        self.assertEqual(response.status_code, 200)
        params = parse_qs(response.context['asset_reset_filter_query'])
        self.assertEqual(params['tab'], ['stock'])
        self.assertNotIn('view', params)
        self.assertNotIn('q', params)

    def test_reset_keeps_type_summary_view(self):
        response = self.client.get('/warehouse/', {'view': 'types', 'q': '设备'})
        self.assertEqual(response.status_code, 200)
        params = parse_qs(response.context['asset_reset_filter_query'])
        self.assertEqual(params['view'], ['types'])

    def test_page_size_alone_does_not_count_as_active_filter(self):
        """只设每页条数时清空按钮应为禁用态，否则按钮永远可点。"""
        response = self.client.get('/warehouse/', {'page_size': '200'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_active_filters'])
        self.assertContains(response, 'is-disabled')

    def test_no_filter_renders_disabled_reset(self):
        response = self.client.get('/warehouse/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_active_filters'])
        self.assertContains(response, '当前没有筛选条件')

    def test_panel_no_longer_has_duplicate_reset_link(self):
        """「清空条件」已移到搜索框旁，面板内不应再重复出现。"""
        response = self.client.get('/warehouse/', {'q': '机器人'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '清空条件')

    def test_metric_cards_keep_current_filters(self):
        """统计卡片不应静默清空筛选。"""
        response = self.client.get('/warehouse/', {'q': '机器人'})
        self.assertEqual(response.status_code, 200)
        asset_params = parse_qs(response.context['asset_filter_query'])
        self.assertEqual(asset_params['q'], ['机器人'])
