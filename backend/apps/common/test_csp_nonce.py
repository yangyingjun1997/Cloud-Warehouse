"""P2-14 回归：CSP 不得再有 unsafe-inline，且模板内联内容必须带 nonce。

背景：过去 script-src / style-src 都带 'unsafe-inline'，等于把 XSS 防线拆掉
一大半——只要能把内容注入 HTML，内联脚本就会执行。
修复：每请求生成 nonce 写进 CSP 头，模板内联 <script>/<style> 带
nonce="{{ csp_nonce }}"；内联事件属性（onclick 等）全部改为 data-* 由
ui-actions.js 统一委托处理。
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class CspPolicyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = Path(settings.BASE_DIR) / 'templates'
        cls.middleware = (
            Path(settings.BASE_DIR) / 'apps' / 'common' / 'middleware.py'
        ).read_text(encoding='utf-8')

    def test_no_unsafe_inline_in_csp(self):
        """只检查真正拼进 CSP 头的字符串，不检查注释里的历史说明。"""
        # 提取 csp = ( ... ) 这一段实际生效的代码
        match = re.search(r'csp\s*=\s*\((.*?)\)\s*\n', self.middleware, re.S)
        self.assertIsNotNone(match, '未找到 CSP 头的构造代码')
        csp_code = match.group(1)
        self.assertNotIn('unsafe-inline', csp_code,
                         'CSP 实际策略不应再允许 unsafe-inline')
        self.assertNotIn('unsafe-eval', csp_code,
                         'CSP 实际策略不应允许 unsafe-eval')

    def test_nonce_is_generated_per_request(self):
        self.assertIn('secrets.token_urlsafe', self.middleware,
                      '应使用加密安全随机数生成 nonce')
        self.assertIn("'nonce-", self.middleware,
                      'CSP 头应包含 nonce 源')

    def test_all_inline_scripts_have_nonce(self):
        problems = []
        for tpl in self.templates.glob('*.html'):
            text = tpl.read_text(encoding='utf-8', errors='replace')
            for match in re.finditer(r'<script(?![^>]*\bsrc=)(?![^>]*\bnonce=)[^>]*>', text):
                problems.append(f'{tpl.name}: {match.group(0)[:60]}')
        self.assertEqual(problems, [], f'内联 script 缺少 nonce: {problems}')

    def test_all_inline_styles_have_nonce(self):
        problems = []
        for tpl in self.templates.glob('*.html'):
            text = tpl.read_text(encoding='utf-8', errors='replace')
            for match in re.finditer(r'<style(?![^>]*\bnonce=)[^>]*>', text):
                problems.append(f'{tpl.name}: {match.group(0)[:60]}')
        self.assertEqual(problems, [], f'内联 style 缺少 nonce: {problems}')

    def test_no_inline_event_handlers_remain(self):
        """onclick/onsubmit/onchange 等内联事件在无 unsafe-inline 时会被浏览器拦，
        必须全部改为 data-* 由 ui-actions.js 处理。"""
        problems = []
        for tpl in self.templates.glob('*.html'):
            text = tpl.read_text(encoding='utf-8', errors='replace')
            for match in re.finditer(r'\son(?:click|submit|change|load|error)="', text):
                line = text[:match.start()].count('\n') + 1
                problems.append(f'{tpl.name}:{line}')
        self.assertEqual(problems, [], f'仍存在内联事件属性: {problems}')

    def test_ui_actions_script_exists_and_is_loaded(self):
        script = (
            Path(settings.BASE_DIR) / 'apps' / 'common' / 'static'
            / 'warehouse' / 'ui-actions.js'
        )
        self.assertTrue(script.exists(), 'ui-actions.js 缺失')
        content = script.read_text(encoding='utf-8')
        for token in ('data-confirm', 'data-action', 'data-href', 'data-auto-submit'):
            self.assertIn(token, content, f'ui-actions.js 未处理 {token}')

        base = (self.templates / 'web_base.html').read_text(encoding='utf-8')
        self.assertIn('warehouse/ui-actions.js', base,
                      'web_base.html 未加载 ui-actions.js')

    def test_context_processor_exposes_nonce(self):
        processors = (
            Path(settings.BASE_DIR) / 'apps' / 'common' / 'context_processors.py'
        ).read_text(encoding='utf-8')
        self.assertIn('csp_nonce', processors,
                      'context processor 未把 nonce 传给模板')
