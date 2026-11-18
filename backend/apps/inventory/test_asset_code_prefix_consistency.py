"""P2-01 回归：资产编码前缀规则必须页面与批量导入一致（SC/DZSB/ZSB/QTZC）。

历史坑：V2 导入曾在本地写死 `(?:SC|DZSB)-`，把 ZSB/QTZC 判为非法，
而页面维护接受四种前缀，导致同一张源表「页面能存、导入被拒」。
"""

from django.test import SimpleTestCase

from apps.inventory.code_services import (
    admin_assigned_code_error,
    is_valid_admin_assigned_code,
    is_valid_warehouse_assigned_code,
    warehouse_assigned_code_error,
)


class WarehouseAssignedCodeTests(SimpleTestCase):
    """源表既有编码：四种前缀都应接受。"""

    def test_all_four_prefixes_accepted(self):
        for code in (
            'SC-G1-YS-0031',
            'DZSB-D7-31-0002-600001',
            'ZSB-001',
            'QTZC-A1',
        ):
            with self.subTest(code=code):
                self.assertTrue(
                    is_valid_warehouse_assigned_code(code),
                    f'{code} 应被接受（页面维护允许这四种前缀）',
                )

    def test_single_segment_codes_accepted(self):
        for code in ('SC-001', 'DZSB-001', 'ZSB-001', 'QTZC-001'):
            with self.subTest(code=code):
                self.assertTrue(is_valid_warehouse_assigned_code(code))

    def test_invalid_prefixes_rejected(self):
        for code in ('AST-001', 'XX-001', 'ABC123', '', 'SC_001'):
            with self.subTest(code=code):
                self.assertFalse(is_valid_warehouse_assigned_code(code))

    def test_lowercase_is_normalized(self):
        """正常化会转大写，小写输入应通过。"""
        self.assertTrue(is_valid_warehouse_assigned_code('sc-g1-ys-0031'))

    def test_error_message_lists_all_prefixes(self):
        message = warehouse_assigned_code_error()
        for prefix in ('SC-', 'DZSB-', 'ZSB-', 'QTZC-'):
            self.assertIn(prefix, message)


class AdminAssignedCodeTests(SimpleTestCase):
    """行政/ERP 编码：只允许 DZSB/ZSB/QTZC，SC 是仓库自编号不算行政码。"""

    def test_admin_prefixes_accepted(self):
        for code in ('DZSB-D7-31-0002-600001', 'ZSB-001', 'QTZC-A1'):
            with self.subTest(code=code):
                self.assertTrue(is_valid_admin_assigned_code(code))

    def test_sc_rejected_for_admin(self):
        self.assertFalse(
            is_valid_admin_assigned_code('SC-G1-YS-0031'),
            'SC 前缀属于仓库自编号，不应被当成行政/ERP 编码',
        )

    def test_invalid_rejected(self):
        for code in ('AST-001', 'XX-1', '', None):
            with self.subTest(code=code):
                self.assertFalse(is_valid_admin_assigned_code(code))

    def test_error_message(self):
        message = admin_assigned_code_error()
        for prefix in ('DZSB-', 'ZSB-', 'QTZC-'):
            self.assertIn(prefix, message)


class PrefixConsistencyTests(SimpleTestCase):
    """页面维护与 V2 导入必须用同一套判断，不能再各写一份正则。"""

    def test_v2_import_uses_shared_helper(self):
        import inspect

        from apps.inventory import v2_import_services

        source = inspect.getsource(v2_import_services.validate_asset_v2_records)
        self.assertIn('is_valid_warehouse_assigned_code', source)
        self.assertIn('is_valid_admin_assigned_code', source)
        # 不应再出现写死的前缀正则
        self.assertNotIn("(?:SC|DZSB)-", source)
