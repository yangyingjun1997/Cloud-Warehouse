from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.audit import record_operation
from apps.common.permissions import can_manage_inventory, is_system_administrator
from apps.notifications.models import Notification

from .models import (
    Asset,
    InventoryCheckAdjustment,
    InventoryCheckLine,
    InventoryCheckScan,
    InventoryCheckTask,
    Location,
    StockItem,
    Warehouse,
)

User = get_user_model()

COUNTABLE_ASSET_STATUSES = {
    Asset.Status.IN_STOCK,
    Asset.Status.MAINTENANCE,
    Asset.Status.REPAIRING,
    Asset.Status.DAMAGED,
}


class InventoryCheckError(Exception):
    pass


def _require_admin(user):
    if not is_system_administrator(user):
        raise InventoryCheckError('只有系统管理员可以创建、复核或取消盘点任务。')


def _require_counter(user):
    if not can_manage_inventory(user):
        raise InventoryCheckError('当前账号没有盘点录入权限。')


def _validate_scope(warehouse, location):
    if location and location.warehouse_id != warehouse.id:
        raise InventoryCheckError('盘点库位不属于选择的仓库。')


def _scope_filter(warehouse_id, location_id):
    query = Q(warehouse_id=warehouse_id)
    if location_id:
        query &= Q(location_id=location_id)
    return query


def _notify(users, title, content, level='info', task=None):
    recipients = [user for user in users if user and user.is_active]
    if not recipients:
        return
    Notification.objects.bulk_create([
        Notification(
            recipient=user,
            title=title,
            content=content,
            level=level,
            related_model='InventoryCheckTask',
            related_object_id=str(task.id) if task else '',
        )
        for user in recipients
    ])


def _administrators():
    return User.objects.filter(
        Q(is_superuser=True) | Q(groups__name='warehouse_admin'),
        is_active=True,
    ).distinct()


def _line_result(line, counted_quantity):
    if line.asset_id:
        if counted_quantity < 1:
            return InventoryCheckLine.Result.SHORTAGE
        if line.expected_status and line.asset.status != line.expected_status:
            return InventoryCheckLine.Result.STATUS_MISMATCH
        if line.expected_location_id and (
            line.observed_warehouse_id != line.expected_warehouse_id
            or line.observed_location_id != line.expected_location_id
        ):
            return InventoryCheckLine.Result.LOCATION_MISMATCH
        if line.observed_warehouse_id and line.observed_warehouse_id != line.expected_warehouse_id:
            return InventoryCheckLine.Result.LOCATION_MISMATCH
        return InventoryCheckLine.Result.MATCHED
    if counted_quantity < line.expected_quantity:
        return InventoryCheckLine.Result.SHORTAGE
    if counted_quantity > line.expected_quantity:
        return InventoryCheckLine.Result.SURPLUS
    if line.expected_location_id and (
        line.observed_warehouse_id != line.expected_warehouse_id
        or line.observed_location_id != line.expected_location_id
    ):
        return InventoryCheckLine.Result.LOCATION_MISMATCH
    if line.observed_warehouse_id and line.observed_warehouse_id != line.expected_warehouse_id:
        return InventoryCheckLine.Result.LOCATION_MISMATCH
    return InventoryCheckLine.Result.MATCHED


