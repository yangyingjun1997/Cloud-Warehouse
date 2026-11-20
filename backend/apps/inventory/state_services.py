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

# 「通过业务单据才能产生」的最终状态。
#
# 这三个状态各有对应的申请单（出售单 / 退货给供应商单 / 报废审批），
# 走审批流才有完整的申请、审批日志和生命周期轨迹。若允许通过台账校正直接写入，
# 相当于绕开审批，事后无法回答「谁批准的报废」。
#
# 例外：仅 is_superuser 可用台账校正直接写入，用于历史数据补录
# （例如系统上线前的旧设备要标记为已报废，但当年没有单据）。
TERMINAL_CORRECTION_STATUSES = frozenset({
    Asset.Status.SOLD,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
})

CORRECTION_TARGET_STATUSES = frozenset({
    Asset.Status.IN_STOCK,
    Asset.Status.PENDING_INSPECTION,
})

# 仓库管理员（warehouse_admin 组或持有 warehouse_state_correction 的角色）
# 可通过台账校正到达的状态。刻意排除三个终态：它们必须走业务单据，
# 只有 is_superuser 能作为例外直接写入。
ADMIN_CORRECTION_TARGET_STATUSES = frozenset({
    Asset.Status.IN_STOCK,
    Asset.Status.PENDING_INSPECTION,
    Asset.Status.MAINTENANCE,
    Asset.Status.REPAIRING,
    Asset.Status.DAMAGED,
    Asset.Status.LOST,
})

# 超级管理员（is_superuser）的例外通道：在管理员范围之上再加三个终态。
SUPERADMIN_CORRECTION_TARGET_STATUSES = (
    ADMIN_CORRECTION_TARGET_STATUSES | TERMINAL_CORRECTION_STATUSES
)

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
    # 三层权限，按「能改到哪一步」递进：
    #   1. 普通持有 warehouse_state_correction 的角色 → 仅「在库 / 待质检」
    #   2. warehouse_admin 组 → 加上维修中 / 损坏 / 丢失（非终态）
    #   3. is_superuser → 例外通道，可用台账校正直接写入三个终态
    #
    # 终态（已售出 / 已退供应商 / 已报废）默认必须通过对应业务单据审批产生。
    # 早期这里用 is_system_administrator 判断，而它把 warehouse_admin 组也算作
    # 「系统管理员」，导致仓库管理员可以直接把资产改成已报废，绕过报废审批。
    is_superadmin = bool(actor and getattr(actor, 'is_superuser', False))
    is_admin = is_system_administrator(actor)

    if is_superadmin:
        allowed_statuses = SUPERADMIN_CORRECTION_TARGET_STATUSES
    elif is_admin:
        allowed_statuses = ADMIN_CORRECTION_TARGET_STATUSES
    else:
        allowed_statuses = CORRECTION_TARGET_STATUSES

    # 终态单独拦一道，即使将来有人误把终态加进管理员清单也仍然生效。
    if to_status in TERMINAL_CORRECTION_STATUSES and not is_superadmin:
        raise AssetStateCorrectionError(
            '已售出、已退供应商、已报废属于最终状态，'
            '必须通过对应的出售单、退货单或报废审批产生；'
            '仅系统超级管理员可在历史数据补录时通过台账校正直接写入。'
        )

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
