"""资产持有人登记：为「不在库」的资产补录当前持有人。

背景（2026-11-18 与用户确认）：
本系统的 `current_holder` 正常只由业务动作写入（借出、领用、归还）。
但历史台账导入的大量资产状态是「已借出/已领用」，却没有持有人记录，
导致「我的借用」看不到这些设备、也无法追责。

因此需要一个受控的补录入口。它与「状态校正」刻意分开：
- 状态校正：改资产状态（在库 ↔ 待质检等），不碰持有人；
- 持有人登记：只补录持有人，不改状态。

安全约束：
1. 只允许对「不在库」的状态登记持有人——在库资产有持有人是矛盾的。
2. 必须填写登记原因，写入审计与资产履历。
3. 单次上限，避免误操作批量覆盖。
4. 不允许登记为「在库」状态，避免绕过出入库流程。
"""

from __future__ import annotations

from django.db import transaction

from apps.common.audit import record_operation

from .models import Asset, AssetLifecycleEvent

# 允许登记持有人的状态：这些状态意味着「资产不在仓库里」。
# 刻意排除 IN_STOCK / PENDING_INSPECTION 等仍在仓的状态，
# 也排除 SOLD / SCRAPPED 等终态（应走客户或处置记录而非持有人）。
HOLDER_REGISTRABLE_STATUSES = frozenset({
    Asset.Status.BORROWED,
    Asset.Status.ISSUED,
    Asset.Status.INTERNAL_BORROW,
    Asset.Status.SHIPPED_OUT,
    Asset.Status.PENDING_RETURN,
    Asset.Status.REPAIRING,
    Asset.Status.MAINTENANCE,
})

MAX_HOLDER_REGISTRATION_BATCH = 50


class AssetHolderRegistrationError(Exception):
    pass


def holder_snapshot(asset):
    """持有人相关字段快照，用于审计前后对比。"""
    return {
        'current_holder_id': str(asset.current_holder_id) if asset.current_holder_id else None,
        'external_holder_name': asset.external_holder_name,
        'external_holder_company': asset.external_holder_company,
        'external_holder_phone': asset.external_holder_phone,
    }


@transaction.atomic
def register_asset_holder(*, asset, holder=None, external_name='', external_company='',
                          external_phone='', reason, actor, expected_updated_at=None):
    """为单件资产登记当前持有人。

    holder 为系统内账号时忽略外部字段；holder 为空时必须提供 external_name（外部人员）。
    """
    if not reason or not reason.strip():
        raise AssetHolderRegistrationError('必须填写登记原因。')

    locked = Asset.objects.select_for_update().filter(pk=asset.pk).first()
    if not locked:
        raise AssetHolderRegistrationError('资产不存在，请刷新页面后重试。')

    if expected_updated_at is not None and locked.updated_at != expected_updated_at:
        raise AssetHolderRegistrationError('该资产已被其他人修改，请刷新页面后重试。')

    if locked.status not in HOLDER_REGISTRABLE_STATUSES:
        status_label = locked.get_status_display()
        raise AssetHolderRegistrationError(
            f'当前状态为「{status_label}」，不能登记持有人。'
            '只有在资产已离开仓库（已借出、已领用、维修中等）时才需要登记持有人；'
            '在库资产请直接走借出或领用流程。'
        )

    has_external = bool(external_name.strip())
    if not holder and not has_external:
        raise AssetHolderRegistrationError('请选择系统内账号，或填写外部持有人姓名。')
    if holder and has_external:
        raise AssetHolderRegistrationError('系统内账号与外部持有人只能二选一。')

    before = holder_snapshot(locked)

    if holder:
        locked.current_holder = holder
        locked.external_holder_name = ''
        locked.external_holder_company = ''
        locked.external_holder_phone = ''
    else:
        locked.current_holder = None
        locked.external_holder_name = external_name.strip()
        locked.external_holder_company = external_company.strip()
        locked.external_holder_phone = external_phone.strip()

    locked.save(update_fields=[
        'current_holder', 'external_holder_name', 'external_holder_company',
        'external_holder_phone', 'updated_at',
    ])

    after = holder_snapshot(locked)
    holder_label = (
        holder.get_full_name().strip() or holder.get_username()
        if holder else f'外部：{locked.external_holder_name}'
    )

    record_operation(
        actor, 'asset_holder_registered', locked,
        summary=f'登记持有人为 {holder_label}：{reason.strip()}',
        before=before, after=after,
    )
    # 履历也记一条，便于在时间线上看到持有人变更。
    # EventType 没有独立的「持有人」类型，用 CORRECTION（台账校正）归类，
    # 因为本质上是补录历史数据，和状态校正属于同一类动作。
    AssetLifecycleEvent.objects.create(
        asset=locked,
        event_type=AssetLifecycleEvent.EventType.CORRECTION,
        title=f'登记持有人：{holder_label}',
        description=f'原因：{reason.strip()}。变更前：{before}；变更后：{after}',
        actor=actor,
        related_model='Asset',
        related_object_id=str(locked.pk),
    )

    return locked