@transaction.atomic
def create_check_task(*, actor, name, warehouse_id, location_id=None, note=''):
    _require_admin(actor)
    warehouse = Warehouse.objects.filter(pk=warehouse_id, is_active=True).first()
    location = Location.objects.filter(pk=location_id).first() if location_id else None
    if not warehouse:
        raise InventoryCheckError('请选择有效的盘点仓库。')
    _validate_scope(warehouse, location)
    if not name.strip():
        raise InventoryCheckError('请填写盘点任务名称。')

    task = InventoryCheckTask.objects.create(
        name=name.strip(), warehouse=warehouse, location=location,
        note=note.strip(), created_by=actor,
    )
    asset_query = Asset.objects.select_related('item_type', 'warehouse', 'location').filter(
        _scope_filter(warehouse.id, location.id if location else None),
        status__in=COUNTABLE_ASSET_STATUSES,
    )
    InventoryCheckLine.objects.bulk_create([
        InventoryCheckLine(
            task=task,
            item_type=asset.item_type,
            asset=asset,
            expected_quantity=1,
            counted_quantity=None,
            expected_asset_code=asset.asset_code,
            expected_serial_number=asset.serial_number,
            expected_status=asset.status,
            expected_warehouse=asset.warehouse,
            expected_location=asset.location,
        )
        for asset in asset_query
    ])
    stock_query = StockItem.objects.select_related('item_type', 'warehouse', 'location').filter(
        _scope_filter(warehouse.id, location.id if location else None),
    )
    InventoryCheckLine.objects.bulk_create([
        InventoryCheckLine(
            task=task,
            item_type=stock.item_type,
            stock_item=stock,
            expected_quantity=stock.quantity,
            counted_quantity=None,
            expected_warehouse=stock.warehouse,
            expected_location=stock.location,
        )
        for stock in stock_query
    ])
    record_operation(actor, 'inventory_check_created', task, summary=f'创建盘点任务 {task.check_no}', after={
        'warehouse': warehouse.name,
        'location': location.code if location else '',
        'line_count': task.lines.count(),
    })
    return task


@transaction.atomic
def start_check_task(task, actor):
    _require_counter(actor)
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status != InventoryCheckTask.Status.DRAFT:
        raise InventoryCheckError('只有草稿状态的盘点任务可以开始。')
    task.status = InventoryCheckTask.Status.IN_PROGRESS
    task.started_by = actor
    task.started_at = timezone.now()
    task.save(update_fields=['status', 'started_by', 'started_at', 'updated_at'])
    record_operation(actor, 'inventory_check_started', task, summary=f'开始盘点任务 {task.check_no}')
    return task


def _resolve_asset(value):
    return Asset.objects.select_related('item_type', 'warehouse', 'location').filter(
        Q(system_asset_no__iexact=value)
        | Q(asset_code__iexact=value)
        | Q(qr_value__iexact=value)
        | Q(serial_number__iexact=value)
        | Q(manufacturer_barcode__iexact=value)
    ).first()


def _resolve_stock(value):
    return StockItem.objects.select_related('item_type', 'warehouse', 'location').filter(
        Q(code__iexact=value) | Q(name__iexact=value)
    ).first()


