"""模板注释规范回归：禁止跨行 {# #}。

Django 的 `{# ... #}` 只支持单行——跨行时它不被当作注释，而是**原样输出到 HTML**，
用户会在页面上看到大段 `{# ... #}` 文本。这个 bug 已经犯过两次
（web_base.html、warehouse_asset_form.html），所以用测试锁死。

需要多行注释时用 `{% comment %} ... {% endcomment %}`。
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

TEMPLATE_DIR = Path(settings.BASE_DIR) / 'templates'


def _iter_templates():
    return sorted(TEMPLATE_DIR.rglob('*.html'))


class TemplateCommentHygieneTests(TestCase):
    """前两个用例只读文件，第三个要渲染页面（需建用户），因此统一用 TestCase。"""
    def test_no_multiline_hash_comments(self):
        """逐字符扫描，找出任何跨行的 {# ... #}。"""
        offenders = []
        for path in _iter_templates():
            text = path.read_text(encoding='utf-8', errors='replace')
            start = 0
            while True:
                begin = text.find('{#', start)
                if begin == -1:
                    break
                end = text.find('#}', begin + 2)
                if end == -1:
                    break
                body = text[begin + 2:end]
                if '\n' in body:
                    line_no = text[:begin].count('\n') + 1
                    first_line = body.strip().splitlines()[0][:50]
                    offenders.append(
                        f'{path.relative_to(TEMPLATE_DIR).as_posix()}:{line_no} → {first_line}'
                    )
                start = end + 2

        self.assertEqual(
            offenders, [],
            '以下模板使用了跨行 {# #} 注释，会原样渲染到页面上；'
            '请改用 {% comment %}...{% endcomment %}：\n  ' + '\n  '.join(offenders),
        )

    def test_comment_tags_are_balanced(self):
        """未闭合的 {# 或 #} 同样会造成渲染异常。"""
        unbalanced = []
        for path in _iter_templates():
            text = path.read_text(encoding='utf-8', errors='replace')
            opens, closes = text.count('{#'), text.count('#}')
            if opens != closes:
                unbalanced.append(
                    f'{path.relative_to(TEMPLATE_DIR).as_posix()}: {opens} 个 {{# vs {closes} 个 #}}'
                )
        self.assertEqual(unbalanced, [], '注释标记未配对：\n  ' + '\n  '.join(unbalanced))

    def test_rendered_pages_have_no_comment_leak(self):
        """实际渲染几个关键页面，确认输出里没有残留的 {#。"""
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from django.test import Client

        user = get_user_model().objects.create_user(
            username='comment_leak_probe', password='Pw#2026aa',
        )
        user.is_staff = True
        user.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        client = Client()
        client.force_login(user)

        for url in ('/', '/login/', '/requests/new/', '/warehouse/', '/permissions/'):
            with self.subTest(url=url):
                response = client.get(url)
                if response.status_code != 200:
                    continue
                content = response.content.decode('utf-8')
                self.assertNotIn('{#', content, f'{url} 渲染出了未解析的模板注释')
                self.assertNotIn('#}', content, f'{url} 渲染出了未解析的模板注释')
