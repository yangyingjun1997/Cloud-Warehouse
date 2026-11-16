from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.common.testing.fixtures import WarehouseTestFixture
from apps.inventory.models import (
    AssetDomainChange,
    AssetLifecycleEvent,
    AssetStateCorrection,
)

from .models import (
    ImmutableAuditQuerySet,
    OperationAuditLog,
    SecurityAuditEvent,
)


class AuditImmutabilityTests(WarehouseTestFixture, TestCase):
    def test_all_immutable_audit_models_use_protected_queryset(self):
        for model in (
            OperationAuditLog,
            SecurityAuditEvent,
            AssetDomainChange,
            AssetLifecycleEvent,
            AssetStateCorrection,
        ):
            with self.subTest(model=model.__name__):
                self.assertIsInstance(model.objects.all(), ImmutableAuditQuerySet)

    def test_operation_audit_log_rejects_queryset_update_and_delete(self):
        log = OperationAuditLog.objects.create(
            action='immutability-test',
            target_model='inventory.asset',
            summary='原始内容',
        )

        with self.assertRaisesMessage(ValidationError, '不允许批量修改'):
            OperationAuditLog.objects.filter(pk=log.pk).update(summary='篡改内容')
        with self.assertRaisesMessage(ValidationError, '不允许批量删除'):
            OperationAuditLog.objects.filter(pk=log.pk).delete()

        log.refresh_from_db()
        self.assertEqual(log.summary, '原始内容')

    def test_lifecycle_event_rejects_instance_and_queryset_changes(self):
        user = self.make_operator('immutable-operator')
        asset = self.make_asset(asset_code='IMMUTABLE-ASSET-001')
        event = AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.NOTE,
            title='原始履历',
            actor=user,
        )

        event.title = '实例篡改'
        with self.assertRaisesMessage(ValidationError, '只允许新增'):
            event.save()
        with self.assertRaisesMessage(ValidationError, '不允许批量修改'):
            AssetLifecycleEvent.objects.filter(pk=event.pk).update(title='批量篡改')
        with self.assertRaisesMessage(ValidationError, '不允许批量删除'):
            AssetLifecycleEvent.objects.filter(pk=event.pk).delete()

        event.refresh_from_db()
        self.assertEqual(event.title, '原始履历')

    def test_users_referenced_by_audit_records_cannot_be_deleted(self):
        user = self.make_operator('protected-audit-operator')
        asset = self.make_asset(asset_code='PROTECTED-AUDIT-ASSET-001')
        OperationAuditLog.objects.create(
            actor=user,
            action='protected-user-test',
            target_model='inventory.asset',
            target_id=asset.pk,
            summary='保留历史责任人',
        )
        AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.NOTE,
            title='保留履史操作人',
            actor=user,
        )

        with self.assertRaises(ProtectedError):
            user.delete()

        self.assertTrue(type(user).objects.filter(pk=user.pk).exists())
