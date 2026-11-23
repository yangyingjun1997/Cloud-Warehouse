"""资产质保到期提醒回归（2026-11-23 新增功能）。

背景：636 件资产带 warranty_until，但系统此前没有任何「即将过保」入口，
到期日只是静态列。设备过保后维修费用从供应商承担变为自行承担。
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import Asset, Category, ItemType, Warehouse
from apps.inventory.warranty_services import (
    DEFAULT_WARNING_DAYS,
    WARRANTY_WARNING_DAYS,
    days_left,
    warranty_expiring_queryset,
    warranty_summary,
)

User = get_user_model()


class WarrantyServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        warehouse = Warehouse.objects.create(name='质保仓', code='WR-WH')
        category = Category.objects.create(name='质保分类', code='WR-CAT')
        item_type = ItemType.objects.create(
            name='质保类型', code='WR-IT', category=category,
        )
        cls.warehouse = warehouse
        cls.item_type = item_type

        def make(code, days_from_today, status=Asset.Status.IN_STOCK):
            return Asset.objects.create(
                name=f'设备{code}', system_asset_no=code,
                item_type=item_type, warehouse=warehouse, status=status,
                warranty_until=cls.today + timedelta(days=days_from_today),
            )

        cls.expired_10 = make('AST-WR-1', -10)
        cls.expired_100 = make('AST-WR-2', -100)
        cls.soon_5 = make('AST-WR-3', 5)
        cls.soon_45 = make('AST-WR-4', 45)
        cls.far_200 = make('AST-WR-5', 200)
        cls.no_warranty = Asset.objects.create(
            name='无质保', system_asset_no='AST-WR-6',
            item_type=item_type, warehouse=warehouse,
            status=Asset.Status.IN_STOCK,
        )
        # 已报废的不应出现在提醒里
        cls.scrapped = make('AST-WR-7', 10, status=Asset.Status.SCRAPPED)
        # 已售出的也不应出现
        cls.sold = make('AST-WR-8', 10, status=Asset.Status.SOLD)

    def test_default_window(self):
        self.assertEqual(DEFAULT_WARNING_DAYS, 60)
        self.assertIn(60, WARRANTY_WARNING_DAYS)

    def test_expired_included(self):
        codes = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertIn('AST-WR-1', codes, '已过保 10 天应出现')
        self.assertIn('AST-WR-2', codes, '已过保 100 天也应出现（已过保一律提醒）')

    def test_within_window_included(self):
        codes = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertIn('AST-WR-3', codes, '5 天内到期应出现')
        self.assertIn('AST-WR-4', codes, '45 天内到期应出现')

    def test_beyond_window_excluded(self):
        codes = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-WR-5', codes, '200 天后到期不应出现')

    def test_no_warranty_excluded(self):
        codes = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-WR-6', codes, '未填质保日不应出现')

    def test_terminal_status_excluded(self):
        codes = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-WR-7', codes, '已报废不应提醒质保')
        self.assertNotIn('AST-WR-8', codes, '已售出不应提醒质保')

    def test_window_parameter_respected(self):
        codes_30 = set(warranty_expiring_queryset(30).values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-WR-4', codes_30, '45 天到期在 30 天窗口外')
        codes_90 = set(warranty_expiring_queryset(90).values_list('system_asset_no', flat=True))
        self.assertIn('AST-WR-4', codes_90)

    def test_invalid_window_falls_back(self):
        codes = set(warranty_expiring_queryset(7).values_list('system_asset_no', flat=True))
        codes_default = set(warranty_expiring_queryset(60).values_list('system_asset_no', flat=True))
        self.assertEqual(codes, codes_default, '非法窗口应回落到默认值')

    def test_days_left(self):
        self.assertEqual(days_left(self.soon_5), 5)
        self.assertEqual(days_left(self.expired_10), -10)
        self.assertIsNone(days_left(self.no_warranty))

    def test_summary_counts(self):
        summary = warranty_summary(60)
        self.assertEqual(summary['expired'], 2, '应统计 2 件已过保')
        self.assertEqual(summary['expiring'], 2, '应统计 2 件即将到期')


class WarrantyPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        warehouse = Warehouse.objects.create(name='质保页仓', code='WP-WH')
        category = Category.objects.create(name='质保页分类', code='WP-CAT')
        item_type = ItemType.objects.create(
            name='质保页类型', code='WP-IT', category=category,
        )
        today = timezone.localdate()
        Asset.objects.create(
            name='即将过保设备', system_asset_no='AST-WP-1',
            item_type=item_type, warehouse=warehouse,
            warranty_until=today + timedelta(days=20),
        )
        Asset.objects.create(
            name='已过保设备', system_asset_no='AST-WP-2',
            item_type=item_type, warehouse=warehouse,
            warranty_until=today - timedelta(days=15),
        )
        cls.reporter = User.objects.create_user(
            username='wp_reporter', password='Pw#2026aa',
        )
        cls.reporter.groups.add(Group.objects.get_or_create(name='warehouse_reports')[0])
        cls.plain = User.objects.create_user(
            username='wp_plain', password='Pw#2026aa',
        )

    def test_page_renders_for_report_role(self):
        self.client.force_login(self.reporter)
        response = self.client.get('/warehouse/warranty/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('即将过保设备', body)
        self.assertIn('已过保设备', body)

    def test_page_rejects_plain_user(self):
        self.client.force_login(self.plain)
        response = self.client.get('/warehouse/warranty/')
        self.assertIn(response.status_code, (302, 403),
                      '无报表权限的账号应被拦')

    def test_window_query_param(self):
        self.client.force_login(self.reporter)
        response = self.client.get('/warehouse/warranty/?days=90')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['days'], 90)

    def test_invalid_window_query_param(self):
        self.client.force_login(self.reporter)
        response = self.client.get('/warehouse/warranty/?days=7')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['days'], DEFAULT_WARNING_DAYS)

    def test_home_shows_warranty_card_for_reporter(self):
        self.client.force_login(self.reporter)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('warranty_expiring_count', response.context)
