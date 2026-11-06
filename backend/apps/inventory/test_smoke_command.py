from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.inventory.models import Asset, Category, ItemType, Location, MaintenanceRecord, Warehouse
from apps.workflow.models import InventoryTransaction, WorkflowRequest


class SmokeBusinessFlowsCommandTests(TestCase):
    def test_smoke_flows_accept_source_assets_without_locations(self):
        actor = get_user_model().objects.create_superuser(
            username='smoke-admin', password='test-password',
        )
        warehouse = Warehouse.objects.create(name='源表仓库', code='SOURCE-WH')
        category = Category.objects.create(name='冒烟分类', code='SMOKE')
        item_type = ItemType.objects.create(
            name='冒烟设备', code='SMOKE-ASSET', category=category, is_serialized=True,
        )
        for index in range(3):
            Asset.objects.create(
                name=f'冒烟资产 {index + 1}',
                item_type=item_type,
                warehouse=warehouse,
            )

        output = StringIO()
        call_command(
            'smoke_business_flows',
            actor=actor.username,
            confirm='确认写入冒烟数据',
            stdout=output,
        )

        self.assertIn('业务冒烟通过', output.getvalue())
        self.assertGreaterEqual(WorkflowRequest.objects.count(), 5)
        self.assertGreaterEqual(InventoryTransaction.objects.count(), 4)
        self.assertEqual(Asset.objects.exclude(status=Asset.Status.IN_STOCK).count(), 0)
        self.assertEqual(Asset.objects.exclude(location__isnull=True).count(), 1)

    def test_rollback_mode_verifies_flows_without_leaving_test_data(self):
        actor = get_user_model().objects.create_superuser(
            username='smoke-rollback-admin', password='test-password',
        )
        warehouse = Warehouse.objects.create(name='回滚冒烟仓库', code='ROLLBACK-WH')
        category = Category.objects.create(name='回滚冒烟分类', code='ROLLBACK-SMOKE')
        item_type = ItemType.objects.create(
            name='回滚冒烟设备', code='ROLLBACK-ASSET', category=category, is_serialized=True,
        )
        for index in range(3):
            Asset.objects.create(
                name=f'回滚冒烟资产 {index + 1}',
                item_type=item_type,
                warehouse=warehouse,
            )

        output = StringIO()
        call_command(
            'smoke_business_flows',
            actor=actor.username,
            confirm='确认写入冒烟数据',
            rollback=True,
            stdout=output,
        )

        self.assertIn('业务冒烟通过', output.getvalue())
        self.assertIn('不会保留', output.getvalue())
        self.assertEqual(WorkflowRequest.objects.count(), 0)
        self.assertEqual(InventoryTransaction.objects.count(), 0)
        self.assertEqual(MaintenanceRecord.objects.count(), 0)
        self.assertEqual(Location.objects.count(), 0)
        self.assertEqual(Asset.objects.exclude(status=Asset.Status.IN_STOCK).count(), 0)
