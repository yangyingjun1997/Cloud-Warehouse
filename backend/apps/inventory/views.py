from io import BytesIO

import qrcode
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import models
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.audit import record_operation
from apps.common.permissions import IsInventoryEditor, IsSystemAdministrator, IsWarehouseOperator
from apps.common.upload_validation import sanitize_image_upload
from .check_services import (
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
from .assembly_services import (
    AssemblyError,
    add_components,
    create_unit_outbound_request,
    create_unit_return_request,
    disassemble_unit,
    remove_component,
    swap_component,
)
from .models import (
    Asset,
    AssetNetworkEndpoint,
    Category,
    ComponentLink,
    CompositeUnit,
    Customer,
    InventoryCheckAdjustment,
    InventoryCheckLine,
    InventoryCheckScan,
    InventoryCheckTask,
    ItemType,
    Location,
    Project,
    ServiceProvider,
    StockItem,
    Supplier,
    Warehouse,
    WorkOrder,
)
from .search import asset_search_query
from .serializers import (
    AssetLifecycleEventSerializer,
    AssetSerializer,
    AssetPublicSerializer,
    AssetNetworkEndpointSerializer,
    CategorySerializer,
    ComponentLinkSerializer,
    CompositeUnitHistorySerializer,
    CompositeUnitSerializer,
    CustomerSerializer,
    ItemTypeSerializer,
    LocationSerializer,
    ProjectSerializer,
    ServiceProviderSerializer,
    StockItemSerializer,
    StockItemPublicSerializer,
    SupplierSerializer,
    WarehouseSerializer,
    WorkOrderSerializer,
    InventoryCheckAdjustmentSerializer,
    InventoryCheckLineSerializer,
    InventoryCheckScanSerializer,
    InventoryCheckTaskSerializer,
)


class AuditedModelViewSet(viewsets.ModelViewSet):
    """Record direct master-data changes that are outside workflow transactions."""

    def _audit_data(self, data):
        return {field: value for field, value in data.items()}

    def perform_create(self, serializer):
        instance = serializer.save()
        record_operation(
            self.request.user,
            f'{instance._meta.model_name}_created',
            instance,
            summary=f'通过接口新建{instance._meta.verbose_name} {instance}',
            after=self._audit_data(serializer.validated_data),
        )

    def perform_update(self, serializer):
        before = {
            field: getattr(serializer.instance, field, None)
            for field in serializer.validated_data
        }
        instance = serializer.save()
        record_operation(
            self.request.user,
            f'{instance._meta.model_name}_updated',
            instance,
            summary=f'通过接口修改{instance._meta.verbose_name} {instance}',
            before=before,
            after=self._audit_data(serializer.validated_data),
        )

    def perform_destroy(self, instance):
        record_operation(
            self.request.user,
            f'{instance._meta.model_name}_deleted',
            instance,
            summary=f'通过接口删除{instance._meta.verbose_name} {instance}',
        )
        instance.delete()


class WarehouseViewSet(AuditedModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    def get_permissions(self):
        return [IsSystemAdministrator()]


class LocationViewSet(AuditedModelViewSet):
    queryset = Location.objects.select_related('warehouse').all()
    serializer_class = LocationSerializer
    def get_permissions(self):
        return [IsSystemAdministrator()]


class CategoryViewSet(AuditedModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    def get_permissions(self):
        return [IsSystemAdministrator()]


class ItemTypeViewSet(AuditedModelViewSet):
    queryset = ItemType.objects.select_related('category').all()
    serializer_class = ItemTypeSerializer
    def get_permissions(self):
        return [IsSystemAdministrator()]


class CustomerViewSet(AuditedModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsSystemAdministrator]


class SupplierViewSet(AuditedModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsSystemAdministrator]


class ServiceProviderViewSet(AuditedModelViewSet):
    queryset = ServiceProvider.objects.all()
    serializer_class = ServiceProviderSerializer
    permission_classes = [IsSystemAdministrator]


class ProjectViewSet(AuditedModelViewSet):
    queryset = Project.objects.select_related('customer').all()
    serializer_class = ProjectSerializer
    permission_classes = [IsSystemAdministrator]


class WorkOrderViewSet(AuditedModelViewSet):
    queryset = WorkOrder.objects.select_related('project', 'customer').all()
    serializer_class = WorkOrderSerializer
    permission_classes = [IsSystemAdministrator]


class AssetViewSet(AuditedModelViewSet):
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    queryset = Asset.objects.select_related('item_type', 'warehouse', 'location', 'current_holder', 'department').all()
    serializer_class = AssetSerializer

    def get_serializer_class(self):
        if not self.request.user.is_authenticated or not IsWarehouseOperator().has_permission(self.request, self):
            return AssetPublicSerializer
        return AssetSerializer

    def get_permissions(self):
        if self.action in {'list', 'retrieve', 'lookup', 'search', 'qr_code', 'lifecycle'}:
            return [IsAuthenticated()]
        return [IsInventoryEditor()]

    @action(detail=True, methods=['get'])
    def lifecycle(self, request, pk=None):
        """资产全生命周期时间线（小程序扫码后展示用）。

        合并两类来源：结构化履历事件（维修/交接/报废/备注）与出入库流水，
        按时间倒序返回，前端直接渲染时间线即可。
        """
        asset = self.get_object()
        events = []
        for event in asset.lifecycle_events.select_related('actor'):
            events.append({
                'kind': event.get_event_type_display(),
                'title': event.title,
                'description': event.description,
                'at': event.event_at,
                'actor': event.actor.get_username() if event.actor else '',
            })
        for item in asset.transactions.select_related('actor', 'request'):
            events.append({
                'kind': '出入库',
                'title': f'{item.get_action_display()} · {item.request.request_no if item.request else "直接登记"}',
                'description': item.comment or '',
                'at': item.created_at,
                'actor': item.actor.get_username() if item.actor else '',
            })
        events.sort(key=lambda row: row['at'], reverse=True)
        return Response({
            'system_asset_no': asset.system_asset_no,
            'asset_code': asset.asset_code,
            'asset_name': asset.name,
            'events': events,
        })

    @action(detail=False, methods=['get'])
    def lookup(self, request):
        value = request.query_params.get('code', '').strip()
        if not value:
            return Response({'detail': '请提供 code 查询参数。'}, status=status.HTTP_400_BAD_REQUEST)
        asset = self.get_queryset().filter(
            models.Q(system_asset_no=value)
            | models.Q(asset_code=value)
            | models.Q(qr_value=value)
            | models.Q(serial_number=value)
            | models.Q(manufacturer_barcode=value)
        ).first()
        if not asset:
            return Response({'detail': '未找到对应资产。'}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(asset).data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        value = request.query_params.get('q', '').strip()
        if not value:
            return Response({'detail': '请提供 q 查询参数。'}, status=status.HTTP_400_BAD_REQUEST)
        assets = self.get_queryset().filter(asset_search_query(value)).distinct()[:50]
        return Response({
            'query': value,
            'count': len(assets),
            'results': self.get_serializer(assets, many=True).data,
        })

    @action(detail=True, methods=['get'], url_path='qr-code')
    def qr_code(self, request, pk=None):
        asset = self.get_object()
        if not asset.system_asset_no:
            return Response({'detail': '该资产尚未生成仓库系统资产编号，暂无二维码。'}, status=status.HTTP_400_BAD_REQUEST)
        image = qrcode.make(asset.qr_value or asset.system_asset_no)
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        response = HttpResponse(buffer.getvalue(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{asset.system_asset_no}.png"'
        return response

    @action(
        detail=True,
        methods=['post'],
        url_path='upload-photo',
        parser_classes=[MultiPartParser, FormParser],
        permission_classes=[IsWarehouseOperator],
    )
    def upload_photo(self, request, pk=None):
        asset = self.get_object()
        photo = request.FILES.get('photo')
        if not photo:
            return Response({'detail': '请上传 photo 文件。'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            sanitized_photo = sanitize_image_upload(photo)
        except ValidationError as exc:
            return Response({'detail': ' '.join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        path = default_storage.save(f'assets/{asset.system_asset_no}/{sanitized_photo.name}', sanitized_photo)
        asset.photo_url = default_storage.url(path)
        asset.save(update_fields=['photo_url', 'updated_at'])
        record_operation(
            request.user,
            'asset_photo_uploaded',
            asset,
            summary=f'上传资产照片 {asset.system_asset_no}',
            after={'photo_url': asset.photo_url},
        )
        return Response(self.get_serializer(asset).data)


class CompositeUnitViewSet(AuditedModelViewSet):
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    queryset = CompositeUnit.objects.select_related('customer').prefetch_related('component_links__asset').all()
    serializer_class = CompositeUnitSerializer

    def get_permissions(self):
        if self.action in {'list', 'retrieve', 'history'}:
            return [IsAuthenticated()]
        return [IsInventoryEditor()]

    def get_serializer_class(self):
        if self.action == 'history':
            return CompositeUnitHistorySerializer
        return CompositeUnitSerializer

    @action(detail=True, methods=['post'], url_path='add-components')
    def add_components(self, request, pk=None):
        """向成品装入组件（免审批，留审计）。"""
        try:
            links = add_components(
                unit=self.get_object(),
                asset_ids=request.data.get('asset_ids') or [],
                actor=request.user,
                note=request.data.get('note', ''),
            )
        except AssemblyError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'已装入 {len(links)} 件组件。'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='remove-component')
    def remove_component(self, request, pk=None):
        """从成品拆出单个组件（仅限未出库成品）。"""
        try:
            asset = remove_component(
                unit=self.get_object(),
                asset_id=request.data.get('asset_id'),
                actor=request.user,
                note=request.data.get('note', ''),
            )
        except AssemblyError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'组件 {asset.asset_code or asset.name} 已拆出并回到在库。'})

    @action(detail=True, methods=['post'], url_path='swap-component')
    def swap_component(self, request, pk=None):
        """换装：原子替换一个组件（仅限未出库成品）。"""
        try:
            old_asset, _ = swap_component(
                unit=self.get_object(),
                old_asset_id=request.data.get('old_asset_id'),
                new_asset_id=request.data.get('new_asset_id'),
                actor=request.user,
                reason=request.data.get('reason', ''),
            )
        except AssemblyError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'换装完成，{old_asset.asset_code or old_asset.name} 已回到在库。'})

    @action(detail=True, methods=['post'], url_path='disassemble')
    def disassemble(self, request, pk=None):
        """拆散成品（免审批，留审计）：组件回在库，编码作废。"""
        try:
            unit = disassemble_unit(
                unit=self.get_object(),
                actor=request.user,
                reason=request.data.get('reason', ''),
            )
        except AssemblyError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'成品设备 {unit} 已拆散，编码作废。'})

    @action(detail=True, methods=['post'], url_path='outbound')
    def outbound(self, request, pk=None):
        """成品出库审批（借用/售出），组件随单一并出库。"""
        from apps.workflow.models import WorkflowRequest
        request_type = request.data.get('request_type', '')
        try:
            request_type = {
                '借用': WorkflowRequest.RequestType.BORROW, 'borrow': WorkflowRequest.RequestType.BORROW,
                '售出': WorkflowRequest.RequestType.SALE, 'sale': WorkflowRequest.RequestType.SALE,
            }[request_type]
        except KeyError:
            return Response({'detail': 'request_type 仅支持 借用/borrow 或 售出/sale。'}, status=status.HTTP_400_BAD_REQUEST)
        customer_id = request.data.get('customer') or None
        customer = Customer.objects.filter(pk=customer_id).first() if customer_id else None
        if customer_id and customer is None:
            return Response({'detail': '所选客户不存在。'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            request_obj = create_unit_outbound_request(
                unit=self.get_object(),
                request_type=request_type,
                actor=request.user,
                usage_location=request.data.get('usage_location', ''),
                reason=request.data.get('reason', ''),
                recipient_name=request.data.get('recipient_name', ''),
                recipient_company=request.data.get('recipient_company', ''),
                recipient_phone=request.data.get('recipient_phone', ''),
                expected_return_date=request.data.get('expected_return_date') or None,
                customer=customer,
            )
        except (AssemblyError, Exception) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'出库申请 {request_obj.request_no} 已提交，待审批。', 'request_no': request_obj.request_no}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='return')
    def return_unit(self, request, pk=None):
        """成品归还审批，组件随单一并回库。"""
        try:
            request_obj = create_unit_return_request(
                unit=self.get_object(),
                actor=request.user,
                reason=request.data.get('reason', ''),
            )
        except (AssemblyError, Exception) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'归还申请 {request_obj.request_no} 已提交，待审批。', 'request_no': request_obj.request_no}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def lookup(self, request):
        """按成品编码/二维码/名称扫码定位成品。"""
        value = request.query_params.get('code', '').strip()
        if not value:
            return Response({'detail': '请提供 code 查询参数。'}, status=status.HTTP_400_BAD_REQUEST)
        unit = self.get_queryset().filter(
            models.Q(unit_code=value) | models.Q(qr_value=value) | models.Q(name__icontains=value)
        ).first()
        if not unit:
            return Response({'detail': '未找到对应成品设备。'}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(unit).data)

    @action(detail=True, methods=['get'])
    def history(self, request, pk=None):
        """完整组装履历（含已拆散记录）。"""
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['get'], url_path='qr-code')
    def qr_code(self, request, pk=None):
        unit = self.get_object()
        if not unit.qr_value:
            return Response({'detail': '该成品设备尚未赋编码，暂无二维码。'}, status=status.HTTP_400_BAD_REQUEST)
        image = qrcode.make(unit.qr_value)
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        response = HttpResponse(buffer.getvalue(), content_type='image/png')
        response['Content-Disposition'] = f'inline; filename="{unit.unit_code}.png"'
        return response


class AssetNetworkEndpointViewSet(AuditedModelViewSet):
    queryset = AssetNetworkEndpoint.objects.select_related('asset').all()
    serializer_class = AssetNetworkEndpointSerializer

    def get_permissions(self):
        return [IsInventoryEditor()]


class StockItemViewSet(AuditedModelViewSet):
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']
    queryset = StockItem.objects.select_related('warehouse', 'location').all()
    serializer_class = StockItemSerializer

    def get_serializer_class(self):
        if not self.request.user.is_authenticated or not IsWarehouseOperator().has_permission(self.request, self):
            return StockItemPublicSerializer
        return StockItemSerializer
    def get_permissions(self):
        return [IsAuthenticated()] if self.action in {'list', 'retrieve'} else [IsInventoryEditor()]


class InventoryCheckTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryCheckTask.objects.select_related(
        'warehouse', 'location', 'created_by', 'started_by', 'reviewed_by',
    ).prefetch_related(
        'lines__item_type', 'lines__asset', 'lines__stock_item',
        'lines__expected_warehouse', 'lines__expected_location',
        'lines__observed_warehouse', 'lines__observed_location',
    ).all()
    serializer_class = InventoryCheckTaskSerializer

    def get_permissions(self):
        if self.action in {'create', 'review', 'reopen', 'cancel'}:
            return [IsSystemAdministrator()]
        if self.action in {'start', 'scan', 'submit'}:
            return [IsInventoryEditor()]
        return [IsWarehouseOperator()]

    def create(self, request, *args, **kwargs):
        try:
            task = create_check_task(
                actor=request.user,
                name=request.data.get('name', ''),
                warehouse_id=request.data.get('warehouse'),
                location_id=request.data.get('location') or None,
                note=request.data.get('note', ''),
            )
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(task).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        try:
            task = start_check_task(self.get_object(), request.user)
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=['post'])
    def scan(self, request, pk=None):
        try:
            quantity = int(request.data.get('quantity', 1))
            scan = scan_check_value(
                self.get_object(), request.user, str(request.data.get('value', '')),
                quantity, str(request.data.get('note', '')),
            )
        except (ValueError, InventoryCheckError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InventoryCheckScanSerializer(scan, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        try:
            task = submit_check_task(self.get_object(), request.user)
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=['post'])
    def correct(self, request, pk=None):
        try:
            quantity = int(request.data.get('quantity', ''))
            line = correct_check_line(
                self.get_object(), request.user, request.data.get('line'), quantity,
                str(request.data.get('note', '')),
            )
        except (ValueError, InventoryCheckError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(InventoryCheckLineSerializer(line, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        try:
            task, adjustments = review_check_task(
                self.get_object(), request.user, str(request.data.get('review_note', '')).strip()
            )
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'task': self.get_serializer(task).data,
            'adjustments': InventoryCheckAdjustmentSerializer(adjustments, many=True, context={'request': request}).data,
        })

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        try:
            task = reopen_check_task(self.get_object(), request.user, str(request.data.get('note', '')))
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        try:
            task = cancel_check_task(self.get_object(), request.user)
        except InventoryCheckError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(task).data)


class InventoryCheckLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryCheckLine.objects.select_related(
        'task', 'item_type', 'asset', 'stock_item', 'expected_warehouse', 'expected_location',
        'observed_warehouse', 'observed_location', 'counted_by',
    ).all()
    serializer_class = InventoryCheckLineSerializer
    permission_classes = [IsWarehouseOperator]

    def get_queryset(self):
        return self.queryset.filter(task__warehouse__is_active=True).order_by('-created_at')


class InventoryCheckScanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryCheckScan.objects.select_related(
        'task', 'line', 'asset', 'stock_item', 'operator', 'observed_warehouse', 'observed_location',
    ).all()
    serializer_class = InventoryCheckScanSerializer
    permission_classes = [IsWarehouseOperator]

    def get_queryset(self):
        return self.queryset.filter(task__warehouse__is_active=True).order_by('-created_at')


class InventoryCheckAdjustmentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryCheckAdjustment.objects.select_related(
        'task', 'line', 'asset', 'stock_item', 'reviewed_by', 'before_warehouse', 'after_warehouse',
        'before_location', 'after_location',
    ).all()
    serializer_class = InventoryCheckAdjustmentSerializer
    permission_classes = [IsWarehouseOperator]

    def get_queryset(self):
        return self.queryset.filter(task__warehouse__is_active=True).order_by('-created_at')
