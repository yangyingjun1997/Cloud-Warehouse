"""审批流核心业务服务。

按职责分区：
- 申请生命周期（create/submit/approve/reject/withdraw）
- 出入库执行（process_request / quick_process_request，含归还差异验收）
- 成品设备联动（_sync_composite_unit_status）
- 借用延期（LoanExtensionRequest 相关）
- 预占（InventoryReservation 生命周期）

说明：process_request 目前集中处理全部单据类型，行数已接近 850。
如继续加差异类型（如归还部分报损），建议按单据类型拆 services/ 子包。
"""
from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.common.audit import record_operation
from apps.common.permissions import (
    PERMISSION_GROUPS,
    can_approve_workflow,
    can_execute_inventory,
    can_manage_inventory,
    is_warehouse_operator,
)
from apps.common.utils import build_daily_code
from apps.inventory.lifecycle_services import ensure_maintenance_from_workflow
from apps.inventory.availability import is_asset_available
from apps.inventory.models import Asset, CompositeUnit, ItemType, Location, StockItem, StockItemHolding, Warehouse
from apps.notifications.models import Notification
from apps.notifications.dingtalk import send_users_or_group

from .models import (
    ApprovalLog,
    ApprovalTask,
    InventoryReservation,
    InventoryTransaction,
    LoanExtensionRequest,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    StockItemLoan,
    WorkflowRequest,
    WorkflowRequestLine,
    sync_asset_availability_states,
)

User = get_user_model()


class WorkflowError(Exception):
    pass


def is_operator(user) -> bool:
    return is_warehouse_operator(user)


def _notify_users(users, title: str, content: str, *, level='info', request=None):
    users = [user for user in users if user and user.is_active]
    notifications = [Notification(recipient=user, title=title, content=content, level=level, related_model='WorkflowRequest' if request else '', related_object_id=str(request.id) if request else '') for user in users]
    if notifications:
        Notification.objects.bulk_create(notifications)
    transaction.on_commit(lambda: send_users_or_group(users, title, content))


def _notify_purchase_users(users, title: str, content: str, *, level='info', purchase_request=None):
    users = [user for user in users if user and user.is_active]
    notifications = [
        Notification(
            recipient=user,
            title=title,
            content=content,
            level=level,
            related_model='PurchaseRequest' if purchase_request else '',
            related_object_id=str(purchase_request.id) if purchase_request else '',
        )
        for user in users
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    transaction.on_commit(lambda: send_users_or_group(users, title, content))


def _log(request, actor, action: str, comment=''):
    return ApprovalLog.objects.create(request=request, actor=actor, action=action, comment=comment)


def _operator_users():
    return User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['warehouse_staff', 'warehouse_admin', *PERMISSION_GROUPS]),
        is_active=True,
    ).distinct()


OUTBOUND_REQUEST_TYPES = {WorkflowRequest.RequestType.BORROW, WorkflowRequest.RequestType.ISSUE, WorkflowRequest.RequestType.SALE}
STOCK_REQUEST_TYPES = OUTBOUND_REQUEST_TYPES | {WorkflowRequest.RequestType.RETURN, WorkflowRequest.RequestType.TRANSFER}
TERMINAL_ASSET_STATUSES = {Asset.Status.SOLD, Asset.Status.RETURNED_TO_VENDOR, Asset.Status.SCRAPPED}
ACTIVE_RETURN_STATUSES = {
    WorkflowRequest.Status.PENDING,
    WorkflowRequest.Status.APPROVED,
    WorkflowRequest.Status.WAITING_WAREHOUSE,
}


def reservation_expiry_time(now=None):
    hours = max(int(getattr(settings, 'RESERVATION_EXPIRY_HOURS', 72)), 1)
    return (now or timezone.now()) + timedelta(hours=hours)


def active_stock_reservation_quantity(stock_item, *, exclude_request=None):
    queryset = InventoryReservation.objects.filter(
        stock_item=stock_item,
        status=InventoryReservation.Status.ACTIVE,
    )
    if exclude_request is not None:
        queryset = queryset.exclude(request_line__request=exclude_request)
    return queryset.aggregate(total=Sum('quantity'))['total'] or 0


def _raise_if_asset_reserved_by_other(asset, request_obj):
    """Expose the reservation conflict before the generic state error."""
    reservation = InventoryReservation.objects.filter(
        asset=asset,
        status=InventoryReservation.Status.ACTIVE,
    ).exclude(request_line__request=request_obj).select_related('request_line__request').first()
    if reservation:
        raise WorkflowError(f'资产 {asset.asset_code or asset.name} 已被其他申请预约，请重新选择。')


def _create_outbound_reservations(request_obj):
    if request_obj.request_type not in OUTBOUND_REQUEST_TYPES:
        return
    lines = list(request_obj.lines.select_related('asset', 'stock_item').order_by('id'))
    asset_ids = sorted(str(line.asset_id) for line in lines if line.asset_id)
    stock_item_ids = sorted(str(line.stock_item_id) for line in lines if line.stock_item_id)
    locked_assets = {
        str(asset.id): asset
        for asset in Asset.objects.select_for_update().filter(pk__in=asset_ids).order_by('id')
    }
    locked_stock_items = {
        str(item.id): item
        for item in StockItem.objects.select_for_update().filter(pk__in=stock_item_ids).order_by('id')
    }
    for line in lines:
        if line.asset_id:
            asset = locked_assets[str(line.asset_id)]
            _raise_if_asset_reserved_by_other(asset, request_obj)
            # 成品出库：组件处于"已组装"锁定态，视为可随成品出库。
            allowed_status = Asset.Status.ASSEMBLED if request_obj.composite_unit_id else Asset.Status.IN_STOCK
            if (
                asset.status != allowed_status
                or (not request_obj.composite_unit_id and not is_asset_available(asset))
            ):
                raise WorkflowError(f'资产 {asset.asset_code or asset.name} 当前不可出库。')
            try:
                InventoryReservation.objects.create(
                    request_line=line,
                    asset=asset,
                    quantity=1,
                    expires_at=reservation_expiry_time(),
                )
            except IntegrityError as exc:
                raise WorkflowError(f'资产 {asset.asset_code} 已被其他申请预约，请重新选择。') from exc
        else:
            stock_item = locked_stock_items[str(line.stock_item_id)]
            available_quantity = stock_item.quantity - active_stock_reservation_quantity(stock_item)
            if available_quantity < line.quantity:
                raise WorkflowError(
                    f'耗材配件 {stock_item.name} 可申请 {max(available_quantity, 0)} {stock_item.unit}，'
                    f'不足以预占 {line.quantity} {stock_item.unit}。'
                )
            InventoryReservation.objects.create(
                request_line=line,
                stock_item=stock_item,
                quantity=line.quantity,
                expires_at=reservation_expiry_time(),
            )


def _finish_reservations(request_obj, status, *, actor=None, reason=''):
    now = timezone.now()
    fields = {'status': status}
    if status == InventoryReservation.Status.RELEASED:
        fields.update({
            'released_at': now,
            'released_by': actor,
            'release_reason': reason[:255],
        })
    elif status == InventoryReservation.Status.CONSUMED:
        fields['consumed_at'] = now
    reservations = InventoryReservation.objects.filter(
        request_line__request=request_obj,
        status=InventoryReservation.Status.ACTIVE,
    )
    asset_ids = list(reservations.exclude(asset_id=None).values_list('asset_id', flat=True))
    reservations.update(**fields, updated_at=now)
    sync_asset_availability_states(asset_ids)


@transaction.atomic
def close_request_and_release_reservations(request_obj, actor=None, reason='', *, automatic=False):
    if actor is not None and not can_approve_workflow(actor):
        raise WorkflowError('只有审批人员可以关闭并释放预约。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.status != WorkflowRequest.Status.PENDING:
        raise WorkflowError('只有待审批申请可以关闭并释放预约。')
    reason = reason.strip() or ('预约超过有效期，系统自动关闭。' if automatic else '')
    if not reason:
        raise WorkflowError('请填写关闭并释放预约的原因。')
    now = timezone.now()
    request_obj.approval_tasks.select_for_update().filter(
        status=ApprovalTask.Status.PENDING,
    ).update(
        status=ApprovalTask.Status.CANCELED,
        comment=reason,
        acted_at=now,
        updated_at=now,
    )
    request_obj.status = WorkflowRequest.Status.CLOSED
    request_obj.save(update_fields=['status', 'updated_at'])
    _finish_reservations(
        request_obj,
        InventoryReservation.Status.RELEASED,
        actor=actor,
        reason=reason,
    )
    action = 'reservation_expire' if automatic else 'reservation_close'
    _log(request_obj, actor, action, reason)
    record_operation(
        actor,
        action,
        request_obj,
        summary=f'{request_obj.request_no} 已关闭并释放预约',
        after={'status': request_obj.status, 'reason': reason},
    )
    _notify_users(
        [request_obj.applicant],
        '库存预约已关闭',
        f'申请 {request_obj.request_no} 已关闭，预约库存已释放。原因：{reason}',
        level='warning',
        request=request_obj,
    )
    return request_obj


def expire_pending_reservations(now=None):
    now = now or timezone.now()
    request_ids = list(InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=now,
        request_line__request__status=WorkflowRequest.Status.PENDING,
    ).values_list('request_line__request_id', flat=True).distinct())
    closed = 0
    for request_id in request_ids:
        request_obj = WorkflowRequest.objects.filter(pk=request_id).first()
        if not request_obj:
            continue
        try:
            close_request_and_release_reservations(
                request_obj,
                reason='预约超过有效期，系统自动关闭。',
                automatic=True,
            )
            closed += 1
        except WorkflowError:
            continue
    return closed


