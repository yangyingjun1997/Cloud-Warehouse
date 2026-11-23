"""import_asset_holders 命令回归（2026-11-23 新增）。"""

import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.inventory.models import Asset, Category, ItemType, Warehouse

User = get_user_model()


class ImportAssetHoldersCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name='命令补录仓', code='CI-WH')
        category = Category.objects.create(name='命令补录分类', code='CI-CAT')
        cls.item_type = ItemType.objects.create(
            name='命令补录类型', code='CI-IT', category=category,
        )
        cls.holder = User.objects.create_user(
            username='ci_holder', password='Pw#2026aa',
        )
        cls.admin = User.objects.create_user(
            username='ci_admin', password='Pw#2026aa', is_superuser=True,
        )
        cls.asset = Asset.objects.create(
            name='命令孤儿', system_asset_no='AST-CI-001',
            item_type=cls.item_type, warehouse=cls.warehouse,
            status=Asset.Status.BORROWED,
        )

    def _write_csv(self, content):
        handle = tempfile.NamedTemporaryFile(
            mode='w', suffix='.csv', delete=False, encoding='utf-8',
        )
        handle.write(content)
        handle.close()
        self.addCleanup(lambda: os.unlink(handle.name))
        return handle.name

    def test_dry_run_does_not_write(self):
        path = self._write_csv('系统编号,持有人账号\nAST-CI-001,ci_holder\n')
        call_command('import_asset_holders', path, '--dry-run', '--actor', 'ci_admin')
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.current_holder_id, '预览模式不得写入')

    def test_real_run_writes(self):
        path = self._write_csv('系统编号,持有人账号\nAST-CI-001,ci_holder\n')
        call_command('import_asset_holders', path, '--actor', 'ci_admin')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_holder_id, self.holder.id)

    def test_missing_file_raises(self):
        with self.assertRaises(CommandError):
            call_command('import_asset_holders', 'C:/not-exist-file.csv',
                         '--actor', 'ci_admin')

    def test_missing_required_column_raises(self):
        path = self._write_csv('姓名\n张三\n')
        with self.assertRaises(CommandError):
            call_command('import_asset_holders', path, '--actor', 'ci_admin')

    def test_non_admin_actor_rejected(self):
        path = self._write_csv('系统编号,持有人账号\nAST-CI-001,ci_holder\n')
        with self.assertRaises(CommandError):
            call_command('import_asset_holders', path, '--actor', 'ci_holder')
