"""生产环境误配置硬保护回归。

`.env.example` 为了本机开发写了 ALLOWED_HOSTS 含 `*`。手工部署直接复制成 .env
会让任意 Host 头通过校验，且界面完全看不出来。这里锁定三道保护。
"""

import os
import subprocess
import sys

from django.test import SimpleTestCase

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BACKEND = os.path.join(REPO_ROOT, 'backend')

BASE_ENV = {
    'DJANGO_SETTINGS_MODULE': 'warehouse_backend.settings',
    'DB_ENGINE': 'django.db.backends.sqlite3',
    'DB_NAME': os.path.join(REPO_ROOT, '.test-run-temp', 'guard.sqlite3'),
    'SECRET_KEY': 'guard-check-key-abcdefghijklmnopqrstuvwxyz0123456789',
    'TMP': os.path.join(REPO_ROOT, '.test-run-temp'),
    'TEMP': os.path.join(REPO_ROOT, '.test-run-temp'),
    'TMPDIR': os.path.join(REPO_ROOT, '.test-run-temp'),
    'PYTHONIOENCODING': 'utf-8',
}

_PROBE = (
    'import django;django.setup();'
    'from django.conf import settings;'
    'print("LOADED", settings.DEBUG, settings.ALLOWED_HOSTS)'
)


def _load(**overrides):
    """在子进程里加载 settings，返回 (是否成功, 输出)。"""
    env = {**os.environ, **BASE_ENV, **overrides}
    result = subprocess.run(
        [sys.executable, '-c', _PROBE], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    output = (result.stdout + result.stderr)
    return ('ImproperlyConfigured' not in output, output)


class ProductionGuardTests(SimpleTestCase):
    def test_wildcard_allowed_hosts_rejected_in_production(self):
        ok, output = _load(DEBUG='0', ALLOWED_HOSTS='127.0.0.1,*')
        self.assertFalse(ok, '生产环境 ALLOWED_HOSTS=* 必须被拒绝')
        self.assertIn('ALLOWED_HOSTS', output)

    def test_empty_allowed_hosts_rejected_in_production(self):
        ok, _ = _load(DEBUG='0', ALLOWED_HOSTS='')
        self.assertFalse(ok, '生产环境 ALLOWED_HOSTS 为空必须被拒绝')

    def test_cors_allow_all_rejected_in_production(self):
        ok, output = _load(
            DEBUG='0',
            ALLOWED_HOSTS='192.168.1.10',
            CORS_ALLOW_ALL_ORIGINS='1',
        )
        self.assertFalse(ok, '生产环境不能开启 CORS_ALLOW_ALL_ORIGINS')
        self.assertIn('CORS', output)

    def test_valid_production_config_loads(self):
        """正常生产配置必须放行，否则会误伤线上部署。"""
        ok, output = _load(
            DEBUG='0',
            ALLOWED_HOSTS='192.168.1.10,cloudwarehouse.dpdns.org',
            CORS_ALLOW_ALL_ORIGINS='0',
        )
        self.assertTrue(ok, f'正常配置被误拒：{output[:400]}')

    def test_development_wildcard_allowed(self):
        """开发环境允许 *，否则本地调试会被卡住。"""
        ok, output = _load(DEBUG='1', ALLOWED_HOSTS='127.0.0.1,*')
        self.assertTrue(ok, f'开发环境不应被拦：{output[:400]}')