@transaction.atomic
def extend_request_reservation(request_obj, actor, hours=24, reason=''):
    if not can_approve_workflow(actor):
        raise WorkflowError('只有审批人员可以延长预约。')
    try:
        hours = int(hours)
    except (TypeError, ValueError) as exc:
        raise WorkflowError('延长时长必须是整数小时。') from exc
    if hours < 1 or hours > 720:
        raise WorkflowError('单次延长时长应为 1 至 720 小时。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.status != WorkflowRequest.Status.PENDING:
        raise WorkflowError('只有待审批申请可以延长预约。')
    reservations = InventoryReservation.objects.select_for_update().filter(
        request_line__request=request_obj,
        status=InventoryReservation.Status.ACTIVE,
    )
    if not reservations.exists():
        raise WorkflowError('该申请当前没有有效预约。')
    now = timezone.now()
    current_expiry = reservations.order_by('-expires_at').values_list('expires_at', flat=True).first()
    new_expiry = max(current_expiry or now, now) + timedelta(hours=hours)
    reservations.update(expires_at=new_expiry, updated_at=now)
    comment = reason.strip() or f'预约延长 {hours} 小时。'
    _log(request_obj, actor, 'reservation_extend', f'{comment} 新有效期：{new_expiry:%Y-%m-%d %H:%M}')
    record_operation(
        actor,
        'reservation_extend',
        request_obj,
        summary=f'{request_obj.request_no} 预约延长至 {new_expiry:%Y-%m-%d %H:%M}',
        after={'expires_at': new_expiry, 'hours': hours, 'reason': comment},
    )
    _notify_users(
        [request_obj.applicant],
        '库存预约已延长',
        f'申请 {request_obj.request_no} 的预约有效期已延长至 {new_expiry:%Y-%m-%d %H:%M}。',
        request=request_obj,
    )
    return new_expiry


def _business_date(value, fallback):
    if value in (None, ''):
        return fallback or timezone.localdate()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise WorkflowError('出入库日期格式不正确。') from exc


def _recipient_snapshot(request_obj):
    if request_obj.recipient_name or request_obj.recipient_company or request_obj.recipient_phone:
        return request_obj.recipient_name, request_obj.recipient_company, request_obj.recipient_phone
    return request_obj.applicant.get_username() if request_obj.applicant else '', request_obj.department.name if request_obj.department else '', ''


def _validate_target_location(request_obj):
    if request_obj.target_location_id and request_obj.target_warehouse_id and request_obj.target_location.warehouse_id != request_obj.target_warehouse_id:
        raise WorkflowError('目标库位不属于目标仓库。')


def _validate_line(line, request_obj):
    if bool(line.asset_id) == bool(line.stock_item_id):
        raise WorkflowError('每条申请明细必须且只能选择一项资产或耗材配件。')
    if line.quantity < 1:
        raise WorkflowError('申请数量必须大于 0。')
    if line.asset_id and line.quantity != 1:
        raise WorkflowError('单件资产每条申请明细的数量必须为 1。')
    if line.stock_item_id and request_obj.request_type not in STOCK_REQUEST_TYPES:
        raise WorkflowError('耗材配件仅支持借用、领用、归还、调拨或售出。')


def _available_stock_return_quantity(request_obj, stock_item):
    holding_quantity = StockItemHolding.objects.filter(
        stock_item=stock_item,
        holder=request_obj.applicant,
    ).values_list('quantity', flat=True).first() or 0
    pending_quantity = WorkflowRequestLine.objects.filter(
        request__applicant=request_obj.applicant,
        request__request_type=WorkflowRequest.RequestType.RETURN,
        request__status__in=ACTIVE_RETURN_STATUSES,
        stock_item=stock_item,
    ).exclude(request=request_obj).aggregate(total=Sum('quantity'))['total'] or 0
    return max(holding_quantity - pending_quantity, 0)


@transaction.atomic
def submit_request(request_obj: WorkflowRequest, actor, comment=''):
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if actor != request_obj.applicant and not is_operator(actor):
        raise WorkflowError('只能由申请人提交自己的申请。')
    if request_obj.status != WorkflowRequest.Status.DRAFT:
        raise WorkflowError('只有草稿状态的申请可以提交。')
    if not request_obj.lines.exists():
        raise WorkflowError('申请至少需要一条资产或耗材明细。')
    if request_obj.request_type == WorkflowRequest.RequestType.TRANSFER and not request_obj.target_warehouse_id:
        raise WorkflowError('调拨申请必须指定目标仓库。')
    if request_obj.request_type in OUTBOUND_REQUEST_TYPES:
        if not request_obj.usage_location.strip():
            raise WorkflowError('出库申请必须填写使用地点。')
    if request_obj.designated_approver and not can_approve_workflow(request_obj.designated_approver):
        raise WorkflowError('指定审批人不具备审批处理权限。')
    _validate_target_location(request_obj)
    for line in request_obj.lines.select_related('asset', 'stock_item'):
        _validate_line(line, request_obj)
        if line.asset and request_obj.request_type in OUTBOUND_REQUEST_TYPES:
            _raise_if_asset_reserved_by_other(line.asset, request_obj)
            # 成品出库：组件处于"已组装"锁定态，视为可随成品出库。
            allowed_status = Asset.Status.ASSEMBLED if request_obj.composite_unit_id else Asset.Status.IN_STOCK
            if (
                line.asset.status != allowed_status
                or (not request_obj.composite_unit_id and not is_asset_available(line.asset))
            ):
                raise WorkflowError(f'资产 {line.asset.asset_code or line.asset.name} 当前不可出库。')
        if line.asset and request_obj.request_type == WorkflowRequest.RequestType.RETURN and not is_operator(actor) and line.asset.current_holder_id != request_obj.applicant_id:
            raise WorkflowError(f'资产 {line.asset.asset_code} 当前不在申请人名下。')
        if line.asset and request_obj.request_type == WorkflowRequest.RequestType.TRANSFER and not is_asset_available(line.asset):
            raise WorkflowError(f'资产 {line.asset.asset_code} 不在库，无法调拨。')
        if line.asset and request_obj.request_type in {WorkflowRequest.RequestType.REPAIR, WorkflowRequest.RequestType.DAMAGE, WorkflowRequest.RequestType.RETURN_TO_VENDOR, WorkflowRequest.RequestType.SCRAP} and line.asset.status in TERMINAL_ASSET_STATUSES:
            raise WorkflowError(f'资产 {line.asset.asset_code} 已处于最终状态，不能重复处理。')
        if (
            line.stock_item
            and request_obj.request_type == WorkflowRequest.RequestType.RETURN
            and not is_operator(actor)
            and _available_stock_return_quantity(request_obj, line.stock_item) < line.quantity
        ):
            raise WorkflowError(f'耗材配件 {line.stock_item.name} 的本人可归还数量不足。')
    _create_outbound_reservations(request_obj)
    request_obj.status = WorkflowRequest.Status.PENDING
    request_obj.save(update_fields=['status', 'updated_at'])
    ApprovalTask.objects.get_or_create(
        request=request_obj,
        status=ApprovalTask.Status.PENDING,
        defaults={'assigned_to': request_obj.designated_approver},
    )
    _log(request_obj, actor, 'submit', request_obj.reason)
    recipients = [request_obj.designated_approver] if request_obj.designated_approver else _operator_users().exclude(pk=actor.pk)
    _notify_users(recipients, '新的仓库申请待处理', f'{request_obj.applicant.get_username()} 提交了申请 {request_obj.request_no}，请及时审核。', request=request_obj)
    return request_obj


@transaction.atomic
def approve_request(request_obj: WorkflowRequest, actor, comment=''):
    if not can_approve_workflow(actor):
        raise WorkflowError('只有仓库人员或管理员可以审核申请。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.status != WorkflowRequest.Status.PENDING:
        raise WorkflowError('只有待审核状态的申请可以审核。')
    task = request_obj.approval_tasks.select_for_update().filter(status=ApprovalTask.Status.PENDING).first()
    if not task:
        raise WorkflowError('申请没有待处理的审核任务。')
    if task.assigned_to_id and task.assigned_to_id != actor.id:
        raise WorkflowError('该申请已指定其他审批人处理。')
    task.approver, task.status, task.comment, task.acted_at = actor, ApprovalTask.Status.APPROVED, comment, timezone.now()
    task.save(update_fields=['approver', 'status', 'comment', 'acted_at', 'updated_at'])
    request_obj.status = WorkflowRequest.Status.WAITING_WAREHOUSE
    request_obj.save(update_fields=['status', 'updated_at'])
    InventoryReservation.objects.filter(
        request_line__request=request_obj,
        status=InventoryReservation.Status.ACTIVE,
    ).update(expires_at=None, updated_at=timezone.now())
    _log(request_obj, actor, 'approve', comment)
    _notify_users([request_obj.applicant], '仓库申请审核通过', f'申请 {request_obj.request_no} 已通过审核，等待仓库处理。', level='success', request=request_obj)
    return request_obj


@transaction.atomic
def reject_request(request_obj: WorkflowRequest, actor, comment=''):
    if not can_approve_workflow(actor):
        raise WorkflowError('只有仓库人员或管理员可以驳回申请。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.status != WorkflowRequest.Status.PENDING:
        raise WorkflowError('只有待审核状态的申请可以驳回。')
    if not comment.strip():
        raise WorkflowError('驳回申请必须填写原因。')
    task = request_obj.approval_tasks.select_for_update().filter(status=ApprovalTask.Status.PENDING).first()
    if task and task.assigned_to_id and task.assigned_to_id != actor.id:
        raise WorkflowError('该申请已指定其他审批人处理。')
    if task:
        task.approver, task.status, task.comment, task.acted_at = actor, ApprovalTask.Status.REJECTED, comment, timezone.now()
        task.save(update_fields=['approver', 'status', 'comment', 'acted_at', 'updated_at'])
    request_obj.status = WorkflowRequest.Status.REJECTED
    request_obj.save(update_fields=['status', 'updated_at'])
    _finish_reservations(
        request_obj,
        InventoryReservation.Status.RELEASED,
        actor=actor,
        reason=f'审批驳回：{comment}',
    )
    _log(request_obj, actor, 'reject', comment)
    _notify_users([request_obj.applicant], '仓库申请被驳回', f'申请 {request_obj.request_no} 已被驳回：{comment}', level='warning', request=request_obj)
    return request_obj


@transaction.atomic
def withdraw_request(request_obj: WorkflowRequest, actor, comment=''):
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.applicant_id != actor.id:
        raise WorkflowError('只有申请人可以撤回自己的申请。')
    if request_obj.status != WorkflowRequest.Status.PENDING:
        raise WorkflowError('只有待审批状态的申请可以撤回。')
    task = request_obj.approval_tasks.select_for_update().filter(status=ApprovalTask.Status.PENDING).first()
    if task:
        task.status = ApprovalTask.Status.CANCELED
        task.comment = '申请人已撤回申请。'
        task.acted_at = timezone.now()
        task.save(update_fields=['status', 'comment', 'acted_at', 'updated_at'])
    request_obj.status = WorkflowRequest.Status.CANCELED
    request_obj.save(update_fields=['status', 'updated_at'])
    _finish_reservations(
        request_obj,
        InventoryReservation.Status.RELEASED,
        actor=actor,
        reason=comment or '申请人主动撤回。',
    )
    _log(request_obj, actor, 'withdraw', comment or '申请人主动撤回。')
    recipients = [task.assigned_to] if task and task.assigned_to else _operator_users().exclude(pk=actor.pk)
    _notify_users(recipients, '申请已撤回', f'申请 {request_obj.request_no} 已由申请人撤回。', level='warning', request=request_obj)
    return request_obj


@transaction.atomic
def hide_request_for_applicant(request_obj: WorkflowRequest, actor, comment=''):
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.applicant_id != actor.id:
        raise WorkflowError('只有申请人可以删除自己的申请记录。')
    if request_obj.status not in {WorkflowRequest.Status.REJECTED, WorkflowRequest.Status.CANCELED, WorkflowRequest.Status.CLOSED}:
        raise WorkflowError('只有被驳回、已撤回或已关闭的申请可以删除。')
    request_obj.is_hidden_by_applicant = True
    request_obj.save(update_fields=['is_hidden_by_applicant', 'updated_at'])
    _log(request_obj, actor, 'hide', '申请人从个人列表中删除记录。')
    return request_obj


def _request_has_active_loan(request_obj, applicant):
    asset_active = request_obj.lines.filter(
        asset__status=Asset.Status.BORROWED,
        asset__current_holder=applicant,
    ).exists()
    stock_active = StockItemLoan.objects.filter(
        source_line__request=request_obj,
        borrower=applicant,
        status=StockItemLoan.Status.ACTIVE,
        outstanding_quantity__gt=0,
    ).exists()
    return asset_active or stock_active


@transaction.atomic
def create_loan_extension_request(source_request, applicant, requested_return_date, reason):
    source_request = WorkflowRequest.objects.select_for_update().get(pk=source_request.pk)
    if source_request.applicant_id != applicant.id:
        raise WorkflowError('只能为本人的借用单申请延期。')
    if source_request.request_type != WorkflowRequest.RequestType.BORROW or source_request.status != WorkflowRequest.Status.DONE:
        raise WorkflowError('只有已完成出库且尚未归还的借用单可以延期。')
    if not source_request.expected_return_date:
        raise WorkflowError('该借用单没有原预计归还日期，请联系仓库人员处理。')
    if not _request_has_active_loan(source_request, applicant):
        raise WorkflowError('该借用单当前没有未归还物品。')
    if isinstance(requested_return_date, str):
        try:
            requested_return_date = date.fromisoformat(requested_return_date)
        except ValueError as exc:
            raise WorkflowError('新的预计归还日期格式不正确。') from exc
    if not requested_return_date or requested_return_date <= source_request.expected_return_date:
        raise WorkflowError('新的预计归还日期必须晚于当前归还日期。')
    reason = reason.strip()
    if not reason:
        raise WorkflowError('请填写延期原因。')
    if LoanExtensionRequest.objects.filter(
        source_request=source_request,
        status=LoanExtensionRequest.Status.PENDING,
    ).exists():
        raise WorkflowError('该借用单已有待审批的延期申请。')
    extension = LoanExtensionRequest.objects.create(
        applicant=applicant,
        source_request=source_request,
        original_return_date=source_request.expected_return_date,
        requested_return_date=requested_return_date,
        reason=reason,
    )
    _log(
        source_request,
        applicant,
        'loan_extension_submit',
        f'申请延期至 {requested_return_date:%Y-%m-%d}：{reason}',
    )
    record_operation(
        applicant,
        'loan_extension_submit',
        extension,
        summary=f'{source_request.request_no} 申请延期至 {requested_return_date:%Y-%m-%d}',
    )
    _notify_users(
        _operator_users().exclude(pk=applicant.pk),
        '新的借用延期申请',
        f'{applicant.get_username()} 申请将借用单 {source_request.request_no} 延期至 '
        f'{requested_return_date:%Y-%m-%d}。',
        request=source_request,
    )
    return extension


@transaction.atomic
def review_loan_extension(extension, actor, approve, comment=''):
    if not can_approve_workflow(actor):
        raise WorkflowError('只有审批人员可以处理借用延期。')
    extension = LoanExtensionRequest.objects.select_for_update().select_related(
        'source_request', 'applicant'
    ).get(pk=extension.pk)
    if extension.status != LoanExtensionRequest.Status.PENDING:
        raise WorkflowError('该延期申请已经处理。')
    if not approve and not comment.strip():
        raise WorkflowError('拒绝延期时必须填写原因。')
    now = timezone.now()
    extension.status = LoanExtensionRequest.Status.APPROVED if approve else LoanExtensionRequest.Status.REJECTED
    extension.reviewed_by = actor
    extension.review_comment = comment.strip()
    extension.reviewed_at = now
    extension.save(update_fields=['status', 'reviewed_by', 'review_comment', 'reviewed_at', 'updated_at'])
    action = 'loan_extension_approve' if approve else 'loan_extension_reject'
    if approve:
        source_request = WorkflowRequest.objects.select_for_update().get(pk=extension.source_request_id)
        source_request.expected_return_date = extension.requested_return_date
        source_request.save(update_fields=['expected_return_date', 'updated_at'])
        StockItemLoan.objects.filter(
            source_line__request=source_request,
            status=StockItemLoan.Status.ACTIVE,
        ).update(expected_return_date=extension.requested_return_date, updated_at=now)
    _log(
        extension.source_request,
        actor,
        action,
        comment.strip() or f'同意延期至 {extension.requested_return_date:%Y-%m-%d}',
    )
    record_operation(
        actor,
        action,
        extension,
        summary=f'{extension.source_request.request_no} 延期申请{extension.get_status_display()}',
        after={'status': extension.status, 'review_comment': extension.review_comment},
    )
    _notify_users(
        [extension.applicant],
        '借用延期审批结果',
        f'借用单 {extension.source_request.request_no} 的延期申请已{extension.get_status_display()}。'
        + (f'审批备注：{extension.review_comment}' if extension.review_comment else ''),
        level='success' if approve else 'warning',
        request=extension.source_request,
    )
    return extension


def _process_asset(request_obj, line, actor, comment, *, return_received=True):
    asset = Asset.objects.select_for_update().select_related('warehouse').get(pk=line.asset_id)
    before_status, source_warehouse, target_warehouse = asset.status, asset.warehouse, None
    holder_name, holder_company, holder_phone = _recipient_snapshot(request_obj)
    conflicting_reservation = InventoryReservation.objects.filter(
        asset=asset,
        status=InventoryReservation.Status.ACTIVE,
    ).exclude(request_line=line).select_related('request_line__request').first()
    own_reservation = InventoryReservation.objects.filter(
        asset=asset,
        request_line=line,
        status=InventoryReservation.Status.ACTIVE,
    ).exists()
    if conflicting_reservation and request_obj.request_type != WorkflowRequest.RequestType.RETURN:
        raise WorkflowError(
            f'资产 {asset.asset_code} 已被申请 '
            f'{conflicting_reservation.request_line.request.request_no} 预约。'
        )
    is_composite = bool(request_obj.composite_unit_id)
    if request_obj.request_type in OUTBOUND_REQUEST_TYPES:
        # 成品出库：组件处于"已组装"锁定态，可随成品出库。
        allowed_status = Asset.Status.ASSEMBLED if is_composite else Asset.Status.IN_STOCK
        if (
            asset.status != allowed_status
            or (
                not is_composite
                and not is_asset_available(asset, allow_reserved=own_reservation)
            )
        ):
            raise WorkflowError(f'资产 {asset.asset_code or asset.name} 已不在库，无法出库。')
        if (
            request_obj.request_type == WorkflowRequest.RequestType.SALE
            or request_obj.is_quick_process
        ) and (request_obj.recipient_name or request_obj.recipient_company or request_obj.recipient_phone):
            asset.current_holder, asset.department = None, None
            asset.external_holder_name, asset.external_holder_company, asset.external_holder_phone = _recipient_snapshot(request_obj)
        else:
            asset.current_holder, asset.department = request_obj.applicant, request_obj.department
            asset.external_holder_name = asset.external_holder_company = asset.external_holder_phone = ''
        asset.status = {WorkflowRequest.RequestType.BORROW: Asset.Status.BORROWED, WorkflowRequest.RequestType.ISSUE: Asset.Status.ISSUED, WorkflowRequest.RequestType.SALE: Asset.Status.SOLD}[request_obj.request_type]
    elif request_obj.request_type == WorkflowRequest.RequestType.RETURN:
        if asset.status in TERMINAL_ASSET_STATUSES:
            raise WorkflowError(f'资产 {asset.asset_code} 已处于最终状态，无法归还。')
        if not return_received:
            # 差异验收：该组件未随归还收回，保持原状态（仍为借出/已组装在途），仅记录差异台账。
            InventoryTransaction.objects.create(
                request=request_obj, asset=asset, actor=actor, action=WorkflowRequest.RequestType.RETURN,
                quantity=0, business_date=request_obj.transaction_date,
                asset_status_before=before_status, asset_status_after=asset.status,
                source_warehouse=source_warehouse, target_warehouse=None,
                holder_name=holder_name, holder_company=holder_company, holder_phone=holder_phone,
                project_code=request_obj.project_code, work_order_no=request_obj.work_order_no,
                cost_amount=asset.purchase_amount, comment=f'归还差异：未收回。{comment}'.strip('。'),
            )
            return
        asset.current_holder = asset.department = None
        asset.external_holder_name = asset.external_holder_company = asset.external_holder_phone = ''
        # 成品归还：组件回到"已组装"锁定态，仍挂在成品下。
        asset.status = Asset.Status.ASSEMBLED if is_composite else Asset.Status.IN_STOCK
        if request_obj.target_warehouse_id:
            asset.warehouse, target_warehouse = request_obj.target_warehouse, request_obj.target_warehouse
        if request_obj.target_location_id:
            asset.location = request_obj.target_location
    elif request_obj.request_type == WorkflowRequest.RequestType.TRANSFER:
        if not is_asset_available(asset):
            raise WorkflowError(f'资产 {asset.asset_code} 不在库，无法调拨。')
        asset.warehouse, asset.location, target_warehouse = request_obj.target_warehouse, request_obj.target_location, request_obj.target_warehouse
    else:
        if asset.status in TERMINAL_ASSET_STATUSES:
            raise WorkflowError(f'资产 {asset.asset_code} 已处于最终状态，不能重复处理。')
        status_map = {WorkflowRequest.RequestType.REPAIR: Asset.Status.REPAIRING, WorkflowRequest.RequestType.DAMAGE: Asset.Status.DAMAGED, WorkflowRequest.RequestType.RETURN_TO_VENDOR: Asset.Status.RETURNED_TO_VENDOR, WorkflowRequest.RequestType.SCRAP: Asset.Status.SCRAPPED}
        if request_obj.request_type not in status_map:
            raise WorkflowError('不支持的资产处理类型。')
        asset.status = status_map[request_obj.request_type]
    asset.save()
    if request_obj.request_type == WorkflowRequest.RequestType.REPAIR:
        ensure_maintenance_from_workflow(
            asset=asset,
            request_obj=request_obj,
            actor=actor,
            previous_status=before_status,
        )
    InventoryTransaction.objects.create(
        request=request_obj, asset=asset, actor=actor, action=request_obj.request_type,
        quantity=1, business_date=request_obj.transaction_date,
        asset_status_before=before_status, asset_status_after=asset.status,
        source_warehouse=source_warehouse, target_warehouse=target_warehouse,
        holder_name=holder_name, holder_company=holder_company, holder_phone=holder_phone,
        project_code=request_obj.project_code, work_order_no=request_obj.work_order_no,
        cost_amount=asset.purchase_amount, comment=comment,
    )


def _process_stock_item(request_obj, line, actor, comment):
    stock_item = StockItem.objects.select_for_update().get(pk=line.stock_item_id)
    if request_obj.request_type not in STOCK_REQUEST_TYPES:
        raise WorkflowError('该申请类型不支持耗材配件。')
    before_quantity, source_warehouse, target_warehouse = stock_item.quantity, stock_item.warehouse, None
    other_reserved_quantity = active_stock_reservation_quantity(
        stock_item,
        exclude_request=request_obj,
    )
    if request_obj.request_type == WorkflowRequest.RequestType.TRANSFER and other_reserved_quantity:
        raise WorkflowError(f'耗材配件 {stock_item.name} 仍有 {other_reserved_quantity} {stock_item.unit} 被申请预占，暂不能调拨。')
    if request_obj.request_type in OUTBOUND_REQUEST_TYPES:
        available_quantity = stock_item.quantity - other_reserved_quantity
        if available_quantity < line.quantity:
            raise WorkflowError(f'耗材配件 {stock_item.name} 可用数量不足。')
        stock_item.quantity -= line.quantity
    elif request_obj.request_type == WorkflowRequest.RequestType.RETURN:
        stock_item.quantity += line.quantity
        if request_obj.target_warehouse_id:
            stock_item.warehouse, target_warehouse = request_obj.target_warehouse, request_obj.target_warehouse
        if request_obj.target_location_id:
            stock_item.location = request_obj.target_location
    elif request_obj.request_type == WorkflowRequest.RequestType.TRANSFER:
        stock_item.warehouse, stock_item.location, target_warehouse = request_obj.target_warehouse, request_obj.target_location, request_obj.target_warehouse
    stock_item.save()
    if not request_obj.is_quick_process and request_obj.request_type in {
        WorkflowRequest.RequestType.BORROW,
        WorkflowRequest.RequestType.RETURN,
    }:
        holding, _ = StockItemHolding.objects.select_for_update().get_or_create(
            stock_item=stock_item,
            holder=request_obj.applicant,
            defaults={'quantity': 0},
        )
        if request_obj.request_type == WorkflowRequest.RequestType.BORROW:
            holding.quantity += line.quantity
            StockItemLoan.objects.update_or_create(
                source_line=line,
                defaults={
                    'borrower': request_obj.applicant,
                    'stock_item': stock_item,
                    'original_quantity': line.quantity,
                    'outstanding_quantity': line.quantity,
                    'expected_return_date': request_obj.expected_return_date,
                    'status': StockItemLoan.Status.ACTIVE,
                    'returned_at': None,
                },
            )
        else:
            if holding.quantity < line.quantity:
                raise WorkflowError(f'耗材配件 {stock_item.name} 的本人可归还数量不足。')
            holding.quantity -= line.quantity
            remaining = line.quantity
            loans = StockItemLoan.objects.select_for_update().filter(
                borrower=request_obj.applicant,
                stock_item=stock_item,
                status=StockItemLoan.Status.ACTIVE,
                outstanding_quantity__gt=0,
            ).order_by('expected_return_date', 'created_at')
            for loan in loans:
                returned = min(loan.outstanding_quantity, remaining)
                loan.outstanding_quantity -= returned
                remaining -= returned
                update_fields = ['outstanding_quantity', 'updated_at']
                if loan.outstanding_quantity == 0:
                    loan.status = StockItemLoan.Status.RETURNED
                    loan.returned_at = timezone.now()
                    update_fields.extend(['status', 'returned_at'])
                loan.save(update_fields=update_fields)
                if remaining == 0:
                    break
        holding.save(update_fields=['quantity', 'updated_at'])
    holder_name, holder_company, holder_phone = _recipient_snapshot(request_obj)
    InventoryTransaction.objects.create(
        request=request_obj, stock_item=stock_item, actor=actor, action=request_obj.request_type,
        quantity=line.quantity, business_date=request_obj.transaction_date,
        stock_quantity_before=before_quantity, stock_quantity_after=stock_item.quantity,
        source_warehouse=source_warehouse, target_warehouse=target_warehouse,
        holder_name=holder_name, holder_company=holder_company, holder_phone=holder_phone,
        project_code=request_obj.project_code, work_order_no=request_obj.work_order_no,
        cost_amount=(stock_item.unit_cost * line.quantity) if stock_item.unit_cost is not None else None,
        comment=comment,
    )


def _sync_composite_unit_status(request_obj, missing_count=0):
    """成品出库/归还执行完成后，同步成品设备自身状态。

    归还时若存在未收回组件（missing_count>0），成品保持"已借出"状态，
    待组件全部收回后再归还申请改为"已组装"。
    """
    if not request_obj.composite_unit_id:
        return
    unit = CompositeUnit.objects.select_for_update().get(pk=request_obj.composite_unit_id)
    status_map = {
        WorkflowRequest.RequestType.BORROW: CompositeUnit.Status.BORROWED,
        WorkflowRequest.RequestType.SALE: CompositeUnit.Status.SOLD,
        WorkflowRequest.RequestType.RETURN: CompositeUnit.Status.ASSEMBLED,
    }
    new_status = status_map.get(request_obj.request_type)
    if request_obj.request_type == WorkflowRequest.RequestType.RETURN and missing_count > 0:
        new_status = CompositeUnit.Status.BORROWED  # 部分归还，成品仍视为借出
    if new_status is None or unit.status == new_status:
        return
    before = unit.status
    unit.status = new_status
    unit.save(update_fields=['status', 'updated_at'])
    record_operation(
        request_obj.applicant,
        'composite_unit_status_synced',
        unit,
        summary=f'成品设备 {unit} 随申请单 {request_obj.request_no} 流转为{unit.get_status_display()}' + (f'（差异：{missing_count} 件未收回）' if missing_count else ''),
        before={'status': before},
        after={'status': new_status, 'request_no': request_obj.request_no, 'missing_count': missing_count},
    )


@transaction.atomic
def process_request(request_obj: WorkflowRequest, actor, comment='', transaction_date=None, return_received_line_ids=None):
    if not can_execute_inventory(actor):
        raise WorkflowError('只有仓库人员或管理员可以执行出入库。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if request_obj.status != WorkflowRequest.Status.WAITING_WAREHOUSE:
        raise WorkflowError('只有审核通过并等待仓库处理的申请可以执行。')
    request_obj.transaction_date = _business_date(transaction_date, request_obj.transaction_date)
    _validate_target_location(request_obj)
    if not request_obj.inventory_no:
        request_obj.inventory_no = build_daily_code('IO', request_obj.transaction_date)
    request_obj.save(update_fields=['inventory_no', 'transaction_date', 'updated_at'])

    # 差异验收：归还单可逐件勾选实际收回；未勾选的组件保持借出并记录差异。
    received_set = set(return_received_line_ids) if return_received_line_ids is not None else None
    is_return = request_obj.request_type == WorkflowRequest.RequestType.RETURN
    missing_lines = []
    for line in request_obj.lines.select_for_update().select_related('asset', 'stock_item'):
        _validate_line(line, request_obj)
        if line.asset_id:
            received = not is_return or received_set is None or str(line.id) in received_set
            _process_asset(request_obj, line, actor, comment, return_received=received)
            if is_return and not received:
                missing_lines.append(line)
        else:
            _process_stock_item(request_obj, line, actor, comment)
    _sync_composite_unit_status(request_obj, missing_count=len(missing_lines))
    request_obj.status = WorkflowRequest.Status.DONE
    request_obj.save(update_fields=['status', 'updated_at'])
    _finish_reservations(request_obj, InventoryReservation.Status.CONSUMED)
    _log(request_obj, actor, 'process', comment)
    if missing_lines:
        missing_names = '、'.join(f'{l.asset.asset_code or l.asset.name}' for l in missing_lines)
        _log(request_obj, actor, 'process_variance', f'归还差异：{len(missing_lines)} 件未收回（{missing_names}）')
        record_operation(
            actor,
            'return_variance',
            request_obj,
            summary=f'归还单 {request_obj.request_no} 差异验收：{len(missing_lines)} 件未收回',
            after={'missing_lines': [{'asset_code': l.asset.asset_code, 'name': l.asset.name} for l in missing_lines]},
        )
        _notify_users(
            [request_obj.applicant],
            '归还存在差异',
            f'归还单 {request_obj.request_no} 验收完成，但有 {len(missing_lines)} 件组件未收回（{missing_names}），请尽快补齐或联系仓库处理。',
            level='warning',
            request=request_obj,
        )
    _notify_users([request_obj.applicant], '仓库申请已完成', f'申请 {request_obj.request_no} 已完成仓库处理。', level='success', request=request_obj)
    return request_obj


@transaction.atomic
def quick_process_request(request_obj: WorkflowRequest, actor, comment='', transaction_date=None):
    """Execute a warehouse-created transaction without an approval task."""
    if not can_execute_inventory(actor):
        raise WorkflowError('只有仓库人员或管理员可以快速出入库。')
    request_obj = WorkflowRequest.objects.select_for_update().get(pk=request_obj.pk)
    if not request_obj.is_quick_process or request_obj.status != WorkflowRequest.Status.DRAFT:
        raise WorkflowError('该单据不能按快速出入库处理。')
    if not request_obj.contact_source.strip():
        raise WorkflowError('请填写外部沟通来源。')
    request_obj.transaction_date = _business_date(transaction_date, request_obj.transaction_date)
    _validate_target_location(request_obj)
    if not request_obj.inventory_no:
        request_obj.inventory_no = build_daily_code('IO', request_obj.transaction_date)
    request_obj.save(update_fields=['inventory_no', 'transaction_date', 'updated_at'])
    for line in request_obj.lines.select_for_update().select_related('asset', 'stock_item'):
        _validate_line(line, request_obj)
        if line.asset_id:
            _process_asset(request_obj, line, actor, comment)
        else:
            _process_stock_item(request_obj, line, actor, comment)
    request_obj.status = WorkflowRequest.Status.DONE
    request_obj.save(update_fields=['status', 'updated_at'])
    _log(request_obj, actor, 'process', f'快速出入库：{request_obj.contact_source}。{comment}'.strip())
    return request_obj


def _purchase_line_snapshot(source_type, source_id, quantity):
    """读取补货建议来源并生成采购草稿明细快照。"""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise WorkflowError('采购数量必须是正整数。') from exc
    if quantity < 1:
        raise WorkflowError('采购数量必须大于 0。')

    if source_type == 'item_type':
        try:
            item_type = ItemType.objects.get(pk=source_id, is_serialized=True)
        except (ItemType.DoesNotExist, ValueError) as exc:
            raise WorkflowError('所选单件资产类型不存在或已不再可用。') from exc
        assets = Asset.objects.filter(
            item_type=item_type,
            status=Asset.Status.IN_STOCK,
        ).exclude(
            reservations__status=InventoryReservation.Status.ACTIVE,
        ).select_related('warehouse', 'location')
        locations = sorted({
            ' / '.join(filter(None, [
                asset.warehouse.name if asset.warehouse else '未分配仓库',
                asset.location.code if asset.location else '未分配库位',
            ]))
            for asset in assets
        })
        return {
            'item_type': item_type,
            'stock_item': None,
            'name_snapshot': item_type.name,
            'code_snapshot': item_type.code,
            'management_mode': '单件资产',
            'quantity': quantity,
            'unit': item_type.unit,
            'reference_unit_cost': None,
            'reference_amount': None,
            'supplier_snapshot': '按具体资产供应商确认',
            'warehouse_snapshot': '；'.join(locations) or '未定位',
            'location_snapshot': '补货建议按物品类型汇总',
        }

    if source_type == 'stock_item':
        try:
            stock_item = StockItem.objects.select_related(
                'supplier_ref', 'warehouse', 'location', 'item_type',
            ).get(pk=source_id)
        except (StockItem.DoesNotExist, ValueError) as exc:
            raise WorkflowError('所选耗材配件不存在或已不再可用。') from exc
        supplier = stock_item.supplier_ref.name if stock_item.supplier_ref_id else stock_item.supplier
        unit_cost = stock_item.unit_cost
        return {
            'item_type': None,
            'stock_item': stock_item,
            'name_snapshot': stock_item.name,
            'code_snapshot': stock_item.code,
            'management_mode': '耗材配件',
            'quantity': quantity,
            'unit': stock_item.unit,
            'reference_unit_cost': unit_cost,
            'reference_amount': unit_cost * quantity if unit_cost is not None else None,
            'supplier_snapshot': supplier or '未填写',
            'warehouse_snapshot': stock_item.warehouse.name if stock_item.warehouse else '未分配仓库',
            'location_snapshot': stock_item.location.code if stock_item.location else '未分配库位',
        }

    raise WorkflowError('采购草稿包含无法识别的补货项目。')


@transaction.atomic
def create_purchase_draft(actor, selections, reason='', note=''):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以创建采购草稿。')
    if not selections:
        raise WorkflowError('请至少选择一项补货项目。')

    seen_sources = set()
    for selection in selections:
        source_key = (selection.get('source_type'), str(selection.get('source_id')))
        if source_key in seen_sources:
            raise WorkflowError('同一补货项目不能重复加入采购草稿。')
        seen_sources.add(source_key)

    purchase_request = PurchaseRequest.objects.create(
        applicant=actor,
        reason=reason.strip(),
        note=note.strip(),
    )
    for selection in selections:
        snapshot = _purchase_line_snapshot(
            selection.get('source_type'),
            selection.get('source_id'),
            selection.get('quantity'),
        )
        PurchaseRequestLine.objects.create(purchase_request=purchase_request, **snapshot)
    record_operation(
        actor,
        'purchase_draft_created',
        purchase_request,
        summary=f'创建采购草稿 {purchase_request.purchase_no}',
        after={
            'line_count': purchase_request.lines.count(),
            'reason': purchase_request.reason,
            'note': purchase_request.note,
        },
    )
    return purchase_request


@transaction.atomic
def update_purchase_draft(purchase_request, actor, quantities, reason='', note=''):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以编辑采购草稿。')
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    if purchase_request.status != PurchaseRequest.Status.DRAFT:
        raise WorkflowError('只有采购草稿可以编辑。')
    before = {
        'reason': purchase_request.reason,
        'note': purchase_request.note,
        'lines': list(purchase_request.lines.values('id', 'name_snapshot', 'quantity')),
    }
    lines = list(purchase_request.lines.select_for_update())
    for line in lines:
        raw_quantity = quantities.get(str(line.id), quantities.get(line.id, line.quantity))
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise WorkflowError(f'{line.name_snapshot} 的采购数量必须是正整数。') from exc
        if quantity < 1:
            raise WorkflowError(f'{line.name_snapshot} 的采购数量必须大于 0。')
        line.quantity = quantity
        if line.reference_unit_cost is not None:
            line.reference_amount = line.reference_unit_cost * quantity
        line.save(update_fields=['quantity', 'reference_amount', 'updated_at'])
    purchase_request.reason = reason.strip()
    purchase_request.note = note.strip()
    purchase_request.save(update_fields=['reason', 'note', 'updated_at'])
    record_operation(
        actor,
        'purchase_draft_updated',
        purchase_request,
        summary=f'编辑采购草稿 {purchase_request.purchase_no}',
        before=before,
        after={
            'reason': purchase_request.reason,
            'note': purchase_request.note,
            'lines': list(purchase_request.lines.values('id', 'name_snapshot', 'quantity')),
        },
    )
    return purchase_request


@transaction.atomic
def delete_purchase_draft(purchase_request, actor):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以删除采购草稿。')
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    if purchase_request.status != PurchaseRequest.Status.DRAFT:
        raise WorkflowError('只有采购草稿可以删除。')
    summary = f'删除采购草稿 {purchase_request.purchase_no}'
    record_operation(
        actor,
        'purchase_draft_deleted',
        purchase_request,
        summary=summary,
        before={
            'purchase_no': purchase_request.purchase_no,
            'line_count': purchase_request.lines.count(),
        },
    )
    purchase_request.delete()


def _purchase_approver_users():
    return User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['warehouse_staff', 'warehouse_admin', 'warehouse_approval']),
        is_active=True,
    ).distinct()


@transaction.atomic
def submit_purchase_request(purchase_request, actor):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以提交采购申请。')
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    if purchase_request.status != PurchaseRequest.Status.DRAFT:
        raise WorkflowError('只有采购草稿可以提交审批。')
    if not purchase_request.lines.exists():
        raise WorkflowError('采购申请至少需要一项物品。')
    if not purchase_request.reason.strip():
        raise WorkflowError('提交采购申请前必须填写采购原因。')
    if purchase_request.designated_approver and not can_approve_workflow(purchase_request.designated_approver):
        raise WorkflowError('指定审批人不具备采购审批权限。')
    now = timezone.now()
    purchase_request.status = PurchaseRequest.Status.PENDING_APPROVAL
    purchase_request.submitted_at = now
    purchase_request.decided_at = None
    purchase_request.approved_by = None
    purchase_request.approval_comment = ''
    purchase_request.save(update_fields=[
        'status', 'submitted_at', 'decided_at', 'approved_by', 'approval_comment', 'updated_at',
    ])
    record_operation(
        actor,
        'purchase_request_submitted',
        purchase_request,
        summary=f'提交采购申请 {purchase_request.purchase_no} 审批',
        after={'status': purchase_request.status, 'submitted_at': now},
    )
    recipients = [purchase_request.designated_approver] if purchase_request.designated_approver_id else _purchase_approver_users()
    _notify_purchase_users(
        recipients,
        '新的采购申请待审批',
        f'{actor.get_username()} 提交了采购申请 {purchase_request.purchase_no}，请及时处理。',
        purchase_request=purchase_request,
    )
    return purchase_request


def _check_purchase_approver(purchase_request, actor):
    if not can_approve_workflow(actor):
        raise WorkflowError('只有具备审批权限的账号可以处理采购申请。')
    if purchase_request.designated_approver_id and purchase_request.designated_approver_id != actor.id:
        raise WorkflowError('该采购申请已指定其他审批人处理。')


@transaction.atomic
def approve_purchase_request(purchase_request, actor, comment=''):
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    _check_purchase_approver(purchase_request, actor)
    if purchase_request.status != PurchaseRequest.Status.PENDING_APPROVAL:
        raise WorkflowError('只有待采购审批的申请可以同意。')
    now = timezone.now()
    purchase_request.status = PurchaseRequest.Status.APPROVED
    purchase_request.approved_by = actor
    purchase_request.decided_at = now
    purchase_request.approval_comment = comment.strip()
    purchase_request.save(update_fields=['status', 'approved_by', 'decided_at', 'approval_comment', 'updated_at'])
    record_operation(
        actor,
        'purchase_request_approved',
        purchase_request,
        summary=f'通过采购申请 {purchase_request.purchase_no}',
        after={'status': purchase_request.status, 'comment': purchase_request.approval_comment},
    )
    _notify_purchase_users(
        [purchase_request.applicant],
        '采购申请已通过',
        f'采购申请 {purchase_request.purchase_no} 已通过审批，后续可进入采购订单和到货登记。',
        level='success',
        purchase_request=purchase_request,
    )
    return purchase_request


@transaction.atomic
def reject_purchase_request(purchase_request, actor, comment=''):
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    _check_purchase_approver(purchase_request, actor)
    if purchase_request.status != PurchaseRequest.Status.PENDING_APPROVAL:
        raise WorkflowError('只有待采购审批的申请可以驳回。')
    if not comment.strip():
        raise WorkflowError('驳回采购申请必须填写原因。')
    now = timezone.now()
    purchase_request.status = PurchaseRequest.Status.REJECTED
    purchase_request.approved_by = actor
    purchase_request.decided_at = now
    purchase_request.approval_comment = comment.strip()
    purchase_request.save(update_fields=['status', 'approved_by', 'decided_at', 'approval_comment', 'updated_at'])
    record_operation(
        actor,
        'purchase_request_rejected',
        purchase_request,
        summary=f'驳回采购申请 {purchase_request.purchase_no}',
        after={'status': purchase_request.status, 'comment': purchase_request.approval_comment},
    )
    _notify_purchase_users(
        [purchase_request.applicant],
        '采购申请被驳回',
        f'采购申请 {purchase_request.purchase_no} 已被驳回：{purchase_request.approval_comment}',
        level='warning',
        purchase_request=purchase_request,
    )
    return purchase_request


@transaction.atomic
def reopen_purchase_request(purchase_request, actor):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以重新编辑采购申请。')
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    if purchase_request.status != PurchaseRequest.Status.REJECTED:
        raise WorkflowError('只有被驳回的采购申请可以重新编辑。')
    before = {'status': purchase_request.status, 'approval_comment': purchase_request.approval_comment}
    purchase_request.status = PurchaseRequest.Status.DRAFT
    purchase_request.submitted_at = None
    purchase_request.decided_at = None
    purchase_request.approved_by = None
    purchase_request.approval_comment = ''
    purchase_request.save(update_fields=[
        'status', 'submitted_at', 'decided_at', 'approved_by', 'approval_comment', 'updated_at',
    ])
    record_operation(
        actor,
        'purchase_request_reopened',
        purchase_request,
        summary=f'重新编辑采购申请 {purchase_request.purchase_no}',
        before=before,
        after={'status': purchase_request.status},
    )
    return purchase_request


def _notify_purchase_order_users(users, title: str, content: str, *, level='info', order=None):
    users = list({user.id: user for user in users if user and user.is_active}.values())
    notifications = [
        Notification(
            recipient=user,
            title=title,
            content=content,
            level=level,
            related_model='PurchaseOrder' if order else '',
            related_object_id=str(order.id) if order else '',
        )
        for user in users
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    transaction.on_commit(lambda: send_users_or_group(users, title, content))


@transaction.atomic
def create_purchase_order(purchase_request, actor):
    """将已批准的采购申请固化为采购订单，不改变库存。"""
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以创建采购订单。')
    purchase_request = PurchaseRequest.objects.select_for_update().get(pk=purchase_request.pk)
    if purchase_request.status != PurchaseRequest.Status.APPROVED:
        raise WorkflowError('只有已通过审批的采购申请可以生成采购订单。')
    if hasattr(purchase_request, 'purchase_order'):
        raise WorkflowError('该采购申请已经生成采购订单。')
    lines = list(purchase_request.lines.select_for_update().select_related('item_type', 'stock_item'))
    if not lines:
        raise WorkflowError('采购申请没有有效明细，不能生成采购订单。')

    order = PurchaseOrder.objects.create(
        purchase_request=purchase_request,
        created_by=actor,
        order_date=timezone.localdate(),
    )
    for source in lines:
        PurchaseOrderLine.objects.create(
            order=order,
            purchase_request_line=source,
            item_type=source.item_type,
            stock_item=source.stock_item,
            name_snapshot=source.name_snapshot,
            code_snapshot=source.code_snapshot,
            management_mode=source.management_mode,
            ordered_quantity=source.quantity,
            unit=source.unit,
            unit_cost=source.reference_unit_cost,
            supplier_snapshot=source.supplier_snapshot,
            note=source.note,
        )
    record_operation(
        actor,
        'purchase_order_created',
        order,
        summary=f'从采购申请 {purchase_request.purchase_no} 生成采购订单 {order.order_no}',
        after={'status': order.status, 'line_count': len(lines)},
    )
    _notify_purchase_order_users(
        [purchase_request.applicant, actor],
        '采购订单已生成',
        f'采购申请 {purchase_request.purchase_no} 已生成采购订单 {order.order_no}，请继续下达采购。',
        order=order,
    )
    return order


@transaction.atomic
def place_purchase_order(order, actor, expected_delivery_date=None, note=''):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以下达采购订单。')
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise WorkflowError('只有待下达的采购订单可以下达。')
    order.status = PurchaseOrder.Status.ORDERED
    order.ordered_at = timezone.now()
    if expected_delivery_date is not None:
        order.expected_delivery_date = expected_delivery_date
    if note.strip():
        order.note = note.strip()
    order.save(update_fields=['status', 'ordered_at', 'expected_delivery_date', 'note', 'updated_at'])
    record_operation(
        actor,
        'purchase_order_placed',
        order,
        summary=f'下达采购订单 {order.order_no}',
        after={'status': order.status, 'expected_delivery_date': order.expected_delivery_date, 'note': order.note},
    )
    _notify_purchase_order_users(
        [order.purchase_request.applicant, order.created_by],
        '采购订单已下达',
        f'采购订单 {order.order_no} 已下达，后续可登记到货。',
        level='success',
        order=order,
    )
    return order


@transaction.atomic
def cancel_purchase_order(order, actor, reason=''):
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以取消采购订单。')
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in {PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.ORDERED}:
        raise WorkflowError('只有待下达或已下达且尚未到货完成的采购订单可以取消。')
    if order.receipts.exists():
        raise WorkflowError('已有到货登记的采购订单不能取消。')
    before = {'status': order.status, 'note': order.note}
    order.status = PurchaseOrder.Status.CANCELED
    order.canceled_at = timezone.now()
    if reason.strip():
        order.note = reason.strip()
    order.save(update_fields=['status', 'canceled_at', 'note', 'updated_at'])
    record_operation(actor, 'purchase_order_canceled', order, summary=f'取消采购订单 {order.order_no}', before=before, after={'status': order.status, 'note': order.note})
    _notify_purchase_order_users(
        [order.purchase_request.applicant, order.created_by],
        '采购订单已取消',
        f'采购订单 {order.order_no} 已取消。' + (f'原因：{order.note}' if order.note else ''),
        level='warning',
        order=order,
    )
    return order


def _validate_asset_profile_uniqueness(profiles):
    """校验逐件资料自身及与台账的 SN/ERP 编码冲突，返回中文错误列表。"""
    errors = []
    seen_sn, seen_code = {}, {}
    for index, profile in enumerate(profiles, start=1):
        sn = (profile.get('serial_number') or '').strip()
        code = (profile.get('asset_code') or '').strip()
        if sn:
            if sn.lower() in seen_sn:
                errors.append(f'第 {index} 件与第 {seen_sn[sn.lower()]} 件的生产厂家 SN 重复（{sn}）。')
            else:
                seen_sn[sn.lower()] = index
            if Asset.objects.filter(serial_number__iexact=sn).exists():
                errors.append(f'第 {index} 件的生产厂家 SN「{sn}」与台账已有资产重复。')
        if code:
            if code.lower() in seen_code:
                errors.append(f'第 {index} 件与第 {seen_code[code.lower()]} 件的资产编码重复（{code}）。')
            else:
                seen_code[code.lower()] = index
            if Asset.objects.filter(asset_code__iexact=code).exists():
                errors.append(f'第 {index} 件的资产编码「{code}」已被占用。')
    return errors


@transaction.atomic
def receive_purchase_order(order, actor, quantities, warehouse, location, received_date=None, note='', asset_profiles=None):
    """登记一次到货，支持部分到货，并生成正式采购入库单和库存流水。

    asset_profiles：可选，{str(order_line_id): [ {serial_number, asset_code, manufacturer_barcode,
    manufacturer, model}, ... ]}，资产类明细按本次到货数量逐件补充资料；列表长度必须等于该行的
    本次到货数量。SN/ERP 资产编码会做批次内与台账全局去重校验，冲突则整批拒绝。
    """
    if not can_execute_inventory(actor):
        raise WorkflowError('只有具备出入库执行权限的账号可以登记采购到货。')
    order = PurchaseOrder.objects.select_for_update().select_related('purchase_request').get(pk=order.pk)
    if order.status not in {PurchaseOrder.Status.ORDERED, PurchaseOrder.Status.PARTIAL_RECEIVED}:
        raise WorkflowError('只有已下达或部分到货的采购订单可以登记到货。')
    warehouse = Warehouse.objects.filter(pk=getattr(warehouse, 'pk', warehouse)).first()
    location = Location.objects.filter(pk=getattr(location, 'pk', location)).first()
    if not warehouse or not location:
        raise WorkflowError('到货入库必须选择仓库和库位。')
    if location.warehouse_id != warehouse.id:
        raise WorkflowError('所选库位不属于所选仓库。')
    lines = list(order.lines.select_for_update().select_related('item_type', 'stock_item'))
    normalized = {}
    for line in lines:
        raw = quantities.get(str(line.id), quantities.get(line.id, 0))
        try:
            quantity = int(raw or 0)
        except (TypeError, ValueError) as exc:
            raise WorkflowError(f'{line.name_snapshot} 的到货数量必须是整数。') from exc
        if quantity < 0:
            raise WorkflowError(f'{line.name_snapshot} 的到货数量不能为负数。')
        if quantity > line.remaining_quantity:
            raise WorkflowError(f'{line.name_snapshot} 本次最多还能到货 {line.remaining_quantity} {line.unit}。')
        normalized[line.id] = quantity
    selected = [(line, normalized[line.id]) for line in lines if normalized[line.id] > 0]
    if not selected:
        raise WorkflowError('请至少填写一项本次到货数量。')

    normalized_profiles = {}
    if asset_profiles:
        for line, quantity in selected:
            if not line.item_type_id:
                continue
            profiles = asset_profiles.get(str(line.id)) or asset_profiles.get(line.id) or []
            profiles = [dict(profile) for profile in profiles if any((profile.get(key) or '').strip() for key in ('serial_number', 'asset_code', 'manufacturer_barcode', 'manufacturer', 'model'))]
            if len(profiles) > quantity:
                raise WorkflowError(f'{line.name_snapshot} 的逐件资料（{len(profiles)} 条）超过本次到货数量（{quantity}）。')
            profile_errors = _validate_asset_profile_uniqueness(profiles)
            if profile_errors:
                raise WorkflowError(f'{line.name_snapshot} 逐件资料有误：' + '；'.join(profile_errors))
            normalized_profiles[line.id] = profiles

    received_date = received_date or timezone.localdate()
    inventory_request = WorkflowRequest.objects.create(
        request_type=WorkflowRequest.RequestType.PURCHASE_RECEIPT,
        applicant=actor,
        status=WorkflowRequest.Status.DONE,
        reason=f'采购订单 {order.order_no} 到货入库',
        application_date=received_date,
        transaction_date=received_date,
        target_warehouse=warehouse,
        target_location=location,
        is_quick_process=True,
        is_hidden_by_applicant=True,
    )
    inventory_request.inventory_no = build_daily_code('IO', received_date)
    inventory_request.save(update_fields=['inventory_no', 'updated_at'])
    receipt = PurchaseReceipt.objects.create(
        order=order,
        inventory_request=inventory_request,
        received_by=actor,
        received_date=received_date,
        note=note.strip(),
    )
    for line, quantity in selected:
        line_before = line.received_quantity
        created_asset_ids = []
        if line.stock_item_id:
            stock_item = StockItem.objects.select_for_update().get(pk=line.stock_item_id)
            before_quantity = stock_item.quantity
            stock_item.quantity += quantity
            stock_item.warehouse = warehouse
            stock_item.location = location
            stock_item.save(update_fields=['quantity', 'warehouse', 'location', 'updated_at'])
            InventoryTransaction.objects.create(
                request=inventory_request,
                stock_item=stock_item,
                actor=actor,
                action=WorkflowRequest.RequestType.PURCHASE_RECEIPT,
                business_date=received_date,
                quantity=quantity,
                stock_quantity_before=before_quantity,
                stock_quantity_after=stock_item.quantity,
                target_warehouse=warehouse,
                cost_amount=(line.unit_cost * quantity) if line.unit_cost is not None else None,
                comment=f'采购订单 {order.order_no} 到货入库',
            )
            WorkflowRequestLine.objects.create(
                request=inventory_request,
                stock_item=stock_item,
                quantity=quantity,
                note=f'来源采购订单 {order.order_no}',
            )
        elif line.item_type_id:
            item_type = ItemType.objects.get(pk=line.item_type_id)
            profiles = normalized_profiles.get(line.id) or []
            for index in range(quantity):
                profile = profiles[index] if index < len(profiles) else {}
                asset = Asset.objects.create(
                    name=line.name_snapshot,
                    item_type=item_type,
                    warehouse=warehouse,
                    location=location,
                    status=Asset.Status.IN_STOCK,
                    purchase_amount=line.unit_cost,
                    purchase_order_no=order.order_no,
                    received_date=received_date,
                    supplier=line.supplier_snapshot,
                    serial_number=(profile.get('serial_number') or '').strip(),
                    asset_code=(profile.get('asset_code') or '').strip() or None,
                    manufacturer_barcode=(profile.get('manufacturer_barcode') or '').strip(),
                    manufacturer=(profile.get('manufacturer') or '').strip(),
                    model=(profile.get('model') or '').strip(),
                    remarks='采购到货' if profile else '采购到货待补充资产信息',
                )
                created_asset_ids.append(asset.system_asset_no)
                InventoryTransaction.objects.create(
                    request=inventory_request,
                    asset=asset,
                    actor=actor,
                    action=WorkflowRequest.RequestType.PURCHASE_RECEIPT,
                    business_date=received_date,
                    quantity=1,
                    asset_status_before='',
                    asset_status_after=asset.status,
                    target_warehouse=warehouse,
                    cost_amount=line.unit_cost,
                    comment=f'采购订单 {order.order_no} 到货入库',
                )
                WorkflowRequestLine.objects.create(
                    request=inventory_request,
                    asset=asset,
                    quantity=1,
                    note=f'来源采购订单 {order.order_no}',
                )
        else:
            raise WorkflowError(f'{line.name_snapshot} 缺少可入库的物品来源。')
        line.received_quantity += quantity
        line.save(update_fields=['received_quantity', 'updated_at'])
        PurchaseReceiptLine.objects.create(
            receipt=receipt,
            order_line=line,
            received_quantity=quantity,
            destination_warehouse=warehouse,
            destination_location=location,
            created_asset_ids=created_asset_ids,
        )
        record_operation(
            actor,
            'purchase_receipt_line_received',
            line,
            summary=f'采购订单 {order.order_no} 到货：{line.name_snapshot} {quantity} {line.unit}',
            before={'received_quantity': line_before},
            after={'received_quantity': line.received_quantity, 'asset_ids': created_asset_ids},
        )
    order.status = (
        PurchaseOrder.Status.RECEIVED
        if all(line.remaining_quantity == 0 for line in lines)
        else PurchaseOrder.Status.PARTIAL_RECEIVED
    )
    order.save(update_fields=['status', 'updated_at'])
    record_operation(
        actor,
        'purchase_receipt_created',
        receipt,
        summary=f'完成采购到货登记 {receipt.receipt_no}，关联入库单 {inventory_request.inventory_no}',
        after={'order_no': order.order_no, 'status': order.status, 'inventory_no': inventory_request.inventory_no},
    )
    _notify_purchase_order_users(
        [order.purchase_request.applicant, order.created_by, actor],
        '采购到货已登记',
        f'采购订单 {order.order_no} 已登记到货，生成入库单 {inventory_request.inventory_no}。',
        level='success',
        order=order,
    )
    return receipt


ASSET_PROFILE_FIELDS = ('serial_number', 'asset_code', 'manufacturer_barcode', 'manufacturer', 'model')
ASSET_PROFILE_LABELS = {
    'serial_number': '生产厂家 SN',
    'asset_code': '资产编码',
    'manufacturer_barcode': '生产厂家条码',
    'manufacturer': '生产厂家',
    'model': '型号',
}


def order_pending_profile_assets(order):
    """采购订单下仍缺资产资料（SN 和 ERP 编码均为空）的在库资产。"""
    order = PurchaseOrder.objects.select_related('purchase_request').get(pk=getattr(order, 'pk', order))
    return (
        Asset.objects.filter(purchase_order_no=order.order_no)
        .filter(Q(serial_number='') | Q(asset_code__isnull=True))
        .select_related('item_type', 'warehouse', 'location')
        .order_by('system_asset_no')
    )


@transaction.atomic
def bulk_update_asset_profiles(order, actor, rows):
    """按系统资产编号批量补录采购到货资产的资料。

    rows: [{system_asset_no, serial_number, asset_code, manufacturer_barcode, manufacturer, model}]
    - 只允许补录本订单到货生成的资产；
    - SN/ERP 编码与台账全局查重（排除自身），批次内也查重；
    - 任何一行冲突则整批拒绝，不产生半更新；
    - 每个被修改的资产写入操作审计和资产生命周期事件。
    返回更新的资产数量。
    """
    if not can_manage_inventory(actor):
        raise WorkflowError('只有具备录入与基础资料权限的账号可以批量补录资产资料。')
    order = PurchaseOrder.objects.get(pk=getattr(order, 'pk', order))
    if not rows:
        raise WorkflowError('没有需要补录的内容。')

    order_assets = {asset.system_asset_no: asset for asset in Asset.objects.select_for_update().filter(purchase_order_no=order.order_no)}
    normalized = []
    for index, row in enumerate(rows, start=1):
        system_no = (row.get('system_asset_no') or '').strip()
        if not system_no:
            raise WorkflowError(f'第 {index} 行缺少系统资产编号。')
        asset = order_assets.get(system_no)
        if not asset:
            raise WorkflowError(f'第 {index} 行的系统资产编号「{system_no}」不属于采购订单 {order.order_no} 的到货资产。')
        profile = {key: (row.get(key) or '').strip() for key in ASSET_PROFILE_FIELDS}
        normalized.append((index, asset, profile))

    # 批次内与台账查重（台账校验排除本批次将更新的资产）
    batch_ids = {asset.id for _, asset, _ in normalized}
    seen_sn, seen_code = {}, {}
    for index, asset, profile in normalized:
        sn, code = profile['serial_number'], profile['asset_code']
        if sn:
            if sn.lower() in seen_sn:
                raise WorkflowError(f'第 {index} 行与第 {seen_sn[sn.lower()]} 行的生产厂家 SN 重复（{sn}）。')
            seen_sn[sn.lower()] = index
            if Asset.objects.filter(serial_number__iexact=sn).exclude(id__in=batch_ids).exists():
                raise WorkflowError(f'第 {index} 行的生产厂家 SN「{sn}」与台账已有资产重复。')
        if code:
            if code.lower() in seen_code:
                raise WorkflowError(f'第 {index} 行与第 {seen_code[code.lower()]} 行的资产编码重复（{code}）。')
            seen_code[code.lower()] = index
            if Asset.objects.filter(asset_code__iexact=code).exclude(id__in=batch_ids).exists():
                raise WorkflowError(f'第 {index} 行的资产编码「{code}」已被占用。')

    from apps.inventory.models import AssetLifecycleEvent

    updated = 0
    for index, asset, profile in normalized:
        before = {key: getattr(asset, key) or '' for key in ASSET_PROFILE_FIELDS}
        changed = {key: value for key, value in profile.items() if value and value != (before[key] or '')}
        # asset_code 允许从空补录；非空改动也允许（纠错），但空值不覆盖已有值
        if not changed:
            continue
        for key, value in changed.items():
            setattr(asset, key, value if key != 'asset_code' else (value or None))
        if asset.remarks == '采购到货待补充资产信息':
            asset.remarks = '采购到货'
        asset.save()
        updated += 1
        changed_labels = '、'.join(f'{ASSET_PROFILE_LABELS[key]}={value}' for key, value in changed.items())
        record_operation(
            actor,
            'purchase_asset_profile_updated',
            asset,
            summary=f'补录采购到货资产 {asset.system_asset_no} 资料：{changed_labels}',
            before=before,
            after={key: getattr(asset, key) or '' for key in ASSET_PROFILE_FIELDS},
        )
        AssetLifecycleEvent.objects.create(
            asset=asset,
            event_type=AssetLifecycleEvent.EventType.NOTE,
            title='采购到货资料补录',
            description=f'来源采购订单 {order.order_no}；补录：{changed_labels}',
            actor=actor,
            related_model='PurchaseOrder',
            related_object_id=str(order.id),
        )
    if updated == 0:
        raise WorkflowError('所有行的资料与现有内容一致，没有需要更新的字段。')
    record_operation(
        actor,
        'purchase_asset_profiles_bulk_updated',
        order,
        summary=f'采购订单 {order.order_no} 批量补录 {updated} 件到货资产资料',
        after={'updated_count': updated},
    )
    return updated
