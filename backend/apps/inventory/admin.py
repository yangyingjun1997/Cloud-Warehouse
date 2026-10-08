import base64
from io import BytesIO

import qrcode
from django.contrib import admin, messages
from django import forms
from django.core.files.storage import default_storage
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from apps.common.admin import ChineseModelAdmin
from apps.common.audit import record_operation
from apps.common.models import UserListPreference
from apps.common.upload_validation import sanitize_image_upload, validate_image_upload
from .models import (
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
    Supplier,
    Warehouse,
    WorkOrder,
)


class AssetNetworkEndpointInline(admin.TabularInline):
    model = AssetNetworkEndpoint
    form = forms.modelform_factory(
        AssetNetworkEndpoint,
        fields='__all__',
        labels={
            'interface_name': '接口名称', 'ip_address': 'IP 地址',
            'subnet_mask': '子网掩码', 'gateway': '默认网关',
            'mac_address': 'MAC 地址', 'hostname': '主机名',
            'vlan_id': 'VLAN 编号', 'is_primary': '是否主接口', 'notes': '备注',
        },
    )
    extra = 0
    fields = ('interface_name', 'ip_address', 'subnet_mask', 'gateway', 'mac_address', 'hostname', 'vlan_id', 'is_primary', 'notes')


class AssetAdminForm(forms.ModelForm):
    photo_file = forms.ImageField(label='上传资产照片', required=False)

    class Meta:
        model = Asset
        fields = '__all__'

    def clean_photo_file(self):
        photo = self.cleaned_data.get('photo_file')
        if photo:
            validate_image_upload(photo)
        return photo


class BusinessReferenceAdmin(ChineseModelAdmin):
    list_display = ('name', 'code', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'contact_name', 'phone', 'notes')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_operation(
            request.user,
            f'{obj._meta.model_name}_{"updated" if change else "created"}',
            obj,
            summary=f'{"修改" if change else "新建"}{obj._meta.verbose_name} {obj}',
            after={'changed_fields': list(form.changed_data)},
        )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(BusinessReferenceAdmin):
    pass


@admin.register(Supplier)
class SupplierAdmin(BusinessReferenceAdmin):
    pass


@admin.register(ServiceProvider)
class ServiceProviderAdmin(BusinessReferenceAdmin):
    pass


@admin.register(Project)
class ProjectAdmin(BusinessReferenceAdmin):
    list_display = ('name', 'code', 'customer', 'is_active', 'updated_at')
    list_filter = ('is_active', 'customer')
    search_fields = ('name', 'code', 'customer__name', 'notes')


@admin.register(WorkOrder)
class WorkOrderAdmin(BusinessReferenceAdmin):
    list_display = ('name', 'code', 'project', 'customer', 'is_active', 'updated_at')
    list_filter = ('is_active', 'project', 'customer')
    search_fields = ('name', 'code', 'project__name', 'customer__name', 'notes')


@admin.register(Warehouse)
class WarehouseAdmin(ChineseModelAdmin):
    field_labels = {'name': '仓库名称', 'code': '仓库编码', 'location_desc': '位置说明', 'is_active': '是否启用'}
    list_display = ('name', 'code', 'is_active', 'created_at')
    search_fields = ('name', 'code')


@admin.register(Location)
class LocationAdmin(ChineseModelAdmin):
    field_labels = {'warehouse': '所属仓库', 'name': '库位名称', 'code': '库位编码', 'description': '库位说明'}
    list_display = ('warehouse', 'name', 'code', 'created_at')
    search_fields = ('name', 'code', 'warehouse__name')


@admin.register(Category)
class CategoryAdmin(ChineseModelAdmin):
    field_labels = {'name': '分类名称', 'code': '分类编码'}
    list_display = ('name', 'code', 'created_at')
    search_fields = ('name', 'code')


