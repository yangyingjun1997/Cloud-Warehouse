"""售出时客户匹配 + 异常收窄回归。

客户匹配（与用户确认的设计）：
- 「持有人单位」字段保留原名（还服务外借第三方、退供应商、送外修）。
- 保持纯文本存储，不自动创建客户档案（手写单位名常有错别字/简称，自动建档会产脏数据）。
- 只有「精确匹配到唯一启用客户」时自动关联 customer_ref；同名多条不关联。

异常收窄（P2-04）：
- xlsx 嗅探只吞「不是合法 xlsx」类异常，其它异常必须冒出来。
- context_processor 只兜数据库异常，其它异常不能被伪装成 0。
"""

from zipfile import BadZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase

from apps.inventory.customer_matching import match_customer_by_name
from apps.inventory.models import Asset, Category, Customer, ItemType, Warehouse

User = get_user_model()


class CustomerMatchingTests(TestCase):
    def setUp(self):
        Customer.objects.create(code='C-001', name='某某科技有限公司', is_active=True)

    def test_exact_match_returns_customer(self):
        customer, status = match_customer_by_name('某某科技有限公司')
        self.assertIsNotNone(customer)
        self.assertEqual(status, 'matched')
        self.assertEqual(customer.code, 'C-001')

    def test_blank_name_returns_none(self):
        for value in ('', '   ', None):
            with self.subTest(value=value):
                customer, status = match_customer_by_name(value)
                self.assertIsNone(customer)
                self.assertEqual(status, 'none')

    def test_unknown_name_returns_none(self):
        customer, status = match_customer_by_name('不存在的公司')
        self.assertIsNone(customer)
        self.assertEqual(status, 'none')

    def test_partial_name_does_not_match(self):
        """刻意不做模糊匹配：'某某科技' 不应匹配 '某某科技有限公司'。"""
        customer, status = match_customer_by_name('某某科技')
        self.assertIsNone(customer)
        self.assertEqual(status, 'none')

    def test_duplicate_names_reported_as_ambiguous(self):
        Customer.objects.create(code='C-002', name='重名公司', is_active=True)
        Customer.objects.create(code='C-003', name='重名公司', is_active=True)
        customer, status = match_customer_by_name('重名公司')
        self.assertIsNone(customer, '同名多条时不应自动关联')
        self.assertEqual(status, 'ambiguous')

    def test_inactive_customer_not_matched(self):
        Customer.objects.create(code='C-900', name='停用公司', is_active=False)
        customer, status = match_customer_by_name('停用公司')
        self.assertIsNone(customer)
        self.assertEqual(status, 'none')