@transaction.atomic
def scan_check_value(task, actor, value, quantity=1, note=''):
    _require_counter(actor)
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    value = value.strip()
    if task.status != InventoryCheckTask.Status.IN_PROGRESS:
        raise InventoryCheckError('只有盘点中的任务可以扫码或录入编码。')
    if not value:
        raise InventoryCheckError('请输入资产编码、原厂 SN、二维码内容或物料编码。')
    if quantity < 1:
        raise InventoryCheckError('实盘数量必须大于 0。')

    asset = _resolve_asset(value)
    stock_item = None if asset else _resolve_stock(value)
    line = None
    result = InventoryCheckScan.Result.NOT_FOUND
    observed_warehouse = getattr(asset, 'warehouse', None) or getattr(stock_item, 'warehouse', None)
    observed_location = getattr(asset, 'location', None) or getattr(stock_item, 'location', None)

    if asset:
        line = task.lines.select_for_update().filter(asset=asset).first()
        in_scope = asset.status in COUNTABLE_ASSET_STATUSES and asset.warehouse_id == task.warehouse_id and (
            not task.location_id or asset.location_id == task.location_id
        )
        if line and line.counted_quantity is not None:
            result = InventoryCheckScan.Result.DUPLICATE
        elif line:
            line.counted_quantity = 1
            line.observed_warehouse = asset.warehouse
            line.observed_location = asset.location
            line.result = _line_result(line, 1)
            line.counted_by, line.counted_at = actor, timezone.now()
            line.save(update_fields=[
                'counted_quantity', 'observed_warehouse', 'observed_location',
                'result', 'counted_by', 'counted_at', 'updated_at',
            ])
            result = InventoryCheckScan.Result.RESOLVED
        elif in_scope:
            line = InventoryCheckLine.objects.create(
                task=task,
                item_type=asset.item_type,
                asset=asset,
                expected_quantity=0,
                counted_quantity=1,
                expected_asset_code='',
                expected_serial_number='',
                expected_status='',
                expected_warehouse=task.warehouse,
                observed_warehouse=asset.warehouse,
                observed_location=asset.location,
                result=InventoryCheckLine.Result.SURPLUS,
                counted_by=actor,
                counted_at=timezone.now(),
            )
            result = InventoryCheckScan.Result.RESOLVED
        else:
            result = InventoryCheckScan.Result.OUT_OF_SCOPE
    elif stock_item:
        line = task.lines.select_for_update().filter(stock_item=stock_item).first()
        in_scope = stock_item.warehouse_id == task.warehouse_id and (
            not task.location_id or stock_item.location_id == task.location_id
        )
        if line and line.counted_quantity is not None:
            result = InventoryCheckScan.Result.DUPLICATE
        elif line:
            line.counted_quantity = quantity
            line.observed_warehouse = stock_item.warehouse
            line.observed_location = stock_item.location
            line.result = _line_result(line, quantity)
            line.counted_by, line.counted_at = actor, timezone.now()
            line.save(update_fields=[
                'counted_quantity', 'observed_warehouse', 'observed_location',
                'result', 'counted_by', 'counted_at', 'updated_at',
            ])
            result = InventoryCheckScan.Result.RESOLVED
        elif in_scope:
            line = InventoryCheckLine.objects.create(
                task=task,
                item_type=stock_item.item_type,
                stock_item=stock_item,
                expected_quantity=0,
                counted_quantity=quantity,
                expected_warehouse=task.warehouse,
                observed_warehouse=stock_item.warehouse,
                observed_location=stock_item.location,
                result=InventoryCheckLine.Result.SURPLUS,
                counted_by=actor,
                counted_at=timezone.now(),
            )
            result = InventoryCheckScan.Result.RESOLVED

    scan = InventoryCheckScan.objects.create(
        task=task,
        line=line,
        scanned_value=value,
        asset=asset,
        stock_item=stock_item,
        quantity=quantity,
        result=result,
        operator=actor,
        observed_warehouse=observed_warehouse,
        observed_location=observed_location,
        note=note.strip(),
    )
    record_operation(actor, 'inventory_check_scanned', task, summary=f'盘点扫描 {task.check_no}: {value}', after={
        'scan_id': str(scan.id),
        'result': result,
        'quantity': quantity,
    })
    return scan


@transaction.atomic
def correct_check_line(task, actor, line_id, quantity, note=''):
    _require_counter(actor)
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status != InventoryCheckTask.Status.IN_PROGRESS:
        raise InventoryCheckError('只有盘点中的任务可以修正盘点结果。')
    line = task.lines.select_for_update().select_related(
        'asset', 'stock_item', 'expected_warehouse', 'expected_location',
    ).filter(pk=line_id).first()
    if not line:
        raise InventoryCheckError('未找到对应的盘点明细。')
    if quantity < 0:
        raise InventoryCheckError('实盘数量不能小于 0。')
    if line.asset_id and quantity not in {0, 1}:
        raise InventoryCheckError('单件资产的实盘数量只能填写 0 或 1。')
    if line.counted_quantity is not None and quantity != line.counted_quantity and not note.strip():
        raise InventoryCheckError('修改已录入的实盘数量必须填写修正原因。')

    before = {'counted_quantity': line.counted_quantity, 'result': line.result}
    if line.asset_id and quantity:
        line.observed_warehouse = line.asset.warehouse
        line.observed_location = line.asset.location
    elif line.asset_id:
        line.observed_warehouse = None
        line.observed_location = None
    elif line.stock_item_id:
        line.observed_warehouse = line.stock_item.warehouse
        line.observed_location = line.stock_item.location
    line.counted_quantity = quantity
    line.result = _line_result(line, quantity)
    line.counted_by, line.counted_at = actor, timezone.now()
    if note.strip():
        line.note = note.strip()
    line.save(update_fields=[
        'counted_quantity', 'observed_warehouse', 'observed_location', 'result',
        'counted_by', 'counted_at', 'note', 'updated_at',
    ])
    record_operation(
        actor,
        'inventory_check_line_corrected',
        line,
        summary=f'修正盘点明细 {task.check_no}',
        before=before,
        after={'counted_quantity': line.counted_quantity, 'result': line.result, 'note': line.note},
    )
    return line


