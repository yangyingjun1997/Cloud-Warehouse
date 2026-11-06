from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from apps.common.audit import record_operation
from apps.workflow.models import WorkflowRequestLine

from .models import Asset, ImportBatch, ImportMutation, StockItem


class ImportRecoveryError(ValueError):
    pass


class ControlledAssetDeletionError(ValueError):
    pass


_SNAPSHOT_EXCLUDED_FIELDS = {'id', 'created_at', 'updated_at'}


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, Decimal, UUID)):
        return str(value)
    return str(value)


def snapshot_instance(instance) -> dict:
    snapshot = {}
    for field in instance._meta.concrete_fields:
        if field.name in _SNAPSHOT_EXCLUDED_FIELDS:
            continue
        snapshot[field.attname] = _json_value(getattr(instance, field.attname))
    return snapshot


def record_import_mutation(*, batch, instance, operation, before_snapshot):
    object_type = (
        ImportMutation.ObjectType.ASSET
        if isinstance(instance, Asset)
        else ImportMutation.ObjectType.STOCK_ITEM
    )
    return ImportMutation.objects.create(
        batch=batch,
        object_type=object_type,
        object_id=instance.pk,
        operation=operation,
        before_snapshot=before_snapshot,
        after_snapshot=snapshot_instance(instance),
    )


def _relation_exists(instance, relation_name) -> bool:
    try:
        relation = getattr(instance, relation_name)
    except (AttributeError, ObjectDoesNotExist):
        return False
    if hasattr(relation, 'exists'):
        return relation.exists()
    return relation is not None


def asset_deletion_blockers(asset: Asset) -> list[str]:
    relations = [
        ('transactions', '出入库记录'),
        ('reservations', '库存预占'),
        ('component_links', '成品组装履历'),
        ('installed_components', '父资产装配履历'),
        ('assembly_history', '子资产装配履历'),
        ('lifecycle_events', '生命周期记录'),
        ('maintenance_records', '维修记录'),
        ('handovers', '交接记录'),
        ('attachments', '附件归档'),
        ('inventory_conversions', '库存管理方式转换'),
        ('inventory_check_lines', '盘点任务'),
        ('inventory_check_scans', '盘点扫码记录'),
        ('inventory_check_adjustments', '盘点修正记录'),
        ('state_corrections', '台账校正记录'),
        ('domain_changes', '资产归属调整记录'),
    ]
    blockers = [label for relation, label in relations if _relation_exists(asset, relation)]
    if WorkflowRequestLine.objects.filter(asset=asset).exists():
        blockers.append('申请单明细')
    return blockers


def stock_deletion_blockers(stock_item: StockItem) -> list[str]:
    relations = [
        ('transactions', '出入库记录'),
        ('reservations', '库存预占'),
        ('loans', '借用记录'),
        ('user_holdings', '个人领用余额'),
        ('inventory_conversions', '库存管理方式转换'),
        ('inventory_check_lines', '盘点任务'),
        ('inventory_check_scans', '盘点扫码记录'),
        ('inventory_check_adjustments', '盘点修正记录'),
        ('purchase_request_lines', '采购申请明细'),
        ('purchase_order_lines', '采购订单明细'),
    ]
    blockers = [label for relation, label in relations if _relation_exists(stock_item, relation)]
    if WorkflowRequestLine.objects.filter(stock_item=stock_item).exists():
        blockers.append('申请单明细')
    return blockers


def _restore_snapshot(instance, snapshot):
    fields = {field.attname: field for field in instance._meta.concrete_fields}
    for field_name, value in snapshot.items():
        field = fields.get(field_name)
        if not field or field.name in _SNAPSHOT_EXCLUDED_FIELDS:
            continue
        setattr(instance, field_name, field.to_python(value) if value is not None else None)
    instance.save()


def _mutation_instance(mutation):
    model = Asset if mutation.object_type == ImportMutation.ObjectType.ASSET else StockItem
    return model.objects.select_for_update().filter(pk=mutation.object_id).first()