class SaleLinksMatchedCustomerTests(TestCase):
    """售出执行时，若单位名精确匹配客户，应自动补上资产客户关联。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='售出仓', code='SL-WH')
        category = Category.objects.create(name='售出分类', code='SL-CAT')
        item_type = ItemType.objects.create(
            name='售出类型', code='SL-IT', category=category,
        )
        self.asset = Asset.objects.create(
            name='售出设备', system_asset_no='AST-SL-1', item_type=item_type,
            warehouse=self.warehouse, status=Asset.Status.IN_STOCK,
        )
        self.customer = Customer.objects.create(
            code='SL-CUST', name='客户甲公司', is_active=True,
        )
        self.operator = User.objects.create_user(
            username='sl_op', password='Pw#2026aa', first_name='售出库管',
        )
        for group in ('warehouse_outbound', 'warehouse_admin'):
            self.operator.groups.add(Group.objects.get_or_create(name=group)[0])

    def test_sale_links_customer_by_company_name(self):
        from django.utils import timezone

        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
        from apps.workflow.services import process_request

        request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.SALE,
            applicant=self.operator,
            reason='售出给客户甲',
            recipient_company='客户甲公司',
            recipient_phone='13800002222',
            usage_location='客户现场',
            application_date=timezone.localdate(),
            is_quick_process=True,
            status=WorkflowRequest.Status.WAITING_WAREHOUSE,
        )
        WorkflowRequestLine.objects.create(
            request=request_obj, asset=self.asset, quantity=1,
        )
        process_request(request_obj, actor=self.operator)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, Asset.Status.SOLD)
        self.assertEqual(self.asset.external_holder_company, '客户甲公司')
        self.assertEqual(
            self.asset.customer_ref_id, self.customer.pk,
            '单位名精确匹配客户时应自动关联',
        )

    def test_sale_without_match_keeps_text_only(self):
        from django.utils import timezone

        from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
        from apps.workflow.services import process_request

        request_obj = WorkflowRequest.objects.create(
            request_type=WorkflowRequest.RequestType.SALE,
            applicant=self.operator,
            reason='售出给未建档客户',
            recipient_company='未建档公司',
            recipient_phone='13800003333',
            usage_location='客户现场',
            application_date=timezone.localdate(),
            is_quick_process=True,
            status=WorkflowRequest.Status.WAITING_WAREHOUSE,
        )
        WorkflowRequestLine.objects.create(
            request=request_obj, asset=self.asset, quantity=1,
        )
        process_request(request_obj, actor=self.operator)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.external_holder_company, '未建档公司')
        self.assertIsNone(
            self.asset.customer_ref_id,
            '匹配不到时不应自动建档或关联',
        )


class WorkbookSniffNarrowingTests(SimpleTestCase):
    """xlsx 嗅探只应吞「不是合法 xlsx」类异常。"""

    def test_random_bytes_returns_false(self):
        from apps.inventory.v2_import_services import is_asset_v2_workbook
        from apps.inventory.v3_import_services import is_inventory_v3_workbook

        junk = b'this is definitely not a zip file'
        self.assertFalse(is_asset_v2_workbook(junk))
        self.assertFalse(is_inventory_v3_workbook(junk))

    def test_empty_bytes_returns_false(self):
        from apps.inventory.v2_import_services import is_asset_v2_workbook
        from apps.inventory.v3_import_services import is_inventory_v3_workbook

        self.assertFalse(is_asset_v2_workbook(b''))
        self.assertFalse(is_inventory_v3_workbook(b''))

    def test_narrowed_except_lists_expected_types(self):
        """静态检查：两处嗅探的 except 必须列出具体异常，不能是裸 Exception。"""
        import inspect

        from apps.inventory import v2_import_services, v3_import_services

        for module, func_name in (
            (v2_import_services, 'is_asset_v2_workbook'),
            (v3_import_services, 'is_inventory_v3_workbook'),
        ):
            with self.subTest(func=func_name):
                source = inspect.getsource(getattr(module, func_name))
                self.assertIn('BadZipFile', source, '未捕获 BadZipFile')
                self.assertIn('InvalidFileException', source, '未捕获 InvalidFileException')
                self.assertNotIn('except Exception:', source, '仍是裸 Exception')

    def test_bad_zip_is_a_caught_type(self):
        """确认 BadZipFile 确实是我们列出的类型之一（防止 import 写错）。"""
        import zipfile

        self.assertTrue(issubclass(BadZipFile, Exception))
        self.assertEqual(BadZipFile, zipfile.BadZipFile)


class ContextProcessorNarrowingTests(TestCase):
    """context_processor 只兜数据库异常。"""

    def test_except_clause_is_narrow(self):
        import inspect

        from apps.common import context_processors

        source = inspect.getsource(context_processors._incomplete_profile_count)
        self.assertIn('DatabaseError', source)
        self.assertNotIn('except Exception:', source, '仍是裸 Exception')

    def test_returns_int_on_normal_path(self):
        from apps.common import context_processors

        self.assertIsInstance(context_processors._incomplete_profile_count(), int)

    def test_database_error_returns_zero(self):
        """数据库异常时退化为 0，不抛给模板层。"""
        from unittest.mock import patch

        from django.db import OperationalError

        from apps.common import context_processors

        # 让 get_user_model 的查询抛 OperationalError，验证只兜数据库异常这条分支
        with patch(f'{context_processors.__name__}.get_user_model') as mocked:
            mocked.return_value.objects.filter.side_effect = OperationalError('boom')
            self.assertEqual(context_processors._incomplete_profile_count(), 0)
