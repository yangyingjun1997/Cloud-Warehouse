"""借用台账盲区识别回归（2026-11-23 新增）。

背景：线上 48 件资产状态为「已借出」，但 current_holder 与
external_holder_name 全为空——Excel 导入时只填状态、没填持有人。
现有催办逻辑要求 request__applicant == current_holder，因此永远匹配不到，
提醒恒为 0。这不是催办 bug，是数据缺失，但此前系统里没有任何提示。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from apps.inventory.borrow_health import (
    orphan_borrowed_assets,
    summarize_borrow_health,
)
from apps.inventory.models import Asset, Category, ItemType, Warehouse

User = get_user_model()


class BorrowHealthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        warehouse = Warehouse.objects.create(name='健康仓', code='BH-WH')
        category = Category.objects.create(name='健康分类', code='BH-CAT')
        cls.item_type = ItemType.objects.create(
            name='健康类型', code='BH-IT', category=category,
        )
        cls.warehouse = warehouse

        def make(code, status, holder=None, external=''):
            return Asset.objects.create(
                name=f'设备{code}', system_asset_no=code,
                item_type=cls.item_type, warehouse=warehouse, status=status,
                current_holder=holder, external_holder_name=external,
            )

        cls.holder = User.objects.create_user(
            username='bh_holder', password='Pw#2026aa',
        )
        # 有持有人（可催办）
        cls.tracked = make('AST-BH-1', Asset.Status.BORROWED, holder=cls.holder)
        # 外部持有人（可追溯但无账号）
        cls.external = make('AST-BH-2', Asset.Status.BORROWED, external='外部工程师')
        # 孤儿：借出但完全无持有人
        cls.orphan1 = make('AST-BH-3', Asset.Status.BORROWED)
        cls.orphan2 = make('AST-BH-4', Asset.Status.ISSUED)
        cls.orphan3 = make('AST-BH-5', Asset.Status.INTERNAL_BORROW)
        # 在库资产不该计入
        cls.in_stock = make('AST-BH-6', Asset.Status.IN_STOCK)

    def test_orphan_query_finds_only_holderless(self):
        codes = set(orphan_borrowed_assets().values_list('system_asset_no', flat=True))
        self.assertEqual(codes, {'AST-BH-3', 'AST-BH-4', 'AST-BH-5'})

    def test_tracked_asset_not_orphan(self):
        codes = set(orphan_borrowed_assets().values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-BH-1', codes)

    def test_external_holder_not_orphan(self):
        codes = set(orphan_borrowed_assets().values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-BH-2', codes,
                         '有外部持有人文本的资产可追溯，不算孤儿')

    def test_in_stock_not_counted(self):
        codes = set(orphan_borrowed_assets().values_list('system_asset_no', flat=True))
        self.assertNotIn('AST-BH-6', codes)

    def test_summary_counts(self):
        summary = summarize_borrow_health()
        self.assertEqual(summary['total'], 5, '在外资产共 5 件')
        self.assertEqual(summary['orphan'], 3, '孤儿 3 件')
        self.assertEqual(summary['tracked'], 2, '可追溯 2 件')
        self.assertAlmostEqual(summary['orphan_ratio'], 0.6, places=2)

    def test_summary_when_no_orphan(self):
        Asset.objects.filter(
            system_asset_no__in=['AST-BH-3', 'AST-BH-4', 'AST-BH-5'],
        ).update(current_holder=self.holder)
        summary = summarize_borrow_health()
        self.assertEqual(summary['orphan'], 0)
        self.assertEqual(summary['orphan_ratio'], 0.0)

    def test_summary_when_no_assets(self):
        Asset.objects.all().delete()
        summary = summarize_borrow_health()
        self.assertEqual(summary['total'], 0)
        self.assertEqual(summary['orphan'], 0)
        self.assertEqual(summary['orphan_ratio'], 0.0)


class ReminderCommandOrphanReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        warehouse = Warehouse.objects.create(name='命令仓', code='CMD-WH')
        category = Category.objects.create(name='命令分类', code='CMD-CAT')
        item_type = ItemType.objects.create(
            name='命令类型', code='CMD-IT', category=category,
        )
        Asset.objects.create(
            name='孤儿设备', system_asset_no='AST-CMD-1',
            item_type=item_type, warehouse=warehouse,
            status=Asset.Status.BORROWED,
        )

    def test_command_reports_orphan(self):
        out = StringIO()
        call_command('send_return_due_reminders', stdout=out)
        output = out.getvalue()
        self.assertIn('盲区', output, '命令应提示借用台账盲区')
        self.assertIn('1 件', output)

    def test_command_show_orphans_lists_assets(self):
        out = StringIO()
        call_command('send_return_due_reminders', '--show-orphans', stdout=out)
        output = out.getvalue()
        self.assertIn('AST-CMD-1', output, '--show-orphans 应列出具体资产')

    def test_command_reports_healthy_when_no_orphan(self):
        holder = User.objects.create_user(
            username='cmd_holder', password='Pw#2026aa',
        )
        Asset.objects.filter(system_asset_no='AST-CMD-1').update(current_holder=holder)
        out = StringIO()
        call_command('send_return_due_reminders', stdout=out)
        output = out.getvalue()
        self.assertIn('借用台账完整', output)
