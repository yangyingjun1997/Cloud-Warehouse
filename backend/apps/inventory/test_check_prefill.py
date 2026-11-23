"""盘点范围复用回归（2026-11-23 新增）。

盘点通常按固定范围重复执行（如「每月 A 仓全盘」），每次重选仓库和库位
是纯重复劳动。支持 ?from_task=<id> 预填上次范围，不需要建「盘点模板表」。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.inventory.models import InventoryCheckTask, Location, Warehouse

User = get_user_model()


class CheckPrefillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.warehouse = Warehouse.objects.create(name='复用仓', code='RF-WH')
        cls.location = Location.objects.create(
            warehouse=cls.warehouse, code='RF-LOC', name='复用库位',
            is_active=True,
        )
        cls.other_location = Location.objects.create(
            warehouse=cls.warehouse, code='RF-LOC2', name='其它库位',
            is_active=True,
        )
        cls.admin = User.objects.create_user(
            username='rf_admin', password='Pw#2026aa', is_superuser=True,
        )
        cls.admin.groups.add(
            Group.objects.get_or_create(name='warehouse_admin')[0]
        )
        cls.task = InventoryCheckTask.objects.create(
            name='上月盘点', warehouse=cls.warehouse, location=cls.location,
            created_by=cls.admin,
        )

    def test_prefill_by_task_id(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            f'/inventory-checks/new/?from_task={self.task.id}'
        )
        self.assertEqual(response.status_code, 200)
        prefill = response.context.get('prefill')
        self.assertIsNotNone(prefill, '带 from_task 时应提供预填数据')
        self.assertEqual(prefill['warehouse_id'], str(self.warehouse.id))
        self.assertEqual(prefill['location_id'], str(self.location.id))
        self.assertIn(self.warehouse.name, prefill['name'])

    def test_prefill_renders_selected_options(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            f'/inventory-checks/new/?from_task={self.task.id}'
        )
        body = response.content.decode('utf-8')
        self.assertIn('selected', body, '预填时应渲染 selected 属性')
        self.assertIn('已复用盘点任务', body, '应显示复用提示条')

    def test_without_from_task_no_prefill(self):
        self.client.force_login(self.admin)
        response = self.client.get('/inventory-checks/new/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context.get('prefill'))

    def test_invalid_task_id_is_ignored(self):
        self.client.force_login(self.admin)
        for value in ('abc', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(value=value):
                response = self.client.get(
                    f'/inventory-checks/new/?from_task={value}'
                )
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context.get('prefill'),
                                  '无效 id 应忽略而非报错')

    def test_reuse_link_rendered_on_list(self):
        self.client.force_login(self.admin)
        response = self.client.get('/inventory-checks/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('from_task=', body, '列表页应有「复用范围」入口')
        self.assertIn('复用范围', body)
