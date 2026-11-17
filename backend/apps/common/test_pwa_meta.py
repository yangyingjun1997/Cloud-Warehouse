import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase


class PwaMetaTests(TestCase):
    """PWA 元数据回归：iOS 不读 manifest 的 icons，必须显式给 apple-touch-icon。"""

    def test_login_page_has_ios_pwa_meta(self):
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('apple-touch-icon', content)
        self.assertIn('apple-mobile-web-app-capable', content)
        self.assertIn('icon-180.png', content)

    def test_no_template_comment_leaks_into_html(self):
        """Django 的 {# #} 只支持单行，跨行会原样渲染成文本，必须用 {% comment %}。"""
        for path in ('/login/', '/health/'):
            response = self.client.get(path)
            if response.status_code != 200:
                continue
            content = response.content.decode('utf-8')
            self.assertNotIn('{#', content, f'{path} 渲染出了未解析的模板注释')
            self.assertNotIn('#}', content, f'{path} 渲染出了未解析的模板注释')

    def test_manifest_lists_png_icons(self):
        # 直接读文件：测试环境不走静态文件服务
        manifest_path = (
            Path(settings.BASE_DIR) / 'apps' / 'common' / 'static'
            / 'warehouse' / 'manifest.webmanifest'
        )
        data = json.loads(manifest_path.read_text(encoding='utf-8'))
        sources = [icon['src'] for icon in data['icons']]
        # iOS 只认 PNG，manifest 里必须同时提供 192 与 512
        self.assertTrue(any('icon-192.png' in s for s in sources))
        self.assertTrue(any('icon-512.png' in s for s in sources))
        self.assertTrue(any(i.get('purpose') == 'maskable' for i in data['icons']))

    def test_png_icon_files_exist(self):
        base = Path(settings.BASE_DIR) / 'apps' / 'common' / 'static' / 'warehouse'
        for name in ('icon-180.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png'):
            path = base / name
            self.assertTrue(path.exists(), f'缺少 {name}')
            self.assertGreater(path.stat().st_size, 500, f'{name} 过小，可能损坏')
