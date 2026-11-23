"""资产持有人批量补录回归（2026-11-23 新增）。

背景：线上 636 件资产中 261 件在外但无任何持有人信息，都无法催办与追责。
现有 V3 导入只支持耗材更新，资产没有更新通道，261 件只能逐件手改。

约束（遵守 AGENTS.md）：
- 只按 system_asset_no 匹配，不用 SN/名称/位置猜测身份
- 只改持有人相关字段，不碰状态/仓库/库位/金额
- 每行独立事务，失败不影响其他行，逐行写审计
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import Department
from apps.inventory.holder_import import (
    HolderImportError,
    apply_holder_rows,
    parse_holder_csv,
)
from apps.inventory.models import Asset, Category, ItemType, Warehouse

User = get_user_model()


class ParseHolderCsvTests(TestCase):
    def test_standard_headers(self):
        rows, errors = parse_holder_csv(
            '系统编号,持有人姓名,部门\nAST-000001,张三,研发部\n'
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['system_asset_no'], 'AST-000001')
        self.assertEqual(rows[0]['holder_name'], '张三')

    def test_header_aliases(self):
        rows, errors = parse_holder_csv(
            'AST,使用人,所属部门\nAST-000002,李四,销售部\n'
        )
        self.assertEqual(len(rows), 1, '应识别别名表头')
        self.assertEqual(rows[0]['holder_name'], '李四')

    def test_bom_and_blank_lines_ignored(self):
        rows, errors = parse_holder_csv(
            '\ufeff系统编号,持有人姓名\n\nAST-000003,王五\n\n'
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, [])

    def test_missing_required_column(self):
        with self.assertRaises(HolderImportError):
            parse_holder_csv('姓名,部门\n张三,研发部\n')

    def test_empty_file(self):
        with self.assertRaises(HolderImportError):
            parse_holder_csv('')

    def test_row_without_holder_reported(self):
        rows, errors = parse_holder_csv('系统编号,持有人姓名\nAST-000004,\n')
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn('未填写任何持有人信息', errors[0][1])

    def test_row_without_code_reported(self):
        rows, errors = parse_holder_csv('系统编号,持有人姓名\n,张三\n')
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(errors), 1)
        self.assertIn('系统编号为空', errors[0][1])

    def test_gbk_encoding(self):
        content = '系统编号,持有人姓名\nAST-000005,赵六\n'.encode('gbk')
        rows, errors = parse_holder_csv(content)
        self.assertEqual(len(rows), 1, '应兼容 Excel 常见的 GBK 编码')
        self.assertEqual(rows[0]['holder_name'], '赵六')


class ApplyHolderRowsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name='补录仓', code='HI-WH')
        category = Category.objects.create(name='补录分类', code='HI-CAT')
        cls.item_type = ItemType.objects.create(
            name='补录类型', code='HI-IT', category=category,
        )
        cls.department = Department.objects.create(name='研发部')
        cls.holder = User.objects.create_user(
            username='hi_holder', password='Pw#2026aa',
        )
        cls.holder2 = User.objects.create_user(
            username='hi_holder2', password='Pw#2026aa',
            first_name='李四',
        )
        cls.actor = User.objects.create_user(
            username='hi_actor', password='Pw#2026aa', is_superuser=True,
        )
        cls.asset = Asset.objects.create(
            name='孤儿设备', system_asset_no='AST-HI-001',
            item_type=cls.item_type, warehouse=cls.warehouse,
            status=Asset.Status.BORROWED,
        )

    def _rows(self, csv_text):
        rows, errors = parse_holder_csv(csv_text)
        self.assertEqual(errors, [])
        return rows

    def test_apply_by_username(self):
        rows = self._rows('系统编号,持有人账号\nAST-HI-001,hi_holder\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['failed'], [])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_holder_id, self.holder.id)

    def test_apply_by_external_holder(self):
        rows = self._rows('系统编号,外部持有人\nAST-HI-001,厂外工程师\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.external_holder_name, '厂外工程师')
        self.assertIsNone(self.asset.current_holder_id)

    def test_apply_by_unique_name(self):
        rows = self._rows('系统编号,持有人姓名\nAST-HI-001,李四\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_holder_id, self.holder2.id)

    def test_ambiguous_name_rejected(self):
        User.objects.create_user(
            username='hi_dup', password='Pw#2026aa', first_name='李四',
        )
        rows = self._rows('系统编号,持有人姓名\nAST-HI-001,李四\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 0)
        self.assertEqual(len(result['failed']), 1)
        self.assertIn('多个账号', result['failed'][0][1])

    def test_unknown_name_rejected(self):
        rows = self._rows('系统编号,持有人姓名\nAST-HI-001,不存在的人\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 0)
        self.assertIn('没有匹配到账号', result['failed'][0][1])

    def test_unknown_asset_rejected(self):
        rows = self._rows('系统编号,持有人账号\nAST-NOT-EXIST,hi_holder\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 0)
        self.assertIn('没有编号为', result['failed'][0][1])

    def test_unknown_department_rejected(self):
        rows = self._rows('系统编号,持有人账号,部门\nAST-HI-001,hi_holder,不存在部门\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 0)
        self.assertIn('不存在', result['failed'][0][1])

    def test_department_applied(self):
        rows = self._rows('系统编号,持有人账号,部门\nAST-HI-001,hi_holder,研发部\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 1)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.department_id, self.department.id)

    def test_dry_run_does_not_write(self):
        rows = self._rows('系统编号,持有人账号\nAST-HI-001,hi_holder\n')
        result = apply_holder_rows(rows, actor=self.actor, dry_run=True)
        self.assertEqual(result['updated'], 1)
        self.assertTrue(result['dry_run'])
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.current_holder_id, '预览模式不得写入')

    def test_no_change_is_skipped(self):
        Asset.objects.filter(pk=self.asset.pk).update(current_holder=self.holder)
        rows = self._rows('系统编号,持有人账号\nAST-HI-001,hi_holder\n')
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 0)
        self.assertEqual(result['skipped'], 1)

    def test_does_not_touch_status_or_location(self):
        """只补持有人，不得改状态、仓库、库位。"""
        original_status = self.asset.status
        original_warehouse = self.asset.warehouse_id
        rows = self._rows('系统编号,持有人账号\nAST-HI-001,hi_holder\n')
        apply_holder_rows(rows, actor=self.actor)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, original_status)
        self.assertEqual(self.asset.warehouse_id, original_warehouse)

    def test_partial_failure_does_not_block_others(self):
        Asset.objects.create(
            name='第二件', system_asset_no='AST-HI-002',
            item_type=self.item_type, warehouse=self.warehouse,
            status=Asset.Status.BORROWED,
        )
        rows = self._rows(
            '系统编号,持有人账号\n'
            'AST-HI-001,hi_holder\n'
            'AST-NOT-EXIST,hi_holder\n'
            'AST-HI-002,hi_holder2\n'
        )
        result = apply_holder_rows(rows, actor=self.actor)
        self.assertEqual(result['updated'], 2, '两行成功')
        self.assertEqual(len(result['failed']), 1, '一行失败')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_holder_id, self.holder.id)

    def test_audit_written(self):
        from apps.common.models import OperationAuditLog

        before = OperationAuditLog.objects.count()
        rows = self._rows('系统编号,持有人账号\nAST-HI-001,hi_holder\n')
        apply_holder_rows(rows, actor=self.actor)
        self.assertGreater(OperationAuditLog.objects.count(), before,
                           '补录持有人必须写审计')
