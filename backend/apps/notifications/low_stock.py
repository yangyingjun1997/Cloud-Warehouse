from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.inventory.models import Asset, ItemType, StockItem
from apps.inventory.availability import available_asset_filter
from apps.workflow.models import InventoryReservation
from apps.common.models import OperationAuditLog

from .dingtalk import send_group_message
from .models import LowStockAlert, Notification


User = get_user_model()


@dataclass(frozen=True)
class StockSnapshot:
    source_type: str
    source_id: UUID
    source_name: str
    current_quantity: int
    safety_stock: int
    unit: str
    warehouse_name: str = ''

    @property
    def is_low(self) -> bool:
        if self.safety_stock <= 0:
            return False
        if self.source_type == LowStockAlert.SourceType.STOCK_ITEM:
            return self.current_quantity <= self.safety_stock
        return self.current_quantity < self.safety_stock


def _notification_recipients():
    return User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['warehouse_admin', 'warehouse_reports']),
        is_active=True,
    ).distinct()


def _message(snapshot: StockSnapshot, *, recovered: bool) -> tuple[str, str]:
    scope = f'，仓库：{snapshot.warehouse_name}' if snapshot.warehouse_name else ''
    if recovered:
        return (
            '安全库存已恢复',
            f'{snapshot.source_name} 当前可用 {snapshot.current_quantity} {snapshot.unit}，'
            f'安全库存 {snapshot.safety_stock} {snapshot.unit}{scope}。',
        )
    return (
        '安全库存预警',
        f'{snapshot.source_name} 当前仅剩 {snapshot.current_quantity} {snapshot.unit}，'
        f'安全库存 {snapshot.safety_stock} {snapshot.unit}{scope}，请及时补充。',
    )


def _deliver_transition(snapshot: StockSnapshot, *, recovered: bool) -> None:
    title, content = _message(snapshot, recovered=recovered)
    notifications = [
        Notification(recipient=user, title=title, content=content, level='success' if recovered else 'warning')
        for user in _notification_recipients()
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    send_group_message(f'售后仓库：{title}', content)


@transaction.atomic
def apply_snapshot(snapshot: StockSnapshot, *, notify: bool = True) -> LowStockAlert | None:
    alert = LowStockAlert.objects.select_for_update().filter(
        source_type=snapshot.source_type,
        source_id=snapshot.source_id,
    ).first()
    if alert is None and not snapshot.is_low:
        return None
    if alert is None:
        alert = LowStockAlert(source_type=snapshot.source_type, source_id=snapshot.source_id)

    was_active = alert.is_active
    now = timezone.now()
    alert.source_name = snapshot.source_name
    alert.current_quantity = snapshot.current_quantity
    alert.safety_stock = snapshot.safety_stock
    alert.is_active = snapshot.is_low
    if snapshot.is_low and not was_active:
        alert.active_since = now
        alert.last_notified_at = now if notify else None
        alert.resolved_at = None
    elif not snapshot.is_low and was_active:
        alert.resolved_at = now
        alert.last_notified_at = now if notify else alert.last_notified_at
    alert.save()

    transitioned = snapshot.is_low != was_active
    if transitioned:
        OperationAuditLog.objects.create(
            action='low_stock_alert_started' if snapshot.is_low else 'low_stock_alert_resolved',
            target_model='LowStockAlert',
            target_id=alert.id,
            summary=f'{"触发" if snapshot.is_low else "解除"}安全库存预警：{snapshot.source_name}',
            before_data={'is_active': was_active},
            after_data={
                'is_active': snapshot.is_low,
                'current_quantity': snapshot.current_quantity,
                'safety_stock': snapshot.safety_stock,
            },
        )
    if notify and transitioned:
        transaction.on_commit(lambda: _deliver_transition(snapshot, recovered=not snapshot.is_low))
    return alert


def stock_item_snapshot(stock_item_id) -> StockSnapshot | None:
    stock_item = StockItem.objects.select_related('warehouse').filter(pk=stock_item_id).first()
    if not stock_item:
        return None
    reserved_quantity = InventoryReservation.objects.filter(
        stock_item=stock_item,
        status=InventoryReservation.Status.ACTIVE,
    ).aggregate(total=Sum('quantity'))['total'] or 0
    return StockSnapshot(
        source_type=LowStockAlert.SourceType.STOCK_ITEM,
        source_id=stock_item.id,
        source_name=f'{stock_item.name}（{stock_item.code}）',
        current_quantity=max(stock_item.quantity - reserved_quantity, 0),
        safety_stock=stock_item.safety_stock,
        unit=stock_item.unit,
        warehouse_name=stock_item.warehouse.name if stock_item.warehouse else '',
    )


def item_type_snapshot(item_type_id) -> StockSnapshot | None:
    item_type = ItemType.objects.filter(pk=item_type_id).annotate(
        available_count=Count(
            'assets',
            filter=available_asset_filter('assets__'),
            distinct=True,
        ),
    ).first()
    if not item_type:
        return None
    return StockSnapshot(
        source_type=LowStockAlert.SourceType.ITEM_TYPE,
        source_id=item_type.id,
        source_name=f'{item_type.name}（{item_type.code}）',
        current_quantity=item_type.available_count,
        safety_stock=item_type.safety_stock if item_type.is_serialized else 0,
        unit=item_type.unit,
    )


def evaluate_stock_item(stock_item_id, *, notify: bool = True):
    snapshot = stock_item_snapshot(stock_item_id)
    return apply_snapshot(snapshot, notify=notify) if snapshot else None


def evaluate_item_type(item_type_id, *, notify: bool = True):
    snapshot = item_type_snapshot(item_type_id)
    return apply_snapshot(snapshot, notify=notify) if snapshot else None


def synchronize_all(*, notify: bool = True) -> None:
    for stock_item_id in StockItem.objects.values_list('id', flat=True).iterator():
        evaluate_stock_item(stock_item_id, notify=notify)
    for item_type_id in ItemType.objects.values_list('id', flat=True).iterator():
        evaluate_item_type(item_type_id, notify=notify)


def send_weekly_summary() -> int:
    synchronize_all(notify=False)
    active_alerts = list(LowStockAlert.objects.filter(is_active=True).order_by('source_type', 'source_name'))
    if not active_alerts:
        return 0
    lines = [
        f'{index}. {alert.source_name}：当前 {alert.current_quantity}，安全库存 {alert.safety_stock}'
        for index, alert in enumerate(active_alerts, start=1)
    ]
    send_group_message('售后仓库：每周安全库存汇总', '\n'.join(lines))
    return len(active_alerts)
