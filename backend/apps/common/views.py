"""售后仓库系统的 Web 页面视图集合。

说明：本文件按业务域分区（锚点注释 `# region`），后续如继续膨胀可按分区
物理拆分到 `apps/<domain>/web_views/`。当前分区：

- 通用（登录、工作台、搜索、通知、偏好设置）
- 仓库台账（warehouse_management、物品编辑、低库存、成品设备）
- 库存导入与盘点（inventory_import、inventory_check_*）
- 资产生命周期（asset_lifecycle、附件下载）
- 审批申请（request_create、request_list、快速出入库）
- 报表与导出（report_dashboard、transaction_*）
- 基础资料维护（warehouse_setup_*、permission_management）

注意：`apps.common.urls` 用 `views.<name>` 引用本文件全部函数，移动函数时
必须同步更新 URL 配置，否则会 404。
"""
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
import base64
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID
from decimal import Decimal
from urllib.parse import quote, urlencode

import qrcode
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Count, F, IntegerField, Max, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek, TruncYear
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.common.audit import form_change_data, record_operation
from apps.common.database import retry_database_operation
from apps.common.consistency import build_consistency_issues
from apps.common.decorators import role_required
from apps.common.models import OperationAuditLog, UserListPreference
from apps.common.permissions import (
    PERMISSION_GROUPS, can_approve_workflow, can_execute_inventory, can_manage_inventory, can_view_reports,
    is_system_administrator, is_warehouse_operator,
)
from apps.accounts.models import Department, UserProfile
from apps.inventory.forms import (
    AssetAttachmentForm,
    AssetHandoverForm,
    AssetManagementForm,
    CategoryManagementForm,
    CustomerManagementForm,
    ItemTypeManagementForm,
    LocationManagementForm,
    DamageApprovalForm,
    MaintenanceCreateForm,
    MaintenanceFinishForm,
    MaintenanceUpdateForm,
    ScrapApprovalForm,
    ServiceProviderManagementForm,
    StockItemManagementForm,
    SupplierManagementForm,
    WarehouseManagementForm,
    ProjectManagementForm,
    WorkOrderManagementForm,
)
from apps.inventory.lifecycle_services import (
    AssetLifecycleError,
    archive_asset_attachment,
    create_damage_approval_request,
    create_handover,
    create_maintenance_record,
    create_scrap_approval_request,
    finish_handover,
    finish_maintenance_record,
    update_maintenance_record,
)
from apps.inventory.availability import available_asset_filter, in_warehouse_asset_filter, is_asset_available
from apps.inventory.conversion_services import (
    InventoryConversionError,
    convert_assets_to_stock,
    convert_stock_to_assets,
)
from apps.inventory.import_services import (
    InventoryImportError,
    apply_inventory_import,
    build_inventory_import_template,
    create_import_batch,
    parse_inventory_workbook,
    validate_inventory_records,
)
from apps.inventory.assembly_import import BomImportError, build_bom_template, import_unit_bom
from apps.inventory.assembly_services import (
    AssemblyError,
    add_components,
    create_unit,
    delete_unit,
    disassemble_unit,
    remove_component,
)
from apps.inventory.models import (
    Asset,
    AssetAttachment,
    AssetHandover,
    AssetLifecycleEvent,
    AssetNetworkEndpoint,
    Category,
    ComponentLink,
    CompositeUnit,
    Customer,
    ImportBatch,
    ImportIssue,
    InventoryCheckAdjustment,
    InventoryCheckLine,
    InventoryCheckScan,
    InventoryCheckTask,
    InventoryConversion,
    ItemType,
    Location,
    MaintenanceRecord,
    Project,
    ServiceProvider,
    StockItem,
    StockItemHolding,
    Warehouse,
    Supplier,
    WorkOrder,
)
from apps.inventory.search import asset_search_query
from apps.inventory.check_services import (
    InventoryCheckError,
    cancel_check_task,
    create_check_task,
    correct_check_line,
    reopen_check_task,
    review_check_task,
    scan_check_value,
    start_check_task,
    submit_check_task,
)
from apps.inventory.check_exports import export_check_task_xlsx
from apps.notifications.models import LowStockAlert, Notification
from apps.notifications.dingtalk import dingtalk_configuration_status
from apps.workflow.models import (
    ApprovalLog,
    ApprovalTask,
    InventoryReservation,
    InventoryTransaction,
    LoanExtensionRequest,
    PurchaseRequest,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceiptLine,
    ReturnReminderLog,
    StockItemLoan,
    WorkflowRequest,
    WorkflowRequestLine,
    WorkflowRequestTemplate,
    OfflineOperation,
)
from apps.workflow.services import (
    WorkflowError,
    create_purchase_draft,
    create_purchase_order,
    place_purchase_order,
    receive_purchase_order,
    cancel_purchase_order,
    delete_purchase_draft,
    approve_purchase_request,
    quick_process_request,
    reject_purchase_request,
    reopen_purchase_request,
    submit_purchase_request,
    submit_request,
    update_purchase_draft,
)

User = get_user_model()

SETUP_SECTIONS = {
    'warehouses': ('仓库', Warehouse, WarehouseManagementForm),
    'locations': ('库位', Location, LocationManagementForm),
    'categories': ('物品分类', Category, CategoryManagementForm),
    'item-types': ('物品类型', ItemType, ItemTypeManagementForm),
    'customers': ('客户资料', Customer, CustomerManagementForm),
    'suppliers': ('供应商资料', Supplier, SupplierManagementForm),
    'service-providers': ('维修服务商', ServiceProvider, ServiceProviderManagementForm),
    'projects': ('项目资料', Project, ProjectManagementForm),
    'work-orders': ('工单资料', WorkOrder, WorkOrderManagementForm),
}


def _asset_qr_image(asset) -> str:
    """Return a compact inline QR image for authenticated warehouse screens.

    仓库系统编号是本系统二维码的稳定内容。
    """
    if not asset or not asset.system_asset_no:
        return ''
    image = qrcode.make(asset.qr_value or asset.system_asset_no)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def health(request):
    try:
        connection.ensure_connection()
    except DatabaseError:
        return JsonResponse({
            'status': 'unavailable',
            'service': 'warehouse-backend',
            'database': 'unavailable',
        }, status=503)
    return JsonResponse({
        'status': 'ok',
        'service': 'warehouse-backend',
        'database': 'ok',
    })


@login_required
def list_preference_save(request):
    """保存当前用户的列表展示偏好（每页条数、可选列等）。

    前端在切换 page_size 或列显示时静默 POST；下次进入同一列表时
    优先使用已保存的偏好，URL 参数仍然优先（便于分享链接）。
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': '仅支持 POST'}, status=405)
    list_key = request.POST.get('list_key', '').strip()
    if not list_key or len(list_key) > 80:
        return JsonResponse({'ok': False, 'error': 'list_key 无效'}, status=400)
    page_size = request.POST.get('page_size', '').strip()
    columns_raw = request.POST.get('columns', '').strip()
    updates = {}
    if page_size in {'50', '100', '200'}:
        updates['page_size'] = int(page_size)
    if columns_raw:
        updates['columns'] = [col.strip() for col in columns_raw.split(',') if col.strip()][:20]
    if not updates:
        return JsonResponse({'ok': False, 'error': '没有可保存的偏好项'}, status=400)
    preference, _ = UserListPreference.objects.get_or_create(
        user=request.user,
        list_key=list_key,
        defaults={'columns': []},
    )
    merged = {**preference.columns} if isinstance(preference.columns, dict) else {}
    merged.update(updates)
    preference.columns = merged
    preference.save(update_fields=['columns', 'updated_at'])
    return JsonResponse({'ok': True, 'saved': updates})


def service_worker(request):
    response = HttpResponse(
        """const STATIC_CACHE = 'warehouse-static-v4';