@admin.register(ItemType)
class ItemTypeAdmin(ChineseModelAdmin):
    field_labels = {'name': '物品类型名称', 'code': '类型编码', 'category': '所属分类', 'is_serialized': '是否单件管理', 'unit': '计量单位', 'safety_stock': '安全库存'}
    list_display = ('name', 'code', 'category', 'is_serialized', 'unit', 'safety_stock')
    search_fields = ('name', 'code')
    list_filter = ('category', 'is_serialized')
    change_list_template = 'admin/inventory/itemtype/change_list.html'

    def get_urls(self):
        return [
            path('bulk-category/', self.admin_site.admin_view(self.bulk_category), name='inventory_itemtype_bulk_category'),
        ] + super().get_urls()

    def bulk_category(self, request):
        categories = Category.objects.order_by('name')
        item_types = ItemType.objects.select_related('category').order_by('name')
        if request.method == 'POST':
            category_ids = set(str(value) for value in categories.values_list('id', flat=True))
            changed = 0
            for item_type in item_types:
                category_id = request.POST.get(f'category_{item_type.id}', '')
                if category_id and category_id in category_ids and str(item_type.category_id) != category_id:
                    item_type.category_id = category_id
                    item_type.save(update_fields=['category', 'updated_at'])
                    changed += 1
            self.message_user(request, f'已更新 {changed} 个物品类型的分类。', messages.SUCCESS)
            return redirect('admin:inventory_itemtype_changelist')
        return render(request, 'admin/inventory/itemtype/bulk_category.html', {
            **self.admin_site.each_context(request),
            'title': '批量调整物品分类',
            'categories': categories,
            'item_types': item_types,
        })


@admin.register(StockItemHolding)
class StockItemHoldingAdmin(ChineseModelAdmin):
    field_labels = {
        'stock_item': '耗材配件',
        'holder': '借用人',
        'quantity': '尚未归还数量',
    }
    list_display = ('holder', 'stock_item', 'quantity', 'updated_at')
    search_fields = ('holder__username', 'stock_item__name', 'stock_item__code')
    list_filter = ('stock_item__warehouse',)
    readonly_fields = ('stock_item', 'holder', 'quantity', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Asset)
