from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture

from .models import Asset
from .search import asset_search_query


class BooleanAssetSearchTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.warehouse = self.make_warehouse(name='中心仓', code='WH-CENTER')
        self.edu_asset = self.make_asset(name='EDU 录播主机', warehouse=self.warehouse)
        self.camera = self.make_asset(name='教室摄像机', warehouse=self.warehouse)
        self.b1_asset = self.make_asset(name='B1 备用主机', warehouse=self.warehouse)
        self.both = self.make_asset(name='B1 EDU 一体机', warehouse=self.warehouse)

    def _search(self, query):
        return set(Asset.objects.filter(asset_search_query(query)).values_list('pk', flat=True))

    def test_not_excludes_matching_assets(self):
        result = self._search('!B1')
        self.assertNotIn(self.b1_asset.pk, result)
        self.assertNotIn(self.both.pk, result)
        self.assertIn(self.edu_asset.pk, result)

    def test_and_combines_conditions(self):
        result = self._search('EDU & B1')
        self.assertEqual(result, {self.both.pk})

    def test_or_combines_conditions(self):
        result = self._search('EDU | B1')
        self.assertEqual(result, {self.edu_asset.pk, self.b1_asset.pk, self.both.pk})

    def test_parentheses_change_precedence(self):
        result = self._search('!B1 & (EDU | 摄像机)')
        self.assertEqual(result, {self.edu_asset.pk, self.camera.pk})

    def test_negated_group(self):
        result = self._search('!(B1 | 摄像机)')
        self.assertEqual(result, {self.edu_asset.pk})

    def test_adjacent_terms_are_and(self):
        self.assertEqual(self._search('B1 EDU'), {self.both.pk})

    def test_plain_keyword_keeps_substring_search(self):
        self.assertEqual(self._search('edu'), {self.edu_asset.pk, self.both.pk})

    def test_case_insensitive_match(self):
        self.assertEqual(self._search('edU'), {self.edu_asset.pk, self.both.pk})

    def test_incomplete_expression_falls_back_to_literal(self):
        self.assertEqual(self._search('!B1 &'), set())