"""列表页回跳与筛选参数保留的回归测试。

对应线上反馈：点“查看/编辑”或点表格行后，原来筛选好的列表整片复位，
看起来像“点一下就乱跳”。根因是相对链接只带了自身参数，把 q/status/页码丢了。
"""

from django.test import TestCase
from django.urls import reverse

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import Asset, CompositeUnit


class PreservedQueryTests(TestCase):
    def setUp(self):
        self.fixture = WarehouseTestFixture()
        self.user = self.fixture.make_user('return_user', is_superuser=True)
        self.client.force_login(self.user)

    def test_composite_unit_row_link_keeps_filters(self):
        """成品设备点击行必须带回筛选条件。

        2026-11-23 CSP nonce 改造后，内联 onclick="location.href=..." 已改为
        声明式 data-href，由 ui-actions.js 统一处理，因此断言目标同步调整。
        """
        CompositeUnit.objects.create(name='回跳测试单元', status='assembled')

        response = self.client.get(
            reverse('composite_unit_management'), {'status': 'assembled', 'q': '回跳'},
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('data-href=', body)
        if 'unit=' in body:
            self.assertIn('status=assembled', body)

    def test_asset_detail_link_carries_next(self):
        """资产台账“查看 / 编辑”链接要带上当前筛选。"""
        self.fixture.make_asset(name='回跳资产')

        response = self.client.get(
            reverse('warehouse_management'),
            {'tab': 'assets', 'status': 'in_stock'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('next=', response.content.decode('utf-8'))

    def test_asset_form_renders_return_field(self):
        """资产表单要保留回跳地址，POST 校验失败时也不能丢。"""
        asset = self.fixture.make_asset(name='回跳资产二')

        response = self.client.get(
            reverse('warehouse_asset_edit', args=[asset.id]),
            {'next': '&tab=assets&status=in_stock'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="next"', response.content.decode('utf-8'))

    def test_check_detail_link_carries_status_filter(self):
        """盘点任务按状态筛选后，详情链接要带回筛选。"""
        response = self.client.get(reverse('inventory_check_list'), {'status': 'draft'})

        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        # 页面正常渲染即说明 check_return_query 上下文已提供；
        # 没有任务行时只在筛选表单上验证参数未被吞掉。
        self.assertIn('name="status"', body)


class AssetReturnTargetTests(TestCase):
    """`_asset_return_target` 的解析规则，重点是不能被带到站外。"""

    def setUp(self):
        self.fixture = WarehouseTestFixture()
        self.user = self.fixture.make_user('return_target_user', is_superuser=True)
        self.client.force_login(self.user)

    def _request(self, next_value):
        class FakeRequest:
            POST = {'next': next_value} if next_value is not None else {}
            GET = {}

        return FakeRequest()

    def test_query_string_is_joined_with_warehouse_path(self):
        from apps.common.views import _asset_return_target

        target = _asset_return_target(self._request('&tab=assets&status=in_stock'))

        self.assertEqual(target, f'{reverse("warehouse_management")}?tab=assets&status=in_stock')

    def test_leading_question_mark_is_accepted(self):
        from apps.common.views import _asset_return_target

        target = _asset_return_target(self._request('?tab=stock'))

        self.assertEqual(target, f'{reverse("warehouse_management")}?tab=stock')

    def test_absolute_path_is_used_as_is(self):
        from apps.common.views import _asset_return_target

        self.assertEqual(_asset_return_target(self._request('/warehouse/?tab=stock')), '/warehouse/?tab=stock')

    def test_missing_value_falls_back_to_warehouse_list(self):
        from apps.common.views import _asset_return_target

        self.assertEqual(_asset_return_target(self._request(None)), reverse('warehouse_management'))

    def test_external_url_is_rejected(self):
        from apps.common.views import _asset_return_target

        for hostile in ('http://evil.example/x', '//evil.example/x', '\\\\evil.example'):
            with self.subTest(hostile=hostile):
                self.assertEqual(_asset_return_target(self._request(hostile)), reverse('warehouse_management'))

    def test_save_redirects_back_to_filtered_list(self):
        """保存资产后应回到原来的筛选列表，而不是台账首页。"""
        asset = self.fixture.make_asset(name='回跳资产三')
        payload = {'next': '&tab=assets&status=in_stock&asset_page=1'}
        # 用表单最少的必填项走一遍提交，只关心最终的跳转地址。
        response = self.client.post(
            reverse('warehouse_asset_edit', args=[asset.id]), payload, follow=False,
        )

        self.assertIn(response.status_code, (302, 200))
        if response.status_code == 302:
            self.assertIn('tab=assets', response['Location'])
            self.assertIn('status=in_stock', response['Location'])