class AssetAdmin(ChineseModelAdmin):
    actions = ('print_qr_labels',)
    form = AssetAdminForm
    field_labels = {'system_asset_no': '仓库系统资产编号', 'asset_code': '公司 ERP 资产编码', 'name': '资产名称', 'item_type': '物品类型', 'manufacturer': '生产厂家', 'model': '型号', 'serial_number': '生产厂家 SN / 序列号', 'manufacturer_barcode': '生产厂家条码 / 二维码内容', 'qr_value': '本系统二维码内容', 'search_aliases': '别名 / 搜索标签', 'received_date': '到货日期', 'purchase_order_no': '采购订单号', 'supplier': '供应商', 'purchase_amount': '采购金额（元）', 'warranty_until': '质保到期日', 'source_import_batch': '导入批次', 'warehouse': '所属仓库', 'location': '所在库位', 'current_holder': '当前持有人', 'external_holder_name': '外部持有人姓名', 'external_holder_company': '外部持有人单位', 'external_holder_phone': '外部持有人联系电话', 'department': '所属部门', 'status': '旧版综合状态（兼容）', 'location_state': '实物位置状态', 'availability_state': '可用性状态', 'quality_state': '质量状态', 'disposition_state': '处置状态', 'photo_url': '资产照片访问地址（上传后自动生成）', 'remarks': '备注'}
    column_options = (
        ('system_asset_no', '仓库系统资产编号'),
        ('asset_code', '公司 ERP 资产编码'),
        ('name', '资产名称'),
        ('item_type', '物品类型'),
        ('manufacturer', '生产厂家'),
        ('model', '型号'),
        ('serial_number', '生产厂家 SN'),
        ('manufacturer_barcode', '生产厂家条码 / 二维码'),
        ('qr_value', '系统二维码内容'),
        ('status', '状态'),
        ('location_state', '实物位置'),
        ('availability_state', '可用性'),
        ('quality_state', '质量状态'),
        ('disposition_state', '处置状态'),
        ('warehouse', '仓库'),
        ('location', '库位'),
        ('current_holder', '当前持有人'),
        ('department', '部门'),
        ('received_date', '到货日期'),
        ('purchase_order_no', '采购订单号'),
        ('network_endpoint_summary', '网络 IP'),
        ('remarks', '备注'),
    )
    default_columns = ('system_asset_no', 'name', 'asset_code', 'manufacturer', 'model', 'serial_number', 'status', 'warehouse', 'location', 'network_endpoint_summary')
    search_fields = ('system_asset_no', 'asset_code', 'name', 'manufacturer', 'model', 'serial_number', 'manufacturer_barcode', 'qr_value', 'search_aliases', 'remarks', 'search_index')
    list_filter = ('status', 'warehouse', 'item_type')
    inlines = [AssetNetworkEndpointInline]
    change_list_template = 'admin/inventory/asset/change_list.html'
    readonly_fields = ('photo_preview', 'system_asset_no', 'qr_value', 'status', 'location_state', 'availability_state', 'quality_state', 'disposition_state', 'current_holder', 'external_holder_name', 'external_holder_company', 'external_holder_phone', 'department')

    @admin.display(description='资产照片预览')
    def photo_preview(self, obj):
        if not obj or not obj.photo_url:
            return '暂未上传照片'
        return format_html('<img src="{}" alt="资产照片" style="max-height: 180px; max-width: 280px;">', obj.photo_url)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('item_type', 'warehouse', 'location', 'current_holder', 'department').prefetch_related('network_endpoints')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        photo = form.cleaned_data.get('photo_file')
        if photo:
            sanitized_photo = sanitize_image_upload(photo)
            path = default_storage.save(f'assets/{obj.system_asset_no}/{sanitized_photo.name}', sanitized_photo)
            obj.photo_url = default_storage.url(path)
            obj.save(update_fields=['photo_url', 'updated_at'])

    def get_list_display(self, request):
        preference = UserListPreference.objects.filter(user=request.user, list_key='inventory.asset.columns').first()
        selected = preference.columns if preference and preference.columns else self.default_columns
        allowed = {key for key, _ in self.column_options}
        selected = [key for key in selected if key in allowed]
        if 'system_asset_no' not in selected:
            selected.insert(0, 'system_asset_no')
        return tuple(selected or self.default_columns)

    def get_urls(self):
        return [
            path('columns/', self.admin_site.admin_view(self.column_preferences), name='inventory_asset_column_preferences'),
            path('print-labels/', self.admin_site.admin_view(self.print_qr_labels_view), name='inventory_asset_print_qr_labels'),
        ] + super().get_urls()

    @admin.action(description='打印选中资产的专属二维码标签')
    def print_qr_labels(self, request, queryset):
        request.session['inventory_asset_label_ids'] = [str(asset_id) for asset_id in queryset.values_list('id', flat=True)]
        return redirect('admin:inventory_asset_print_qr_labels')

    def print_qr_labels_view(self, request):
        asset_ids = request.session.get('inventory_asset_label_ids', [])
        if not asset_ids:
            self.message_user(request, '请先在资产列表中勾选需要打印标签的资产。', messages.WARNING)
            return redirect('admin:inventory_asset_changelist')
        assets_by_id = {
            str(asset.id): asset
            for asset in Asset.objects.select_related('item_type').filter(id__in=asset_ids)
        }
        labels = []
        skipped = []
        for asset_id in asset_ids:
            asset = assets_by_id.get(asset_id)
            if not asset:
                continue
            if not asset.system_asset_no:
                skipped.append(asset)
                continue
            image = qrcode.make(asset.qr_value or asset.system_asset_no)
            buffer = BytesIO()
            image.save(buffer, format='PNG')
            labels.append({
                'asset': asset,
                'qr_image': base64.b64encode(buffer.getvalue()).decode('ascii'),
            })
        if skipped:
            self.message_user(
                request,
                f'{len(skipped)} 件资产尚未生成仓库系统编号（{ ", ".join(a.name for a in skipped[:5]) }等），未生成标签。',
                messages.WARNING,
            )
        return render(request, 'admin/inventory/asset/print_qr_labels.html', {
            **self.admin_site.each_context(request),
            'title': '打印资产二维码标签',
            'labels': labels,
        })

    def column_preferences(self, request):
        preference, _ = UserListPreference.objects.get_or_create(user=request.user, list_key='inventory.asset.columns')
        if request.method == 'POST':
            selected = request.POST.getlist('columns')
            order = {key: request.POST.get(f'order_{key}', '999') for key in selected}
            valid = {key for key, _ in self.column_options}
            selected = [key for key in selected if key in valid]
            selected.sort(key=lambda key: (int(order[key]) if order[key].isdigit() else 999, key))
            if 'system_asset_no' not in selected:
                selected.insert(0, 'system_asset_no')
            preference.columns = selected
            preference.save(update_fields=['columns', 'updated_at'])
            self.message_user(request, '资产列配置已保存。', messages.SUCCESS)
            return redirect('admin:inventory_asset_changelist')
        selected = preference.columns or list(self.default_columns)
        return render(request, 'admin/inventory/asset/column_preferences.html', {
            **self.admin_site.each_context(request),
            'title': '配置资产列',
            'column_options': self.column_options,
            'selected_columns': selected,
        })

    @admin.display(description='网络 IP')
    def network_endpoint_summary(self, obj):
        values = [endpoint.ip_address for endpoint in obj.network_endpoints.all()]
        return ', '.join(values) if values else '-'


