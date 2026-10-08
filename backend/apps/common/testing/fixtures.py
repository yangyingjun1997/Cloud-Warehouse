"""跨应用共享的测试数据工厂。

目的：替代各 tests.py 里重复的 setUp 样板（用户/角色组/仓库/物品类型/资产），
让新测试聚焦业务断言本身。用法：

    from apps.common.testing.fixtures import WarehouseTestFixture

    class MyTests(WarehouseTestFixture, TestCase):
        def test_something(self):
            asset = self.make_asset(name='示波器')
            ...

注意：工厂只建最小可用数据集；需要特殊状态时用关键字参数覆盖，
不要为了复用而把工厂参数膨胀到不可读。
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.inventory.models import Asset, Category, ItemType, Warehouse

User = get_user_model()


class WarehouseTestFixture:
    """为 TestCase 提供常用业务对象的一键工厂。

    约定：所有 make_* 方法幂等可多次调用，返回值可直接用于断言。
    """

    def make_user(self, username, *, groups=(), is_superuser=False, password='password123'):
        user = User.objects.create_user(username=username, password=password, is_superuser=is_superuser)
        for group_name in groups:
            group, _ = Group.objects.get_or_create(name=group_name)
            user.groups.add(group)
        return user

    def make_operator(self, username='operator'):
        return self.make_user(username, groups=('warehouse_staff',))

    def make_employee(self, username='employee'):
        return self.make_user(username)

    def make_category(self, name='测试分类', code='TEST-CAT'):
        return Category.objects.get_or_create(name=name, defaults={'code': code})[0]

    def make_item_type(self, name='测试类型', code='TEST-TYPE', category=None):
        return ItemType.objects.get_or_create(
            name=name,
            defaults={'code': code, 'category': category or self.make_category()},
        )[0]

    def make_warehouse(self, name='测试仓', code='TEST-WH'):
        return Warehouse.objects.get_or_create(name=name, defaults={'code': code})[0]

    def make_asset(self, *, name='测试资产', asset_code=None, item_type=None, warehouse=None, status=Asset.Status.IN_STOCK, **overrides):
        return Asset.objects.create(
            name=name,
            asset_code=asset_code,
            item_type=item_type or self.make_item_type(),
            warehouse=warehouse or self.make_warehouse(),
            status=status,
            **overrides,
        )
