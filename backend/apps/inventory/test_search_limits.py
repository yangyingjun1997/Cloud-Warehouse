from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.inventory.models import Asset
from apps.inventory.search import (
    MAX_BOOLEAN_SEARCH_DEPTH,
    MAX_BOOLEAN_SEARCH_TOKENS,
    MAX_SEARCH_QUERY_LENGTH,
    asset_search_query,
)

User = get_user_model()


class SearchLimitTests(TestCase):
    """搜索解析器必须有上限：否则深嵌套括号会 RecursionError 打成 500。"""

    def test_deep_nesting_falls_back_to_literal(self):
        for depth in (50, 200, 1000, 5000):
            expression = '(' * depth + 'abc' + ')' * depth
            # 不应抛异常；超限时退回字面量搜索
            query = asset_search_query(expression)
            self.assertIsNotNone(query)

    def test_many_tokens_falls_back(self):
        expression = ' '.join(['!zzz'] * (MAX_BOOLEAN_SEARCH_TOKENS + 10))
        query = asset_search_query(expression)
        self.assertIsNotNone(query)

    def test_overlong_query_is_truncated(self):
        expression = 'a' * (MAX_SEARCH_QUERY_LENGTH + 500)
        query = asset_search_query(expression)
        self.assertIsNotNone(query)

    def test_reasonable_nesting_still_parses(self):
        depth = MAX_BOOLEAN_SEARCH_DEPTH - 2
        expression = '(' * depth + 'abc' + ')' * depth
        query = asset_search_query(expression)
        self.assertIsNotNone(query)

    def test_normal_boolean_expression_still_works(self):
        query = asset_search_query('abc & def')
        self.assertIsNotNone(query)


class SearchEndpointRobustnessTests(TestCase):
    """深嵌套输入经过真实端点应返回 200 而不是 500。"""

    def setUp(self):
        self.user = User.objects.create_user(username='search-user', password='pw')
        self.user.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.client.force_login(self.user)

    def test_lookup_endpoint_survives_deep_nesting(self):
        expression = '(' * 1200 + 'abc' + ')' * 1200
        response = self.client.get('/assets/lookup/', {'q': expression})
        self.assertEqual(response.status_code, 200)

    def test_warehouse_endpoint_survives_deep_nesting(self):
        expression = '(' * 1200 + 'abc' + ')' * 1200
        response = self.client.get('/warehouse/', {'q': expression})
        self.assertEqual(response.status_code, 200)

    def test_api_search_survives_deep_nesting(self):
        from rest_framework.test import APIClient
        api_client = APIClient()
        api_client.force_authenticate(user=self.user)
        expression = '(' * 1200 + 'abc' + ')' * 1200
        response = api_client.get(
            '/api/inventory/assets/search/', {'q': expression},
        )
        self.assertEqual(response.status_code, 200)
