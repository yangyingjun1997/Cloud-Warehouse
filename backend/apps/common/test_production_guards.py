"""生产环境误配置硬保护回归。

`.env.example` 为了本机开发写了 ALLOWED_HOSTS 含 `*`。手工部署直接复制成 .env
会让任意 Host 头通过校验，且界面完全看不出来。这里锁定三道保护。

实现注意（2026-11-18 修复）：
最初用 subprocess 启动子进程加载 settings 来验证，但子进程会重新初始化 Django，
在测试进程内用 SQLite 内存库时会互相干扰并导致整个测试进程崩溃（表现为
「Creating test database」之后无任何输出）。改为直接调用 settings 里的校验函数，
不启动子进程、不重复初始化 Django。
"""

from django.test import SimpleTestCase

from warehouse_backend.settings_guards import (
    ProductionConfigError,
    validate_production_config,
)


class ProductionGuardTests(SimpleTestCase):
    def test_wildcard_allowed_hosts_rejected(self):
        with self.assertRaises(ProductionConfigError) as ctx:
            validate_production_config(
                debug=False, allowed_hosts=['127.0.0.1', '*'],
                cors_allow_all=False,
            )
        self.assertIn('ALLOWED_HOSTS', str(ctx.exception))

    def test_empty_allowed_hosts_rejected(self):
        with self.assertRaises(ProductionConfigError):
            validate_production_config(debug=False, allowed_hosts=[], cors_allow_all=False)

    def test_cors_allow_all_rejected(self):
        with self.assertRaises(ProductionConfigError) as ctx:
            validate_production_config(
                debug=False, allowed_hosts=['192.168.1.10'], cors_allow_all=True,
            )
        self.assertIn('CORS', str(ctx.exception))

    def test_valid_production_config_passes(self):
        # 正常生产配置必须放行，否则会误伤线上部署
        validate_production_config(
            debug=False,
            allowed_hosts=['192.168.1.10', 'cloudwarehouse.dpdns.org'],
            cors_allow_all=False,
        )

    def test_development_allows_wildcard(self):
        # 开发环境允许 *，否则本地调试会被卡住
        validate_production_config(
            debug=True, allowed_hosts=['127.0.0.1', '*'], cors_allow_all=True,
        )

    def test_live_settings_are_valid(self):
        """当前运行配置必须自身通过校验，否则服务根本起不来。"""
        from django.conf import settings

        validate_production_config(
            debug=settings.DEBUG,
            allowed_hosts=list(settings.ALLOWED_HOSTS),
            cors_allow_all=getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False),
        )
