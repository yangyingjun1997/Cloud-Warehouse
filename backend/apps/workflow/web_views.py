from datetime import date, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import OperationalError
from django.db.models import Q, Sum
from django.utils import timezone
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.common.permissions import can_approve_workflow, can_execute_inventory, is_system_administrator, is_warehouse_operator
from apps.common.database import retry_database_operation
from apps.inventory.models import Asset, StockItem, Warehouse

from .models import ApprovalTask, InventoryReservation, LoanExtensionRequest, OfflineOperation, PurchaseRequest, StockItemLoan, WorkflowRequest, WorkflowRequestLine
from .offline_services import (
    apply_offline_operation,
    querydict_payload,
    receive_offline_operation,
    reject_offline_operation,
)
from .services import (
    WorkflowError, approve_request, close_request_and_release_reservations,
    create_loan_extension_request, extend_request_reservation,
    hide_request_for_applicant, process_request, reject_request,
    review_loan_extension, withdraw_request,
)


def _role_label(user):
    if is_system_administrator(user):
        return '系统管理员'
    if is_warehouse_operator(user):
        return '仓库人员'
    return '普通员工'


def _base_context(user):
    return {
        'user_is_operator': is_warehouse_operator(user),
        'user_is_system_admin': is_system_administrator(user),
        'user_can_use_admin': is_system_administrator(user),
        'user_role_label': _role_label(user),
    }


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@login_required
def offline_operation_sync(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': '仅支持提交离线操作。'}, status=405)
    if not can_execute_inventory(request.user):
        return JsonResponse({'ok': False, 'message': '当前账号没有离线出入库权限。'}, status=403)
    try:
        operation, created = receive_offline_operation(
            operator=request.user,
            client_operation_id=request.POST.get('client_operation_id', ''),
            client_created_at=request.POST.get('client_created_at', ''),
            payload=querydict_payload(request.POST),
        )
    except WorkflowError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    return JsonResponse({
        'ok': True,
        'created': created,
        'operation_id': str(operation.id),
        'status': operation.status,
        'status_label': operation.get_status_display(),
        'message': '离线记录已送达服务器，等待仓库人员复核后执行。',
    })


@login_required
def offline_operation_list(request):
    if not can_execute_inventory(request.user):
        messages.error(request, '当前账号没有查看离线操作的权限。')
        return redirect('home')
    if request.method == 'POST':
        operation = get_object_or_404(OfflineOperation, pk=request.POST.get('operation_id'))
        action = request.POST.get('action', '')
        try:
            if action == 'apply':
                operation = apply_offline_operation(operation, request.user)
                if operation.status == OfflineOperation.Status.APPLIED:
                    messages.success(request, f'离线记录已执行，生成出入库单 {operation.resulting_request.inventory_no}。')
                else:
                    messages.error(request, f'库存状态发生冲突：{operation.error_message}')
            elif action == 'reject':
                reject_offline_operation(operation, request.user, request.POST.get('reason', ''))
                messages.success(request, '离线记录已拒绝，库存未发生变化。')
            else:
                raise WorkflowError('不支持的离线操作。')
        except WorkflowError as exc:
            messages.error(request, str(exc))
        return redirect('offline_operation_list')

    operations = OfflineOperation.objects.select_related(
        'operator', 'reviewed_by', 'resulting_request'
    )
    selected_status = request.GET.get('status', '')
    query = request.GET.get('q', '').strip()
    if selected_status:
        operations = operations.filter(status=selected_status)
    if query:
        operations = operations.filter(
            Q(operator__username__icontains=query)
            | Q(error_message__icontains=query)
            | Q(resulting_request__inventory_no__icontains=query)
        )
    operations = list(operations[:200])
    def valid_ids(key):
        values = set()
        for operation in operations:
            for item_id in operation.payload.get(key, []):
                try:
                    values.add(str(UUID(str(item_id))))
                except (TypeError, ValueError):
                    continue
        return values

    asset_ids = valid_ids('asset_ids')
    stock_item_ids = valid_ids('stock_item_ids')
    assets = {
        str(item.id): item
        for item in Asset.objects.filter(pk__in=asset_ids).select_related('warehouse', 'location')
    }
    stock_items = {
        str(item.id): item
        for item in StockItem.objects.filter(pk__in=stock_item_ids).select_related('warehouse', 'location')
    }
    request_type_labels = dict(WorkflowRequest.RequestType.choices)
    for operation in operations:
        payload = operation.payload
        value = lambda key: (payload.get(key) or [''])[0] if isinstance(payload.get(key), list) else payload.get(key, '')
        operation.request_type_label = request_type_labels.get(value('request_type'), value('request_type') or '未知类型')
        operation.item_count = len(payload.get('asset_ids', [])) + len(payload.get('stock_item_ids', []))
        operation.contact_source_label = value('contact_source') or '-'
        operation.reason_label = value('reason') or '-'
        operation.recipient_label = value('recipient_name') or '-'
        operation.project_label = value('project_code') or '-'
        operation.work_order_label = value('work_order_no') or '-'
        operation.item_rows = []
        for item_id in payload.get('asset_ids', []):
            item = assets.get(str(item_id))
            operation.item_rows.append({
                'name': item.name if item else '资产已不存在',
                'code': item.asset_code if item else str(item_id),
                'status': item.get_status_display() if item else '无法执行',
                'position': f'{item.warehouse.name if item and item.warehouse else "未分配仓库"} / {item.location.code if item and item.location else "未分配库位"}',
                'quantity': '1 件',
            })
        for item_id in payload.get('stock_item_ids', []):
            item = stock_items.get(str(item_id))
            quantity = value(f'stock_quantity_{item_id}') or '1'
            operation.item_rows.append({
                'name': item.name if item else '耗材配件已不存在',
                'code': item.code if item else str(item_id),
                'status': f'当前库存 {item.quantity} {item.unit}' if item else '无法执行',
                'position': f'{item.warehouse.name if item and item.warehouse else "未分配仓库"} / {item.location.code if item and item.location else "未分配库位"}',
                'quantity': f'{quantity} {item.unit if item else "件"}',
            })
    return render(request, 'workflow/offline_operation_list.html', {
        **_base_context(request.user),
        'operations': operations,
        'status_choices': OfflineOperation.Status.choices,
        'selected_status': selected_status,
        'query': query,
    })


def _filter_workflow_queryset(queryset, params, prefix=''):
    path = prefix
    line_path = f'{path}lines__'
    query = params.get('q', '').strip()
    if query:
        queryset = queryset.filter(
            Q(**{f'{path}request_no__icontains' if prefix else 'request_no__icontains': query})
            | Q(**{f'{path}inventory_no__icontains' if prefix else 'inventory_no__icontains': query})
            | Q(**{f'{path}reason__icontains' if prefix else 'reason__icontains': query})
            | Q(**{f'{path}recipient_name__icontains' if prefix else 'recipient_name__icontains': query})
            | Q(**{f'{path}project_code__icontains' if prefix else 'project_code__icontains': query})
            | Q(**{f'{path}work_order_no__icontains' if prefix else 'work_order_no__icontains': query})
            | Q(**{f'{path}applicant__username__icontains' if prefix else 'applicant__username__icontains': query})
            | Q(**{f'{line_path}asset__search_index__icontains' if prefix else 'lines__asset__search_index__icontains': query})
            | Q(**{f'{line_path}stock_item__name__icontains' if prefix else 'lines__stock_item__name__icontains': query})
            | Q(**{f'{line_path}stock_item__code__icontains' if prefix else 'lines__stock_item__code__icontains': query})
        )
    for field in ('request_type', 'status'):
        value = params.get(field, '').strip()
        if value:
            queryset = queryset.filter(**{f'{path}{field}' if prefix else field: value})
    if params.get('warehouse'):
        warehouse_lookup = f'{path}target_warehouse_id' if prefix else 'target_warehouse_id'
        asset_warehouse_lookup = f'{path}lines__asset__warehouse_id' if prefix else 'lines__asset__warehouse_id'
        stock_warehouse_lookup = f'{path}lines__stock_item__warehouse_id' if prefix else 'lines__stock_item__warehouse_id'
        queryset = queryset.filter(
            Q(**{warehouse_lookup: params['warehouse']})
            | Q(**{asset_warehouse_lookup: params['warehouse']})
            | Q(**{stock_warehouse_lookup: params['warehouse']})
        )
    for field in ('project_code', 'work_order_no'):
        if params.get(field):
            queryset = queryset.filter(**{f'{path}{field}__icontains' if prefix else f'{field}__icontains': params[field].strip()})
    for field, lookup in (
        ('application_start', 'application_date__gte'),
        ('application_end', 'application_date__lte'),
        ('return_start', 'expected_return_date__gte'),
        ('return_end', 'expected_return_date__lte'),
    ):
        parsed = _parse_date(params.get(field, ''))
        if parsed:
            queryset = queryset.filter(**{f'{path}{lookup}' if prefix else lookup: parsed})
    return queryset.distinct()


def _can_view_request(user, workflow_request):
    if workflow_request.applicant_id == user.id:
        return not workflow_request.is_hidden_by_applicant
    if not is_warehouse_operator(user):
        return False
    task = workflow_request.approval_tasks.first()
    if workflow_request.status == WorkflowRequest.Status.WAITING_WAREHOUSE:
        return True
    return bool(task and (task.assigned_to_id is None or task.assigned_to_id == user.id))


@login_required
def approval_queue(request):
    if not (can_approve_workflow(request.user) or can_execute_inventory(request.user)):
        messages.error(request, '当前账号没有仓库审批权限。')
        return redirect('home')
    pending_tasks = ApprovalTask.objects.filter(
        status=ApprovalTask.Status.PENDING,
    ).filter(
        Q(assigned_to__isnull=True) | Q(assigned_to=request.user)
    ).select_related('request', 'request__applicant', 'assigned_to').prefetch_related(
        'request__lines__asset__item_type', 'request__lines__asset__warehouse',
        'request__lines__asset__location', 'request__lines__stock_item__item_type',
        'request__lines__stock_item__warehouse', 'request__lines__stock_item__location',
    ).order_by('request__application_date', 'request__created_at')
    pending_tasks = _filter_workflow_queryset(pending_tasks, request.GET, prefix='request__')
    waiting_requests = WorkflowRequest.objects.filter(
        status=WorkflowRequest.Status.WAITING_WAREHOUSE,
    ).select_related('applicant').prefetch_related(
        'lines__asset__item_type', 'lines__asset__warehouse', 'lines__asset__location',
        'lines__stock_item__item_type', 'lines__stock_item__warehouse', 'lines__stock_item__location',
    ).order_by('application_date', 'created_at')
    waiting_requests = _filter_workflow_queryset(waiting_requests, request.GET)
    completed_tasks = ApprovalTask.objects.filter(
        approver=request.user,
        status__in=[ApprovalTask.Status.APPROVED, ApprovalTask.Status.REJECTED],
    ).select_related('request').prefetch_related(
        'request__lines__asset__item_type', 'request__lines__asset__warehouse',
        'request__lines__asset__location', 'request__lines__stock_item__item_type',
        'request__lines__stock_item__warehouse', 'request__lines__stock_item__location',
    ).order_by('-acted_at')
    completed_tasks = _filter_workflow_queryset(completed_tasks, request.GET, prefix='request__')[:8]
    pending_purchase_requests = PurchaseRequest.objects.none()
    if can_approve_workflow(request.user):
        pending_purchase_requests = PurchaseRequest.objects.filter(
            status=PurchaseRequest.Status.PENDING_APPROVAL,
        ).filter(
            Q(designated_approver__isnull=True) | Q(designated_approver=request.user),
        ).select_related('applicant', 'designated_approver').prefetch_related('lines').order_by(
            'application_date', 'created_at',
        )
    return render(request, 'workflow/approval_queue.html', {
        **_base_context(request.user),
        'pending_tasks': pending_tasks,
        'waiting_requests': waiting_requests,
        'completed_tasks': completed_tasks,
        'pending_purchase_requests': pending_purchase_requests,
        'query': request.GET.get('q', '').strip(),
        'selected_request_type': request.GET.get('request_type', ''),
        'selected_status': request.GET.get('status', ''),
        'selected_warehouse': request.GET.get('warehouse', ''),
        'project_code': request.GET.get('project_code', ''),
        'work_order_no': request.GET.get('work_order_no', ''),
        'application_start': request.GET.get('application_start', ''),
        'application_end': request.GET.get('application_end', ''),
        'return_start': request.GET.get('return_start', ''),
        'return_end': request.GET.get('return_end', ''),
        'request_types': [choice for choice in WorkflowRequest.RequestType.choices if choice[0] != WorkflowRequest.RequestType.PURCHASE_RECEIPT],
        'status_choices': WorkflowRequest.Status.choices,
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
    })


@login_required
def reservation_dashboard(request):
    if not is_warehouse_operator(request.user):
        messages.error(request, '当前账号没有仓库业务权限。')
        return redirect('home')
    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            if action in {'close_reservation', 'extend_reservation'}:
                request_obj = WorkflowRequest.objects.filter(pk=request.POST.get('request_id')).first()
                if not request_obj:
                    raise WorkflowError('未找到对应申请。')
                if action == 'close_reservation':
                    close_request_and_release_reservations(
                        request_obj,
                        request.user,
                        request.POST.get('reason', ''),
                    )
                    messages.success(request, '申请已关闭，全部预约库存已释放。')
                else:
                    expires_at = extend_request_reservation(
                        request_obj,
                        request.user,
                        request.POST.get('hours', 24),
                        request.POST.get('reason', ''),
                    )
                    messages.success(request, f'预约已延长至 {expires_at:%Y-%m-%d %H:%M}。')
            elif action in {'approve_extension', 'reject_extension'}:
                extension = LoanExtensionRequest.objects.filter(pk=request.POST.get('extension_id')).first()
                if not extension:
                    raise WorkflowError('未找到延期申请。')
                review_loan_extension(
                    extension,
                    request.user,
                    action == 'approve_extension',
                    request.POST.get('comment', ''),
                )
                messages.success(request, '延期申请已处理。')
            else:
                raise WorkflowError('不支持的操作。')
        except WorkflowError as exc:
            messages.error(request, str(exc))
        except OperationalError:
            messages.error(request, '数据库正在处理其他操作，请稍后重试。')
        query_string = request.POST.get('return_query', '').strip()
        return redirect(f'{request.path}?{query_string}' if query_string else request.path)

    now = timezone.now()
    reservations = InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
        request_line__request__status__in=[
            WorkflowRequest.Status.PENDING,
            WorkflowRequest.Status.WAITING_WAREHOUSE,
        ],
    ).select_related(
        'request_line__request__applicant', 'request_line__request__department',
        'request_line__asset__warehouse', 'request_line__asset__location',
        'request_line__stock_item__warehouse', 'request_line__stock_item__location',
    )
    query = request.GET.get('q', '').strip()
    if query:
        reservations = reservations.filter(
            Q(request_line__request__request_no__icontains=query)
            | Q(request_line__request__applicant__username__icontains=query)
            | Q(request_line__request__project_code__icontains=query)
            | Q(request_line__request__work_order_no__icontains=query)
            | Q(request_line__asset__search_index__icontains=query)
            | Q(request_line__stock_item__name__icontains=query)
            | Q(request_line__stock_item__code__icontains=query)
        )
    selected_status = request.GET.get('status', '')
    if selected_status == 'pending':
        reservations = reservations.filter(request_line__request__status=WorkflowRequest.Status.PENDING)
    elif selected_status == 'waiting':
        reservations = reservations.filter(request_line__request__status=WorkflowRequest.Status.WAITING_WAREHOUSE)
    elif selected_status == 'expiring':
        reservations = reservations.filter(expires_at__isnull=False, expires_at__lte=now + timedelta(hours=24))
    selected_warehouse = request.GET.get('warehouse', '')
    if selected_warehouse:
        reservations = reservations.filter(
            Q(request_line__asset__warehouse_id=selected_warehouse)
            | Q(request_line__stock_item__warehouse_id=selected_warehouse)
        )
    for field, lookup in (
        ('application_start', 'request_line__request__application_date__gte'),
        ('application_end', 'request_line__request__application_date__lte'),
        ('return_start', 'request_line__request__expected_return_date__gte'),
        ('return_end', 'request_line__request__expected_return_date__lte'),
    ):
        parsed = _parse_date(request.GET.get(field, ''))
        if parsed:
            reservations = reservations.filter(**{lookup: parsed})
    reservations = list(reservations.order_by('expires_at', 'created_at')[:300])
    for reservation in reservations:
        reservation.is_expiring = bool(
            reservation.expires_at and reservation.expires_at <= now + timedelta(hours=24)
        )

    active_asset_count = InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
        asset__isnull=False,
    ).count()
    active_stock_quantity = InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
        stock_item__isnull=False,
    ).aggregate(total=Sum('quantity'))['total'] or 0
    expiring_reservation_count = InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
        expires_at__isnull=False,
        expires_at__lte=now + timedelta(hours=24),
        request_line__request__status=WorkflowRequest.Status.PENDING,
    ).count()
    overdue_asset_rows = list(WorkflowRequestLine.objects.filter(
        request__request_type=WorkflowRequest.RequestType.BORROW,
        request__status=WorkflowRequest.Status.DONE,
        request__expected_return_date__lt=timezone.localdate(),
        asset__status='borrowed',
    ).select_related(
        'request__applicant', 'asset__item_type', 'asset__warehouse', 'asset__location',
    ).order_by('request__expected_return_date', 'request__applicant__username')[:100])
    overdue_stock_rows = list(StockItemLoan.objects.filter(
        status=StockItemLoan.Status.ACTIVE,
        outstanding_quantity__gt=0,
        expected_return_date__lt=timezone.localdate(),
    ).select_related(
        'borrower', 'stock_item__warehouse', 'stock_item__location', 'source_line__request',
    ).order_by('expected_return_date', 'borrower__username')[:100])
    overdue_asset_count = len(overdue_asset_rows)
    overdue_stock_quantity = sum(item.outstanding_quantity for item in overdue_stock_rows)
    pending_extensions = LoanExtensionRequest.objects.filter(
        status=LoanExtensionRequest.Status.PENDING,
    ).select_related('applicant', 'source_request').prefetch_related(
        'source_request__lines__asset', 'source_request__lines__stock_item',
    ).order_by('created_at')
    return render(request, 'workflow/reservation_dashboard.html', {
        **_base_context(request.user),
        'reservations': reservations,
        'active_asset_count': active_asset_count,
        'active_stock_quantity': active_stock_quantity,
        'expiring_reservation_count': expiring_reservation_count,
        'overdue_asset_count': overdue_asset_count,
        'overdue_stock_quantity': overdue_stock_quantity,
        'overdue_asset_rows': overdue_asset_rows,
        'overdue_stock_rows': overdue_stock_rows,
        'pending_extensions': pending_extensions,
        'can_manage_reservations': can_approve_workflow(request.user),
        'query': query,
        'selected_status': selected_status,
        'selected_warehouse': selected_warehouse,
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'current_query': request.GET.urlencode(),
        'application_start': request.GET.get('application_start', ''),
        'application_end': request.GET.get('application_end', ''),
        'return_start': request.GET.get('return_start', ''),
        'return_end': request.GET.get('return_end', ''),
        'now': now,
    })