class ComponentLinkInline(admin.TabularInline):
    model = ComponentLink
    extra = 0
    can_delete = False
    fields = ('asset', 'assembled_at', 'disassembled_at', 'assemble_request_no', 'disassemble_request_no', 'note')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CompositeUnit)
class CompositeUnitAdmin(ChineseModelAdmin):
    field_labels = {
        'unit_code': '成品编码（行政赋予）', 'name': '成品名称', 'model': '型号',
        'qr_value': '系统二维码内容', 'status': '状态', 'customer': '客户', 'remarks': '备注',
    }
    list_display = ('unit_code', 'name', 'model', 'status', 'customer', 'active_component_count', 'created_at')
    list_filter = ('status',)
    search_fields = ('unit_code', 'name', 'model', 'qr_value', 'component_links__asset__asset_code', 'component_links__asset__serial_number')
    readonly_fields = ('qr_value', 'status')
    inlines = [ComponentLinkInline]

    @admin.display(description='当前组件数')
    def active_component_count(self, obj):
        return obj.component_links.filter(disassembled_at__isnull=True).count()

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('customer').prefetch_related('component_links')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_operation(
            request.user,
            'composite_unit_updated' if change else 'composite_unit_created',
            obj,
            summary=f'通过后台{"修改" if change else "新建"}成品设备 {obj}',
        )


@admin.register(AssetNetworkEndpoint)
class AssetNetworkEndpointAdmin(ChineseModelAdmin):
    field_labels = {'asset': '资产', 'interface_name': '接口名称', 'ip_address': 'IP 地址', 'subnet_mask': '子网掩码', 'gateway': '默认网关', 'mac_address': 'MAC 地址', 'hostname': '主机名', 'vlan_id': 'VLAN ID', 'is_primary': '是否主接口', 'notes': '备注'}
    list_display = ('asset_link', 'interface_name', 'ip_address', 'subnet_mask', 'gateway', 'mac_address', 'hostname', 'vlan_id', 'is_primary')
    search_fields = ('asset__asset_code', 'asset__name', 'ip_address', 'mac_address', 'hostname')
    list_filter = ('is_primary',)

    @admin.display(description='资产')
    def asset_link(self, obj):
        url = reverse('admin:inventory_asset_change', args=[obj.asset_id])
        return format_html('<a href="{}">{} - {}</a>', url, obj.asset.asset_code, obj.asset.name)


