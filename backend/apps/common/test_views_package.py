"""views 包转发层回归（P2-03 方案 B）。

背景：common/views.py 原为 5084 行单文件、89 个顶层函数。
完整拆分的回归风险高，而当前开发模式下 Git 冲突/IDE 卡顿影响有限。
因此采用渐进式方案：views.py 改名为 views/legacy.py，外面包一层转发，
新功能写进独立模块，legacy 逐步搬运。

本测试锁住「转发层不能漏名字」，否则 urls.py 或测试会在运行时才炸。
"""

import types

from django.test import SimpleTestCase
from django.urls import reverse


class ViewsPackageForwardingTests(SimpleTestCase):
    """包必须完整转发 legacy 的名字。"""

    def test_views_is_package(self):
        from apps.common import views

        self.assertTrue(hasattr(views, '__path__'), 'views 应为包而非单文件模块')

    def test_legacy_module_accessible(self):
        from apps.common.views import legacy

        self.assertEqual(legacy.__name__, 'apps.common.views.legacy')

    def test_all_public_views_forwarded(self):
        """urls.py 引用的视图必须能从包访问。

        不用 get_resolver()（会触网/触库），改为直接解析 urls.py 的源码文本，
        这样在 SimpleTestCase 下也能安全运行。
        """
        from pathlib import Path

        from apps.common import views

        urls_path = Path(views.__file__).parent.parent / 'urls.py'
        source = urls_path.read_text(encoding='utf-8')

        # 抓 `views.xxx` 形式的引用（urls.py 里就是这种写法）
        import re

        referenced = set(re.findall(r'\bviews\.([A-Za-z_]\w*)', source))
        missing = sorted(
            name for name in referenced
            if not hasattr(views, name)
        )
        self.assertEqual(missing, [], f'以下被 urls.py 引用的视图未从包转发: {missing}')

    def test_private_helpers_forwarded(self):
        """测试与内部逻辑会用私有辅助函数，必须也能访问。"""
        from apps.common import views

        for helper in ('_asset_return_target', '_user_role_label',
                       '_safe_next_url', '_filter_request_queryset'):
            with self.subTest(helper=helper):
                self.assertTrue(hasattr(views, helper), f'{helper} 未转发')

    def test_count_matches_legacy(self):
        """包可见的可调用对象数量应与 legacy 一致，防止静默丢名字。"""
        from apps.common import views
        from apps.common.views import legacy

        def countable(module):
            return {
                name for name in dir(module)
                if not name.startswith('__') and callable(getattr(module, name, None))
                and not isinstance(getattr(module, name, None), types.ModuleType)
            }

        legacy_names = countable(legacy)
        package_names = countable(views)
        missing = legacy_names - package_names
        self.assertEqual(missing, set(), f'包缺少这些可调用名字: {sorted(missing)[:10]}')

    def test_key_urls_still_resolve(self):
        """URL 反向解析能拿到路径（reverse 不触库，可安全在 SimpleTestCase 跑）。"""
        for name in ('home', 'request_list', 'request_create',
                     'warehouse_management', 'inventory_import'):
            with self.subTest(url_name=name):
                url = reverse(name)
                self.assertTrue(url.startswith('/'))

    def test_no_module_objects_leak_into_public_namespace(self):
        """模块对象不应出现在公开命名空间（会干扰 `views.xxx` 解析）。"""
        from apps.common import views

        leaked = [
            name for name in dir(views)
            if not name.startswith('_')
            and isinstance(getattr(views, name, None), types.ModuleType)
        ]
        # legacy 本身是子模块，允许存在
        leaked = [name for name in leaked if name != 'legacy']
        self.assertEqual(leaked, [], f'公开命名空间混入模块对象: {leaked}')