const STATIC_ASSETS = [
  '/static/warehouse/manifest.webmanifest',
  '/static/warehouse/icon.svg',
  '/static/warehouse/scanner.js',
  '/static/warehouse/offline.js',
  '/static/warehouse/vendor/zxing-browser-0.1.5.min.js',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(STATIC_CACHE).then(cache => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys
      .filter(key => key.startsWith('warehouse-pages-') || (key.startsWith('warehouse-static-') && key !== STATIC_CACHE))
      .map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener('message', event => {
  if (event.data?.type === 'CLEAR_PAGE_CACHES') {
    event.waitUntil(caches.keys().then(keys => Promise.all(
      keys.filter(key => key.startsWith('warehouse-pages-')).map(key => caches.delete(key))
    )));
  }
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;
  const url = new URL(request.url);
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(request).then(cached => cached || fetch(request).then(response => {
      const copy = response.clone();
      caches.open(STATIC_CACHE).then(cache => cache.put(request, copy));
      return response;
    })));
    return;
  }
});
""",
        content_type='application/javascript',
    )
    response['Cache-Control'] = 'no-cache'
    return response


@login_required
def logout_view(request):
    if request.method != 'POST':
        return redirect('home')
    try:
        retry_database_operation(lambda: auth_logout(request))
    except DatabaseError:
        messages.error(request, '退出登录时数据库繁忙，请稍后重试。')
        return redirect('home')
    return redirect('login')


def _user_role_label(user, is_operator: bool, is_system_admin: bool) -> str:
    if is_system_admin:
        return '系统管理员'
    if is_operator:
        return '仓库人员'
    if user.is_staff:
        return '后台人员'
    return '普通员工'


def _get_by_uuid(model, value):
    if not value:
        return None
    try:
        UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
    return model.objects.filter(pk=value).first()


def _unique_uuid_values(values):
    result = []
    for value in values:
        try:
            normalized = str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            continue
        if normalized not in result:
            result.append(normalized)
    return result


def _selection_values(request):
    source = request.POST if request.method == 'POST' else request.GET
    asset_values = source.getlist('asset_ids')
    stock_values = source.getlist('stock_item_ids')
    # Keep the original single-item payload working for old bookmarks and clients.
    if not asset_values and source.get('asset_id'):
        asset_values = [source.get('asset_id')]
    if not stock_values and source.get('stock_item_id'):
        stock_values = [source.get('stock_item_id')]
    return _unique_uuid_values(asset_values), _unique_uuid_values(stock_values)


def _load_selected_items(request):
    asset_ids, stock_item_ids = _selection_values(request)
    unit_ids = _unique_uuid_values(
        (request.POST if request.method == 'POST' else request.GET).getlist('unit_ids')
    )
    assets_by_id = {
        str(item.id): item
        for item in Asset.objects.select_related(
            'item_type', 'warehouse', 'location', 'current_holder'
        ).filter(pk__in=asset_ids)
    }
    stock_items_by_id = {
        str(item.id): item
        for item in StockItem.objects.select_related(
            'item_type', 'warehouse', 'location'
        ).filter(pk__in=stock_item_ids)
    }
    units_by_id = {
        str(item.id): item
        for item in CompositeUnit.objects.select_related('customer').filter(pk__in=unit_ids)
    }
    selected_assets = [assets_by_id[item_id] for item_id in asset_ids if item_id in assets_by_id]
    selected_stock_items = [stock_items_by_id[item_id] for item_id in stock_item_ids if item_id in stock_items_by_id]
    selected_units = [units_by_id[item_id] for item_id in unit_ids if item_id in units_by_id]
    source = request.POST if request.method == 'POST' else request.GET
    for item in selected_stock_items:
        quantity = source.get(f'stock_quantity_{item.id}', '')
        item.selected_quantity = quantity if str(quantity).isdigit() and int(quantity) > 0 else '1'
    return asset_ids, stock_item_ids, unit_ids, selected_assets, selected_stock_items, selected_units


def _attach_unit_candidate_state(units, user, *, quick=False):
    """计算成品设备的可办理类型与状态文案（与 _attach_request_candidate_state 同款）。"""
    for unit in units:
        allowed = []
        holder_label = ''
        if unit.status == CompositeUnit.Status.ASSEMBLED:
            allowed.append(WorkflowRequest.RequestType.BORROW)
            if quick:
                allowed.append(WorkflowRequest.RequestType.SALE)
        elif unit.status == CompositeUnit.Status.BORROWED:
            active_links = [link for link in unit.component_links.all() if link.disassembled_at is None]
            holders = {
                link.asset.current_holder.username
                for link in active_links
                if link.asset and link.asset.current_holder_id
            }
            if len(holders) == 1:
                holder_label = f'，借用人 {next(iter(holders))}'
            if user.is_superuser or any(link.asset and link.asset.current_holder_id == user.id for link in active_links):
                allowed.append(WorkflowRequest.RequestType.RETURN)
        unit.request_status_label = f'{unit.get_status_display()}{holder_label}'
        unit.allowed_request_types = ','.join(allowed)


def _search_composite_units(query):
    return list(CompositeUnit.objects.select_related('customer').prefetch_related(
        'component_links__asset__current_holder',
    ).filter(
        Q(name__icontains=query) | Q(unit_code__icontains=query) | Q(model__icontains=query)
    ).exclude(
        status=CompositeUnit.Status.DISASSEMBLED
    ).order_by('-created_at')[:12])


def _expand_unit_lines(request_type, selected_units):
    """把选中的成品展开为 (asset_lines, primary_unit)；多选/类型不符时抛 WorkflowError。"""
    primary_unit = None
    if not selected_units:
        return [], None
    if len(selected_units) > 1:
        raise WorkflowError('一次只能办理一台成品设备，请分开提交。')
    primary_unit = selected_units[0]
    active_links = list(primary_unit.component_links.filter(disassembled_at__isnull=True).select_related('asset'))
    if not active_links:
        raise WorkflowError(f'成品设备 {primary_unit} 当前没有组件，无法办理。')
    if request_type == WorkflowRequest.RequestType.BORROW and primary_unit.status != CompositeUnit.Status.ASSEMBLED:
        raise WorkflowError(f'成品设备 {primary_unit} 当前为{primary_unit.get_status_display()}，不能借用出库。')
    if request_type == WorkflowRequest.RequestType.SALE and primary_unit.status != CompositeUnit.Status.ASSEMBLED:
        raise WorkflowError(f'成品设备 {primary_unit} 当前为{primary_unit.get_status_display()}，不能售出。')
    if request_type == WorkflowRequest.RequestType.RETURN:
        if primary_unit.status != CompositeUnit.Status.BORROWED:
            raise WorkflowError(f'成品设备 {primary_unit} 当前为{primary_unit.get_status_display()}，不能归还。')
        # 只纳入仍在借出的组件（差异归还后已收回的不重复）。
        active_links = [link for link in active_links if link.asset and link.asset.status == Asset.Status.BORROWED]
        if not active_links:
            raise WorkflowError(f'成品设备 {primary_unit} 的组件已全部收回，无需再归还。')
    return [link.asset for link in active_links], primary_unit


def _stock_quantity_from_post(request, stock_item_id):
    value = request.POST.get(f'stock_quantity_{stock_item_id}', '').strip()
    if not value and len(request.POST.getlist('stock_item_ids')) <= 1:
        value = request.POST.get('quantity', '').strip()
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowError('请为每项耗材配件填写正确数量。') from exc
    if quantity < 1:
        raise WorkflowError('耗材配件数量必须大于 0。')
    return quantity


def _attach_request_candidate_state(assets, stock_items, user, *, quick=False):
    holding_map = {
        str(item.stock_item_id): item.quantity
        for item in StockItemHolding.objects.filter(
            holder=user,
            stock_item_id__in=[stock_item.id for stock_item in stock_items],
            quantity__gt=0,
        )
    }
    terminal_statuses = {Asset.Status.SOLD, Asset.Status.RETURNED_TO_VENDOR, Asset.Status.SCRAPPED}
    reserved_asset_ids = set(
        InventoryReservation.objects.filter(
            asset_id__in=[asset.id for asset in assets],
            status=InventoryReservation.Status.ACTIVE,
        ).values_list('asset_id', flat=True)
    )
    reserved_stock_map = {
        str(row['stock_item_id']): row['total']
        for row in InventoryReservation.objects.filter(
            stock_item_id__in=[item.id for item in stock_items],
            status=InventoryReservation.Status.ACTIVE,
        ).values('stock_item_id').annotate(total=Sum('quantity'))
    }
    for asset in assets:
        allowed = []
        asset.is_reserved = asset.id in reserved_asset_ids
        asset.request_status_label = asset.get_status_display()
        if asset.is_reserved:
            asset.request_status_label = f'{asset.request_status_label}（已被申请占用）'
        elif asset.availability_state != Asset.AvailabilityState.AVAILABLE:
            asset.request_status_label = (
                f'{asset.request_status_label}（{asset.get_availability_state_display()}）'
            )
        if (
            asset.status == Asset.Status.IN_STOCK
            and asset.availability_state == Asset.AvailabilityState.AVAILABLE
            and asset.quality_state == Asset.QualityState.NORMAL
            and asset.disposition_state == Asset.DispositionState.INTERNAL
            and not asset.is_reserved
        ):
            allowed.extend([
                WorkflowRequest.RequestType.BORROW,
                WorkflowRequest.RequestType.ISSUE,
            ])
            if quick:
                allowed.extend([
                    WorkflowRequest.RequestType.SALE,
                    WorkflowRequest.RequestType.TRANSFER,
                ])
        if quick and not asset.is_reserved:
            if asset.status != Asset.Status.IN_STOCK and asset.status not in terminal_statuses:
                allowed.append(WorkflowRequest.RequestType.RETURN)
            if asset.status not in terminal_statuses:
                allowed.extend([
                    WorkflowRequest.RequestType.REPAIR,
                    WorkflowRequest.RequestType.DAMAGE,
                    WorkflowRequest.RequestType.RETURN_TO_VENDOR,
                    WorkflowRequest.RequestType.SCRAP,
                ])
        elif asset.current_holder_id == user.id and asset.status != Asset.Status.IN_STOCK:
            allowed.append(WorkflowRequest.RequestType.RETURN)
        asset.allowed_request_types = ','.join(dict.fromkeys(allowed))
    for stock_item in stock_items:
        held_quantity = holding_map.get(str(stock_item.id), 0)
        reserved_quantity = reserved_stock_map.get(str(stock_item.id), 0)
        available_quantity = max(stock_item.quantity - reserved_quantity, 0)
        stock_item.held_quantity = held_quantity
        stock_item.reserved_quantity = reserved_quantity
        stock_item.available_quantity = available_quantity
        stock_item.request_status_label = f'实存 {stock_item.quantity} {stock_item.unit}，可用 {available_quantity} {stock_item.unit}'
        allowed = []
        if available_quantity > 0:
            allowed.extend([
                WorkflowRequest.RequestType.BORROW,
                WorkflowRequest.RequestType.ISSUE,
            ])
            if quick:
                allowed.append(WorkflowRequest.RequestType.SALE)
        if quick:
            allowed.append(WorkflowRequest.RequestType.RETURN)
            if reserved_quantity == 0:
                allowed.append(WorkflowRequest.RequestType.TRANSFER)
        elif held_quantity > 0:
            allowed.append(WorkflowRequest.RequestType.RETURN)
        stock_item.allowed_request_types = ','.join(dict.fromkeys(allowed))


def _candidate_incompatibility_message(item, request_type):
    type_label = dict(WorkflowRequest.RequestType.choices).get(request_type, request_type or '当前操作')
    status_label = getattr(item, 'request_status_label', '')
    if not status_label and isinstance(item, Asset):
        status_label = item.get_status_display()
    status_label = status_label or '当前不可用'
    return f'{item.name} 当前状态为“{status_label}”，不能办理“{type_label}”。'


def _serialized_type_availability(queryset=None):
    queryset = queryset if queryset is not None else ItemType.objects.all()
    return queryset.annotate(
        available_count=Count(
            'assets',
            filter=available_asset_filter('assets__'),
            distinct=True,
        ),
    )


def _stock_item_availability(queryset=None):
    queryset = queryset if queryset is not None else StockItem.objects.all()
    return queryset.annotate(
        reserved_quantity=Coalesce(
            Sum(
                'reservations__quantity',
                filter=Q(reservations__status=InventoryReservation.Status.ACTIVE),
                output_field=IntegerField(),
            ),
            0,
        ),
    ).annotate(available_count=F('quantity') - F('reserved_quantity'))


@login_required
def home(request):
    user_is_operator = is_warehouse_operator(request.user)
    user_is_system_admin = is_system_administrator(request.user)
    asset_count = Asset.objects.count()
    status_counts = {
        row['status']: row['total']
        for row in Asset.objects.values('status').annotate(total=Count('id'))
    }
    low_serialized_types = _serialized_type_availability(
        ItemType.objects.filter(is_serialized=True, safety_stock__gt=0)
    ).filter(available_count__lt=F('safety_stock'))
    low_stock_items = _stock_item_availability(
        StockItem.objects.filter(safety_stock__gt=0)
    ).filter(available_count__lte=F('safety_stock'))
    status_definitions = [
        ('in_stock', '在库', 'teal'),
        ('borrowed', '借出', 'amber'),
        ('issued', '领用', 'blue'),
        ('maintenance', '维修中', 'coral'),
        ('lost', '丢失', 'ink'),
    ]
    status_breakdown = [
        {
            'key': key,
            'label': label,
            'count': status_counts.get(key, 0),
            'percentage': round(status_counts.get(key, 0) / asset_count * 100) if asset_count else 0,
            'tone': tone,
            'url': f'/warehouse/?tab=assets&status={key}',
        }
        for key, label, tone in status_definitions
    ]
    operator_pending_requests = WorkflowRequest.objects.filter(
        Q(status=WorkflowRequest.Status.WAITING_WAREHOUSE)
        | Q(
            status=WorkflowRequest.Status.PENDING,
            approval_tasks__assigned_to__isnull=True,
        )
        | Q(
            status=WorkflowRequest.Status.PENDING,
            approval_tasks__assigned_to=request.user,
        )
    ).select_related('applicant', 'department').distinct().order_by('-created_at')
    my_requests = WorkflowRequest.objects.filter(applicant=request.user, is_hidden_by_applicant=False).select_related(
        'department'
    ).order_by('-created_at')
    my_assets = Asset.objects.filter(current_holder=request.user).select_related(
        'item_type'
    ).order_by('-updated_at')
    notifications = Notification.objects.filter(recipient=request.user).select_related(
        'recipient'
    ).order_by('-created_at')
    pending_requests = operator_pending_requests if user_is_operator else my_requests.filter(
        status__in=[WorkflowRequest.Status.DRAFT, WorkflowRequest.Status.PENDING, WorkflowRequest.Status.WAITING_WAREHOUSE]
    )
    overdue_asset_count = 0
    overdue_stock_count = 0
    if user_is_operator:
        today = timezone.localdate()
        overdue_asset_count = WorkflowRequestLine.objects.filter(
            request__request_type=WorkflowRequest.RequestType.BORROW,
            request__status=WorkflowRequest.Status.DONE,
            request__expected_return_date__lt=today,
            asset__status=Asset.Status.BORROWED,
        ).values('asset_id').distinct().count()
        overdue_stock_count = StockItemLoan.objects.filter(
            status=StockItemLoan.Status.ACTIVE,
            outstanding_quantity__gt=0,
            expected_return_date__lt=today,
        ).count()
    context = {
        'user_is_operator': user_is_operator,
        'user_is_system_admin': user_is_system_admin,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_can_view_reports': can_view_reports(request.user),
        'user_role_label': _user_role_label(request.user, user_is_operator, user_is_system_admin),
        'asset_count': asset_count,
        'my_asset_count': my_assets.count(),
        'in_stock_count': Asset.objects.filter(available_asset_filter()).count(),
        'borrowed_count': status_counts.get('borrowed', 0) + status_counts.get('issued', 0),
        'low_stock_count': low_serialized_types.count() + low_stock_items.count(),
        'pending_request_count': pending_requests.count(),
        'overdue_asset_count': overdue_asset_count,
        'overdue_stock_count': overdue_stock_count,
        'overdue_total': overdue_asset_count + overdue_stock_count,
        'request_count': WorkflowRequest.objects.count() if user_is_operator else my_requests.count(),
        'notification_count': notifications.count(),
        'unread_notification_count': notifications.filter(is_read=False).count(),
        'status_breakdown': status_breakdown,
        'pending_requests': pending_requests[:5],
        'recent_assets': Asset.objects.select_related('item_type', 'warehouse', 'location').order_by('-created_at')[:8]
        if user_is_operator else my_assets[:8],
        'recent_notifications': notifications[:8],
    }
    return render(request, 'home.html', context)


@login_required
def asset_lookup(request):
    query = request.GET.get('q', '').strip()
    user_is_operator = is_warehouse_operator(request.user)
    user_is_system_admin = is_system_administrator(request.user)
    assets = Asset.objects.none()
    if query:
        assets = Asset.objects.select_related(
            'item_type', 'warehouse', 'location', 'current_holder'
        ).filter(asset_search_query(query)).distinct().order_by('system_asset_no')

        # 扫码直达：二维码/编码精确命中唯一记录时跳过列表直接打开对应页面。
        exact_asset = Asset.objects.filter(Q(system_asset_no=query) | Q(qr_value=query) | Q(asset_code=query)).first()
        if exact_asset and Asset.objects.filter(Q(system_asset_no=query) | Q(qr_value=query) | Q(asset_code=query)).count() == 1:
            return redirect('asset_lifecycle', asset_id=exact_asset.id)
        exact_unit = CompositeUnit.objects.filter(Q(qr_value=query) | Q(unit_code=query)).first()
        if exact_unit and CompositeUnit.objects.filter(Q(qr_value=query) | Q(unit_code=query)).count() == 1:
            return redirect(f'{reverse("composite_unit_management")}?unit={exact_unit.id}')

    reserved_asset_ids = set(InventoryReservation.objects.filter(
        asset_id__in=[asset.id for asset in assets],
        status=InventoryReservation.Status.ACTIVE,
    ).values_list('asset_id', flat=True))
    asset_results = []
    for asset in assets:
        if asset.id in reserved_asset_ids:
            asset_availability = '已被申请预约'
        elif is_asset_available(asset):
            asset_availability = '可申请'
        elif asset.current_holder_id == request.user.id:
            asset_availability = '可归还'
        else:
            asset_availability = '暂不可申请'
        asset_results.append({'asset': asset, 'availability': asset_availability})

    person_results = []
    if query and user_is_operator:
        user_model = get_user_model()
        people = user_model.objects.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(profile__employee_no__icontains=query)
            | Q(profile__phone__icontains=query)
            | Q(profile__dingtalk_user_id__icontains=query)
        ).select_related('profile').distinct().order_by('username')[:30]
        for person in people:
            person_requests = list(WorkflowRequest.objects.filter(
                applicant=person,
            ).prefetch_related('lines__asset', 'lines__stock_item').order_by(
                '-application_date', '-created_at',
            )[:100])
            person_assets = list(Asset.objects.select_related(
                'item_type', 'warehouse', 'location',
            ).filter(current_holder=person).order_by('asset_code'))
            display_name = ' '.join(filter(None, [person.first_name, person.last_name])) or person.username
            person_results.append({
                'key': f'user:{person.pk}',
                'name': display_name,
                'username': person.username,
                'employee_no': getattr(getattr(person, 'profile', None), 'employee_no', ''),
                'requests': person_requests,
                'assets': person_assets,
            })

        external_requests = WorkflowRequest.objects.filter(
            recipient_name__icontains=query,
        ).exclude(recipient_name='').prefetch_related(
            'lines__asset', 'lines__stock_item',
        ).order_by('-application_date', '-created_at')[:100]
        grouped_external = {}
        for workflow_request in external_requests:
            grouped_external.setdefault(workflow_request.recipient_name.strip(), []).append(workflow_request)
        for name, requests in grouped_external.items():
            if any(result['name'] == name for result in person_results):
                continue
            external_assets = list(Asset.objects.select_related(
                'item_type', 'warehouse', 'location',
            ).filter(external_holder_name__iexact=name).order_by('asset_code'))
            person_results.append({
                'key': f'external:{name}',
                'name': name,
                'username': '',
                'employee_no': '',
                'requests': requests,
                'assets': external_assets,
            })

    unit_results = []
    request_results = []
    if query:
        unit_results = list(
            CompositeUnit.objects.filter(
                Q(name__icontains=query) | Q(unit_code__icontains=query) | Q(model__icontains=query)
            ).select_related('customer').order_by('-created_at')[:30]
        )
        request_results = list(
            WorkflowRequest.objects.filter(
                Q(request_no__icontains=query) | Q(inventory_no__icontains=query) | Q(reason__icontains=query)
            ).select_related('applicant', 'department').order_by('-created_at')[:30]
        )

    return render(request, 'asset_lookup.html', {
        'assets': assets,
        'asset_results': asset_results,
        'person_results': person_results,
        'unit_results': unit_results,
        'request_results': request_results,
        'query': query,
        'user_is_operator': user_is_operator,
        'user_is_system_admin': user_is_system_admin,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, user_is_operator, user_is_system_admin),
    })


@login_required
def notification_center(request):
    notifications = Notification.objects.filter(recipient=request.user).order_by('-created_at')
    if request.method == 'POST':
        notifications.filter(is_read=False).update(is_read=True)
        return redirect('notification_center')
    return render(request, 'notification_center.html', {
        'notifications': notifications[:100],
        'unread_count': notifications.filter(is_read=False).count(),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': is_warehouse_operator(request.user),
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(
            request.user,
            is_warehouse_operator(request.user),
            is_system_administrator(request.user),
        ),
    })


@login_required
def notification_open(request, notification_id):
    notification = Notification.objects.filter(pk=notification_id, recipient=request.user).first()
    if not notification:
        messages.error(request, '未找到该通知。')
        return redirect('notification_center')
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read', 'updated_at'])
    if notification.related_model == 'WorkflowRequest' and notification.related_object_id:
        return redirect(reverse('workflow_detail', kwargs={'request_id': notification.related_object_id}))
    if notification.related_model == 'PurchaseRequest' and notification.related_object_id:
        return redirect(reverse('purchase_draft_detail', kwargs={'purchase_id': notification.related_object_id}))
    if notification.related_model == 'PurchaseOrder' and notification.related_object_id:
        return redirect(reverse('purchase_order_detail', kwargs={'order_id': notification.related_object_id}))
    return redirect('notification_center')


@role_required(is_system_administrator, message='当前账号没有系统设置权限。')
def permission_management(request):
    group_names = [*PERMISSION_GROUPS, 'warehouse_admin']
    groups = {}
    for group_name in group_names:
        groups[group_name], _ = Group.objects.get_or_create(name=group_name)
    if request.method == 'POST':
        if request.POST.get('action') == 'purge_data':
            if not request.user.is_superuser:
                messages.error(request, '只有服务器超级管理员可以执行系统清理。')
            elif request.POST.get('confirmation', '').strip() != '清空所有业务数据':
                messages.error(request, '确认文字不正确，未执行清理。')
            elif not request.user.check_password(request.POST.get('password', '')):
                messages.error(request, '当前账号密码不正确，未执行清理。')
            else:
                with transaction.atomic():
                    Notification.objects.all().delete()
                    LowStockAlert.objects.all().delete()
                    OperationAuditLog.objects.all().delete()
                    InventoryCheckAdjustment.objects.all().delete()
                    InventoryCheckScan.objects.all().delete()
                    InventoryCheckLine.objects.all().delete()
                    InventoryCheckTask.objects.all().delete()
                    InventoryTransaction.objects.all().delete()
                    ReturnReminderLog.objects.all().delete()
                    LoanExtensionRequest.objects.all().delete()
                    StockItemLoan.objects.all().delete()
                    InventoryReservation.objects.all().delete()
                    WorkflowRequestTemplate.objects.all().delete()
                    ApprovalTask.objects.all().delete()
                    ApprovalLog.objects.all().delete()
                    WorkflowRequest.objects.all().delete()
                    AssetNetworkEndpoint.objects.all().delete()
                    InventoryConversion.objects.all().delete()
                    StockItemHolding.objects.all().delete()
                    Asset.objects.all().delete()
                    StockItem.objects.all().delete()
                    ImportIssue.objects.all().delete()
                    ImportBatch.objects.all().delete()
                    Location.objects.all().delete()
                    ItemType.objects.all().delete()
                    Category.objects.all().delete()
                    Warehouse.objects.all().delete()
                    UserListPreference.objects.all().delete()
                    UserProfile.objects.exclude(user=request.user).delete()
                    User.objects.exclude(pk=request.user.pk).delete()
                    Department.objects.all().delete()
                messages.success(request, '已清空全部业务数据和其他账号，当前超级管理员及角色组已保留。')
            return redirect('permission_management')
        if request.POST.get('action') == 'create_user':
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '')
            employee_no = request.POST.get('employee_no', '').strip()
            permissions = set(request.POST.getlist('permissions'))
            is_admin = request.POST.get('is_admin') == '1'
            department_id = request.POST.get('department_id') or None
            department = Department.objects.filter(pk=department_id).first()
            phone = request.POST.get('phone', '').strip()
            dingtalk_user_id = request.POST.get('dingtalk_user_id', '').strip()
            if not username or not password or not employee_no:
                messages.error(request, '请填写登录账号、初始密码和员工工号。')
            elif len(password) < 8:
                messages.error(request, '初始密码至少需要 8 个字符。')
            elif not permissions.issubset(PERMISSION_GROUPS):
                messages.error(request, '请选择有效的业务权限。')
            elif User.objects.filter(username=username).exists():
                messages.error(request, '该登录账号已存在。')
            elif UserProfile.objects.filter(employee_no=employee_no).exists():
                messages.error(request, '该员工工号已存在。')
            else:
                try:
                    with transaction.atomic():
                        target = User.objects.create_user(username=username, password=password)
                        target.groups.add(*(groups[name] for name in permissions))
                        if is_admin:
                            target.groups.add(groups['warehouse_admin'])
                            target.is_staff = True
                            target.save(update_fields=['is_staff'])
                        UserProfile.objects.create(
                            user=target,
                            employee_no=employee_no,
                            department=department,
                            phone=phone,
                            dingtalk_user_id=dingtalk_user_id,
                            is_dingtalk_bound=bool(dingtalk_user_id),
                            dingtalk_bound_at=timezone.now() if dingtalk_user_id else None,
                        )
                except IntegrityError:
                    messages.error(request, '账号或员工工号已存在，请刷新页面后重新检查。')
                else:
                    messages.success(request, f'已创建账号 {username} 并分配所选权限。')
            return redirect('permission_management')
        if request.POST.get('action') == 'delete_user':
            target = User.objects.filter(pk=request.POST.get('user_id')).first()
            if not target:
                messages.error(request, '未找到要删除的账号。')
            elif target.pk == request.user.pk:
                messages.error(request, '不能删除当前登录账号。')
            elif target.is_superuser:
                messages.error(request, '服务器超级管理员账号不能在此页面删除。')
            else:
                try:
                    with transaction.atomic():
                        record_operation(
                            request.user,
                            'user_deleted',
                            target,
                            summary=f'删除系统账号 {target.username}',
                            before={'username': target.username, 'is_staff': target.is_staff},
                        )
                        target.delete()
                except ProtectedError:
                    messages.error(request, '该账号已有受保护的业务记录，不能删除；请保留账号以维持历史追溯。')
                else:
                    messages.success(request, f'已删除账号 {target.username}。')
            return redirect('permission_management')
        target = User.objects.filter(pk=request.POST.get('user_id')).first()
        permissions = set(request.POST.getlist('permissions'))
        is_admin = request.POST.get('is_admin') == '1'
        if not target or not permissions.issubset(PERMISSION_GROUPS):
            messages.error(request, '请选择有效的账号和权限。')
        elif target.is_superuser:
            messages.error(request, '超级管理员角色只能通过服务器运维账号管理，不能在此页面降级。')
        else:
            target.groups.remove(*groups.values())
            legacy_group = Group.objects.filter(name='warehouse_staff').first()
            if legacy_group:
                target.groups.remove(legacy_group)
            target.groups.add(*(groups[name] for name in permissions))
            if is_admin:
                target.groups.add(groups['warehouse_admin'])
            target.is_staff = bool(permissions or is_admin)
            target.save(update_fields=['is_staff'])
            dingtalk_user_id = request.POST.get('dingtalk_user_id', '').strip()
            profile = UserProfile.objects.filter(user=target).first()
            if profile:
                profile.dingtalk_user_id = dingtalk_user_id
                profile.is_dingtalk_bound = bool(dingtalk_user_id)
                profile.dingtalk_bound_at = timezone.now() if dingtalk_user_id else None
                profile.save(update_fields=[
                    'dingtalk_user_id', 'is_dingtalk_bound', 'dingtalk_bound_at', 'updated_at',
                ])
            messages.success(request, f'已更新 {target.username} 的业务权限。')
        return redirect('permission_management')
    rows = []
    for user in User.objects.prefetch_related('groups').order_by('username'):
        names = {group.name for group in user.groups.all()}
        rows.append({'user': user, 'profile': UserProfile.objects.filter(user=user).first(), 'permissions': names & set(PERMISSION_GROUPS), 'is_admin': 'warehouse_admin' in names, 'role_label': '超级管理员' if user.is_superuser else '仓库管理员' if 'warehouse_admin' in names else '、'.join(PERMISSION_GROUPS[name] for name in names if name in PERMISSION_GROUPS) or '普通员工'})
    return render(request, 'permission_management.html', {
        'user_rows': rows,
        'permission_choices': PERMISSION_GROUPS,
        'departments': Department.objects.filter(is_active=True).order_by('name'),
        'dingtalk_status': dingtalk_configuration_status(),
        'user_can_use_admin': True,
        'user_is_operator': is_warehouse_operator(request.user),
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, is_warehouse_operator(request.user), True),
    })


@role_required(is_system_administrator, message='当前账号没有审计日志查看权限。')
def audit_log_list(request):
    """系统管理员只读检索业务操作审计日志。"""
    query = request.GET.get('q', '').strip()
    actor_username = request.GET.get('actor', '').strip()
    action = request.GET.get('action', '').strip()
    target_model = request.GET.get('target_model', '').strip()
    target_id = request.GET.get('target_id', '').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()
    start_value = end_value = None
    try:
        start_value = date.fromisoformat(start_date) if start_date else None
        end_value = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        start_value = end_value = None

    logs = OperationAuditLog.objects.select_related('actor').all()
    if query:
        logs = logs.filter(
            Q(summary__icontains=query)
            | Q(action__icontains=query)
            | Q(target_model__icontains=query)
            | Q(actor__username__icontains=query)
        )
    if actor_username:
        logs = logs.filter(actor__username=actor_username)
    if action:
        logs = logs.filter(action=action)
    if target_model:
        logs = logs.filter(target_model=target_model)
    if target_id:
        try:
            logs = logs.filter(target_id=UUID(target_id))
        except ValueError:
            logs = logs.none()
    if start_value:
        logs = logs.filter(created_at__date__gte=start_value)
    if end_value:
        logs = logs.filter(created_at__date__lte=end_value)

    page = Paginator(logs.order_by('-created_at'), 50).get_page(request.GET.get('page'))
    for log in page:
        log.before_json = json.dumps(log.before_data or {}, ensure_ascii=False, indent=2, default=str)
        log.after_json = json.dumps(log.after_data or {}, ensure_ascii=False, indent=2, default=str)

    return render(request, 'audit_log_list.html', {
        'audit_logs': page,
        'query': query,
        'selected_actor': actor_username,
        'selected_action': action,
        'selected_target_model': target_model,
        'target_id': target_id,
        'start_date': start_date,
        'end_date': end_date,
        'actors': get_user_model().objects.filter(
            operation_audit_logs__isnull=False,
        ).distinct().order_by('username'),
        'actions': OperationAuditLog.objects.values_list('action', flat=True).distinct().order_by('action'),
        'target_models': OperationAuditLog.objects.values_list(
            'target_model', flat=True,
        ).distinct().order_by('target_model'),
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@role_required(is_system_administrator, message='当前账号没有数据一致性检查权限。')
def consistency_check(request):
    if request.GET.get('download') == 'csv':
        return consistency_check_export(request)
    severity = request.GET.get('severity', '').strip()
    code = request.GET.get('code', '').strip()
    query = request.GET.get('q', '').strip()
    all_issues = build_consistency_issues()
    issues = all_issues[:]
    if severity in {'error', 'warning'}:
        issues = [item for item in issues if item['severity'] == severity]
    if code:
        issues = [item for item in issues if item['code'] == code]
    if query:
        query_lower = query.casefold()
        issues = [
            item for item in issues
            if query_lower in ' '.join(
                str(item[key]) for key in ('code', 'object_type', 'target', 'message')
            ).casefold()
        ]
    page = Paginator(issues, 50).get_page(request.GET.get('page'))
    return render(request, 'consistency_check.html', {
        'issues': page,
        'issue_count': len(issues),
        'error_count': sum(item['severity'] == 'error' for item in issues),
        'warning_count': sum(item['severity'] == 'warning' for item in issues),
        'severity': severity,
        'code': code,
        'query': query,
        'codes': sorted({item['code'] for item in all_issues}),
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@role_required(is_system_administrator, message='当前账号没有数据一致性检查导出权限。')
def consistency_check_export(request):
    issues = build_consistency_issues()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="consistency-check.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['级别', '问题代码', '对象类型', '对象', '问题说明', '处理建议', '详情链接'])
    for item in issues:
        writer.writerow([
            '错误' if item['severity'] == 'error' else '警告',
            item['code'], item['object_type'], item['target'], item['message'], item['suggestion'], item['url'],
        ])
    return response


@role_required(is_warehouse_operator, message='当前账号没有仓库管理权限。')
def warehouse_management(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    location_state = request.GET.get('location_state', '').strip()
    availability_state = request.GET.get('availability_state', '').strip() or request.GET.get('availability', '').strip()
    quality_state = request.GET.get('quality_state', '').strip()
    disposition_state = request.GET.get('disposition_state', '').strip()
    warehouse_id = request.GET.get('warehouse', '').strip()
    item_type_id = request.GET.get('item_type', '').strip()
    tab = request.GET.get('tab', 'assets')
    assets = Asset.objects.select_related('item_type', 'warehouse', 'location').order_by('-updated_at')
    stock_items = StockItem.objects.select_related('item_type', 'warehouse', 'location').order_by('name')
    if query:
        assets = assets.filter(asset_search_query(query)).distinct()
        stock_items = stock_items.filter(
            Q(name__icontains=query) | Q(code__icontains=query) | Q(item_type__name__icontains=query)
        ).distinct()
    selected_statuses = [value for value in status.split(',') if value]
    if selected_statuses:
        assets = assets.filter(status__in=selected_statuses)
    state_filters = {
        'location_state': location_state,
        'availability_state': availability_state,
        'quality_state': quality_state,
        'disposition_state': disposition_state,
    }
    valid_states = {
        'location_state': {value for value, _ in Asset.LocationState.choices},
        'availability_state': {value for value, _ in Asset.AvailabilityState.choices},
        'quality_state': {value for value, _ in Asset.QualityState.choices},
        'disposition_state': {value for value, _ in Asset.DispositionState.choices},
    }
    for field, value in state_filters.items():
        if value in valid_states[field]:
            assets = assets.filter(**{field: value})
    if warehouse_id:
        assets = assets.filter(warehouse_id=warehouse_id)
        stock_items = stock_items.filter(warehouse_id=warehouse_id)
    if item_type_id:
        assets = assets.filter(item_type_id=item_type_id)
        stock_items = stock_items.filter(item_type_id=item_type_id)
    low_serialized_types = _serialized_type_availability(
        ItemType.objects.filter(is_serialized=True, safety_stock__gt=0)
    ).filter(available_count__lt=F('safety_stock'))
    low_stock = _stock_item_availability(
        StockItem.objects.filter(safety_stock__gt=0)
    ).filter(available_count__lte=F('safety_stock')).count() + low_serialized_types.count()
    page_size = request.GET.get('page_size', '').strip()
    if not page_size:
        preference = UserListPreference.objects.filter(
            user=request.user, list_key='warehouse_management',
        ).first()
        if preference and isinstance(preference.columns, dict):
            page_size = str(preference.columns.get('page_size', ''))
    page_size = int(page_size) if page_size in {'50', '100', '200'} else 100
    asset_page = Paginator(assets, page_size).get_page(request.GET.get('asset_page'))
    stock_page = Paginator(stock_items, page_size).get_page(request.GET.get('stock_page'))
    filter_params = {
        'q': query,
        'status': status,
        'location_state': location_state,
        'availability_state': availability_state,
        'quality_state': quality_state,
        'disposition_state': disposition_state,
        'warehouse': warehouse_id,
        'item_type': item_type_id,
        'page_size': page_size,
    }
    filter_params = {key: value for key, value in filter_params.items() if value not in ('', None)}
    return render(request, 'warehouse_management.html', {
        'assets': asset_page,
        'stock_items': stock_page,
        'query': query,
        'selected_status': status,
        'selected_statuses': selected_statuses,
        'selected_location_state': location_state,
        'selected_availability_state': availability_state,
        'selected_quality_state': quality_state,
        'selected_disposition_state': disposition_state,
        'page_size': page_size,
        'selected_warehouse': warehouse_id,
        'selected_item_type': item_type_id,
        'active_tab': tab if tab in {'assets', 'stock'} else 'assets',
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'item_types': ItemType.objects.order_by('name'),
        'status_choices': Asset.Status.choices,
        'location_state_choices': Asset.LocationState.choices,
        'availability_state_choices': Asset.AvailabilityState.choices,
        'quality_state_choices': Asset.QualityState.choices,
        'disposition_state_choices': Asset.DispositionState.choices,
        'asset_filter_query': urlencode({**filter_params, 'tab': 'assets'}),
        'stock_filter_query': urlencode({**filter_params, 'tab': 'stock'}),
        'asset_count': assets.count(),
        'stock_item_count': stock_items.count(),
        'low_stock_count': low_stock,
        'low_serialized_types': low_serialized_types,
        'user_can_manage_inventory': can_manage_inventory(request.user),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_manage_inventory, message='当前账号没有库存资料维护权限。')
def warehouse_item_create(request):
    return render(request, 'warehouse_item_create.html', {
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(is_warehouse_operator, message='当前账号没有库存提醒权限。')
def low_stock_alerts(request):
    serialized_types = _serialized_type_availability(
        ItemType.objects.filter(is_serialized=True, safety_stock__gt=0)
    ).filter(available_count__lt=F('safety_stock')).order_by('name')
    stock_items = _stock_item_availability(
        StockItem.objects.select_related('item_type', 'warehouse', 'location').filter(safety_stock__gt=0)
    ).filter(available_count__lte=F('safety_stock')).order_by('name')
    rows = []
    for item_type in serialized_types:
        location = Asset.objects.filter(
            available_asset_filter(), item_type=item_type,
        ).select_related('warehouse', 'location').first()
        rows.append({
            'kind': '单件资产', 'name': item_type.name, 'code': item_type.code,
            'current': item_type.available_count, 'safety_stock': item_type.safety_stock,
            'shortage': max(item_type.safety_stock - item_type.available_count, 0), 'unit': item_type.unit,
            'warehouse': location.warehouse if location else None,
            'location': location.location if location else None,
            'updated_at': item_type.updated_at,
            'url': f'{reverse("warehouse_management")}?tab=assets&item_type={item_type.id}&status=in_stock',
        })
    for stock_item in stock_items:
        rows.append({
            'kind': '耗材配件', 'name': stock_item.name, 'code': stock_item.code,
            'current': stock_item.available_count, 'safety_stock': stock_item.safety_stock,
            'shortage': max(stock_item.safety_stock - stock_item.available_count, 0), 'unit': stock_item.unit,
            'warehouse': stock_item.warehouse, 'location': stock_item.location,
            'updated_at': stock_item.updated_at,
            'url': reverse('warehouse_stock_item_edit', args=[stock_item.id]),
        })
    rows.sort(key=lambda row: (-row['shortage'], row['name']))
    return render(request, 'low_stock_alerts.html', {
        'alert_rows': rows,
        'user_can_manage_inventory': can_manage_inventory(request.user),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_manage_inventory, message='当前账号没有补货建议查看权限。')
def replenishment_suggestions(request):
    """将安全库存缺口整理为仓管可执行的补货清单。

    这是只读计算结果：不会自动改库存，也不会把建议当作采购订单。
    单件资产按物品类型汇总，耗材配件按具体物品汇总。
    """
    if request.method == 'POST' and request.POST.get('action') == 'create_purchase_draft':
        selections = []
        for selected in request.POST.getlist('selected_item'):
            try:
                source_type, source_id = selected.split(':', 1)
            except ValueError as exc:
                messages.error(request, '补货项目格式不正确，请刷新页面后重试。')
                return redirect('replenishment_suggestions')
            selections.append({
                'source_type': source_type,
                'source_id': source_id,
                'quantity': request.POST.get(f'purchase_quantity_{source_type}_{source_id}', '1'),
            })
        try:
            purchase_request = create_purchase_draft(
                request.user,
                selections,
                reason=request.POST.get('reason', ''),
                note=request.POST.get('note', ''),
            )
        except (ValueError, WorkflowError) as exc:
            messages.error(request, str(exc))
            return redirect('replenishment_suggestions')
        messages.success(request, f'采购草稿 {purchase_request.purchase_no} 已创建，请继续核对数量和说明。')
        return redirect('purchase_draft_edit', purchase_id=purchase_request.id)

    query = request.GET.get('q', '').strip()
    rows = []

    serialized_types = _serialized_type_availability(
        ItemType.objects.filter(is_serialized=True, safety_stock__gt=0)
    ).filter(available_count__lt=F('safety_stock')).order_by('name')
    for item_type in serialized_types:
        if query and not any(
            query.casefold() in value.casefold()
            for value in (item_type.name, item_type.code)
            if value
        ):
            continue
        available_assets = Asset.objects.filter(
            available_asset_filter(), item_type=item_type,
        ).select_related('warehouse', 'location')
        locations = sorted({
            ' / '.join(filter(None, [
                asset.warehouse.name if asset.warehouse else '未分配仓库',
                asset.location.code if asset.location else '未分配库位',
            ]))
            for asset in available_assets
        })
        rows.append({
            'source_type': 'item_type',
            'source_id': str(item_type.id),
            'kind': '单件资产',
            'name': item_type.name,
            'code': item_type.code,
            'current': item_type.available_count,
            'safety_stock': item_type.safety_stock,
            'suggested_quantity': max(item_type.safety_stock - item_type.available_count, 0),
            'unit': item_type.unit,
            'supplier': '按具体资产供应商确认',
            'locations': locations,
            'reference_cost': None,
            'reference_cost_label': '需按型号和供应商询价',
        })

    stock_items = _stock_item_availability(
        StockItem.objects.select_related('item_type', 'supplier_ref', 'warehouse', 'location').filter(
            safety_stock__gt=0,
        )
    ).filter(available_count__lte=F('safety_stock')).order_by('name')
    for stock_item in stock_items:
        searchable = ' '.join(filter(None, [
            stock_item.name,
            stock_item.code,
            stock_item.item_type.name if stock_item.item_type_id else '',
            stock_item.supplier,
            stock_item.supplier_ref.name if stock_item.supplier_ref_id else '',
        ]))
        if query and query.casefold() not in searchable.casefold():
            continue
        shortage = max(stock_item.safety_stock - stock_item.available_count, 0)
        reference_cost = stock_item.unit_cost * shortage if stock_item.unit_cost is not None else None
        rows.append({
            'source_type': 'stock_item',
            'source_id': str(stock_item.id),
            'kind': '耗材配件',
            'name': stock_item.name,
            'code': stock_item.code,
            'current': stock_item.available_count,
            'safety_stock': stock_item.safety_stock,
            'suggested_quantity': shortage,
            'unit': stock_item.unit,
            'supplier': stock_item.supplier_ref.name if stock_item.supplier_ref_id else (stock_item.supplier or '未填写'),
            'locations': [' / '.join(filter(None, [
                stock_item.warehouse.name if stock_item.warehouse else '未分配仓库',
                stock_item.location.code if stock_item.location else '未分配库位',
            ]))],
            'reference_cost': reference_cost,
            'reference_cost_label': f'按单价 {stock_item.unit_cost:.2f} 元估算' if stock_item.unit_cost is not None else '未填写单价',
        })

    rows.sort(key=lambda row: (-row['suggested_quantity'], row['name']))
    if request.GET.get('download') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = "attachment; filename*=UTF-8''replenishment-suggestions.csv"
        writer = csv.writer(response)
        writer.writerow(['管理方式', '物品名称', '编码', '当前可用量', '最低库存提醒值', '建议补货量', '单位', '供应商', '仓库/库位', '参考补货金额（元）', '说明'])
        for row in rows:
            writer.writerow([
                row['kind'], row['name'], row['code'], row['current'], row['safety_stock'],
                row['suggested_quantity'], row['unit'], row['supplier'], '；'.join(row['locations']),
                row['reference_cost'] if row['reference_cost'] is not None else '', row['reference_cost_label'],
            ])
        return response

    return render(request, 'replenishment_suggestions.html', {
        'rows': rows,
        'query': query,
        'total_suggested_quantity': sum(row['suggested_quantity'] for row in rows),
        'reference_cost_total': sum((row['reference_cost'] or Decimal('0')) for row in rows),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_manage_inventory, message='当前账号没有采购草稿查看权限。')
def purchase_draft_list(request):
    if request.method == 'POST' and request.POST.get('action') == 'submit_purchase':
        purchase_request = get_object_or_404(PurchaseRequest, pk=request.POST.get('purchase_id'))
        try:
            submit_purchase_request(purchase_request, request.user)
        except WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'采购申请 {purchase_request.purchase_no} 已提交审批。')
        return redirect('purchase_draft_list')
    drafts = PurchaseRequest.objects.select_related(
        'applicant', 'designated_approver', 'approved_by',
    ).prefetch_related('lines').exclude(status=PurchaseRequest.Status.ARCHIVED)
    return render(request, 'purchase_draft_list.html', {
        'purchase_drafts': drafts,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(lambda user: can_manage_inventory(user) or can_approve_workflow(user), message='当前账号没有采购申请查看权限。')
def purchase_draft_detail(request, purchase_id):
    purchase_request = get_object_or_404(
        PurchaseRequest.objects.select_related('applicant', 'designated_approver', 'approved_by').prefetch_related('lines'),
        pk=purchase_id,
    )
    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            if action == 'submit':
                submit_purchase_request(purchase_request, request.user)
                messages.success(request, f'采购申请 {purchase_request.purchase_no} 已提交审批。')
            elif action == 'approve':
                approve_purchase_request(purchase_request, request.user, request.POST.get('comment', ''))
                messages.success(request, '采购申请已通过审批。')
            elif action == 'reject':
                reject_purchase_request(purchase_request, request.user, request.POST.get('comment', ''))
                messages.success(request, '采购申请已驳回，并已记录原因。')
            elif action == 'reopen':
                reopen_purchase_request(purchase_request, request.user)
                messages.success(request, '采购申请已恢复为草稿，可以重新编辑。')
            elif action == 'create_order':
                create_purchase_order(purchase_request, request.user)
                messages.success(request, '采购订单已生成，请继续下达采购。')
            else:
                raise WorkflowError('不支持的采购申请操作。')
        except WorkflowError as exc:
            messages.error(request, str(exc))
        return redirect('purchase_draft_detail', purchase_id=purchase_request.id)

    can_submit = can_manage_inventory(request.user) and purchase_request.status == PurchaseRequest.Status.DRAFT
    can_approve_purchase = bool(
        can_approve_workflow(request.user)
        and purchase_request.status == PurchaseRequest.Status.PENDING_APPROVAL
        and (not purchase_request.designated_approver_id or purchase_request.designated_approver_id == request.user.id)
    )
    can_reopen = can_manage_inventory(request.user) and purchase_request.status == PurchaseRequest.Status.REJECTED
    purchase_order = getattr(purchase_request, 'purchase_order', None)
    return render(request, 'purchase_draft_detail.html', {
        'purchase_request': purchase_request,
        'purchase_order': purchase_order,
        'can_submit': can_submit,
        'can_approve_purchase': can_approve_purchase,
        'can_reopen': can_reopen,
        'can_create_order': can_manage_inventory(request.user) and purchase_request.status == PurchaseRequest.Status.APPROVED and not purchase_order,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(lambda user: can_manage_inventory(user) or can_execute_inventory(user), message='当前账号没有采购订单权限。')
def purchase_order_list(request):
    orders = PurchaseOrder.objects.select_related(
        'purchase_request', 'created_by', 'purchase_request__applicant',
    ).prefetch_related('lines').order_by('-order_date', '-created_at')
    return render(request, 'purchase_order_list.html', {
        'purchase_orders': orders,
        'user_can_manage_inventory': can_manage_inventory(request.user),
        'user_can_execute_inventory': can_execute_inventory(request.user),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(lambda user: can_manage_inventory(user) or can_execute_inventory(user), message='当前账号没有采购订单权限。')
def purchase_order_detail(request, order_id):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related(
            'purchase_request', 'purchase_request__applicant', 'created_by',
        ).prefetch_related('lines__purchase_request_line', 'receipts__lines'),
        pk=order_id,
    )
    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            if action == 'place':
                expected_date = request.POST.get('expected_delivery_date', '').strip()
                expected_delivery_date = date.fromisoformat(expected_date) if expected_date else None
                place_purchase_order(order, request.user, expected_delivery_date, request.POST.get('note', ''))
                messages.success(request, '采购订单已下达。')
            elif action == 'cancel':
                cancel_purchase_order(order, request.user, request.POST.get('reason', ''))
                messages.success(request, '采购订单已取消。')
            elif action == 'receive':
                warehouse = get_object_or_404(Warehouse, pk=request.POST.get('warehouse_id'))
                location = get_object_or_404(Location, pk=request.POST.get('location_id'))
                quantities = {
                    str(line.id): request.POST.get(f'received_{line.id}', '0')
                    for line in order.lines.all()
                }
                asset_profiles = {}
                for line in order.lines.all():
                    if not line.item_type_id:
                        continue
                    profiles = []
                    for index in range(1, 9):
                        prefix = f'profile_{line.id}_{index}_'
                        serial = request.POST.get(prefix + 'serial_number', '').strip()
                        code = request.POST.get(prefix + 'asset_code', '').strip()
                        barcode = request.POST.get(prefix + 'manufacturer_barcode', '').strip()
                        manufacturer = request.POST.get(prefix + 'manufacturer', '').strip()
                        model = request.POST.get(prefix + 'model', '').strip()
                        if not any([serial, code, barcode, manufacturer, model]):
                            continue
                        profiles.append({
                            'serial_number': serial, 'asset_code': code,
                            'manufacturer_barcode': barcode, 'manufacturer': manufacturer, 'model': model,
                        })
                    if profiles:
                        asset_profiles[str(line.id)] = profiles
                received_date = request.POST.get('received_date', '').strip()
                receive_purchase_order(
                    order,
                    request.user,
                    quantities,
                    warehouse,
                    location,
                    date.fromisoformat(received_date) if received_date else None,
                    request.POST.get('receipt_note', ''),
                    asset_profiles=asset_profiles,
                )
                messages.success(request, '到货已登记，库存和入库流水已更新。')
            elif action == 'complete_profiles':
                from apps.workflow.services import bulk_update_asset_profiles
                upload = request.FILES.get('profile_file')
                if not upload:
                    raise WorkflowError('请上传补录 Excel 文件。')
                rows = _parse_asset_profile_workbook(upload.read())
                updated = bulk_update_asset_profiles(order, request.user, rows)
                messages.success(request, f'已批量补录 {updated} 件资产的资料。')
            else:
                raise WorkflowError('不支持的采购订单操作。')
        except (WorkflowError, ValueError) as exc:
            messages.error(request, str(exc) or '日期格式不正确。')
        return redirect('purchase_order_detail', order_id=order.id)
    order.refresh_from_db()
    from apps.workflow.services import order_pending_profile_assets
    pending_assets = list(order_pending_profile_assets(order))
    return render(request, 'purchase_order_detail.html', {
        'purchase_order': order,
        'purchase_receipts': order.receipts.all(),
        'pending_profile_assets': pending_assets,
        'can_edit_profiles': can_manage_inventory(request.user),
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'locations': Location.objects.select_related('warehouse').order_by('warehouse__name', 'code'),
        'can_place': can_manage_inventory(request.user) and order.status == PurchaseOrder.Status.DRAFT,
        'can_cancel': can_manage_inventory(request.user) and order.status in {PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.ORDERED},
        'can_receive': can_execute_inventory(request.user) and order.status in {PurchaseOrder.Status.ORDERED, PurchaseOrder.Status.PARTIAL_RECEIVED},
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


_ASSET_PROFILE_HEADERS = ['系统资产编号', '生产厂家 SN', '资产编码', '生产厂家条码', '生产厂家', '型号']
_ASSET_PROFILE_KEYS = ['system_asset_no', 'serial_number', 'asset_code', 'manufacturer_barcode', 'manufacturer', 'model']


def _parse_asset_profile_workbook(content: bytes):
    """解析资产资料补录 Excel，返回 rows 列表；表头不匹配或为空时抛 ValueError。"""
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise WorkflowError('无法读取 Excel 文件，请使用下载的补录模板。') from exc
    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = [str(cell).strip() if cell is not None else '' for cell in next(rows_iter)]
    except StopIteration:
        workbook.close()
        raise WorkflowError('Excel 文件为空。')
    if header[:2] != _ASSET_PROFILE_HEADERS[:2]:
        workbook.close()
        raise WorkflowError('表头不正确，第一列必须是「系统资产编号」，请使用下载的补录模板。')
    rows = []
    for cells in rows_iter:
        values = [str(cell).strip() if cell is not None else '' for cell in cells]
        if not any(values):
            continue
        rows.append(dict(zip(_ASSET_PROFILE_KEYS, values + [''] * (len(_ASSET_PROFILE_KEYS) - len(values)))))
    workbook.close()
    if not rows:
        raise WorkflowError('Excel 中没有需要补录的数据行。')
    return rows


@role_required(can_manage_inventory, message='当前账号没有采购订单权限。')
def purchase_order_profile_template(request, order_id):
    """下载本订单的到货资产资料补录模板（预填系统资产编号和现有资料）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from apps.workflow.services import order_pending_profile_assets
    order = get_object_or_404(PurchaseOrder, pk=order_id)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '资产资料补录'
    sheet.append(_ASSET_PROFILE_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for asset in order_pending_profile_assets(order):
        sheet.append([
            asset.system_asset_no, asset.serial_number or '', asset.asset_code or '',
            asset.manufacturer_barcode or '', asset.manufacturer or '', asset.model or '',
        ])
    notes = workbook.create_sheet('填写说明')
    notes.append(['1. 系统资产编号列不可修改，用于匹配本订单到货生成的资产。'])
    notes.append(['2. 生产厂家 SN、资产编码会与台账全局查重，重复时整批拒绝导入。'])
    notes.append(['3. 留空的字段不会覆盖已有内容。'])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="asset-profiles-{order.order_no}.xlsx"'
    return response


PURCHASE_REPORT_ORDER_STATUSES = (
    PurchaseOrder.Status.ORDERED,
    PurchaseOrder.Status.PARTIAL_RECEIVED,
    PurchaseOrder.Status.RECEIVED,
)


def _supplier_price_rows(supplier_text='', item_text='', start_date=None, end_date=None):
    """按供应商+物品聚合采购价格历史（来源 PurchaseOrderLine）。"""
    lines = (
        PurchaseOrderLine.objects
        .filter(unit_cost__isnull=False)
        .filter(order__status__in=PURCHASE_REPORT_ORDER_STATUSES)
        .select_related('order')
        .order_by('supplier_snapshot', 'name_snapshot', 'order__order_date')
    )
    if supplier_text:
        lines = lines.filter(supplier_snapshot__icontains=supplier_text)
    if item_text:
        lines = lines.filter(Q(name_snapshot__icontains=item_text) | Q(code_snapshot__icontains=item_text))
    if start_date:
        lines = lines.filter(order__order_date__gte=start_date)
    if end_date:
        lines = lines.filter(order__order_date__lte=end_date)
    return lines


@role_required(can_view_reports, message='当前账号没有报表权限。')
def supplier_price_history(request):
    """供应商价格历史：按供应商+物品维度的历次采购单价序列。"""
    supplier_text = request.GET.get('supplier', '').strip()
    item_text = request.GET.get('item', '').strip()
    start_text, end_text = request.GET.get('start_date', ''), request.GET.get('end_date', '')
    start_date = end_date = None
    try:
        start_date = date.fromisoformat(start_text) if start_text else None
        end_date = date.fromisoformat(end_text) if end_text else None
    except ValueError:
        pass
    lines = _supplier_price_rows(supplier_text, item_text, start_date, end_date)
    # 按供应商+物品分组
    groups = {}
    for line in lines:
        key = (line.supplier_snapshot or '未填写', line.name_snapshot, line.code_snapshot or '')
        groups.setdefault(key, []).append(line)
    grouped = [
        {
            'supplier': key[0], 'name': key[1], 'code': key[2],
            'entries': [
                {'date': line.order.order_date, 'order_no': line.order.order_no, 'unit_cost': line.unit_cost, 'quantity': line.ordered_quantity, 'unit': line.unit}
                for line in items
            ],
            'latest_cost': items[-1].unit_cost,
            'min_cost': min(item.unit_cost for item in items),
            'max_cost': max(item.unit_cost for item in items),
            'count': len(items),
        }
        for key, items in sorted(groups.items(), key=lambda pair: (pair[0][0], pair[0][1]))
    ]
    suppliers = (
        PurchaseOrderLine.objects.filter(
            unit_cost__isnull=False,
            order__status__in=PURCHASE_REPORT_ORDER_STATUSES,
        )
        .exclude(supplier_snapshot='')
        .values_list('supplier_snapshot', flat=True)
        .distinct().order_by('supplier_snapshot')
    )
    return render(request, 'supplier_price_history.html', {
        'grouped': grouped,
        'suppliers': suppliers,
        'selected_supplier': supplier_text,
        'item_query': item_text,
        'start_date': start_text,
        'end_date': end_text,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_view_reports, message='当前账号没有报表导出权限。')
def supplier_price_history_export(request):
    """供应商价格历史 CSV 导出（与页面筛选一致）。"""
    supplier_text = request.GET.get('supplier', '').strip()
    item_text = request.GET.get('item', '').strip()
    start_text, end_text = request.GET.get('start_date', ''), request.GET.get('end_date', '')
    start_date = end_date = None
    try:
        start_date = date.fromisoformat(start_text) if start_text else None
        end_date = date.fromisoformat(end_text) if end_text else None
    except ValueError:
        pass
    lines = _supplier_price_rows(supplier_text, item_text, start_date, end_date)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="supplier-price-history.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['供应商', '物品名称', '物品编码', '采购订单', '下单日期', '单价（元）', '订购数量', '单位'])
    for line in lines:
        writer.writerow([
            line.supplier_snapshot or '未填写', line.name_snapshot, line.code_snapshot or '',
            line.order.order_no, line.order.order_date.isoformat(),
            line.unit_cost, line.ordered_quantity, line.unit,
        ])
    return response


def _supplier_reconciliation_lines(supplier_text='', start_date=None, end_date=None, date_basis='order'):
    """返回有效采购订单的对账行和指定期间内的实际到货数量。"""
    lines = list(
        PurchaseOrderLine.objects
        .filter(order__status__in=PURCHASE_REPORT_ORDER_STATUSES)
        .select_related('order')
        .order_by('supplier_snapshot', 'order__order_date')
    )
    if supplier_text:
        lines = [line for line in lines if supplier_text.casefold() in (line.supplier_snapshot or '').casefold()]
    if date_basis == 'order':
        if start_date:
            lines = [line for line in lines if line.order.order_date >= start_date]
        if end_date:
            lines = [line for line in lines if line.order.order_date <= end_date]
        return [(line, line.received_quantity, [line.order.order_date]) for line in lines]

    receipt_lines = PurchaseReceiptLine.objects.filter(
        order_line__order__status__in=PURCHASE_REPORT_ORDER_STATUSES,
    ).select_related('receipt', 'order_line', 'order_line__order')
    if start_date:
        receipt_lines = receipt_lines.filter(receipt__received_date__gte=start_date)
    if end_date:
        receipt_lines = receipt_lines.filter(receipt__received_date__lte=end_date)
    period_receipts = {}
    for receipt_line in receipt_lines:
        key = receipt_line.order_line_id
        bucket = period_receipts.setdefault(key, {'quantity': 0, 'dates': []})
        bucket['quantity'] += receipt_line.received_quantity
        bucket['dates'].append(receipt_line.receipt.received_date)
    return [
        (line, period_receipts[line.id]['quantity'], sorted(set(period_receipts[line.id]['dates'])))
        for line in lines if line.id in period_receipts
    ]


def _supplier_reconciliation_context(request):
    supplier_text = request.GET.get('supplier', '').strip()
    start_text, end_text = request.GET.get('start_date', ''), request.GET.get('end_date', '')
    date_basis = request.GET.get('date_basis', 'order')
    if date_basis not in {'order', 'receipt'}:
        date_basis = 'order'
    start_date = end_date = None
    try:
        start_date = date.fromisoformat(start_text) if start_text else None
        end_date = date.fromisoformat(end_text) if end_text else None
    except ValueError:
        pass
    return supplier_text, start_text, end_text, date_basis, start_date, end_date


@role_required(can_view_reports, message='当前账号没有报表权限。')
def supplier_reconciliation(request):
    """采购与到货汇总：默认按下单日期，也可按实际到货日期统计。"""
    supplier_text, start_text, end_text, date_basis, start_date, end_date = _supplier_reconciliation_context(request)
    lines = _supplier_reconciliation_lines(supplier_text, start_date, end_date, date_basis)
    suppliers = {}
    for line, period_received_quantity, report_dates in lines:
        key = line.supplier_snapshot or '未填写'
        bucket = suppliers.setdefault(key, {
            'supplier': key, 'order_nos': set(), 'ordered_qty': 0, 'received_qty': 0,
            'ordered_amount': Decimal('0'), 'received_amount': Decimal('0'), 'lines': [],
        })
        bucket['order_nos'].add(line.order.order_no)
        bucket['ordered_qty'] += line.ordered_quantity
        bucket['received_qty'] += period_received_quantity
        if line.unit_cost is not None:
            bucket['ordered_amount'] += line.unit_cost * line.ordered_quantity
            bucket['received_amount'] += line.unit_cost * period_received_quantity
        bucket['lines'].append({
            'order_no': line.order.order_no,
            'order_date': line.order.order_date,
            'report_dates': report_dates,
            'name': line.name_snapshot,
            'unit_cost': line.unit_cost,
            'unit': line.unit,
            'ordered_quantity': line.ordered_quantity,
            'received_quantity': period_received_quantity,
            'ordered_amount': (line.unit_cost * line.ordered_quantity) if line.unit_cost is not None else None,
            'received_amount': (line.unit_cost * period_received_quantity) if line.unit_cost is not None else None,
        })
    rows = []
    for bucket in suppliers.values():
        bucket['order_count'] = len(bucket['order_nos'])
        rows.append(bucket)
    rows.sort(key=lambda row: row['supplier'])
    return render(request, 'supplier_reconciliation.html', {
        'rows': rows,
        'selected_supplier': supplier_text,
        'start_date': start_text,
        'end_date': end_text,
        'date_basis': date_basis,
        'date_basis_choices': [('order', '下单日期'), ('receipt', '到货日期')],
        'suppliers': (
            PurchaseOrderLine.objects.filter(order__status__in=PURCHASE_REPORT_ORDER_STATUSES).exclude(supplier_snapshot='')
            .values_list('supplier_snapshot', flat=True).distinct().order_by('supplier_snapshot')
        ),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_view_reports, message='当前账号没有报表导出权限。')
def supplier_reconciliation_export(request):
    """采购与到货汇总明细 CSV 导出，与页面使用相同统计口径。"""
    supplier_text, start_text, end_text, date_basis, start_date, end_date = _supplier_reconciliation_context(request)
    lines = _supplier_reconciliation_lines(supplier_text, start_date, end_date, date_basis)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="supplier-reconciliation.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['供应商', '采购订单', '下单日期', '统计到货日期', '物品名称', '物品编码', '单价（元）', '订购数量', '统计期间到货数量', '订购金额（元）', '统计期间到货金额（元）'])
    for line, period_received_quantity, report_dates in lines:
        ordered_amount = (line.unit_cost * line.ordered_quantity) if line.unit_cost is not None else ''
        received_amount = (line.unit_cost * period_received_quantity) if line.unit_cost is not None else ''
        writer.writerow([
            line.supplier_snapshot or '未填写', line.order.order_no, line.order.order_date.isoformat(),
            '、'.join(value.isoformat() for value in report_dates),
            line.name_snapshot, line.code_snapshot or '', line.unit_cost if line.unit_cost is not None else '',
            line.ordered_quantity, period_received_quantity, ordered_amount, received_amount,
        ])
    return response


@role_required(can_manage_inventory, message='当前账号没有采购草稿编辑权限。')
def purchase_draft_edit(request, purchase_id):
    purchase_request = get_object_or_404(
        PurchaseRequest.objects.prefetch_related('lines'),
        pk=purchase_id,
    )
    if request.method == 'POST':
        quantities = {
            str(line.id): request.POST.get(f'quantity_{line.id}', line.quantity)
            for line in purchase_request.lines.all()
        }
        try:
            update_purchase_draft(
                purchase_request,
                request.user,
                quantities,
                reason=request.POST.get('reason', ''),
                note=request.POST.get('note', ''),
            )
        except (ValueError, WorkflowError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'采购草稿 {purchase_request.purchase_no} 已保存。')
            return redirect('purchase_draft_list')
    if purchase_request.status != PurchaseRequest.Status.DRAFT:
        messages.error(request, '只有采购草稿可以编辑。')
        return redirect('purchase_draft_detail', purchase_id=purchase_request.id)
    return render(request, 'purchase_draft_form.html', {
        'purchase_request': purchase_request,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_manage_inventory, message='当前账号没有采购草稿删除权限。')
def purchase_draft_delete(request, purchase_id):
    if request.method != 'POST':
        return redirect('purchase_draft_list')
    purchase_request = get_object_or_404(PurchaseRequest, pk=purchase_id)
    try:
        purchase_no = purchase_request.purchase_no
        delete_purchase_draft(purchase_request, request.user)
    except WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'采购草稿 {purchase_no} 已删除。')
    return redirect('purchase_draft_list')


@role_required(can_manage_inventory, message='当前账号没有成品设备管理权限。')
def composite_unit_management(request):
    """成品设备管理：创建空壳、装入/拆出组件、拆散整机。

    借用/售出/归还走申请审批流（成品出库/归还通过服务层发起 WorkflowRequest）。
    """
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '')
    selected = None
    unit_id = request.GET.get('unit') or request.POST.get('unit_id')
    if unit_id:
        selected = CompositeUnit.objects.select_related('customer').prefetch_related(
            'component_links__asset__item_type', 'component_links__asset__warehouse',
        ).filter(pk=unit_id).first()

    if request.GET.get('download') == 'bom_template':
        response = HttpResponse(
            build_bom_template().getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = "attachment; filename*=UTF-8''unit-bom-template.xlsx"
        return response

    if request.method == 'POST':
        action = request.POST.get('action', '')
        try:
            if action == 'import_bom':
                upload = request.FILES.get('workbook')
                if not upload:
                    raise BomImportError('请选择要导入的 Excel 文件。')
                if not upload.name.lower().endswith('.xlsx'):
                    raise BomImportError('仅支持 .xlsx 文件。')
                if upload.size > 10 * 1024 * 1024:
                    raise BomImportError('Excel 文件不能超过 10 MB。')
                units = import_unit_bom(actor=request.user, content=upload.read())
                messages.success(request, f'BOM 导入完成：新建 {len(units)} 台成品设备并装入组件。')
                return redirect('composite_unit_management')

            if action == 'create_unit':
                unit = create_unit(
                    actor=request.user,
                    name=request.POST.get('name', ''),
                    unit_code=request.POST.get('unit_code', ''),
                    model=request.POST.get('model', ''),
                    remarks=request.POST.get('remarks', ''),
                )
                messages.success(request, f'成品设备已创建：{unit}，请继续装入组件。')
                return redirect(f'{reverse("composite_unit_management")}?unit={unit.id}')

            if selected is None:
                raise AssemblyError('请先选择要操作的成品设备。')

            if action == 'add_components':
                links = add_components(
                    unit=selected,
                    asset_ids=request.POST.getlist('asset_ids'),
                    actor=request.user,
                    note=request.POST.get('note', ''),
                )
                messages.success(request, f'已装入 {len(links)} 件组件。')
            elif action == 'remove_component':
                asset = remove_component(
                    unit=selected,
                    asset_id=request.POST.get('asset_id'),
                    actor=request.user,
                    note=request.POST.get('note', ''),
                )
                messages.success(request, f'已拆出组件 {asset.asset_code or asset.name}，回到在库。')
            elif action == 'disassemble':
                disassemble_unit(unit=selected, actor=request.user, reason=request.POST.get('reason', ''))
                messages.success(request, f'成品设备 {selected} 已拆散，组件已全部回到在库。')
                return redirect('composite_unit_management')
            elif action == 'delete':
                force_delete = request.POST.get('force') == '1' and is_system_administrator(request.user)
                label = delete_unit(
                    unit=selected,
                    actor=request.user,
                    reason=request.POST.get('reason', ''),
                    force=force_delete,
                )
                if force_delete:
                    messages.success(request, f'成品设备 {label} 已强制删除（含历史履历），在装组件已释放回在库。')
                else:
                    messages.success(request, f'成品设备 {label} 已删除。')
                return redirect('composite_unit_management')
            else:
                raise AssemblyError('未知操作。')
            return redirect(f'{reverse("composite_unit_management")}?unit={selected.id}')
        except AssemblyError as exc:
            messages.error(request, str(exc))
        except BomImportError as exc:
            messages.error(request, f'BOM 导入失败，未写入任何数据。{exc}')

    units = CompositeUnit.objects.select_related('customer').prefetch_related('component_links').annotate(
        # 列表"组件数"只统计仍在装的组件；已拆出的历史履历不计入，与详情页口径一致。
        active_component_count=Count('component_links', filter=Q(component_links__disassembled_at__isnull=True)),
    )
    if status_filter in dict(CompositeUnit.Status.choices):
        units = units.filter(status=status_filter)
    else:
        # 默认隐藏已拆散的历史成品，避免污染列表；需查看时主动筛选"已拆散"。
        units = units.exclude(status=CompositeUnit.Status.DISASSEMBLED)
    if query:
        units = units.filter(
            Q(name__icontains=query) | Q(unit_code__icontains=query) | Q(model__icontains=query)
        )
    units = units.order_by('-created_at')[:100]

    # 组件筛选用独立参数 aq，避免与列表搜索 q 互相干扰。
    asset_query = request.GET.get('aq', '').strip()
    candidate_assets = None
    candidate_total = 0
    if selected and selected.status in {CompositeUnit.Status.DRAFT, CompositeUnit.Status.ASSEMBLED}:
        base_qs = Asset.objects.select_related('item_type', 'warehouse', 'location').filter(
            available_asset_filter(),
        ).order_by('item_type__name', 'asset_code')
        if asset_query:
            base_qs = base_qs.filter(asset_search_query(asset_query)).distinct()
        candidate_total = base_qs.count()
        # 候选资产可能上千，全部平铺会把页面撑到几千像素；未筛选时只展示前 30 条并引导筛选。
        candidate_assets = base_qs[:30 if not asset_query else 200]

    active_links = []
    inactive_links = []
    if selected:
        all_links = sorted(selected.component_links.all(), key=lambda l: l.assembled_at, reverse=True)
        active_links = [link for link in all_links if link.disassembled_at is None]
        inactive_links = [link for link in all_links if link.disassembled_at is not None]

    return render(request, 'composite_unit.html', {
        'units': units,
        'selected': selected,
        'active_links': active_links,
        'inactive_links': inactive_links,
        'can_force_delete': is_system_administrator(request.user),
        'candidate_assets': candidate_assets,
        'candidate_total': candidate_total,
        'status_choices': CompositeUnit.Status.choices,
        'status_filter': status_filter,
        'query': query,
        'asset_query': asset_query,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_manage_inventory, message='当前账号没有库存转换权限。')
def inventory_conversion(request):
    direction = request.GET.get('direction') or request.POST.get('direction') or InventoryConversion.Direction.STOCK_TO_ASSET
    if direction not in dict(InventoryConversion.Direction.choices):
        direction = InventoryConversion.Direction.STOCK_TO_ASSET
    query = request.GET.get('q', '').strip()
    eligible_assets = Asset.objects.select_related('item_type', 'warehouse', 'location').filter(
        available_asset_filter(),
    ).order_by('item_type__name', 'asset_code')
    if query:
        eligible_assets = eligible_assets.filter(asset_search_query(query)).distinct()
    eligible_assets = eligible_assets[:200]
    if request.method == 'POST':
        try:
            if direction == InventoryConversion.Direction.STOCK_TO_ASSET:
                try:
                    quantity = int(request.POST.get('quantity', '0'))
                except ValueError:
                    raise InventoryConversionError('转换数量必须是整数。')
                conversion, _ = convert_stock_to_assets(
                    actor=request.user,
                    stock_item_id=request.POST.get('stock_item_id'),
                    quantity=quantity,
                    serial_numbers=request.POST.get('serial_numbers', ''),
                    note=request.POST.get('note', ''),
                )
            else:
                conversion, _ = convert_assets_to_stock(
                    actor=request.user,
                    asset_ids=request.POST.getlist('asset_ids'),
                    target_stock_item_id=request.POST.get('target_stock_item_id') or None,
                    note=request.POST.get('note', ''),
                )
            messages.success(request, f'转换完成，记录编号 {conversion.conversion_no}。')
            return redirect('inventory_conversion')
        except InventoryConversionError as exc:
            messages.error(request, str(exc))
    return render(request, 'inventory_conversion.html', {
        'direction': direction,
        'direction_choices': InventoryConversion.Direction.choices,
        'stock_items': StockItem.objects.select_related('item_type', 'warehouse', 'location').filter(quantity__gt=0).order_by('name'),
        'target_stock_items': StockItem.objects.select_related('item_type', 'warehouse', 'location').order_by('name'),
        'eligible_assets': eligible_assets,
        'query': query,
        'preselected_asset_id': request.GET.get('asset_id', ''),
        'preselected_stock_item_id': request.GET.get('stock_item_id', ''),
        'recent_conversions': InventoryConversion.objects.select_related('item_type', 'stock_item', 'created_by').prefetch_related('assets')[:20],
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(is_system_administrator, message='当前账号没有库存导入权限。')
def inventory_import(request):
    if request.GET.get('download') == 'template':
        response = HttpResponse(
            build_inventory_import_template().getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = "attachment; filename*=UTF-8''inventory-import-template.xlsx"
        return response

    if request.method == 'POST':
        upload = request.FILES.get('workbook')
        mode = request.POST.get('mode', ImportBatch.Mode.INCREMENTAL)
        should_apply = request.POST.get('apply') == '1'
        if not upload:
            messages.error(request, '请选择要导入的 Excel 文件。')
        elif not upload.name.lower().endswith('.xlsx'):
            messages.error(request, '仅支持 .xlsx 文件。')
        elif upload.size > 10 * 1024 * 1024:
            messages.error(request, 'Excel 文件不能超过 10 MB。')
        elif mode not in dict(ImportBatch.Mode.choices):
            messages.error(request, '导入模式无效。')
        elif should_apply and mode == ImportBatch.Mode.BASELINE and request.POST.get('baseline_confirmation', '').strip() != '确认全量基准导入':
            messages.error(request, '全量基准导入必须输入“确认全量基准导入”。')
        else:
            try:
                content = upload.read()
                records, issues, summary = parse_inventory_workbook(content)
                issues.extend(validate_inventory_records(records))
                summary = {
                    **summary,
                    'error_count': sum(issue['severity'] == 'error' for issue in issues),
                    'warning_count': sum(issue['severity'] == 'warning' for issue in issues),
                }
                batch = create_import_batch(
                    actor=request.user, filename=upload.name, content=content, mode=mode,
                    records=records, issues=issues, summary=summary,
                )
                if should_apply:
                    if summary['error_count']:
                        messages.error(request, '预检存在阻断问题，未写入正式库存。请打开批次查看并修正 Excel。')
                    else:
                        apply_inventory_import(actor=request.user, batch=batch, records=records)
                        messages.success(request, f'{batch.get_mode_display()}完成，共处理 {len(records)} 行。')
                else:
                    messages.success(request, f'预检完成：可处理 {len(records)} 行，发现 {summary["error_count"]} 个阻断问题。')
                return redirect(f'{reverse("inventory_import")}?batch={batch.id}')
            except InventoryImportError as exc:
                messages.error(request, str(exc))
    selected_batch = _get_by_uuid(ImportBatch, request.GET.get('batch'))
    return render(request, 'inventory_import.html', {
        'mode_choices': ImportBatch.Mode.choices,
        'recent_batches': ImportBatch.objects.select_related('created_by').annotate(issue_count=Count('issues'))[:12],
        'selected_batch': selected_batch,
        'selected_issues': selected_batch.issues.all()[:200] if selected_batch else [],
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@login_required
def inventory_check_list(request):
    if not is_warehouse_operator(request.user):
        messages.error(request, '当前账号没有盘点权限。')
        return redirect('home')
    tasks = InventoryCheckTask.objects.select_related('warehouse', 'location', 'created_by', 'reviewed_by').annotate(
        line_total=Count('lines'),
        line_counted=Count('lines', filter=Q(lines__counted_quantity__isnull=False)),
        line_differences=Count('lines', filter=~Q(lines__result=InventoryCheckLine.Result.MATCHED)),
    ).order_by('-created_at')
    status = request.GET.get('status', '').strip()
    if status in dict(InventoryCheckTask.Status.choices):
        tasks = tasks.filter(status=status)
    return render(request, 'inventory_check_list.html', {
        'tasks': tasks,
        'status_choices': InventoryCheckTask.Status.choices,
        'selected_status': status,
        'can_create_check': is_system_administrator(request.user),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(is_system_administrator, message='当前账号没有盘点任务管理权限。')
def inventory_check_create(request):
    if request.method == 'POST':
        try:
            task = create_check_task(
                actor=request.user,
                name=request.POST.get('name', ''),
                warehouse_id=request.POST.get('warehouse_id'),
                location_id=request.POST.get('location_id') or None,
                note=request.POST.get('note', ''),
            )
            messages.success(request, f'盘点任务 {task.check_no} 已创建，共生成 {task.lines.count()} 条账面明细。')
            return redirect('inventory_check_detail', task_id=task.id)
        except InventoryCheckError as exc:
            messages.error(request, str(exc))
    return render(request, 'inventory_check_create.html', {
        'warehouses': Warehouse.objects.filter(is_active=True).prefetch_related('locations').order_by('name'),
        'locations': Location.objects.select_related('warehouse').order_by('warehouse__name', 'code'),
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@login_required
def inventory_check_detail(request, task_id):
    task = InventoryCheckTask.objects.select_related(
        'warehouse', 'location', 'created_by', 'started_by', 'reviewed_by'
    ).filter(pk=task_id).first()
    if not task or not is_warehouse_operator(request.user):
        raise Http404('未找到可查看的盘点任务。')
    can_scan = can_manage_inventory(request.user) and task.status == InventoryCheckTask.Status.IN_PROGRESS
    can_correct = can_scan
    can_start = can_manage_inventory(request.user) and task.status == InventoryCheckTask.Status.DRAFT
    can_submit = can_scan
    can_review = is_system_administrator(request.user) and task.status == InventoryCheckTask.Status.PENDING_REVIEW
    if request.method == 'POST':
        action = request.POST.get('action', '')
        offline_sync = request.headers.get('X-Offline-Sync') == '1' or request.POST.get('offline_sync') == '1'
        try:
            if action == 'start' and can_start:
                start_check_task(task, request.user)
                messages.success(request, '盘点任务已开始，可以扫码或输入编码。')
            elif action == 'scan' and can_scan:
                try:
                    quantity = int(request.POST.get('quantity', '1'))
                except ValueError:
                    raise InventoryCheckError('实盘数量必须是整数。')
                scan = scan_check_value(task, request.user, request.POST.get('scanned_value', ''), quantity, request.POST.get('note', ''))
                messages.success(request, f'扫描完成：{scan.get_result_display()}。')
                if offline_sync:
                    return JsonResponse({'ok': True, 'message': f'扫描完成：{scan.get_result_display()}。'})
            elif action == 'correct' and can_correct:
                try:
                    quantity = int(request.POST.get('quantity', ''))
                except ValueError:
                    raise InventoryCheckError('修正数量必须是整数。')
                correct_check_line(
                    task,
                    request.user,
                    request.POST.get('line_id'),
                    quantity,
                    request.POST.get('correction_note', ''),
                )
                messages.success(request, '盘点明细已修正，并已记录操作审计。')
            elif action == 'submit' and can_submit:
                submit_check_task(task, request.user)
                messages.success(request, '盘点结果已提交，等待管理员复核。')
            elif action == 'review' and can_review:
                _, adjustments = review_check_task(task, request.user, request.POST.get('review_note', '').strip())
                messages.success(request, f'盘点已完成，执行了 {len(adjustments)} 项差异调整。')
            elif action == 'reopen' and can_review:
                reopen_check_task(task, request.user, request.POST.get('reopen_note', '').strip())
                messages.success(request, '盘点任务已退回重盘。')
            elif action == 'cancel' and is_system_administrator(request.user):
                cancel_check_task(task, request.user)
                messages.success(request, '盘点任务已取消。')
            else:
                raise InventoryCheckError('当前状态或账号不允许执行该操作。')
        except InventoryCheckError as exc:
            if offline_sync:
                return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
            messages.error(request, str(exc))
        return redirect('inventory_check_detail', task_id=task.id)

    lines = task.lines.select_related(
        'item_type', 'asset', 'stock_item', 'expected_warehouse', 'expected_location',
        'observed_warehouse', 'observed_location', 'counted_by',
    ).order_by('result', 'item_type__name', 'expected_asset_code', 'stock_item__name')
    scans = task.scans.select_related('line', 'asset', 'stock_item', 'operator').order_by('-created_at')[:30]
    line_total = lines.count()
    counted_count = lines.filter(counted_quantity__isnull=False).count()
    difference_count = lines.exclude(result=InventoryCheckLine.Result.MATCHED).count()
    return render(request, 'inventory_check_detail.html', {
        'task': task,
        'lines': lines,
        'scans': scans,
        'line_total': line_total,
        'counted_count': counted_count,
        'difference_count': difference_count,
        'shortage_count': lines.filter(result=InventoryCheckLine.Result.SHORTAGE).count(),
        'surplus_count': lines.filter(result=InventoryCheckLine.Result.SURPLUS).count(),
        'location_mismatch_count': lines.filter(result=InventoryCheckLine.Result.LOCATION_MISMATCH).count(),
        'status_mismatch_count': lines.filter(result=InventoryCheckLine.Result.STATUS_MISMATCH).count(),
        'can_start': can_start,
        'can_scan': can_scan,
        'can_correct': can_correct,
        'can_submit': can_submit,
        'can_review': can_review,
        'can_cancel': is_system_administrator(request.user) and task.status in {InventoryCheckTask.Status.DRAFT, InventoryCheckTask.Status.IN_PROGRESS},
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@login_required
def inventory_check_export(request, task_id):
    if not is_warehouse_operator(request.user):
        messages.error(request, '当前账号没有导出盘点结果的权限。')
        return redirect('home')
    task = InventoryCheckTask.objects.filter(pk=task_id).first()
    if not task:
        raise Http404('未找到要导出的盘点任务。')
    output = export_check_task_xlsx(task)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    filename = quote(f'{task.check_no}-盘点结果.xlsx')
    response['Content-Disposition'] = f"attachment; filename*=UTF-8''{filename}"
    return response


@role_required(can_manage_inventory, message='当前账号没有资产资料维护权限。')
def warehouse_asset_edit(request, asset_id=None):
    asset = Asset.objects.filter(pk=asset_id).first() if asset_id else None
    is_create = asset is None
    form = AssetManagementForm(request.POST or None, instance=asset)
    if request.method == 'POST' and form.is_valid():
        before, after = form_change_data(form)
        with transaction.atomic():
            asset = form.save()
            record_operation(
                request.user,
                'asset_created' if is_create else 'asset_updated',
                asset,
                summary=f'{"录入" if is_create else "修改"}资产 {asset.system_asset_no}',
                before=before,
                after=after,
            )
        messages.success(request, f'已保存资产 {asset.system_asset_no}。')
        return redirect('warehouse_management')
    return render(request, 'warehouse_asset_form.html', {
        'form': form,
        'asset': asset,
        'asset_qr_image': _asset_qr_image(asset) if asset else '',
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@login_required
def asset_lifecycle(request, asset_id):
    if not is_warehouse_operator(request.user):
        messages.error(request, '当前账号没有查看资产生命周期的权限。')
        return redirect('home')
    asset = get_object_or_404(
        Asset.objects.select_related('item_type', 'warehouse', 'location', 'current_holder', 'department'),
        pk=asset_id,
    )
    can_manage = can_manage_inventory(request.user)
    can_handover = can_execute_inventory(request.user)
    tab = request.GET.get('tab', 'timeline')
    if tab not in {'timeline', 'repairs', 'handover', 'attachments'}:
        tab = 'timeline'

    holder_name = asset.external_holder_name
    if asset.current_holder_id:
        holder_name = asset.current_holder.get_full_name() or asset.current_holder.get_username()

    maintenance_create_form = MaintenanceCreateForm(prefix='create-repair')
    maintenance_update_form = None
    maintenance_finish_form = None
    handover_form = AssetHandoverForm(
        prefix='handover',
        initial={
            'from_holder_name': holder_name,
            'handover_date': timezone.localdate(),
        },
    )
    attachment_form = AssetAttachmentForm(prefix='attachment', asset=asset)
    scrap_form = ScrapApprovalForm(prefix='scrap')
    damage_form = DamageApprovalForm(prefix='damage')

    selected_maintenance = None
    selected_maintenance_id = request.GET.get('maintenance') or request.POST.get('maintenance_id')
    if selected_maintenance_id:
        selected_maintenance = MaintenanceRecord.objects.filter(
            pk=selected_maintenance_id,
            asset=asset,
        ).select_related('responsible_user', 'source_request').first()
    if selected_maintenance:
        maintenance_update_form = MaintenanceUpdateForm(instance=selected_maintenance, prefix='repair')
        maintenance_finish_form = MaintenanceFinishForm(
            prefix='finish',
            initial={
                'resolution': selected_maintenance.resolution,
                'actual_cost': selected_maintenance.actual_cost,
                'returned_date': selected_maintenance.returned_date,
            },
        )

    if request.method == 'POST':
        action = request.POST.get('action', '')
        redirect_tab = 'timeline'
        try:
            if action == 'create_maintenance' and can_manage:
                redirect_tab = 'repairs'
                maintenance_create_form = MaintenanceCreateForm(request.POST, prefix='create-repair')
                if maintenance_create_form.is_valid():
                    record = create_maintenance_record(
                        asset=asset,
                        actor=request.user,
                        **maintenance_create_form.cleaned_data,
                    )
                    messages.success(request, f'已新建维修单 {record.maintenance_no}。')
                    return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=repairs&maintenance={record.id}')
            elif action == 'update_maintenance' and can_manage and selected_maintenance:
                redirect_tab = 'repairs'
                maintenance_update_form = MaintenanceUpdateForm(
                    request.POST,
                    instance=selected_maintenance,
                    prefix='repair',
                )
                if maintenance_update_form.is_valid():
                    record = update_maintenance_record(
                        selected_maintenance,
                        request.user,
                        **maintenance_update_form.cleaned_data,
                    )
                    messages.success(request, f'维修单 {record.maintenance_no} 已更新。')
                    return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=repairs&maintenance={record.id}')
            elif action in {'complete_maintenance', 'cancel_maintenance'} and can_manage and selected_maintenance:
                redirect_tab = 'repairs'
                maintenance_finish_form = MaintenanceFinishForm(request.POST, prefix='finish')
                if maintenance_finish_form.is_valid():
                    record = finish_maintenance_record(
                        selected_maintenance,
                        request.user,
                        cancel=action == 'cancel_maintenance',
                        **maintenance_finish_form.cleaned_data,
                    )
                    messages.success(request, f'维修单 {record.maintenance_no} 已{record.get_status_display()}。')
                    return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=repairs')
            elif action == 'create_handover' and can_handover:
                redirect_tab = 'handover'
                handover_form = AssetHandoverForm(request.POST, prefix='handover')
                if handover_form.is_valid():
                    handover = create_handover(
                        asset=asset,
                        actor=request.user,
                        **handover_form.cleaned_data,
                    )
                    messages.success(request, f'已新建交接记录 {handover.handover_no}。')
                    return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=handover')
            elif action in {'confirm_handover', 'cancel_handover'} and can_handover:
                redirect_tab = 'handover'
                handover = AssetHandover.objects.filter(
                    pk=request.POST.get('handover_id'),
                    asset=asset,
                ).first()
                if not handover:
                    raise AssetLifecycleError('未找到要处理的交接记录。')
                finish_handover(handover, request.user, cancel=action == 'cancel_handover')
                messages.success(request, f'交接记录 {handover.handover_no} 已处理。')
                return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=handover')
            elif action == 'archive_attachment' and can_manage:
                redirect_tab = 'attachments'
                attachment_form = AssetAttachmentForm(
                    request.POST,
                    request.FILES,
                    prefix='attachment',
                    asset=asset,
                )
                if attachment_form.is_valid():
                    attachment = archive_asset_attachment(
                        asset=asset,
                        actor=request.user,
                        **attachment_form.cleaned_data,
                    )
                    messages.success(request, f'附件“{attachment.display_name}”已归档。')
                    return redirect(f'{reverse("asset_lifecycle", args=[asset.id])}?tab=attachments')
            elif action == 'request_scrap' and can_manage:
                scrap_form = ScrapApprovalForm(request.POST, prefix='scrap')
                if scrap_form.is_valid():
                    request_obj = create_scrap_approval_request(
                        asset=asset,
                        actor=request.user,
                        **scrap_form.cleaned_data,
                    )
                    messages.success(request, f'报废申请 {request_obj.request_no} 已提交审批。')
                    return redirect('workflow_detail', request_id=request_obj.id)
            elif action == 'request_damage' and can_manage:
                damage_form = DamageApprovalForm(request.POST, prefix='damage')
                if damage_form.is_valid():
                    request_obj = create_damage_approval_request(
                        asset=asset,
                        actor=request.user,
                        **damage_form.cleaned_data,
                    )
                    messages.success(request, f'报损申请 {request_obj.request_no} 已提交审批。')
                    return redirect('workflow_detail', request_id=request_obj.id)
            else:
                raise AssetLifecycleError('当前账号或资产状态不允许执行该操作。')
        except (AssetLifecycleError, WorkflowError) as exc:
            messages.error(request, str(exc))
        tab = redirect_tab

    maintenance_records = asset.maintenance_records.select_related(
        'source_request', 'responsible_user', 'created_by', 'completed_by',
    ).prefetch_related('attachments')
    handovers = asset.handovers.select_related('to_user', 'created_by', 'confirmed_by').prefetch_related('attachments')
    attachments = asset.attachments.select_related('maintenance', 'handover', 'uploaded_by')

    timeline = []
    for event in asset.lifecycle_events.select_related('actor'):
        related_url = ''
        if event.related_model == 'inventory.maintenancerecord':
            related_url = f'{reverse("asset_lifecycle", args=[asset.id])}?tab=repairs&maintenance={event.related_object_id}'
        elif event.related_model == 'inventory.assethandover':
            related_url = f'{reverse("asset_lifecycle", args=[asset.id])}?tab=handover'
        elif event.related_model == 'inventory.assetattachment':
            related_url = f'{reverse("asset_lifecycle", args=[asset.id])}?tab=attachments'
        elif event.related_model == 'workflow.workflowrequest':
            related_url = reverse('workflow_detail', args=[event.related_object_id])
        timeline.append({
            'at': event.event_at,
            'kind': event.get_event_type_display(),
            'title': event.title,
            'description': event.description,
            'actor': event.actor,
            'url': related_url,
        })
    for item in asset.transactions.select_related('request', 'actor', 'source_warehouse', 'target_warehouse'):
        timeline.append({
            'at': item.created_at,
            'kind': '出入库',
            'title': f'{item.get_action_display()} · {item.request.inventory_no or item.request.request_no}',
            'description': f'状态：{dict(Asset.Status.choices).get(item.asset_status_before, item.asset_status_before)} -> {dict(Asset.Status.choices).get(item.asset_status_after, item.asset_status_after)}',
            'actor': item.actor,
            'url': reverse('workflow_detail', args=[item.request_id]),
        })
    for line in asset.inventory_check_lines.exclude(counted_at__isnull=True).select_related('task', 'counted_by'):
        timeline.append({
            'at': line.counted_at,
            'kind': '盘点',
            'title': f'{line.task.check_no} · {line.get_result_display()}',
            'description': line.note,
            'actor': line.counted_by,
            'url': reverse('inventory_check_detail', args=[line.task_id]),
        })
    timeline.sort(key=lambda row: row['at'], reverse=True)

    return render(request, 'asset_lifecycle.html', {
        'asset': asset,
        'holder_name': holder_name,
        'tab': tab,
        'timeline': timeline,
        'maintenance_records': maintenance_records,
        'selected_maintenance': selected_maintenance,
        'maintenance_create_form': maintenance_create_form,
        'maintenance_update_form': maintenance_update_form,
        'maintenance_finish_form': maintenance_finish_form,
        'handovers': handovers,
        'handover_form': handover_form,
        'attachments': attachments,
        'attachment_form': attachment_form,
        'scrap_form': scrap_form,
        'damage_form': damage_form,
        'can_manage_lifecycle': can_manage,
        'can_handover': can_handover,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@login_required
def asset_attachment_download(request, attachment_id):
    if not is_warehouse_operator(request.user):
        raise Http404('未找到该附件。')
    attachment = get_object_or_404(AssetAttachment, pk=attachment_id)
    try:
        file_handle = attachment.file.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404('附件文件不存在。')
    download_name = attachment.display_name
    original_suffix = Path(attachment.file.name).suffix
    if original_suffix and not Path(download_name).suffix:
        download_name = f'{download_name}{original_suffix}'
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=download_name,
    )


@role_required(can_manage_inventory, message='当前账号没有耗材配件维护权限。')
def warehouse_stock_item_edit(request, stock_item_id=None):
    stock_item = StockItem.objects.filter(pk=stock_item_id).first() if stock_item_id else None
    is_create = stock_item is None
    form = StockItemManagementForm(request.POST or None, instance=stock_item)
    if request.method == 'POST' and form.is_valid():
        before, after = form_change_data(form)
        with transaction.atomic():
            stock_item = form.save()
            if is_create:
                after['quantity'] = stock_item.quantity
            record_operation(
                request.user,
                'stock_item_created' if is_create else 'stock_item_updated',
                stock_item,
                summary=f'{"录入" if is_create else "修改"}耗材配件 {stock_item.code}',
                before=before,
                after=after,
            )
        messages.success(request, f'已保存耗材配件 {stock_item.name}。')
        return redirect('warehouse_management')
    return render(request, 'warehouse_stock_item_form.html', {
        'form': form,
        'stock_item': stock_item,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(is_system_administrator, message='当前账号没有基础资料维护权限。')
def warehouse_setup(request, section):
    config = SETUP_SECTIONS.get(section)
    if not config:
        messages.error(request, '未找到该基础资料页面。')
        return redirect('warehouse_management')
    title, model, _ = config
    entries = model.objects.all()
    if section == 'locations':
        entries = entries.select_related('warehouse')
    elif section == 'item-types':
        entries = entries.select_related('category')
    elif section == 'projects':
        entries = entries.select_related('customer')
    elif section == 'work-orders':
        entries = entries.select_related('project', 'customer')
    return render(request, 'warehouse_setup.html', {
        'section': section,
        'section_title': title,
        'entries': entries,
        'setup_sections': [(key, config[0]) for key, config in SETUP_SECTIONS.items() if key != 'categories'],
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@role_required(is_system_administrator, message='当前账号没有基础资料维护权限。')
def warehouse_setup_edit(request, section, object_id=None):
    config = SETUP_SECTIONS.get(section)
    if not config:
        messages.error(request, '未找到该基础资料页面。')
        return redirect('warehouse_management')
    title, model, form_class = config
    instance = model.objects.filter(pk=object_id).first() if object_id else None
    if object_id and not instance:
        messages.error(request, '未找到需要编辑的记录。')
        return redirect('warehouse_setup', section=section)
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        is_create = instance is None
        before, after = form_change_data(form)
        with transaction.atomic():
            instance = form.save()
            record_operation(
                request.user,
                f'{section}_created' if is_create else f'{section}_updated',
                instance,
                summary=f'{"新建" if is_create else "修改"}{title} {instance}',
                before=before,
                after=after,
            )
        messages.success(request, f'已保存{title}“{instance}”。')
        return redirect('warehouse_setup', section=section)
    return render(request, 'warehouse_setup_form.html', {
        'section': section,
        'section_title': title,
        'form': form,
        'instance': instance,
        'setup_sections': [(key, config[0]) for key, config in SETUP_SECTIONS.items() if key != 'categories'],
        'user_can_use_admin': True,
        'user_is_operator': True,
        'user_is_system_admin': True,
        'user_role_label': _user_role_label(request.user, True, True),
    })


@role_required(can_manage_inventory, message='当前账号没有二维码标签管理权限。')
def warehouse_qr_labels(request):
    asset_ids = request.POST.getlist('asset_ids')
    if not asset_ids:
        messages.error(request, '请先勾选至少一项资产再打印二维码。')
        return redirect('warehouse_management')
    assets = Asset.objects.select_related('item_type').filter(pk__in=asset_ids).order_by('system_asset_no')
    labels = []
    skipped = 0
    for asset in assets:
        if not asset.system_asset_no:
            skipped += 1
            continue
        labels.append({
            'asset': asset,
            'qr_image': _asset_qr_image(asset),
        })
    if skipped:
        messages.warning(request, f'{skipped} 件资产尚未生成仓库系统编号，未生成标签。')
    if not labels:
        return redirect('warehouse_management')
    return render(request, 'warehouse_qr_labels.html', {
        'labels': labels,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


def _query_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _filter_request_queryset(queryset, params):
    query = params.get('q', '').strip()
    if query:
        queryset = queryset.filter(
            Q(request_no__icontains=query)
            | Q(inventory_no__icontains=query)
            | Q(reason__icontains=query)
            | Q(recipient_name__icontains=query)
            | Q(recipient_company__icontains=query)
            | Q(recipient_phone__icontains=query)
            | Q(usage_location__icontains=query)
            | Q(project_code__icontains=query)
            | Q(work_order_no__icontains=query)
            | Q(applicant__username__icontains=query)
            | Q(designated_approver__username__icontains=query)
            | Q(lines__asset__search_index__icontains=query)
            | Q(lines__stock_item__name__icontains=query)
            | Q(lines__stock_item__code__icontains=query)
        )
    if params.get('request_type'):
        queryset = queryset.filter(request_type=params['request_type'])
    if params.get('status'):
        queryset = queryset.filter(status=params['status'])
    if params.get('warehouse'):
        queryset = queryset.filter(
            Q(target_warehouse_id=params['warehouse'])
            | Q(lines__asset__warehouse_id=params['warehouse'])
            | Q(lines__stock_item__warehouse_id=params['warehouse'])
        )
    if params.get('project_code'):
        queryset = queryset.filter(project_code__icontains=params['project_code'].strip())
    if params.get('work_order_no'):
        queryset = queryset.filter(work_order_no__icontains=params['work_order_no'].strip())
    application_start = _query_date(params.get('application_start', ''))
    application_end = _query_date(params.get('application_end', ''))
    return_start = _query_date(params.get('return_start', ''))
    return_end = _query_date(params.get('return_end', ''))
    if application_start:
        queryset = queryset.filter(application_date__gte=application_start)
    if application_end:
        queryset = queryset.filter(application_date__lte=application_end)
    if return_start:
        queryset = queryset.filter(expected_return_date__gte=return_start)
    if return_end:
        queryset = queryset.filter(expected_return_date__lte=return_end)
    return queryset.distinct()


@login_required
def request_list(request):
    requests = _filter_request_queryset(
        WorkflowRequest.objects.filter(applicant=request.user, is_hidden_by_applicant=False),
        request.GET,
    ).prefetch_related('lines__asset', 'lines__stock_item').order_by('-application_date', '-created_at')
    return render(request, 'request_list.html', {
        'requests': requests,
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
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': is_warehouse_operator(request.user),
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(
            request.user,
            is_warehouse_operator(request.user),
            is_system_administrator(request.user),
        ),
    })


@role_required(is_warehouse_operator, message='当前账号没有出入库记录权限。')
def transaction_list(request):
    transactions = InventoryTransaction.objects.select_related(
        'request', 'asset', 'stock_item', 'actor', 'source_warehouse', 'target_warehouse',
    ).order_by('-business_date', '-created_at')
    query = request.GET.get('q', '').strip()
    if query:
        transactions = transactions.filter(
            Q(request__inventory_no__icontains=query)
            | Q(request__request_no__icontains=query)
            | Q(request__project_code__icontains=query)
            | Q(request__work_order_no__icontains=query)
            | Q(asset__search_index__icontains=query)
            | Q(stock_item__name__icontains=query)
            | Q(stock_item__code__icontains=query)
            | Q(holder_name__icontains=query)
            | Q(holder_company__icontains=query)
            | Q(actor__username__icontains=query)
            | Q(comment__icontains=query)
        )
    if request.GET.get('action'):
        transactions = transactions.filter(action=request.GET['action'])
    if request.GET.get('warehouse'):
        transactions = transactions.filter(
            Q(source_warehouse_id=request.GET['warehouse']) | Q(target_warehouse_id=request.GET['warehouse'])
        )
    if request.GET.get('project_code'):
        transactions = transactions.filter(project_code__icontains=request.GET['project_code'].strip())
    if request.GET.get('work_order_no'):
        transactions = transactions.filter(work_order_no__icontains=request.GET['work_order_no'].strip())
    for param, lookup in (
        ('transaction_start', 'business_date__gte'),
        ('transaction_end', 'business_date__lte'),
    ):
        parsed = _query_date(request.GET.get(param, ''))
        if parsed:
            transactions = transactions.filter(**{lookup: parsed})
    transactions = transactions.distinct()
    page_size = request.GET.get('page_size', '100')
    page_size = int(page_size) if page_size in {'50', '100', '200'} else 100
    page = Paginator(transactions, page_size).get_page(request.GET.get('page'))
    return render(request, 'transaction_list.html', {
        'transactions': page,
        'query': query,
        'selected_action': request.GET.get('action', ''),
        'selected_warehouse': request.GET.get('warehouse', ''),
        'project_code': request.GET.get('project_code', ''),
        'work_order_no': request.GET.get('work_order_no', ''),
        'transaction_start': request.GET.get('transaction_start', ''),
        'transaction_end': request.GET.get('transaction_end', ''),
        'page_size': page_size,
        'action_choices': WorkflowRequest.RequestType.choices,
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'transaction_count': transactions.count(),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_execute_inventory, message='当前账号没有快速出入库权限。')
def quick_inventory_process(request):
    query = request.GET.get('q', request.POST.get('item_query', '')).strip()
    asset_ids, stock_item_ids, unit_ids, selected_assets, selected_stock_items, selected_units = _load_selected_items(request)
    if request.method == 'POST':
        try:
            request_type = request.POST.get('request_type', '')
            recipient_name = request.POST.get('recipient_name', '').strip()
            recipient_phone = request.POST.get('recipient_phone', '').strip()
            usage_location = request.POST.get('usage_location', '').strip()
            contact_source = request.POST.get('contact_source', '').strip()
            reason = request.POST.get('reason', '').strip()
            transaction_date_text = request.POST.get('transaction_date', '').strip()
            expected_return_text = request.POST.get('expected_return_date', '').strip()
            target_warehouse = _get_by_uuid(Warehouse, request.POST.get('target_warehouse'))
            target_location = _get_by_uuid(Location, request.POST.get('target_location'))
            customer_ref = _get_by_uuid(Customer, request.POST.get('customer_ref'))
            project_ref = _get_by_uuid(Project, request.POST.get('project_ref'))
            work_order_ref = _get_by_uuid(WorkOrder, request.POST.get('work_order_ref'))
            if request_type not in dict(WorkflowRequest.RequestType.choices):
                raise WorkflowError('请选择有效的出入库类型。')
            if request_type == WorkflowRequest.RequestType.PURCHASE_RECEIPT:
                raise WorkflowError('采购入库只能通过采购订单的到货登记办理。')
            if not reason or not contact_source:
                raise WorkflowError('请填写处理原因和沟通来源。')
            if request_type in {
                WorkflowRequest.RequestType.BORROW,
                WorkflowRequest.RequestType.ISSUE,
                WorkflowRequest.RequestType.SALE,
            } and (not recipient_name or not recipient_phone or not usage_location):
                raise WorkflowError('该出库类型必须填写接收人、联系电话和使用或交付地点。')
            expected_return_date = None
            if expected_return_text:
                expected_return_date = _query_date(expected_return_text)
                if not expected_return_date:
                    raise WorkflowError('请填写正确的预计归还日期。')
            if request_type == WorkflowRequest.RequestType.BORROW and not expected_return_date:
                raise WorkflowError('借用必须填写预计归还日期。')
            if request_type in {
                WorkflowRequest.RequestType.RETURN,
                WorkflowRequest.RequestType.TRANSFER,
            } and (not target_warehouse or not target_location):
                raise WorkflowError('归还或调拨必须选择目标仓库和目标库位。')
            if target_location and target_warehouse and target_location.warehouse_id != target_warehouse.id:
                raise WorkflowError('目标库位不属于所选目标仓库。')
            if transaction_date_text:
                transaction_date = _query_date(transaction_date_text)
                if not transaction_date:
                    raise WorkflowError('请填写正确的出入库日期。')
            else:
                transaction_date = timezone.localdate()
            if len(asset_ids) != len(selected_assets) or len(stock_item_ids) != len(selected_stock_items):
                raise WorkflowError('部分已选物品不存在，请移除后重新选择。')
            unit_line_assets, primary_unit = _expand_unit_lines(request_type, selected_units)
            if not selected_assets and not selected_stock_items and not unit_line_assets:
                raise WorkflowError('请至少选择一项物品。')
            asset_only_types = {
                WorkflowRequest.RequestType.REPAIR,
                WorkflowRequest.RequestType.DAMAGE,
                WorkflowRequest.RequestType.RETURN_TO_VENDOR,
                WorkflowRequest.RequestType.SCRAP,
            }
            if request_type in asset_only_types and (selected_stock_items or selected_units):
                raise WorkflowError('维修、报损、退货给供应商和报废只能处理单件资产。')
            if request_type in {
                WorkflowRequest.RequestType.ISSUE,
                WorkflowRequest.RequestType.TRANSFER,
            } and selected_units:
                raise WorkflowError('领用和调拨不支持成品设备，请使用借用、售出或归还。')
            _attach_request_candidate_state(selected_assets, selected_stock_items, request.user, quick=True)
            for item in [*selected_assets, *selected_stock_items]:
                if request_type not in item.allowed_request_types.split(','):
                    raise WorkflowError(_candidate_incompatibility_message(item, request_type))
            stock_quantities = {
                str(item.id): _stock_quantity_from_post(request, item.id)
                for item in selected_stock_items
            }
            with transaction.atomic():
                quick_request = WorkflowRequest.objects.create(
                    request_type=request_type, applicant=request.user,
                    department=getattr(getattr(request.user, 'profile', None), 'department', None),
                    reason=reason, recipient_name=recipient_name,
                    recipient_company=request.POST.get('recipient_company', '').strip(),
                    recipient_phone=recipient_phone, usage_location=usage_location,
                    customer_ref=customer_ref, project_ref=project_ref, work_order_ref=work_order_ref,
                    project_code=request.POST.get('project_code', '').strip(),
                    work_order_no=request.POST.get('work_order_no', '').strip(),
                    application_date=transaction_date,
                    expected_return_date=expected_return_date,
                    transaction_date=transaction_date,
                    target_warehouse=target_warehouse,
                    target_location=target_location,
                    is_quick_process=True, contact_source=contact_source,
                    composite_unit=primary_unit,
                )
                WorkflowRequestLine.objects.bulk_create([
                    *[
                        WorkflowRequestLine(request=quick_request, asset=item, quantity=1)
                        for item in selected_assets
                    ],
                    *[
                        WorkflowRequestLine(request=quick_request, asset=item, quantity=1)
                        for item in unit_line_assets
                    ],
                    *[
                        WorkflowRequestLine(
                            request=quick_request,
                            stock_item=item,
                            quantity=stock_quantities[str(item.id)],
                        )
                        for item in selected_stock_items
                    ],
                ])
                quick_process_request(
                    quick_request,
                    request.user,
                    request.POST.get('comment', '').strip(),
                    transaction_date=transaction_date,
                )
            messages.success(request, f'快速出入库已完成，出入库单号：{quick_request.inventory_no}。')
            return redirect('workflow_detail', request_id=quick_request.id)
        except (ValueError, WorkflowError) as exc:
            messages.error(request, str(exc))
    assets = Asset.objects.none()
    stock_items = StockItem.objects.none()
    units = []
    if query:
        assets = list(Asset.objects.select_related(
            'item_type', 'warehouse', 'location', 'current_holder'
        ).filter(asset_search_query(query)).order_by('asset_code')[:24])
        stock_items = list(StockItem.objects.select_related(
            'item_type', 'warehouse', 'location'
        ).filter(
            Q(code__icontains=query) | Q(name__icontains=query) | Q(item_type__name__icontains=query)
        ).order_by('name')[:24])
        units = _search_composite_units(query)
    else:
        assets, stock_items = [], []
    _attach_request_candidate_state(
        [*assets, *selected_assets],
        [*stock_items, *selected_stock_items],
        request.user,
        quick=True,
    )
    _attach_unit_candidate_state([*units, *selected_units], request.user, quick=True)
    return render(request, 'quick_inventory_process.html', {
        'query': query, 'assets': assets, 'stock_items': stock_items, 'units': units,
        'selected_assets': selected_assets, 'selected_stock_items': selected_stock_items,
        'selected_units': selected_units,
        'selected_request_type': request.POST.get('request_type') or request.GET.get('request_type') or WorkflowRequest.RequestType.BORROW,
        'default_transaction_date': request.POST.get('transaction_date') or timezone.localdate().isoformat(),
        'default_expected_return_date': request.POST.get('expected_return_date') or timezone.localdate().isoformat(),
        'request_types': [choice for choice in WorkflowRequest.RequestType.choices if choice[0] != WorkflowRequest.RequestType.PURCHASE_RECEIPT],
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'locations': Location.objects.select_related('warehouse').order_by('warehouse__name', 'code'),
        'customers': Customer.objects.filter(is_active=True).order_by('name'),
        'projects': Project.objects.filter(is_active=True).select_related('customer').order_by('name'),
        'work_orders': WorkOrder.objects.filter(is_active=True).select_related('project').order_by('name'),
        'user_can_use_admin': is_system_administrator(request.user), 'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_view_reports, message='当前账号没有报表查看权限。')
def report_dashboard(request):
    today = timezone.localdate()
    period = request.GET.get('period', 'month')
    start_text, end_text = request.GET.get('start_date', ''), request.GET.get('end_date', '')
    default_days = {'day': 0, 'week': 6, 'month': 29, 'year': 364}.get(period, 29)
    try:
        start_date = date.fromisoformat(start_text) if start_text else today - timedelta(days=default_days)
        end_date = date.fromisoformat(end_text) if end_text else today
    except ValueError:
        start_date, end_date = today - timedelta(days=29), today
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    granularity = request.GET.get('granularity', 'auto')
    range_days = (end_date - start_date).days
    if granularity == 'auto':
        granularity = 'day' if range_days <= 62 else 'week' if range_days <= 366 else 'month' if range_days <= 1095 else 'year'
    truncator = {'day': TruncDay, 'week': TruncWeek, 'month': TruncMonth, 'year': TruncYear}.get(granularity, TruncDay)
    transactions = InventoryTransaction.objects.filter(business_date__range=(start_date, end_date)).select_related(
        'asset', 'stock_item', 'source_warehouse', 'target_warehouse'
    ).order_by('created_at')
    action = request.GET.get('action', '')
    warehouse_id = request.GET.get('warehouse', '')
    project_code = request.GET.get('project_code', '').strip()
    work_order_no = request.GET.get('work_order_no', '').strip()
    if action:
        transactions = transactions.filter(action=action)
    if warehouse_id:
        transactions = transactions.filter(Q(source_warehouse_id=warehouse_id) | Q(target_warehouse_id=warehouse_id))
    if project_code:
        transactions = transactions.filter(project_code__icontains=project_code)
    if work_order_no:
        transactions = transactions.filter(work_order_no__icontains=work_order_no)
    chart_metric = request.GET.get('metric', 'count')
    chart_rows = transactions.annotate(bucket=truncator('business_date')).values('bucket').annotate(
        count=Count('id'), cost=Sum('cost_amount')
    ).order_by('bucket')
    action_counts = {}
    for transaction in transactions:
        action_counts[transaction.action] = action_counts.get(transaction.action, 0) + 1
    action_labels = dict(WorkflowRequest.RequestType.choices)
    dimension_definitions = [
        ('location_state', '实物位置', Asset.LocationState.choices),
        ('availability_state', '可用性', Asset.AvailabilityState.choices),
        ('quality_state', '质量状态', Asset.QualityState.choices),
        ('disposition_state', '处置状态', Asset.DispositionState.choices),
    ]
    dimension_rows = []
    for field_name, dimension_label, choices in dimension_definitions:
        counts = {
            row[field_name]: row['total']
            for row in Asset.objects.values(field_name).annotate(total=Count('id'))
        }
        dimension_rows.append({
            'label': dimension_label,
            'rows': [
                {'label': label, 'count': counts.get(value, 0)}
                for value, label in choices
            ],
        })
    low_stock_items = _stock_item_availability(
        StockItem.objects.filter(safety_stock__gt=0).select_related('item_type', 'warehouse')
    ).filter(available_count__lte=F('safety_stock')).order_by('available_count', 'name')[:10]
    low_serialized_types = _serialized_type_availability(
        ItemType.objects.filter(is_serialized=True, safety_stock__gt=0)
    ).filter(available_count__lt=F('safety_stock')).order_by('available_count', 'name')[:10]
    current_inventory_cost = sum(
        (asset.purchase_amount or Decimal('0'))
        for asset in Asset.objects.filter(in_warehouse_asset_filter()).only('purchase_amount')
    ) + sum(
        item.quantity * (item.unit_cost or Decimal('0'))
        for item in StockItem.objects.only('quantity', 'unit_cost')
    )
    return render(request, 'report_dashboard.html', {
        'days': range_days + 1,
        'start_date': start_date, 'end_date': end_date, 'period': period,
        'granularity': granularity, 'chart_metric': chart_metric,
        'selected_action': action, 'selected_warehouse': warehouse_id,
        'project_code': project_code, 'work_order_no': work_order_no,
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'action_choices': WorkflowRequest.RequestType.choices,
        'transaction_total': transactions.count(),
        'action_cost_total': transactions.aggregate(total=Sum('cost_amount'))['total'] or 0,
        'current_inventory_cost': current_inventory_cost,
        'action_rows': [
            {'label': action_labels.get(action, action), 'count': count}
            for action, count in sorted(action_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        'dimension_rows': dimension_rows,
        'asset_total': max(Asset.objects.count(), 1),
        'chart_data': [{'label': row['bucket'].strftime('%Y-%m-%d' if granularity == 'day' else '%Y-%m'), 'value': float(row['cost'] or 0) if chart_metric == 'cost' else row['count']} for row in chart_rows],
        'low_stock_items': low_stock_items,
        'low_serialized_types': low_serialized_types,
        'low_alert_count': low_stock_items.count() + low_serialized_types.count(),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


def _stale_inventory_rows(request):
    query = request.GET.get('q', '').strip()
    warehouse_text = request.GET.get('warehouse', '').strip()
    item_type_text = request.GET.get('item_type', '').strip()
    days_text = request.GET.get('days', '90').strip()
    try:
        days = max(1, min(int(days_text), 3650))
    except (TypeError, ValueError):
        days = 90
    cutoff = timezone.localdate() - timedelta(days=days)

    warehouse = _get_by_uuid(Warehouse, warehouse_text)
    item_type = _get_by_uuid(ItemType, item_type_text)
    assets = Asset.objects.filter(status=Asset.Status.IN_STOCK).select_related(
        'item_type', 'warehouse', 'location',
    ).annotate(last_movement=Max('transactions__business_date'))
    stock_items = StockItem.objects.filter(quantity__gt=0).select_related(
        'item_type', 'warehouse', 'location',
    ).annotate(last_movement=Max('transactions__business_date'))
    if warehouse_text and warehouse:
        assets = assets.filter(warehouse=warehouse)
        stock_items = stock_items.filter(warehouse=warehouse)
    elif warehouse_text:
        assets = assets.none()
        stock_items = stock_items.none()
    if item_type_text and item_type:
        assets = assets.filter(item_type=item_type)
        stock_items = stock_items.filter(item_type=item_type)
    elif item_type_text:
        assets = assets.none()
        stock_items = stock_items.none()
    if query:
        assets = assets.filter(
            Q(search_index__icontains=query)
            | Q(item_type__name__icontains=query)
            | Q(warehouse__name__icontains=query)
            | Q(location__name__icontains=query)
        )
        stock_items = stock_items.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(item_type__name__icontains=query)
            | Q(warehouse__name__icontains=query)
            | Q(location__name__icontains=query)
        )

    rows = []
    for asset in assets:
        last_activity = asset.last_movement or asset.received_date or timezone.localtime(asset.created_at).date()
        if last_activity > cutoff:
            continue
        rows.append({
            'kind': '资产',
            'name': asset.name,
            'code': asset.system_asset_no,
            'item_type': asset.item_type.name,
            'warehouse': asset.warehouse.name if asset.warehouse else '未分配',
            'location': asset.location.code if asset.location else '未分配',
            'quantity': 1,
            'unit': '件',
            'last_activity': last_activity,
            'idle_days': (timezone.localdate() - last_activity).days,
            'cost_value': asset.purchase_amount or Decimal('0'),
            'url': reverse('asset_lifecycle', kwargs={'asset_id': asset.id}),
        })
    for stock_item in stock_items:
        last_activity = stock_item.last_movement or timezone.localtime(stock_item.created_at).date()
        if last_activity > cutoff:
            continue
        rows.append({
            'kind': '耗材配件',
            'name': stock_item.name,
            'code': stock_item.code,
            'item_type': stock_item.item_type.name if stock_item.item_type else '未分类',
            'warehouse': stock_item.warehouse.name if stock_item.warehouse else '未分配',
            'location': stock_item.location.code if stock_item.location else '未分配',
            'quantity': stock_item.quantity,
            'unit': stock_item.unit,
            'last_activity': last_activity,
            'idle_days': (timezone.localdate() - last_activity).days,
            'cost_value': stock_item.quantity * (stock_item.unit_cost or Decimal('0')),
            'url': reverse('warehouse_stock_item_edit', kwargs={'stock_item_id': stock_item.id}),
        })
    rows.sort(key=lambda row: (-row['idle_days'], row['kind'], row['name'], row['code']))
    return rows, days, query, warehouse_text, item_type_text, cutoff


@role_required(can_view_reports, message='当前账号没有报表查看权限。')
def stale_inventory_report(request):
    if request.GET.get('download') == 'csv':
        return stale_inventory_export(request)
    rows, days, query, warehouse_text, item_type_text, cutoff = _stale_inventory_rows(request)
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    total_cost = sum((row['cost_value'] for row in rows), Decimal('0'))
    return render(request, 'stale_inventory_report.html', {
        'rows': page,
        'row_count': len(rows),
        'total_cost': total_cost,
        'days': days,
        'query': query,
        'selected_warehouse': warehouse_text,
        'selected_item_type': item_type_text,
        'cutoff': cutoff,
        'warehouses': Warehouse.objects.filter(is_active=True).order_by('name'),
        'item_types': ItemType.objects.select_related('category').order_by('name'),
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': True,
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(request.user, True, is_system_administrator(request.user)),
    })


@role_required(can_view_reports, message='当前账号没有报表导出权限。')
def stale_inventory_export(request):
    rows, days, query, warehouse_text, item_type_text, cutoff = _stale_inventory_rows(request)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stale-inventory.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['管理方式', '名称', '系统编号/编码', '物品类型', '仓库', '库位', '当前数量', '单位', '最后活动日期', '闲置天数', '库存成本价值（元）'])
    for row in rows:
        writer.writerow([
            row['kind'], row['name'], row['code'], row['item_type'], row['warehouse'], row['location'],
            row['quantity'], row['unit'], row['last_activity'].isoformat(), row['idle_days'], row['cost_value'],
        ])
    return response


@role_required(can_view_reports, message='当前账号没有报表导出权限。')
def transaction_export(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="inventory-transactions.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['出入库单号', '关联申请单', '申请日期', '出入库日期', '系统操作时间', '处理类型', '物品编码', '物品名称', '数量', '库存成本价值（元）', '项目编号', '工单编号', '资产状态变化', '库存数量变化', '来源仓库', '目标仓库', '接收人', '接收单位', '执行人', '处理说明'])
    transactions = InventoryTransaction.objects.select_related('request', 'asset', 'stock_item', 'source_warehouse', 'target_warehouse', 'actor').order_by('-created_at')
    query = request.GET.get('q', '').strip()
    if query:
        transactions = transactions.filter(
            Q(request__inventory_no__icontains=query)
            | Q(request__request_no__icontains=query)
            | Q(asset__search_index__icontains=query)
            | Q(stock_item__name__icontains=query)
            | Q(stock_item__code__icontains=query)
            | Q(holder_name__icontains=query)
            | Q(holder_company__icontains=query)
            | Q(project_code__icontains=query)
            | Q(work_order_no__icontains=query)
            | Q(comment__icontains=query)
        )
    start_text = request.GET.get('transaction_start') or request.GET.get('start_date', '')
    end_text = request.GET.get('transaction_end') or request.GET.get('end_date', '')
    try:
        if start_text: transactions = transactions.filter(business_date__gte=date.fromisoformat(start_text))
        if end_text: transactions = transactions.filter(business_date__lte=date.fromisoformat(end_text))
    except ValueError:
        pass
    if request.GET.get('action'): transactions = transactions.filter(action=request.GET['action'])
    if request.GET.get('warehouse'): transactions = transactions.filter(Q(source_warehouse_id=request.GET['warehouse']) | Q(target_warehouse_id=request.GET['warehouse']))
    if request.GET.get('project_code'): transactions = transactions.filter(project_code__icontains=request.GET['project_code'])
    if request.GET.get('work_order_no'): transactions = transactions.filter(work_order_no__icontains=request.GET['work_order_no'])
    action_labels = dict(WorkflowRequest.RequestType.choices)
    for item in transactions.iterator():
        writer.writerow([
            item.request.inventory_no or '', item.request.request_no, item.request.application_date.isoformat(), item.business_date.isoformat(),
            timezone.localtime(item.created_at).strftime('%Y-%m-%d %H:%M:%S'),
            action_labels.get(item.action, item.action), item.asset.asset_code if item.asset else item.stock_item.code,
            item.asset.name if item.asset else item.stock_item.name, item.quantity,
            item.cost_amount or '', item.project_code, item.work_order_no,
            f'{item.asset_status_before} -> {item.asset_status_after}' if item.asset else '',
            f'{item.stock_quantity_before} -> {item.stock_quantity_after}' if item.stock_item else '',
            item.source_warehouse.name if item.source_warehouse else '', item.target_warehouse.name if item.target_warehouse else '',
            item.holder_name, item.holder_company, item.actor.username if item.actor else '', item.comment,
        ])
    return response


@login_required
def request_create(request, request_id=None):
    allowed_types = {
        WorkflowRequest.RequestType.BORROW,
        WorkflowRequest.RequestType.RETURN,
        WorkflowRequest.RequestType.ISSUE,
    }
    draft = None
    if request_id:
        draft = WorkflowRequest.objects.filter(
            pk=request_id,
            applicant=request.user,
            status=WorkflowRequest.Status.DRAFT,
            is_quick_process=False,
        ).first()
        if not draft:
            raise Http404('未找到可编辑的申请草稿。')
    selected_template = None
    if request.GET.get('template'):
        selected_template = WorkflowRequestTemplate.objects.filter(
            pk=request.GET.get('template'),
            owner=request.user,
        ).first()
    query = request.GET.get('q', request.POST.get('item_query', '')).strip()
    availability = request.GET.get('availability', request.POST.get('availability', '')).strip()
    asset_ids, stock_item_ids, unit_ids, selected_assets, selected_stock_items, selected_units = _load_selected_items(request)
    if request.method == 'GET' and draft and not asset_ids and not stock_item_ids:
        selected_assets = list(Asset.objects.select_related(
            'item_type', 'warehouse', 'location', 'current_holder'
        ).filter(workflowrequestline__request=draft).order_by('asset_code'))
        selected_stock_items = list(StockItem.objects.select_related(
            'item_type', 'warehouse', 'location'
        ).filter(workflowrequestline__request=draft).order_by('name'))
        line_quantity_map = {
            str(line.stock_item_id): line.quantity
            for line in draft.lines.filter(stock_item__isnull=False)
        }
        for item in selected_stock_items:
            item.selected_quantity = line_quantity_map.get(str(item.id), 1)
        asset_ids = [str(item.id) for item in selected_assets]
        stock_item_ids = [str(item.id) for item in selected_stock_items]
    if request.method == 'POST':
        action = request.POST.get('action', 'submit')
        offline_sync = request.headers.get('X-Offline-Sync') == '1' or request.POST.get('offline_sync') == '1'
        request_type = request.POST.get('request_type', '')
        reason = request.POST.get('reason', '').strip()
        recipient_name = request.POST.get('recipient_name', '').strip()
        recipient_company = request.POST.get('recipient_company', '').strip()
        recipient_phone = request.POST.get('recipient_phone', '').strip()
        usage_location = request.POST.get('usage_location', '').strip()
        application_date_text = request.POST.get('application_date', '').strip()
        expected_return_text = request.POST.get('expected_return_date', '').strip()
        designated_approver = User.objects.filter(
            pk=request.POST.get('designated_approver') or None,
            is_active=True,
            groups__name__in=['warehouse_approval', 'warehouse_staff', 'warehouse_admin'],
        ).distinct().first()
        customer_ref = _get_by_uuid(Customer, request.POST.get('customer_ref'))
        project_ref = _get_by_uuid(Project, request.POST.get('project_ref'))
        work_order_ref = _get_by_uuid(WorkOrder, request.POST.get('work_order_ref'))
        try:
            if action not in {'submit', 'save_draft', 'save_template'}:
                raise WorkflowError('不支持的申请操作。')
            if request.POST.get('designated_approver') and not designated_approver:
                raise WorkflowError('指定审批人不存在或没有仓库审批权限，请重新选择。')
            if request_type not in allowed_types:
                raise WorkflowError('请选择有效的申请类型。')
            if application_date_text:
                application_date = _query_date(application_date_text)
                if not application_date:
                    raise WorkflowError('请填写正确的申请日期。')
            else:
                application_date = timezone.localdate()
            expected_return_date = None
            if expected_return_text:
                expected_return_date = date.fromisoformat(expected_return_text)
            if len(asset_ids) != len(selected_assets) or len(stock_item_ids) != len(selected_stock_items):
                raise WorkflowError('部分已选物品不存在，请移除后重新选择。')
            _attach_request_candidate_state(selected_assets, selected_stock_items, request.user)
            stock_quantities = {
                str(item.id): _stock_quantity_from_post(request, item.id)
                for item in selected_stock_items
            }

            if action == 'save_template':
                template_name = request.POST.get('template_name', '').strip()
                if not template_name:
                    raise WorkflowError('请填写模板名称。')
                borrow_days = 7
                if expected_return_date:
                    borrow_days = max((expected_return_date - application_date).days, 1)
                template, _ = WorkflowRequestTemplate.objects.update_or_create(
                    owner=request.user,
                    name=template_name,
                    defaults={
                        'request_type': request_type,
                        'recipient_name': recipient_name,
                        'recipient_company': recipient_company,
                        'recipient_phone': recipient_phone,
                        'usage_location': usage_location,
                        'customer_ref': customer_ref,
                        'project_ref': project_ref,
                        'work_order_ref': work_order_ref,
                        'project_code': request.POST.get('project_code', '').strip(),
                        'work_order_no': request.POST.get('work_order_no', '').strip(),
                        'reason': reason,
                        'default_borrow_days': borrow_days,
                        'designated_approver': designated_approver,
                    },
                )
                messages.success(request, f'常用模板“{template.name}”已保存，物品清单不会写入模板。')
                return redirect(f'{reverse("request_create")}?template={template.id}')

            # submit 与 save_draft 都会用到成品展开结果（草稿也要持久化选中的成品行），
            # 统一在分支外计算，避免仅 submit 时定义导致 save_draft 引用未绑定变量。
            unit_line_assets, primary_unit = _expand_unit_lines(request_type, selected_units)

            if action == 'submit':
                if not reason:
                    raise WorkflowError('请填写申请原因。')
                if request_type in {
                    WorkflowRequest.RequestType.BORROW,
                    WorkflowRequest.RequestType.ISSUE,
                }:
                    if not recipient_name:
                        raise WorkflowError('请填写实际使用人或接收人。')
                    if not recipient_phone:
                        raise WorkflowError('请填写联系电话。')
                    if not usage_location:
                        raise WorkflowError('请填写使用地点。')
                if request_type == WorkflowRequest.RequestType.BORROW and not expected_return_date:
                    raise WorkflowError('借用申请请填写预计归还日期。')
                if request_type == WorkflowRequest.RequestType.ISSUE and selected_units:
                    raise WorkflowError('领用不支持成品设备，请使用借用或售出。')
                if not selected_assets and not selected_stock_items and not unit_line_assets:
                    raise WorkflowError('请至少选择一项物品。')
                for item in [*selected_assets, *selected_stock_items]:
                    if request_type not in item.allowed_request_types.split(','):
                        raise WorkflowError(_candidate_incompatibility_message(item, request_type))
                for stock_item in selected_stock_items:
                    quantity = stock_quantities[str(stock_item.id)]
                    if request_type in {
                        WorkflowRequest.RequestType.BORROW,
                        WorkflowRequest.RequestType.ISSUE,
                    } and stock_item.available_quantity < quantity:
                        raise WorkflowError(f'{stock_item.name} 当前可申请数量不足。')
                    if request_type == WorkflowRequest.RequestType.RETURN and stock_item.held_quantity < quantity:
                        raise WorkflowError(f'{stock_item.name} 的本人可归还数量不足。')
            with transaction.atomic():
                workflow_request = draft or WorkflowRequest(applicant=request.user)
                workflow_request.request_type = request_type
                workflow_request.department = getattr(getattr(request.user, 'profile', None), 'department', None)
                workflow_request.reason = reason
                workflow_request.designated_approver = designated_approver
                workflow_request.recipient_name = recipient_name if request_type != WorkflowRequest.RequestType.RETURN else ''
                workflow_request.recipient_company = recipient_company if request_type != WorkflowRequest.RequestType.RETURN else ''
                workflow_request.recipient_phone = recipient_phone if request_type != WorkflowRequest.RequestType.RETURN else ''
                workflow_request.usage_location = usage_location if request_type != WorkflowRequest.RequestType.RETURN else ''
                workflow_request.customer_ref = customer_ref
                workflow_request.project_ref = project_ref
                workflow_request.work_order_ref = work_order_ref
                workflow_request.project_code = request.POST.get('project_code', '').strip()
                workflow_request.work_order_no = request.POST.get('work_order_no', '').strip()
                workflow_request.application_date = application_date
                workflow_request.expected_return_date = expected_return_date if request_type == WorkflowRequest.RequestType.BORROW else None
                workflow_request.status = WorkflowRequest.Status.DRAFT
                workflow_request.composite_unit = primary_unit if action == 'submit' else None
                workflow_request.save()
                workflow_request.lines.all().delete()
                WorkflowRequestLine.objects.bulk_create([
                    *[
                        WorkflowRequestLine(request=workflow_request, asset=item, quantity=1)
                        for item in selected_assets
                    ],
                    *[
                        WorkflowRequestLine(request=workflow_request, asset=item, quantity=1)
                        for item in unit_line_assets
                    ],
                    *[
                        WorkflowRequestLine(
                            request=workflow_request,
                            stock_item=item,
                            quantity=stock_quantities[str(item.id)],
                        )
                        for item in selected_stock_items
                    ],
                ])
                if action == 'submit':
                    submit_request(workflow_request, request.user)
            if action == 'save_draft':
                if offline_sync:
                    return JsonResponse({'ok': True, 'message': '申请草稿已保存。', 'request_id': str(workflow_request.id)})
                messages.success(request, '申请草稿已保存，可稍后继续编辑。')
                return redirect('request_edit', request_id=workflow_request.id)
            messages.success(request, '申请已提交，物品已预占并等待仓库审核。')
            return redirect('request_list')
        except (ValueError, WorkflowError) as exc:
            if offline_sync:
                return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
            messages.error(request, str(exc))

    assets, stock_items, units = [], [], []
    if query:
        assets = list(Asset.objects.select_related(
            'item_type', 'warehouse', 'location', 'current_holder'
        ).filter(asset_search_query(query)).order_by('asset_code')[:24])
        stock_items = list(StockItem.objects.select_related(
            'item_type', 'warehouse', 'location'
        ).filter(
            Q(code__iexact=query) | Q(code__icontains=query) | Q(name__icontains=query) | Q(item_type__name__icontains=query)
        ).order_by('name')[:24])
        units = _search_composite_units(query)
    _attach_request_candidate_state(
        [*assets, *selected_assets],
        [*stock_items, *selected_stock_items],
        request.user,
    )
    _attach_unit_candidate_state([*units, *selected_units], request.user)
    if availability == 'available':
        assets = [item for item in assets if item.allowed_request_types]
        stock_items = [item for item in stock_items if item.allowed_request_types]
        units = [item for item in units if item.allowed_request_types]
    initial_source = selected_template or draft
    initial_application_date = timezone.localdate()
    initial_expected_return_date = timezone.localdate()
    if selected_template:
        initial_expected_return_date = initial_application_date + timedelta(days=selected_template.default_borrow_days)
    elif draft:
        initial_application_date = draft.application_date
        initial_expected_return_date = draft.expected_return_date or timezone.localdate()
    form_values = {
        'recipient_name': getattr(initial_source, 'recipient_name', '') or request.user.get_username(),
        'recipient_company': getattr(initial_source, 'recipient_company', '') or getattr(
            getattr(getattr(request.user, 'profile', None), 'department', None), 'name', ''
        ),
        'recipient_phone': getattr(initial_source, 'recipient_phone', ''),
        'usage_location': getattr(initial_source, 'usage_location', ''),
        'project_code': getattr(initial_source, 'project_code', ''),
        'work_order_no': getattr(initial_source, 'work_order_no', ''),
        'customer_ref': str(getattr(initial_source, 'customer_ref_id', '') or ''),
        'project_ref': str(getattr(initial_source, 'project_ref_id', '') or ''),
        'work_order_ref': str(getattr(initial_source, 'work_order_ref_id', '') or ''),
        'reason': getattr(initial_source, 'reason', ''),
        'designated_approver': str(getattr(initial_source, 'designated_approver_id', '') or ''),
    }
    if request.method == 'POST':
        for field in form_values:
            form_values[field] = request.POST.get(field, '')
    selected_request_type = (
        request.POST.get('request_type')
        or request.GET.get('request_type')
        or getattr(initial_source, 'request_type', '')
        or WorkflowRequest.RequestType.BORROW
    )
    return render(request, 'request_create.html', {
        'assets': assets,
        'stock_items': stock_items,
        'units': units,
        'query': query,
        'availability': availability,
        'selected_assets': selected_assets,
        'selected_stock_items': selected_stock_items,
        'selected_units': selected_units,
        'selected_request_type': selected_request_type,
        'default_application_date': request.POST.get('application_date') or initial_application_date.isoformat(),
        'default_expected_return_date': request.POST.get('expected_return_date') or initial_expected_return_date.isoformat(),
        'request_types': [
            (WorkflowRequest.RequestType.BORROW, '借用'),
            (WorkflowRequest.RequestType.RETURN, '归还'),
            (WorkflowRequest.RequestType.ISSUE, '领用'),
        ],
        'approver_choices': User.objects.filter(
            is_active=True,
            groups__name__in=['warehouse_approval', 'warehouse_staff', 'warehouse_admin'],
        ).distinct().order_by('username'),
        'customers': Customer.objects.filter(is_active=True).order_by('name'),
        'projects': Project.objects.filter(is_active=True).select_related('customer').order_by('name'),
        'work_orders': WorkOrder.objects.filter(is_active=True).select_related('project').order_by('name'),
        'draft': draft,
        'form_values': form_values,
        'request_templates': WorkflowRequestTemplate.objects.filter(owner=request.user).order_by('name'),
        'selected_template': selected_template,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': is_warehouse_operator(request.user),
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(
            request.user,
            is_warehouse_operator(request.user),
            is_system_administrator(request.user),
        ),
    })


@login_required
def request_template_delete(request, template_id):
    if request.method != 'POST':
        raise Http404()
    template = WorkflowRequestTemplate.objects.filter(pk=template_id, owner=request.user).first()
    if not template:
        raise Http404('未找到申请模板。')
    name = template.name
    template.delete()
    messages.success(request, f'常用模板“{name}”已删除。')
    return redirect('request_create')


@login_required
def request_candidate_scan(request):
    value = request.GET.get('value', '').strip()
    request_type = request.GET.get('request_type', WorkflowRequest.RequestType.BORROW)
    quick = request.GET.get('quick') == '1' and can_execute_inventory(request.user)
    if not value:
        return JsonResponse({'ok': False, 'message': '请输入扫码内容。'}, status=400)
    assets = list(Asset.objects.select_related(
        'item_type', 'warehouse', 'location', 'current_holder'
    ).filter(
        Q(system_asset_no__iexact=value)
        | Q(asset_code__iexact=value)
        | Q(serial_number__iexact=value)
        | Q(manufacturer_barcode__iexact=value)
        | Q(qr_value__iexact=value)
    )[:2])
    stock_items = list(StockItem.objects.select_related(
        'item_type', 'warehouse', 'location'
    ).filter(code__iexact=value)[:2])
    if len(assets) + len(stock_items) == 0:
        return JsonResponse({'ok': False, 'message': f'未找到编码“{value}”对应的物品。'}, status=404)
    if len(assets) + len(stock_items) > 1:
        return JsonResponse({'ok': False, 'message': '该编码匹配多个物品，请使用搜索列表确认。'}, status=409)
    _attach_request_candidate_state(assets, stock_items, request.user, quick=quick)
    if assets:
        asset = assets[0]
        payload = {
            'kind': 'asset', 'id': str(asset.id), 'name': asset.name,
            'code': asset.system_asset_no, 'system_asset_no': asset.system_asset_no,
            'asset_code': asset.asset_code, 'unit': '件',
            'meta': f'{asset.item_type.name} · {asset.request_status_label}',
            'status': asset.status,
            'status_label': asset.request_status_label,
            'allowed_types': asset.allowed_request_types,
        }
    else:
        stock_item = stock_items[0]
        payload = {
            'kind': 'stock', 'id': str(stock_item.id), 'name': stock_item.name,
            'code': stock_item.code, 'unit': stock_item.unit,
            'meta': f'{stock_item.item_type.name if stock_item.item_type else "未分类"} · 可申请 {stock_item.available_quantity} {stock_item.unit}',
            'status': 'available' if stock_item.available_quantity else 'unavailable',
            'status_label': stock_item.request_status_label,
            'allowed_types': stock_item.allowed_request_types,
        }
    if request_type not in payload['allowed_types'].split(','):
        item = assets[0] if assets else stock_items[0]
        return JsonResponse({
            'ok': False,
            'eligible': False,
            'message': _candidate_incompatibility_message(item, request_type),
            'item': payload,
        }, status=409)
    return JsonResponse({'ok': True, 'eligible': True, 'item': payload})


@login_required
def my_loans(request):
    today = timezone.localdate()
    assets = list(Asset.objects.select_related('item_type', 'warehouse', 'location').filter(
        current_holder=request.user,
        status=Asset.Status.BORROWED,
    ).order_by('name', 'asset_code'))
    for asset in assets:
        line = WorkflowRequestLine.objects.select_related('request').filter(
            asset=asset,
            request__applicant=request.user,
            request__request_type=WorkflowRequest.RequestType.BORROW,
            request__status=WorkflowRequest.Status.DONE,
        ).order_by('-request__transaction_date', '-created_at').first()
        asset.loan_request = line.request if line else None
        asset.loan_due_date = line.request.expected_return_date if line else None
        asset.loan_is_overdue = bool(asset.loan_due_date and asset.loan_due_date < today)
    stock_loans = StockItemLoan.objects.select_related(
        'stock_item', 'source_line__request'
    ).filter(
        borrower=request.user,
        status=StockItemLoan.Status.ACTIVE,
        outstanding_quantity__gt=0,
    ).order_by('expected_return_date', 'stock_item__name')
    source_request_ids = {
        asset.loan_request.id for asset in assets if asset.loan_request
    } | {
        loan.source_line.request_id for loan in stock_loans
    }
    latest_extensions = {
        extension.source_request_id: extension
        for extension in LoanExtensionRequest.objects.filter(
            source_request_id__in=source_request_ids,
        ).select_related('reviewed_by').order_by('source_request_id', '-created_at')
    }
    for asset in assets:
        asset.loan_extension = latest_extensions.get(asset.loan_request.id) if asset.loan_request else None
    for loan in stock_loans:
        loan.loan_extension = latest_extensions.get(loan.source_line.request_id)
    return render(request, 'my_loans.html', {
        'asset_loans': assets,
        'stock_loans': stock_loans,
        'today': today,
        'user_can_use_admin': is_system_administrator(request.user),
        'user_is_operator': is_warehouse_operator(request.user),
        'user_is_system_admin': is_system_administrator(request.user),
        'user_role_label': _user_role_label(
            request.user,
            is_warehouse_operator(request.user),
            is_system_administrator(request.user),
        ),
    })
