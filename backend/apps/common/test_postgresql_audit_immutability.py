from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import AssetLifecycleEvent

from .models import OperationAuditLog


@skipUnless(connection.vendor == 'postgresql', '需要 PostgreSQL 触发器语义。')
class PostgreSQLAuditImmutabilityTests(WarehouseTestFixture, TransactionTestCase):
    reset_sequences = True

    def test_raw_sql_cannot_update_or_delete_operation_audit_log(self):
        log = OperationAuditLog.objects.create(
            action='postgres-trigger-test',
            target_model='inventory.asset',
            summary='原始审计内容',
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE common_operationauditlog SET summary = %s WHERE id = %s',
                        ['篡改内容', log.pk],
                    )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM common_operationauditlog WHERE id = %s',
                        [log.pk],
                    )

        log.refresh_from_db()
        self.assertEqual(log.summary, '原始审计内容')

    def test_raw_sql_cannot_update_or_delete_lifecycle_event(self):
        actor = self.make_operator('postgres-audit-operator')
        asset = self.make_asset(asset_code='POSTGRES-AUDIT-001')
        event = AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.NOTE,
            title='原始生命周期事件',
            actor=actor,
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE inventory_assetlifecycleevent SET title = %s WHERE id = %s',
                        ['篡改事件', event.pk],
                    )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        'DELETE FROM inventory_assetlifecycleevent WHERE id = %s',
                        [event.pk],
                    )

        event.refresh_from_db()
        self.assertEqual(event.title, '原始生命周期事件')
