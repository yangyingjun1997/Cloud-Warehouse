"""导航模板标签测试。"""

from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase


class NavActiveTagTest(SimpleTestCase):
    """导航高亮必须准确，否则菜单会同时亮起多项或都不亮。"""

    def _render(self, template_source: str, url_name: str) -> str:
        request = RequestFactory().get('/')
        request.resolver_match = type('Match', (), {'url_name': url_name})()
        return Template(template_source).render(Context({'request': request})).strip()

    def test_matching_url_name_marks_active(self):
        rendered = self._render(
            "{% load nav %}{% nav_active 'inventory_import' 'home' as on %}{% if on %}active{% endif %}",
            'inventory_import',
        )
        self.assertEqual(rendered, 'active')

    def test_non_matching_url_name_is_not_active(self):
        rendered = self._render(
            "{% load nav %}{% nav_active 'inventory_import' 'home' as on %}{% if on %}active{% endif %}",
            'asset_lookup',
        )
        self.assertEqual(rendered, '')

    def test_missing_resolver_match_does_not_crash(self):
        request = RequestFactory().get('/')
        request.resolver_match = None
        rendered = Template(
            "{% load nav %}{% nav_active 'home' as on %}{% if on %}active{% endif %}",
        ).render(Context({'request': request})).strip()
        self.assertEqual(rendered, '')

    def test_prefix_tag_matches_group(self):
        rendered = self._render(
            "{% load nav %}{% nav_active_prefix 'inventory_' as on %}{% if on %}active{% endif %}",
            'inventory_check_detail',
        )
        self.assertEqual(rendered, 'active')

    def test_prefix_tag_rejects_other_group(self):
        rendered = self._render(
            "{% load nav %}{% nav_active_prefix 'inventory_' as on %}{% if on %}active{% endif %}",
            'asset_lookup',
        )
        self.assertEqual(rendered, '')