class LifecycleReadOnlyAdmin(ChineseModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(LifecycleReadOnlyAdmin):
    list_display = ('maintenance_no', 'asset', 'repair_type', 'status', 'service_provider', 'responsible_user', 'created_at', 'completed_at')
    list_filter = ('repair_type', 'status', 'is_under_warranty')
    search_fields = ('maintenance_no', 'asset__asset_code', 'asset__name', 'service_provider', 'rma_no', 'outbound_tracking_no', 'return_tracking_no')
    readonly_fields = [field.name for field in MaintenanceRecord._meta.fields]


@admin.register(AssetHandover)
class AssetHandoverAdmin(LifecycleReadOnlyAdmin):
    list_display = ('handover_no', 'asset', 'from_holder_name', 'to_holder_name', 'handover_date', 'status', 'confirmed_by')
    list_filter = ('status', 'handover_date')
    search_fields = ('handover_no', 'asset__asset_code', 'asset__name', 'from_holder_name', 'to_holder_name')
    readonly_fields = [field.name for field in AssetHandover._meta.fields]


@admin.register(AssetAttachment)
class AssetAttachmentAdmin(LifecycleReadOnlyAdmin):
    list_display = ('display_name', 'asset', 'category', 'maintenance', 'handover', 'uploaded_by', 'created_at')
    list_filter = ('category',)
    search_fields = ('display_name', 'asset__asset_code', 'asset__name', 'note')
    readonly_fields = [field.name for field in AssetAttachment._meta.fields]


@admin.register(AssetLifecycleEvent)
class AssetLifecycleEventAdmin(LifecycleReadOnlyAdmin):
    list_display = ('event_at', 'asset', 'event_type', 'title', 'actor')
    list_filter = ('event_type',)
    search_fields = ('asset__asset_code', 'asset__name', 'title', 'description')
    readonly_fields = [field.name for field in AssetLifecycleEvent._meta.fields]


class ImportIssueInline(admin.TabularInline):
    model = ImportIssue
    extra = 0
    fields = ('sheet_name', 'row_number', 'severity', 'code', 'message', 'resolved', 'resolution_note')
    readonly_fields = ('sheet_name', 'row_number', 'severity', 'code', 'message')


@admin.register(ImportBatch)
class ImportBatchAdmin(ChineseModelAdmin):
    field_labels = {'source_filename': '源文件名', 'mode': '导入模式', 'source_sha256': '文件校验值', 'status': '状态', 'summary': '预检结果', 'imported_asset_count': '已导入资产数', 'imported_stock_item_count': '已导入耗材配件数', 'imported_network_endpoint_count': '已导入网络接口数', 'created_by': '执行人'}
    list_display = ('source_filename', 'mode', 'status', 'imported_asset_count', 'imported_stock_item_count', 'imported_network_endpoint_count', 'created_by', 'created_at')
    list_filter = ('mode', 'status')
    search_fields = ('source_filename', 'source_sha256')
    readonly_fields = ('source_filename', 'source_sha256', 'summary', 'imported_asset_count', 'imported_stock_item_count', 'imported_network_endpoint_count', 'created_by')
    inlines = [ImportIssueInline]


class InventoryCheckLineInline(admin.TabularInline):
    model = InventoryCheckLine
    extra = 0
    fields = ('item_type', 'asset', 'stock_item', 'expected_quantity', 'counted_quantity', 'result', 'counted_by', 'counted_at')
    readonly_fields = fields
    can_delete = False


@admin.register(InventoryCheckTask)
class InventoryCheckTaskAdmin(ChineseModelAdmin):
    field_labels = {
        'check_no': '盘点任务编号',
        'name': '任务名称',
        'warehouse': '盘点仓库',
        'location': '盘点库位',
        'status': '任务状态',
        'note': '任务说明',
        'created_by': '创建人',
        'started_by': '开始人',
        'reviewed_by': '复核人',
        'started_at': '开始时间',
        'submitted_at': '提交时间',
        'completed_at': '完成时间',
        'reopened_by': '退回人',
        'reopened_at': '退回时间',
        'reopen_note': '退回原因',
    }
    list_display = ('check_no', 'name', 'warehouse', 'location', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'warehouse')
    search_fields = ('check_no', 'name')
    readonly_fields = ('check_no', 'status', 'created_by', 'started_by', 'reviewed_by', 'reopened_by', 'started_at', 'submitted_at', 'completed_at', 'reopened_at', 'reopen_note')
    inlines = [InventoryCheckLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryCheckLine)
class InventoryCheckLineAdmin(ChineseModelAdmin):
    field_labels = {
        'task': '盘点任务', 'item_type': '物品类型', 'asset': '资产', 'stock_item': '耗材配件',
        'expected_quantity': '账面数量', 'counted_quantity': '实盘数量', 'expected_asset_code': '账面资产编码',
        'expected_serial_number': '账面原厂 SN', 'expected_status': '账面状态', 'result': '盘点结果',
        'counted_by': '盘点人', 'counted_at': '盘点时间', 'note': '备注',
    }
    list_display = ('task', 'item_type', 'asset', 'stock_item', 'expected_quantity', 'counted_quantity', 'result', 'counted_by')
    list_filter = ('result', 'item_type')
    search_fields = ('task__check_no', 'expected_asset_code', 'expected_serial_number', 'asset__asset_code', 'stock_item__name')
    readonly_fields = [field.name for field in InventoryCheckLine._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryCheckScan)
class InventoryCheckScanAdmin(ChineseModelAdmin):
    field_labels = {
        'task': '盘点任务', 'line': '盘点明细', 'scanned_value': '扫描内容', 'asset': '资产',
        'stock_item': '耗材配件', 'quantity': '数量', 'result': '扫描结果', 'operator': '操作人',
        'observed_warehouse': '现场仓库', 'observed_location': '现场库位', 'note': '备注',
    }
    list_display = ('created_at', 'task', 'scanned_value', 'result', 'asset', 'stock_item', 'operator')
    list_filter = ('result', 'task')
    search_fields = ('task__check_no', 'scanned_value', 'asset__asset_code', 'stock_item__name')
    readonly_fields = [field.name for field in InventoryCheckScan._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryCheckAdjustment)
class InventoryCheckAdjustmentAdmin(ChineseModelAdmin):
    field_labels = {
        'task': '盘点任务', 'line': '盘点明细', 'action': '调整类型', 'asset': '资产', 'stock_item': '耗材配件',
        'before_quantity': '调整前数量', 'after_quantity': '调整后数量', 'before_status': '调整前状态',
        'after_status': '调整后状态', 'before_warehouse': '调整前仓库', 'after_warehouse': '调整后仓库',
        'before_location': '调整前库位', 'after_location': '调整后库位', 'reviewed_by': '复核人',
        'review_note': '复核说明', 'executed_at': '执行时间',
    }
    list_display = ('executed_at', 'task', 'action', 'asset', 'stock_item', 'before_quantity', 'after_quantity', 'reviewed_by')
    list_filter = ('action', 'reviewed_by')
    search_fields = ('task__check_no', 'asset__asset_code', 'stock_item__name')
    readonly_fields = [field.name for field in InventoryCheckAdjustment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportIssue)
class ImportIssueAdmin(ChineseModelAdmin):
    field_labels = {'batch': '导入批次', 'sheet_name': '工作表', 'row_number': '行号', 'severity': '严重程度', 'code': '问题代码', 'message': '问题说明', 'source_data': '源数据', 'resolved': '是否已处理', 'resolution_note': '处理说明'}
    list_display = ('batch', 'sheet_name', 'row_number', 'severity', 'code', 'message', 'resolved')
    list_filter = ('severity', 'resolved', 'sheet_name')
    search_fields = ('message', 'code', 'sheet_name')
    readonly_fields = ('batch', 'sheet_name', 'row_number', 'severity', 'code', 'message', 'source_data')


@admin.register(StockItem)
class StockItemAdmin(ChineseModelAdmin):
    field_labels = {'name': '耗材配件名称', 'code': '耗材配件编码', 'unit': '计量单位', 'quantity': '当前库存数量', 'safety_stock': '安全库存', 'supplier': '供应商', 'unit_cost': '单价（元）', 'item_type': '物品类型', 'source_import_batch': '导入批次', 'warehouse': '所属仓库', 'location': '所在库位'}
    list_display = ('name', 'item_type', 'code', 'quantity', 'safety_stock', 'warehouse', 'location')
    search_fields = ('name', 'code', 'item_type__name')
    list_filter = ('warehouse', 'item_type')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append('quantity')
        return fields


@admin.register(InventoryConversion)
class InventoryConversionAdmin(ChineseModelAdmin):
    field_labels = {
        'conversion_no': '转换编号', 'direction': '转换方向', 'item_type': '物品类型',
        'stock_item': '耗材配件', 'assets': '关联单件资产', 'quantity': '转换数量',
        'note': '转换说明', 'created_by': '操作人',
    }
    list_display = ('conversion_no', 'direction', 'item_type', 'stock_item', 'quantity', 'created_by', 'created_at')
    list_filter = ('direction', 'item_type', 'created_by')
    search_fields = ('conversion_no', 'stock_item__code', 'stock_item__name', 'assets__asset_code', 'assets__serial_number')
    readonly_fields = [field.name for field in InventoryConversion._meta.fields] + ['assets']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
