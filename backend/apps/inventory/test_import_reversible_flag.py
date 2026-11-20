"""P2-07 回归：V2 导入不可撤销的显式标记与提示。

背景：V2 导入不写 ImportMutation 账本，因此 rollback_import_batch 无法撤销。
模板原有的撤销区块条件要求 mutations.exists，所以 V2 批次本来就不会显示撤销按钮——
用户不会点到才报错。但页面没有任何说明「为什么没有撤销入口」，容易困惑。

修复：导入时在 batch.summary 写 reversible 标记，页面据此给出明确提示。
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from apps.inventory.models import ImportBatch

TEMPLATE = Path(settings.BASE_DIR) / 'templates' / 'inventory_import.html'


class ReversibleFlagTests(SimpleTestCase):
    """标记语义：V2 不可撤销、V3 可撤销。"""

    def test_v2_marks_not_reversible(self):
        import inspect

        from apps.inventory import v2_import_services

        source = inspect.getsource(v2_import_services.apply_asset_v2_import)
        self.assertIn("'reversible': False", source,
                      'V2 导入必须显式标记 reversible=False')

    def test_v3_marks_reversible(self):
        import inspect

        from apps.inventory import v3_import_services

        source = inspect.getsource(v3_import_services.apply_inventory_v3_import)
        self.assertIn("'reversible': True", source,
                      'V3 导入必须显式标记 reversible=True')

    def test_v2_does_not_write_mutations(self):
        """V2 从不调用 record_import_mutation（这是标记 False 的依据）。"""
        from apps.inventory import v2_import_services

        source = Path(v2_import_services.__file__).read_text(encoding='utf-8')
        self.assertNotIn('record_import_mutation(', source,
                         'V2 不应写 mutation 账本')
        self.assertNotIn('snapshot_instance(', source,
                         'V2 不应做快照')

    def test_v3_writes_mutations(self):
        from apps.inventory import v3_import_services

        source = Path(v3_import_services.__file__).read_text(encoding='utf-8')
        self.assertIn('record_import_mutation', source,
                      'V3 应有 mutation 账本，才能支持撤销')


class NonReversibleNoticeTemplateTests(SimpleTestCase):
    """页面提示：V2 批次应显示不可撤销说明。"""

    def _template(self):
        return TEMPLATE.read_text(encoding='utf-8')

    def test_template_checks_reversible_flag(self):
        self.assertIn('summary.reversible', self._template(),
                      '模板未读取 reversible 标记')

    def test_template_shows_warning_text(self):
        text = self._template()
        self.assertIn('不支持自动撤销', text, '缺少不可撤销的用户提示')
        self.assertIn('V3 模板', text, '提示中应引导用户改用 V3 模板')

    def test_rollback_block_still_requires_mutations(self):
        """撤销入口本身仍要求 mutations 存在，不能因为加了提示就放开。"""
        self.assertIn('selected_batch.mutations.exists', self._template())


class RollbackRejectsEmptyMutationsTests(TestCase):
    """服务层：无 mutations 的批次必须被明确拒绝。"""

    def test_rollback_raises_without_mutations(self):
        from django.contrib.auth import get_user_model

        from apps.inventory.import_recovery_services import (
            ImportRecoveryError,
            rollback_import_batch,
        )

        user = get_user_model().objects.create_user(
            username='p207_super', password='Pw#2026aa', is_superuser=True,
        )
        batch = ImportBatch.objects.create(
            source_filename='v2-import.xlsx',
            source_sha256='0' * 64,
            mode=ImportBatch.Mode.INCREMENTAL,
            status=ImportBatch.Status.IMPORTED,
            summary={'template_version': 2, 'reversible': False},
            created_by=user,
        )
        with self.assertRaises(ImportRecoveryError) as ctx:
            rollback_import_batch(
                actor=user, batch=batch,
                confirmation=str(batch.id), reason='测试撤销',
            )
        self.assertIn('可逆变更账本', str(ctx.exception))