@transaction.atomic
def submit_check_task(task, actor):
    _require_counter(actor)
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status != InventoryCheckTask.Status.IN_PROGRESS:
        raise InventoryCheckError('只有盘点中的任务可以提交复核。')
    now = timezone.now()
    pending_lines = task.lines.select_for_update().filter(counted_quantity__isnull=True)
    for line in pending_lines:
        line.counted_quantity = 0
        line.result = InventoryCheckLine.Result.SHORTAGE
        line.counted_by, line.counted_at = actor, now
        line.save(update_fields=['counted_quantity', 'result', 'counted_by', 'counted_at', 'updated_at'])
    task.status = InventoryCheckTask.Status.PENDING_REVIEW
    task.submitted_at = now
    task.save(update_fields=['status', 'submitted_at', 'updated_at'])
    record_operation(actor, 'inventory_check_submitted', task, summary=f'提交盘点复核 {task.check_no}')
    _notify(
        _administrators(),
        '盘点任务待复核',
        f'盘点任务 {task.check_no} 已提交，请复核盘盈、盘亏和库位差异。',
        level='warning',
        task=task,
    )
    return task


@transaction.atomic
def reopen_check_task(task, actor, note=''):
    _require_admin(actor)
    if not note.strip():
        raise InventoryCheckError('退回重盘必须填写原因。')
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status != InventoryCheckTask.Status.PENDING_REVIEW:
        raise InventoryCheckError('只有待复核状态的盘点任务可以退回重盘。')
    task.status = InventoryCheckTask.Status.IN_PROGRESS
    task.reopened_by = actor
    task.reopened_at = timezone.now()
    task.reopen_note = note.strip()
    task.save(update_fields=['status', 'reopened_by', 'reopened_at', 'reopen_note', 'updated_at'])
    record_operation(
        actor,
        'inventory_check_reopened',
        task,
        summary=f'退回重盘 {task.check_no}',
        after={'note': task.reopen_note},
    )
    _notify(
        [task.started_by, task.created_by],
        '盘点任务退回重盘',
        f'盘点任务 {task.check_no} 已退回重盘，原因：{task.reopen_note}',
        level='warning',
        task=task,
    )
    return task


def _adjust_asset(line, actor, action, review_note):
    asset = Asset.objects.select_for_update().get(pk=line.asset_id)
    before_status, before_warehouse, before_location = asset.status, asset.warehouse, asset.location
    if action == InventoryCheckAdjustment.Action.SHORTAGE:
        if asset.status == Asset.Status.IN_STOCK:
            asset.status = Asset.Status.LOST
    elif action == InventoryCheckAdjustment.Action.SURPLUS:
        asset.status = Asset.Status.IN_STOCK
        asset.warehouse = line.observed_warehouse or line.task.warehouse
        asset.location = line.observed_location
    elif action == InventoryCheckAdjustment.Action.LOCATION:
        asset.warehouse = line.observed_warehouse or asset.warehouse
        asset.location = line.observed_location
    elif action == InventoryCheckAdjustment.Action.STATUS:
        asset.status = line.expected_status or Asset.Status.IN_STOCK
        if asset.status == Asset.Status.IN_STOCK:
            asset.current_holder = None
            asset.department = None
            asset.external_holder_name = ''
            asset.external_holder_company = ''
            asset.external_holder_phone = ''
    asset.save(update_fields=[
        'status', 'warehouse', 'location', 'current_holder', 'department',
        'external_holder_name', 'external_holder_company', 'external_holder_phone', 'updated_at',
    ])
    adjustment = InventoryCheckAdjustment.objects.create(
        task=line.task,
        line=line,
        action=action,
        asset=asset,
        before_quantity=1,
        after_quantity=1 if action != InventoryCheckAdjustment.Action.SHORTAGE else 0,
        before_status=before_status,
        after_status=asset.status,
        before_warehouse=before_warehouse,
        after_warehouse=asset.warehouse,
        before_location=before_location,
        after_location=asset.location,
        reviewed_by=actor,
        review_note=review_note,
        executed_at=timezone.now(),
    )
    return adjustment