def _rollback_blockers(batch, mutations) -> list[str]:
    blockers = []
    for mutation in mutations:
        instance = _mutation_instance(mutation)
        label = f'{mutation.get_object_type_display()} {mutation.object_id}'
        if not instance:
            blockers.append(f'{label} 已不存在，无法确认当前状态。')
            continue
        current_snapshot = snapshot_instance(instance)
        if current_snapshot != mutation.after_snapshot:
            changed_fields = sorted(
                key for key in set(current_snapshot) | set(mutation.after_snapshot)
                if current_snapshot.get(key) != mutation.after_snapshot.get(key)
            )
            blockers.append(
                f'{label} 在导入后又被修改，不能自动覆盖（差异字段：{"、".join(changed_fields)}）。'
            )
            continue
        if mutation.operation != ImportMutation.Operation.CREATE:
            continue
        if instance.source_import_batch_id != batch.id:
            blockers.append(f'{label} 已被后续导入批次接管。')
            continue
        history = asset_deletion_blockers(instance) if isinstance(instance, Asset) else stock_deletion_blockers(instance)
        if history:
            blockers.append(f'{label} 已产生后续业务历史：{"、".join(history)}。')
    return blockers


@transaction.atomic
def rollback_import_batch(*, actor, batch: ImportBatch, confirmation: str, reason: str):
    if not getattr(actor, 'is_superuser', False):
        raise ImportRecoveryError('只有超级管理员可以撤销导入批次。')
    if batch.status != ImportBatch.Status.IMPORTED:
        raise ImportRecoveryError('只有已导入且未撤销的批次可以执行撤销。')
    if confirmation.strip() != str(batch.id):
        raise ImportRecoveryError('确认文字不正确，请完整输入批次 ID。')
    if not reason.strip():
        raise ImportRecoveryError('必须填写撤销原因。')
    batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
    mutations = list(batch.mutations.select_for_update().order_by('-created_at'))
    if not mutations:
        raise ImportRecoveryError('该批次没有可逆变更账本，不能自动撤销。')
    blockers = _rollback_blockers(batch, mutations)
    if blockers:
        raise ImportRecoveryError('撤销被阻止：' + '；'.join(blockers))

    for mutation in mutations:
        instance = _mutation_instance(mutation)
        if mutation.operation == ImportMutation.Operation.CREATE:
            instance.delete()
        else:
            _restore_snapshot(instance, mutation.before_snapshot)
    rolled_back_at = timezone.now()
    batch.mutations.update(rolled_back_at=rolled_back_at, rolled_back_by=actor)
    batch.status = ImportBatch.Status.ROLLED_BACK
    batch.summary = {
        **batch.summary,
        'rollback_reason': reason.strip(),
        'rolled_back_at': rolled_back_at.isoformat(),
        'rolled_back_by': actor.get_username(),
    }
    batch.save(update_fields=['status', 'summary', 'updated_at'])
    record_operation(
        actor, 'inventory_import_batch_rolled_back', batch,
        summary=f'撤销导入批次 {batch.source_filename}',
        before={'status': ImportBatch.Status.IMPORTED},
        after={
            'status': batch.status,
            'reason': reason.strip(),
            'mutation_count': len(mutations),
        },
    )
    return batch


@transaction.atomic
def controlled_delete_test_asset(*, actor, asset: Asset, confirmation: str, reason: str):
    if not getattr(actor, 'is_superuser', False):
        raise ControlledAssetDeletionError('只有超级管理员可以删除无历史测试资产。')
    expected_values = {asset.system_asset_no.upper()}
    if asset.asset_code:
        expected_values.add(asset.asset_code.upper())
    if confirmation.strip().upper() not in expected_values:
        raise ControlledAssetDeletionError('确认文字不正确，请输入该资产的 AST 或当前主资产编码。')
    if not reason.strip():
        raise ControlledAssetDeletionError('必须填写删除原因。')
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    blockers = asset_deletion_blockers(asset)
    if blockers:
        raise ControlledAssetDeletionError(f'该资产已有业务历史，禁止删除：{"、".join(blockers)}。')
    snapshot = {
        'asset': snapshot_instance(asset),
        'identifiers': [
            {
                'identifier_type': row.identifier_type,
                'value': row.value,
                'is_primary': row.is_primary,
                'is_active': row.is_active,
            }
            for row in asset.identifiers.all()
        ],
        'network_endpoints': [
            {
                'interface_name': row.interface_name,
                'ip_address': row.ip_address,
                'mac_address': row.mac_address,
                'hostname': row.hostname,
            }
            for row in asset.network_endpoints.all()
        ],
        'reason': reason.strip(),
    }
    label = asset.system_asset_no
    record_operation(
        actor, 'controlled_test_asset_deleted', asset,
        summary=f'删除无业务历史测试资产 {label}', before=snapshot,
        after={'deleted': True, 'reason': reason.strip()},
    )
    asset.delete()
    return label
