"""依赖锁一致性校验的回归（2026-11-23）。

三个依赖文件分工：
- requirements.txt        直接依赖与版本范围（人读）
- requirements-lock.txt   完整固定版本（跨平台）
- requirements-linux.txt  lock + 仅服务器需要的包（gunicorn）
服务器实际安装 requirements-linux.txt。必须保证三者不漂移。
"""

import importlib.util
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BACKEND = Path(settings.BASE_DIR)


def load_checker():
    path = BACKEND / 'scripts' / 'check_requirements_consistency.py'
    spec = importlib.util.spec_from_file_location('req_consistency', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['req_consistency'] = module
    spec.loader.exec_module(module)
    return module


class RequirementsConsistencyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.checker = load_checker()

    def test_three_files_exist(self):
        for name in ('requirements.txt', 'requirements-lock.txt', 'requirements-linux.txt'):
            with self.subTest(file=name):
                self.assertTrue((BACKEND / name).exists(), f'{name} 缺失')

    def test_direct_deps_covered(self):
        problems = self.checker.check_direct_deps_covered()
        self.assertEqual(problems, [], f'直接依赖未被固定：{problems}')

    def test_linux_includes_lock(self):
        problems = self.checker.check_linux_includes_lock()
        self.assertEqual(problems, [], f'linux 依赖文件问题：{problems}')

    def test_full_check_passes(self):
        """不比对已装版本（--venv），只做静态一致性。"""
        exit_code = self.checker.main([])
        self.assertEqual(exit_code, 0, '依赖文件应保持一致')

    def test_normalize_pep503(self):
        self.assertEqual(self.checker.normalize('Django'), 'django')
        self.assertEqual(self.checker.normalize('django_cors_headers'), 'django-cors-headers')
        self.assertEqual(self.checker.normalize('psycopg.binary'), 'psycopg-binary')

    def test_parse_lock_returns_pinned(self):
        locked = self.checker.parse_lock(BACKEND / 'requirements-lock.txt')
        self.assertIn('django', locked, 'lock 必须固定 Django')
        self.assertRegex(locked['django'], r'^\d+\.\d+')

    def test_linux_pins_gunicorn(self):
        text = (BACKEND / 'requirements-linux.txt').read_text(encoding='utf-8')
        self.assertIn('gunicorn==', text, 'gunicorn 必须固定版本')


class RequirementsDriftDetectionTests(SimpleTestCase):
    """校验器必须能发现漂移，否则只是个摆设。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.checker = load_checker()

    def test_detects_unpinned_linux_package(self):
        import tempfile

        original = self.checker.LINUX
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'requirements-linux.txt'
            fake.write_text(
                '-r requirements-lock.txt\n'
                'gunicorn>=26.0\n'   # 故意不固定
                'somepkg\n',          # 故意不固定
                encoding='utf-8',
            )
            self.checker.LINUX = fake
            try:
                problems = self.checker.check_linux_includes_lock()
            finally:
                self.checker.LINUX = original
            self.assertTrue(
                any('未固定版本' in p for p in problems),
                f'应检出未固定版本的包，实际：{problems}',
            )

    def test_detects_missing_lock_reference(self):
        import tempfile

        original = self.checker.LINUX
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'requirements-linux.txt'
            fake.write_text('gunicorn==26.2.0\n', encoding='utf-8')
            self.checker.LINUX = fake
            try:
                problems = self.checker.check_linux_includes_lock()
            finally:
                self.checker.LINUX = original
            self.assertTrue(
                any('未包含' in p for p in problems),
                f'应检出缺少 -r lock，实际：{problems}',
            )