@login_required
def loan_extension_create(request, request_id):
    source_request = WorkflowRequest.objects.filter(
        pk=request_id,
        applicant=request.user,
        request_type=WorkflowRequest.RequestType.BORROW,
        status=WorkflowRequest.Status.DONE,
    ).first()
    if not source_request:
        raise Http404('未找到可延期的借用单。')
    if request.method != 'POST':
        return redirect('my_loans')
    try:
        create_loan_extension_request(
            source_request,
            request.user,
            request.POST.get('requested_return_date', ''),
            request.POST.get('reason', ''),
        )
        messages.success(request, '延期申请已提交，等待审批。')
    except WorkflowError as exc:
        messages.error(request, str(exc))
    except OperationalError:
        messages.error(request, '数据库正在处理其他操作，请稍后重试。')
    return redirect('my_loans')


@login_required
def workflow_detail(request, request_id):
    workflow_request = WorkflowRequest.objects.select_related(
        'applicant', 'department', 'designated_approver', 'target_warehouse', 'target_location'
    ).prefetch_related(
        'lines__asset__item_type', 'lines__asset__warehouse', 'lines__asset__location',
        'lines__stock_item__item_type', 'lines__stock_item__warehouse', 'lines__stock_item__location',
        'approval_tasks__assigned_to', 'approval_tasks__approver',
        'approval_logs__actor', 'inventory_transactions__actor',
        'inventory_transactions__asset', 'inventory_transactions__stock_item',
    ).filter(pk=request_id).first()
    if not workflow_request or not _can_view_request(request.user, workflow_request):
        raise Http404('未找到可查看的出入库申请。')
    approval_task = workflow_request.approval_tasks.first()
    can_approve = bool(
        can_approve_workflow(request.user)
        and workflow_request.status == WorkflowRequest.Status.PENDING
        and approval_task
        and (approval_task.assigned_to_id is None or approval_task.assigned_to_id == request.user.id)
    )
    can_process = bool(
        can_execute_inventory(request.user)
        and workflow_request.status == WorkflowRequest.Status.WAITING_WAREHOUSE
    )
    can_withdraw = bool(
        workflow_request.applicant_id == request.user.id
        and workflow_request.status == WorkflowRequest.Status.PENDING
    )
    can_hide = bool(
        workflow_request.applicant_id == request.user.id
        and workflow_request.status in {
            WorkflowRequest.Status.REJECTED,
            WorkflowRequest.Status.CANCELED,
            WorkflowRequest.Status.CLOSED,
        }
    )
    if request.method == 'POST':
        action = request.POST.get('action')
        comment = request.POST.get('comment', '').strip()
        try:
            if action == 'approve' and can_approve:
                retry_database_operation(lambda: approve_request(workflow_request, request.user, comment))
                messages.success(request, '已同意该申请，审批人已自动记录为当前登录账号。')
            elif action == 'reject' and can_approve:
                retry_database_operation(lambda: reject_request(workflow_request, request.user, comment))
                messages.success(request, '已拒绝该申请，并已将原因通知申请人。')
            elif action == 'process' and can_process:
                return_received = request.POST.getlist('return_received_lines') or None
                retry_database_operation(lambda: process_request(
                    workflow_request,
                    request.user,
                    comment,
                    transaction_date=request.POST.get('transaction_date'),
                    return_received_line_ids=return_received,
                ))
                if return_received is not None and workflow_request.request_type == WorkflowRequest.RequestType.RETURN:
                    total = workflow_request.lines.filter(asset__isnull=False).count()
                    if len(return_received) < total:
                        messages.warning(request, f'出入库已执行。差异验收：{total - len(return_received)} 件组件未收回，已记录差异并通知申请人。')
                    else:
                        messages.success(request, '出入库已执行，库存动作台账已生成。')
                else:
                    messages.success(request, '出入库已执行，库存动作台账已生成。')
            elif action == 'withdraw' and can_withdraw:
                retry_database_operation(lambda: withdraw_request(workflow_request, request.user, comment))
                messages.success(request, '申请已撤回，仓库人员已收到通知。')
            elif action == 'hide' and can_hide:
                retry_database_operation(lambda: hide_request_for_applicant(workflow_request, request.user))
                messages.success(request, '申请已从“我的申请”中删除，审计记录仍由管理员保留。')
                return redirect('request_list')
            else:
                raise WorkflowError('当前状态不允许执行该操作。')
        except WorkflowError as exc:
            messages.error(request, str(exc))
        except OperationalError:
            messages.error(request, '数据库正在处理其他操作，请稍后重试。')
        return redirect('workflow_detail', request_id=workflow_request.id)
    return render(request, 'workflow/workflow_detail.html', {
        **_base_context(request.user),
        'workflow_request': workflow_request,
        'approval_task': approval_task,
        'can_approve': can_approve,
        'can_process': can_process,
        'can_withdraw': can_withdraw,
        'can_hide': can_hide,
    })
