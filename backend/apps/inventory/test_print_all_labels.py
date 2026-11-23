"""二维码批量打印（按筛选跨页）回归（2026-11-23 新增）。

背景：台账分页后只能勾当前页，30 件以上要翻页重复操作。
新增 all_filtered=1 入口，按当前筛选条件一次打印全部，单次上限 500 张。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.inventory.models import Asset, Category, ItemType, Location, Warehouse

User = get_user_model()


class PrintAllFilteredTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name='打印仓', code='PR-WH')
        cls.other_warehouse = Warehouse.objects.create(name='其它仓', code='PR-WH2')
        category = Category.objects.create(name='打印分类', code='PR-CAT')
        cls.item_type = ItemType.objects.create(
            name='打印类型', code='PR-IT', category=category,
        )
        cls.printer = User.objects.create_user(
            username='pr_printer', password='Pw#2026aa',
        )
        cls.printer.groups.add(
            Group.objects.get_or_create(name='warehouse_label_print')[0]
        )
        cls.plain = User.objects.create_user(
            username='pr_plain', password='Pw#2026aa',
        )

        # 本仓 5 件
        for index in range(5):
            Asset.objects.create(
                name=f'本仓设备{index}', system_asset_no=f'AST-PR-{index:03d}',
                item_type=cls.item_type, warehouse=cls.warehouse,
                status=Asset.Status.IN_STOCK,
            )
        # 其它仓 3 件
        for index in range(3):
            Asset.objects.create(
                name=f'其它仓设备{index}', system_asset_no=f'AST-PR-OTH-{index}',
                item_type=cls.item_type, warehouse=cls.other_warehouse,
                status=Asset.Status.IN_STOCK,
            )

    def test_print_all_without_filter_includes_everything(self):
        self.client.force_login(self.printer)
        response = self.client.get('/warehouse/qr-labels/?all_filtered=1')
        self.assertEqual(response.status_code, 200)
        labels = response.context.get('labels') or []
        self.assertEqual(len(labels), 8, '不筛选时应包含全部 8 件')

    def test_print_all_respects_warehouse_filter(self):
        """warehouse 参数接受 UUID（不是名称），与台账页的筛选口径一致。"""
        self.client.force_login(self.printer)
        response = self.client.get(
            f'/warehouse/qr-labels/?all_filtered=1&warehouse={self.warehouse.id}'
        )
        self.assertEqual(response.status_code, 200)
        labels = response.context.get('labels') or []
        self.assertEqual(len(labels), 5, '按仓库筛选应只含本仓 5 件')

    def test_without_selection_and_without_flag_is_rejected(self):
        self.client.force_login(self.printer)
        response = self.client.get('/warehouse/qr-labels/')
        self.assertEqual(response.status_code, 302,
                         '既没勾选也没 all_filtered 时应重定向并提示')

    def test_plain_user_cannot_print(self):
        self.client.force_login(self.plain)
        response = self.client.get('/warehouse/qr-labels/?all_filtered=1')
        self.assertIn(response.status_code, (302, 403),
                      '无打印权限的账号应被拦')

    def test_empty_filter_result_is_handled(self):
        self.client.force_login(self.printer)
        response = self.client.get(
            '/warehouse/qr-labels/?all_filtered=1&warehouse=不存在的仓库'
        )
        self.assertEqual(response.status_code, 302,
                         '筛选无结果时应重定向并提示，而非报错')

    def test_button_rendered_on_inventory_page(self):
        self.client.force_login(self.printer)
        response = self.client.get('/warehouse/?tab=assets')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('all_filtered=1', body, '台账页应有「打印全部标签」入口')


class PrintAllCapTests(TestCase):
    """单次上限保护。"""

    @classmethod
    def setUpTestData(cls):
        warehouse = Warehouse.objects.create(name='上限仓', code='CAP-WH')
        category = Category.objects.create(name='上限分类', code='CAP-CAT')
        item_type = ItemType.objects.create(
            name='上限类型', code='CAP-IT', category=category,
        )
        Asset.objects.bulk_create([
            Asset(
                name=f'上限设备{index}', system_asset_no=f'AST-CAP-{index:04d}',
                item_type=item_type, warehouse=warehouse,
                status=Asset.Status.IN_STOCK,
            )
            for index in range(510)
        ])
        cls.printer = User.objects.create_user(
            username='cap_printer', password='Pw#2026aa',
        )
        cls.printer.groups.add(
            Group.objects.get_or_create(name='warehouse_label_print')[0]
        )

    def test_cap_limits_label_count(self):
        self.client.force_login(self.printer)
        response = self.client.get('/warehouse/qr-labels/?all_filtered=1')
        self.assertEqual(response.status_code, 200)
        labels = response.context.get('labels') or []
        self.assertEqual(len(labels), 500, '超过 500 件应截断到 500')
