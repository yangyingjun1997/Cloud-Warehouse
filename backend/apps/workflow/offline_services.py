from datetime import date
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.audit import record_operation
from apps.common.permissions import can_execute_inventory
from apps.inventory.models import Asset, Customer, Location, Project, StockItem, Warehouse, WorkOrder

from .models import OfflineOperation, WorkflowRequest, WorkflowRequestLine
from .services import WorkflowError, quick_process_request


def querydict_payload(data):
    ignored = {'csrfmiddlewaretoken', 'offline_sync', 'client_operation_id', 'client_created_at'}
    return {
        key: [str(value) for value in data.getlist(key)]
        for key in data.keys()
        if key not in ignored
    }


def _values(payload, key):
    value = payload.get(key, [])
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if value not in (None, '') else []


def _value(payload, key, default=''):
    values = _values(payload, key)
    return values[0].strip() if values else default


def _uuid(value, label):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise WorkflowError(f'{label}格式不正确。') from exc


def _date(value, label, *, default=None):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WorkflowError(f'{label}格式不正确。') from exc


def _object(model, value, label):
    object_id = _uuid(value, label)
    if not object_id:
        return None
    instance = model.objects.filter(pk=object_id).first()
    if not instance:
        raise WorkflowError(f'所选{label}已不存在。')
    return instance


@transaction.atomic
def receive_offline_operation(*, operator, client_operation_id, client_created_at, payload):
    if not can_execute_inventory(operator):
        raise WorkflowError('当前账号没有离线出入库登记权限。')
    operation_id = _uuid(client_operation_id, '客户端操作编号')
    parsed_created_at = parse_datetime(client_created_at or '')
    if not parsed_created_at:
        raise WorkflowError('客户端登记时间格式不正确。')
    if timezone.is_naive(parsed_created_at):
        parsed_created_at = timezone.make_aware(parsed_created_at, timezone.get_current_timezone())

    operation, created = OfflineOperation.objects.select_for_update().get_or_create(
        client_operation_id=operation_id,
        defaults={
            'operator': operator,
            'client_created_at': parsed_created_at,
            'payload': payload,
            'retry_count': 1,
        },
    )
    if operation.operator_id != operator.id:
        raise WorkflowError('该离线操作编号已由其他账号使用。')
    if not created:
        operation.retry_count += 1
        operation.save(update_fields=['retry_count', 'updated_at'])
        return operation, False

    record_operation(
        operator,
        'offline_operation_received',
        operation,
        summary=f'接收离线快速出入库 {operation.client_operation_id}',
        after={'status': operation.status, 'client_created_at': parsed_created_at.isoformat()},
    )
    return operation, True


def _build_quick_request(operation):
    payload = operation.payload
    request_type = _value(payload, 'request_type')
    if request_type not in dict(WorkflowRequest.RequestType.choices):
        raise WorkflowError('离线记录中的出入库类型无效。')

    reason = _value(payload, 'reason')
    contact_source = _value(payload, 'contact_source')
    recipient_name = _value(payload, 'recipient_name')
    recipient_phone = _value(payload, 'recipient_phone')
    usage_location = _value(payload, 'usage_location')
    if not reason or not contact_source:
        raise WorkflowError('离线记录缺少处理原因或沟通来源。')
    outbound_types = {
        WorkflowRequest.RequestType.BORROW,
        WorkflowRequest.RequestType.ISSUE,
        WorkflowRequest.RequestType.SALE,
    }
    if request_type in outbound_types and (not recipient_name or not recipient_phone or not usage_location):
        raise WorkflowError('该出库类型缺少接收人、联系电话或使用地点。')

    transaction_date = _date(
        _value(payload, 'transaction_date'),
        '出入库日期',
        default=timezone.localdate(operation.client_created_at),
    )
    expected_return_date = _date(_value(payload, 'expected_return_date'), '预计归还日期')
    if request_type == WorkflowRequest.RequestType.BORROW and not expected_return_date:
        raise WorkflowError('借用记录缺少预计归还日期。')

    target_warehouse = _object(Warehouse, _value(payload, 'target_warehouse'), '目标仓库')
    target_location = _object(Location, _value(payload, 'target_location'), '目标库位')
    if request_type in {WorkflowRequest.RequestType.RETURN, WorkflowRequest.RequestType.TRANSFER}:
        if not target_warehouse or not target_location:
            raise WorkflowError('归还或调拨记录缺少目标仓库和目标库位。')
    if target_location and target_warehouse and target_location.warehouse_id != target_warehouse.id:
        raise WorkflowError('目标库位不属于所选目标仓库。')

    asset_ids = [_uuid(value, '资产编号') for value in _values(payload, 'asset_ids')]
    stock_ids = [_uuid(value, '耗材配件编号') for value in _values(payload, 'stock_item_ids')]
    assets = list(Asset.objects.filter(pk__in=asset_ids))
    stock_items = list(StockItem.objects.filter(pk__in=stock_ids))
    if len(assets) != len(set(asset_ids)) or len(stock_items) != len(set(stock_ids)):
        raise WorkflowError('离线记录中的部分物品已被删除或编号无效。')
    if not assets and not stock_items:
        raise WorkflowError('离线记录没有待处理物品。')
    if request_type in {
        WorkflowRequest.RequestType.REPAIR,
        WorkflowRequest.RequestType.DAMAGE,
        WorkflowRequest.RequestType.RETURN_TO_VENDOR,
        WorkflowRequest.RequestType.SCRAP,
    } and stock_items:
        raise WorkflowError('维修、报损、退货给供应商和报废只能处理单件资产。')

    customer = _object(Customer, _value(payload, 'customer_ref'), '客户')
    project = _object(Project, _value(payload, 'project_ref'), '项目')
    work_order = _object(WorkOrder, _value(payload, 'work_order_ref'), '工单')
    quick_request = WorkflowRequest.objects.create(
        request_type=request_type,
        applicant=operation.operator,
        department=getattr(getattr(operation.operator, 'profile', None), 'department', None),
        reason=reason,
        recipient_name=recipient_name,
        recipient_company=_value(payload, 'recipient_company'),
        recipient_phone=recipient_phone,
        usage_location=usage_location,
        customer_ref=customer,
        project_ref=project,
        work_order_ref=work_order,
        project_code=_value(payload, 'project_code'),
        work_order_no=_value(payload, 'work_order_no'),
        application_date=transaction_date,
        expected_return_date=expected_return_date,
        transaction_date=transaction_date,
        target_warehouse=target_warehouse,
        target_location=target_location,
        is_quick_process=True,
        contact_source=contact_source,
    )
    WorkflowRequestLine.objects.bulk_create([
        *[WorkflowRequestLine(request=quick_request, asset=asset, quantity=1) for asset in assets],
        *[
            WorkflowRequestLine(
                request=quick_request,
                stock_item=stock_item,
                quantity=_stock_quantity(payload, stock_item),
            )
            for stock_item in stock_items
        ],
    ])
    return quick_request, transaction_date