def _adjust_stock(line, actor, action, review_note):
    stock_item = StockItem.objects.select_for_update().get(pk=line.stock_item_id)
    before_quantity, before_warehouse, before_location = stock_item.quantity, stock_item.warehouse, stock_item.location
    stock_item.quantity = line.counted_quantity or 0
    if action == InventoryCheckAdjustment.Action.LOCATION:
        stock_item.warehouse = line.observed_warehouse or stock_item.warehouse
        stock_item.location = line.observed_location
    stock_item.save(update_fields=['quantity', 'warehouse', 'location', 'updated_at'])
    adjustment = InventoryCheckAdjustment.objects.create(
        task=line.task,
        line=line,
        action=action,
        stock_item=stock_item,
        before_quantity=before_quantity,
        after_quantity=stock_item.quantity,
        before_warehouse=before_warehouse,
        after_warehouse=stock_item.warehouse,
        before_location=before_location,
        after_location=stock_item.location,
        reviewed_by=actor,
        review_note=review_note,
        executed_at=timezone.now(),
    )
    return adjustment


@transaction.atomic
def review_check_task(task, actor, review_note=''):
    _require_admin(actor)
    if not review_note.strip():
        raise InventoryCheckError('管理员复核必须填写说明。')
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status != InventoryCheckTask.Status.PENDING_REVIEW:
        raise InventoryCheckError('只有待复核状态的盘点任务可以确认。')
    adjustments = []
    for line in task.lines.select_for_update().select_related('task', 'asset', 'stock_item'):
        if line.result == InventoryCheckLine.Result.MATCHED:
            continue
        if line.result == InventoryCheckLine.Result.SHORTAGE:
            action = InventoryCheckAdjustment.Action.SHORTAGE
        elif line.result == InventoryCheckLine.Result.SURPLUS:
            action = InventoryCheckAdjustment.Action.SURPLUS
        elif line.result == InventoryCheckLine.Result.STATUS_MISMATCH:
            action = InventoryCheckAdjustment.Action.STATUS
        else:
            action = InventoryCheckAdjustment.Action.LOCATION
        adjustment = _adjust_asset(line, actor, action, review_note) if line.asset_id else _adjust_stock(line, actor, action, review_note)
        adjustments.append(adjustment)
        record_operation(actor, 'inventory_check_adjusted', adjustment, summary=f'执行盘点调整 {task.check_no}', after={
            'action': action,
            'line': str(line.id),
            'after_quantity': adjustment.after_quantity,
            'after_status': adjustment.after_status,
        })
    task.status = InventoryCheckTask.Status.COMPLETED
    task.reviewed_by = actor
    task.completed_at = timezone.now()
    task.save(update_fields=['status', 'reviewed_by', 'completed_at', 'updated_at'])
    record_operation(actor, 'inventory_check_completed', task, summary=f'完成盘点任务 {task.check_no}', after={'adjustment_count': len(adjustments)})
    _notify(
        [task.created_by],
        '盘点任务已完成',
        f'盘点任务 {task.check_no} 已复核完成，共执行 {len(adjustments)} 项调整。',
        level='success',
        task=task,
    )
    return task, adjustments


@transaction.atomic
def cancel_check_task(task, actor):
    _require_admin(actor)
    task = InventoryCheckTask.objects.select_for_update().get(pk=task.pk)
    if task.status not in {InventoryCheckTask.Status.DRAFT, InventoryCheckTask.Status.IN_PROGRESS}:
        raise InventoryCheckError('只有草稿或盘点中的任务可以取消。')
    task.status = InventoryCheckTask.Status.CANCELED
    task.save(update_fields=['status', 'updated_at'])
    record_operation(actor, 'inventory_check_canceled', task, summary=f'取消盘点任务 {task.check_no}')
    return task
