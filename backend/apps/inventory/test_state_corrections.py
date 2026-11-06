from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.common.models import OperationAuditLog
from apps.common.testing.fixtures import WarehouseTestFixture

from .lifecycle_services import create_maintenance_record, finish_maintenance_record
from .models import Asset, AssetLifecycleEvent, AssetStateCorrection, Location, MaintenanceRecord
from .state_services import (
    AssetStateCorrectionError,
    RETURNABLE_ASSET_STATUSES,
    correct_asset_states,
)


class AssetStateCorrectionTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.user = self.make_user('corrector', groups=('warehouse_state_correction',))
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(
            warehouse=self.warehouse, code='A-01', name='A-01',
        )

    def test_repairing_asset_can_be_corrected_without_inventory_transaction(self):
        asset = self.make_asset(status=Asset.Status.REPAIRING)
        result = correct_asset_states(
            assets=[asset],
            to_status=Asset.Status.IN_STOCK,
            reason='历史台账误填，实物始终在库。',
            actor=self.user,
            warehouse=self.warehouse,
            location=self.location,
            source=AssetStateCorrection.Source.LEGACY_REVIEW,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.IN_STOCK)
        self.assertEqual(asset.location, self.location)
        self.assertEqual(len(result), 1)
        self.assertEqual(OperationAuditLog.objects.filter(action='asset_state_corrected').count(), 1)
        self.assertEqual(
            AssetLifecycleEvent.objects.filter(event_type=AssetLifecycleEvent.EventType.CORRECTION).count(),
            1,
        )

    def test_terminal_asset_cannot_be_corrected(self):
        asset = self.make_asset(status=Asset.Status.SCRAPPED)
        with self.assertRaises(AssetStateCorrectionError):
            correct_asset_states(
                assets=[asset], to_status=Asset.Status.IN_STOCK, reason='错误测试',
                actor=self.user, warehouse=self.warehouse, location=self.location,
            )

    def test_stale_asset_version_rejects_entire_correction_without_audit(self):
        asset = self.make_asset(status=Asset.Status.REPAIRING)
        stale_updated_at = asset.updated_at
        concurrent_updated_at = stale_updated_at + timedelta(seconds=1)
        Asset.objects.filter(pk=asset.pk).update(
            name='其他账号已修改名称',
            updated_at=concurrent_updated_at,
        )
        audit_count = OperationAuditLog.objects.count()
        event_count = AssetLifecycleEvent.objects.count()

        with self.assertRaisesMessage(AssetStateCorrectionError, '已被其他账号修改'):
            correct_asset_states(
                assets=[asset],
                to_status=Asset.Status.IN_STOCK,
                reason='旧页面提交的校正',
                actor=self.user,
                warehouse=self.warehouse,
                location=self.location,
                expected_updated_at_by_id={asset.pk: stale_updated_at},
            )

        asset.refresh_from_db()
        self.assertEqual(asset.name, '其他账号已修改名称')
        self.assertEqual(asset.status, Asset.Status.REPAIRING)
        self.assertEqual(asset.updated_at, concurrent_updated_at)
        self.assertEqual(OperationAuditLog.objects.count(), audit_count)
        self.assertEqual(AssetLifecycleEvent.objects.count(), event_count)
        self.assertFalse(AssetStateCorrection.objects.filter(asset=asset).exists())

    def test_correction_is_append_only(self):
        asset = self.make_asset(status=Asset.Status.DAMAGED)
        correction = correct_asset_states(
            assets=[asset], to_status=Asset.Status.PENDING_INSPECTION, reason='待重新质检',
            actor=self.user, warehouse=self.warehouse, location=self.location,
        )[0]
        correction.reason = '尝试覆盖'
        with self.assertRaises(ValidationError):
            correction.save()
        with self.assertRaises(ValidationError):
            correction.delete()

    def test_returnable_statuses_exclude_repairing(self):
        self.assertIn(Asset.Status.ISSUED, RETURNABLE_ASSET_STATUSES)
        self.assertIn(Asset.Status.BORROWED, RETURNABLE_ASSET_STATUSES)
        self.assertNotIn(Asset.Status.REPAIRING, RETURNABLE_ASSET_STATUSES)
        self.assertNotIn(Asset.Status.MAINTENANCE, RETURNABLE_ASSET_STATUSES)
        self.assertNotIn(Asset.Status.DAMAGED, RETURNABLE_ASSET_STATUSES)


class HistoricalMaintenanceTests(WarehouseTestFixture, TestCase):
    def setUp(self):
        self.user = self.make_operator()
        self.warehouse = self.make_warehouse()
        self.location = Location.objects.create(
            warehouse=self.warehouse, code='R-01', name='维修返回区',
        )

    def test_historical_repairing_record_returns_to_selected_location(self):
        asset = self.make_asset(status=Asset.Status.REPAIRING)
        record = create_maintenance_record(
            asset=asset,
            actor=self.user,
            activate_asset=False,
            source=MaintenanceRecord.Source.LEGACY_IMPORT,
            fault_description='历史台账补录',
        )
        self.assertEqual(record.previous_asset_status, Asset.Status.IN_STOCK)
        finish_maintenance_record(
            record,
            self.user,
            destination='warehouse',
            target_warehouse=self.warehouse,
            target_location=self.location,
            resolution='已完成维修并验收入库',
        )
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.IN_STOCK)
        self.assertEqual(asset.location, self.location)