def _stock_quantity(payload, stock_item):
    raw_value = _value(payload, f'stock_quantity_{stock_item.id}', '1')
    try:
        quantity = int(raw_value)
    except ValueError as exc:
        raise WorkflowError(f'{stock_item.name} 的数量必须是整数。') from exc
    if quantity < 1:
        raise WorkflowError(f'{stock_item.name} 的数量必须大于 0。')
    return quantity


def apply_offline_operation(operation, actor):
    if not can_execute_inventory(actor):
        raise WorkflowError('当前账号没有复核离线出入库的权限。')
    with transaction.atomic():
        operation = OfflineOperation.objects.select_for_update().get(pk=operation.pk)
        if operation.status == OfflineOperation.Status.APPLIED:
            return operation
        if operation.status == OfflineOperation.Status.REJECTED:
            raise WorkflowError('该离线操作已被拒绝，不能再次执行。')
        operation.retry_count += 1
        try:
            with transaction.atomic():
                quick_request, transaction_date = _build_quick_request(operation)
                quick_process_request(
                    quick_request,
                    actor,
                    _value(operation.payload, 'comment'),
                    transaction_date=transaction_date,
                )
        except (ValueError, WorkflowError) as exc:
            operation.status = OfflineOperation.Status.CONFLICT
            operation.error_message = str(exc)
            operation.conflict_details = {
                'message': str(exc),
                'checked_at': timezone.now().isoformat(),
            }
            operation.reviewed_by = actor
            operation.reviewed_at = timezone.now()
            operation.save(update_fields=[
                'status', 'error_message', 'conflict_details', 'reviewed_by',
                'reviewed_at', 'retry_count', 'updated_at',
            ])
            record_operation(
                actor,
                'offline_operation_conflict',
                operation,
                summary=f'离线出入库冲突：{exc}',
                after={'status': operation.status, 'error': str(exc)},
            )
            return operation

        operation.status = OfflineOperation.Status.APPLIED
        operation.resulting_request = quick_request
        operation.error_message = ''
        operation.conflict_details = {}
        operation.applied_at = timezone.now()
        operation.reviewed_by = actor
        operation.reviewed_at = operation.applied_at
        operation.save(update_fields=[
            'status', 'resulting_request', 'error_message', 'conflict_details',
            'applied_at', 'reviewed_by', 'reviewed_at', 'retry_count', 'updated_at',
        ])
        record_operation(
            actor,
            'offline_operation_applied',
            operation,
            summary=f'执行离线出入库 {quick_request.inventory_no}',
            after={'status': operation.status, 'request_id': str(quick_request.id)},
        )
        return operation


@transaction.atomic
def reject_offline_operation(operation, actor, reason):
    if not can_execute_inventory(actor):
        raise WorkflowError('当前账号没有复核离线出入库的权限。')
    operation = OfflineOperation.objects.select_for_update().get(pk=operation.pk)
    if operation.status == OfflineOperation.Status.APPLIED:
        raise WorkflowError('已执行的离线操作不能拒绝。')
    if operation.status == OfflineOperation.Status.REJECTED:
        return operation
    reason = reason.strip()
    if not reason:
        raise WorkflowError('拒绝离线操作时必须填写原因。')
    operation.status = OfflineOperation.Status.REJECTED
    operation.error_message = reason
    operation.reviewed_by = actor
    operation.reviewed_at = timezone.now()
    operation.save(update_fields=['status', 'error_message', 'reviewed_by', 'reviewed_at', 'updated_at'])
    record_operation(
        actor,
        'offline_operation_rejected',
        operation,
        summary=f'拒绝离线出入库：{reason}',
        after={'status': operation.status, 'reason': reason},
    )
    return operation
