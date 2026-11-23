"""P2-04 回归：供应山价格历史与采购汇总必须分页，且单组条目有上限。

背景：两个报表都是「按供应商（×物品）聚合」。分组本身行数有限，
但 supplier_price_history 的每组 entries 会持有该组合的全部采购明细，
未分页时一次请求把所有明细对象拉进内存并渲染，数据量大时明显变慢。
修复：分组结果分页 + 每组最多展示 20 条条目（完整历史走导出）。
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.workflow.models import (
    PurchaseOrder, PurchaseOrderLine, PurchaseRequest, PurchaseRequestLine,
)
from apps.inventory.models import Supplier

User = get_user_model()


class SupplierReportPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='p204_super', password='Pw#2026aa', is_superuser=True,
        )
        cls.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        cls.admin.groups.add(Group.objects.get_or_create(name='warehouse_reports')[0])
        cls.admin.groups.add(Group.objects.get_or_create(name='warehouse_finance')[0])

        cls.supplier = Supplier.objects.create(code='P204-SUP', name='分页供应商')
        # 采购明细有 CHECK 约束 purchase_line_has_source：必须关联 item_type 或 stock_item
        from apps.inventory.models import Category, ItemType

        category = Category.objects.create(name='分页分类', code='P204-CAT')
        cls.item_type = ItemType.objects.create(
            name='分页物品类型', code='P204-IT', category=category,
        )
        cls.request = PurchaseRequest.objects.create(
            purchase_no='PR-P204-1', applicant=cls.admin,
            status=PurchaseRequest.Status.APPROVED,
        )
        cls.order = PurchaseOrder.objects.create(
            order_no='PO-P204-1', purchase_request=cls.request,
            created_by=cls.admin, status=PurchaseOrder.Status.ORDERED,
            order_date=date(2026, 11, 1),
        )

        # 造 3 个物品 × 25 条价格记录 = 75 条明细，确保单组超过 20 条。
        # 注意 purchase_request_line 是 OneToOne：每条订单明细需要独立的申请明细。
        for item_index in range(3):
            for entry_index in range(25):
                request_line = PurchaseRequestLine.objects.create(
                    purchase_request=cls.request,
                    item_type=cls.item_type,
                    name_snapshot=f'物品{item_index}',
                    code_snapshot=f'CODE-{item_index}',
                    quantity=1,
                    unit='件',
                )
                PurchaseOrderLine.objects.create(
                    order=cls.order,
                    purchase_request_line=request_line,
                    item_type=cls.item_type,
                    name_snapshot=f'物品{item_index}',
                    code_snapshot=f'CODE-{item_index}',
                    supplier_snapshot=cls.supplier.name,
                    ordered_quantity=1,
                    received_quantity=0,
                    unit_cost=Decimal('100.00') + entry_index,
                    unit='件',
                    management_mode='耗材',
                )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_price_history_page_renders(self):
        response = self.client.get('/reports/supplier-prices/')
        self.assertEqual(response.status_code, 200)

    def test_page_size_whitelist(self):
        """非法 page_size 回落 50，不报错。"""
        for value in ('20', '50', '100', '200', '999', 'abc', '-5'):
            with self.subTest(page_size=value):
                response = self.client.get(f'/reports/supplier-prices/?page_size={value}')
                self.assertEqual(response.status_code, 200)

    def test_invalid_page_falls_back_safely(self):
        for value in ('abc', '-1', '99999'):
            with self.subTest(page=value):
                response = self.client.get(f'/reports/supplier-prices/?page={value}')
                self.assertEqual(response.status_code, 200)

    def test_entries_limited_per_group(self):
        """单组最多展示 20 条，超出部分标记截断。"""
        response = self.client.get('/reports/supplier-prices/?page_size=200')
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertTrue(page_obj.object_list, '应有分组数据')
        for row in page_obj.object_list:
            with self.subTest(supplier=row['supplier'], name=row['name']):
                self.assertLessEqual(len(row['entries_shown']), 20,
                                     '单组展示条目不应超过 20')
                if row['count'] > 20:
                    self.assertTrue(row['entries_truncated'],
                                    '超过 20 条时应标记为已截断')

    def test_export_still_returns_full_data(self):
        """导出不受分页影响，仍输出全部明细。"""
        response = self.client.get('/reports/supplier-prices.csv')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8-sig')
        data_rows = [line for line in body.splitlines() if line.strip()][1:]
        self.assertEqual(len(data_rows), 75,
                         f'导出应包含全部 75 条明细，实际 {len(data_rows)}')

    def test_reconciliation_page_renders(self):
        response = self.client.get('/reports/supplier-reconciliation/')
        self.assertEqual(response.status_code, 200)
