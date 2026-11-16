"""资产状态流转和历史台账校正。

正常出入库必须产生业务单据；历史数据写错时使用台账校正，二者不能混用。
"""

from __future__ import annotations

from django.db import transaction

from apps.common.audit import record_operation
from apps.common.permissions import is_system_administrator

from apps.workflow.models import InventoryReservation

from .models import Asset, AssetLifecycleEvent, AssetStateCorrection


RETURNABLE_ASSET_STATUSES = frozenset({
    Asset.Status.BORROWED,
    Asset.Status.ISSUED,
    Asset.Status.SHIPPED_OUT,
    # 迁移期兼容旧台账；完成 11.2 数据核对后可删除。
    Asset.Status.INTERNAL_BORROW,
    Asset.Status.PENDING_RETURN,
})

TERMINAL_ASSET_STATUSES = frozenset({
    Asset.Status.SOLD,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
    Asset.Status.CONVERTED_TO_STOCK,
    Asset.Status.DISABLED,
})

CORRECTION_TARGET_STATUSES = frozenset({
    Asset.Status.IN_STOCK,
    Asset.Status.PENDING_INSPECTION,
})

# 管理员状态调整范围：仍排除仍在流程中的状态（待出库、待归还验收、已组装、
# 已转耗材），这些必须走各自业务单据，不能靠改字段绕过。
ADMIN_CORRECTION_TARGET_STATUSES = frozenset({
    Asset.Status.IN_STOCK,
    Asset.Status.PENDING_INSPECTION,
    Asset.Status.MAINTENANCE,
    Asset.Status.REPAIRING,
    Asset.Status.SOLD,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
    Asset.Status.DAMAGED,
    Asset.Status.LOST,
})

MAX_CORRECTION_BATCH_SIZE = 50


class AssetStateCorrectionError(Exception):
    pass


def asset_state_snapshot(asset):
    return {
        'status': asset.status,
        'location_state': asset.location_state,
        'availability_state': asset.availability_state,
        'quality_state': asset.quality_state,
        'disposition_state': asset.disposition_state,
        'warehouse_id': str(asset.warehouse_id) if asset.warehouse_id else None,
        'location_id': str(asset.location_id) if asset.location_id else None,
        'current_holder_id': str(asset.current_holder_id) if asset.current_holder_id else None,
        'external_holder_name': asset.external_holder_name,
        'external_holder_company': asset.external_holder_company,
        'external_holder_phone': asset.external_holder_phone,
    }


@transaction.atomic
def correct_asset_states(*, assets, to_status, reason, actor, warehouse=None, location=None,
                         source=AssetStateCorrection.Source.MANUAL, expected_updated_at_by_id=None):
    asset_ids = [asset.pk for asset in assets]
    if not asset_ids:
        raise AssetStateCorrectionError('请至少选择一件资产。')
    if len(asset_ids) > MAX_CORRECTION_BATCH_SIZE:
        raise AssetStateCorrectionError(f'单次最多校正 {MAX_CORRECTION_BATCH_SIZE} 件资产。')
    is_admin = is_system_administrator(actor)
    allowed_statuses = ADMIN_CORRECTION_TARGET_STATUSES if is_admin else CORRECTION_TARGET_STATUSES
    if to_status not in allowed_statuses:
        if is_admin:
            raise AssetStateCorrectionError('管理员状态调整不支持该目标状态，请走对应业务单据。')
        raise AssetStateCorrectionError('台账校正只允许改为“在库”或“待质检”。')
    if not reason or not reason.strip():
        raise AssetStateCorrectionError('必须填写校正原因。')
    if not warehouse or not location:
        raise AssetStateCorrectionError('状态调整必须选择资产当前仓库和库位。')
    if warehouse and location and location.warehouse_id != warehouse.id:
        raise AssetStateCorrectionError('目标库位不属于所选仓库。')

    locked_assets = list(Asset.objects.select_for_update().filter(pk__in=asset_ids).order_by('pk'))
    if len(locked_assets) != len(set(asset_ids)):
        raise AssetStateCorrectionError('部分资产不存在，请刷新页面后重试。')
    expected_versions = {
        str(asset_id): value for asset_id, value in (expected_updated_at_by_id or {}).items()
    }

    results = []
    for asset in locked_assets:
        expected_updated_at = expected_versions.get(str(asset.pk))
        if expected_updated_at is not None and asset.updated_at != expected_updated_at:
            raise AssetStateCorrectionError(
                f'资产 {asset} 已被其他账号修改，本次未执行；请刷新页面后重新核对。'
            )
        if InventoryReservation.objects.filter(
            asset=asset, status=InventoryReservation.Status.ACTIVE,
        ).exists():
            raise AssetStateCorrectionError(f'资产 {asset} 存在有效预占，必须先解除预占再调整状态。')
        if asset.status in TERMINAL_ASSET_STATUSES:
            raise AssetStateCorrectionError(
                f'资产 {asset} 已处于最终状态，必须通过冲销或反向业务单据处理。'
            )
        before = asset_state_snapshot(asset)
        from_status = asset.status
        asset.status = to_status
        if warehouse:
            asset.warehouse = warehouse
        if location:
            asset.location = location
        if to_status == Asset.Status.IN_STOCK:
            asset.current_holder = None
            asset.department = None
            asset.external_holder_name = ''
            asset.external_holder_company = ''
            asset.external_holder_phone = ''
        asset.save()
        after = asset_state_snapshot(asset)
        correction = AssetStateCorrection.objects.create(
            asset=asset,
            from_status=from_status,
            to_status=to_status,
            before_snapshot=before,
            after_snapshot=after,
            reason=reason.strip(),
            source=source,
            actor=actor,
            verified_by=actor,
            target_warehouse=warehouse,
            target_location=location,
        )
        record_operation(
            actor,
            'asset_state_corrected',
            correction,
            summary=f'台账校正：{asset} {dict(Asset.Status.choices).get(from_status)} -> {asset.get_status_display()}',
            before=before,
            after=after,
        )
        AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.CORRECTION,
            title='台账校正',
            description=f'{dict(Asset.Status.choices).get(from_status, from_status)} -> {asset.get_status_display()}；原因：{reason.strip()}',
            actor=actor,
            related_model=correction._meta.label_lower,
            related_object_id=str(correction.pk),
        )
        results.append(correction)
    return results
