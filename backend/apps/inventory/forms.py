from pathlib import Path

from django import forms
from django.contrib.auth import get_user_model

from apps.common.upload_validation import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    validate_attachment_upload,
)

from .models import (
    Asset,
    AssetAttachment,
    AssetHandover,
    AssetStateCorrection,
    Category,
    Customer,
    AssetTypeCode,
    InventoryDomain,
    ItemType,
    Location,
    Manufacturer,
    MaintenanceRecord,
    Project,
    ProductModelCode,
    ServiceProvider,
    StockItem,
    Supplier,
    Warehouse,
    WorkOrder,
)
from .code_services import SN_PLACEHOLDERS, normalize_identifier

User = get_user_model()


class AssetManagementForm(forms.ModelForm):
    identity_change_reason = forms.CharField(
        label='编码或身份变更原因', required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text='修改行政编码或其他身份字段时必填；新建资产可不填。',
    )

    class Meta:
        model = Asset
        fields = [
            'domain', 'code_source', 'asset_type_code', 'product_model_code', 'asset_code',
            'name', 'item_type', 'manufacturer_ref', 'manufacturer', 'model', 'serial_number', 'sn_status',
            'manufacturer_barcode', 'search_aliases', 'received_date',
            'purchase_order_no', 'supplier_ref', 'supplier', 'purchase_amount', 'warranty_until',
            'project',
            'customer_ref', 'work_order', 'warehouse', 'location', 'photo_url', 'remarks',
        ]
        labels = {
            'domain': '资产归属', 'code_source': '主资产编码方式',
            'asset_type_code': '设备类型代码', 'product_model_code': '型号代码',
            'name': '资产名称', 'item_type': '物品类型', 'asset_code': '主资产编码',
            'manufacturer_ref': '生产厂家资料', 'manufacturer': '生产厂家名称（未建档时填写）',
            'model': '型号', 'serial_number': '生产厂家 SN / 序列号',
            'sn_status': '生产厂家 SN 状态',
            'manufacturer_barcode': '生产厂家条码/二维码内容', 'search_aliases': '别名/搜索标签',
            'received_date': '到货日期', 'purchase_order_no': '采购订单号',
            'supplier_ref': '供应商资料', 'supplier': '供应商名称（未建档时填写）',
            'purchase_amount': '采购金额（元）',
            'warranty_until': '质保到期日', 'project': '所属项目（选填）',
            'customer_ref': '客户（选填）',
            'work_order': '关联工单（选填）', 'warehouse': '所属仓库',
            'location': '所在库位', 'photo_url': '资产照片地址', 'remarks': '备注',
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        if args and args[0] is not None and instance is not None:
            data = args[0].copy()
            # Older edit clients do not submit the identity fields introduced in
            # V2. Keep the stored identity only when a field is genuinely absent;
            # an explicitly submitted blank value must still be validated.
            identity_defaults = {
                'domain': instance.domain_id,
                'code_source': instance.code_source,
                'asset_type_code': instance.asset_type_code_id,
                'product_model_code': instance.product_model_code_id,
                'asset_code': instance.asset_code,
            }
            for field_name, value in identity_defaults.items():
                if field_name not in data and value not in (None, ''):
                    data[field_name] = str(value)
            if 'domain' not in data:
                fallback_domain = InventoryDomain.objects.filter(code='RND').first()
                if fallback_domain:
                    data['domain'] = str(fallback_domain.id)
            if 'serial_number' not in data and instance.serial_number:
                data['serial_number'] = instance.serial_number
            if 'sn_status' not in data:
                serial_number = data.get('serial_number', instance.serial_number)
                data['sn_status'] = (
                    Asset.SnStatus.PROVIDED if serial_number
                    else instance.sn_status or Asset.SnStatus.PENDING
                )
            args = (data, *args[1:])
        super().__init__(*args, **kwargs)
        self.sn_warnings = []
        self.fields['domain'].queryset = InventoryDomain.objects.filter(is_active=True).order_by('code')
        self.fields['asset_type_code'].queryset = AssetTypeCode.objects.filter(is_active=True).order_by('code')
        self.fields['product_model_code'].queryset = ProductModelCode.objects.filter(is_active=True).select_related('asset_type_code').order_by('asset_type_code__code', 'code')
        self.fields['manufacturer_ref'].queryset = Manufacturer.objects.filter(is_active=True).order_by('name')
        self.fields['project'].queryset = Project.objects.filter(is_active=True).order_by('name')
        self.fields['work_order'].queryset = WorkOrder.objects.filter(is_active=True).order_by('name')
        widgets = {
            'received_date': forms.DateInput(attrs={'type': 'date'}),
            'warranty_until': forms.DateInput(attrs={'type': 'date'}),
            'purchase_amount': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'remarks': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        warehouse = cleaned_data.get('warehouse')
        location = cleaned_data.get('location')
        if warehouse and location and location.warehouse_id != warehouse.id:
            self.add_error('location', '所选库位不属于该仓库。')
        domain = cleaned_data.get('domain')
        code_source = cleaned_data.get('code_source')
        asset_type = cleaned_data.get('asset_type_code')
        product_model = cleaned_data.get('product_model_code')
        asset_code = normalize_identifier(cleaned_data.get('asset_code'))
        if self.instance.pk and domain and self.instance.domain_id and domain.id != self.instance.domain_id:
            self.add_error('domain', '资产归属不能在普通编辑中直接修改，请使用受控的归属调整功能。')
        if code_source == Asset.CodeSource.MARKET_GENERATED:
            if not asset_type:
                self.add_error('asset_type_code', '生成 SC 编码前必须选择设备类型。')
            if not product_model:
                self.add_error('product_model_code', '生成 SC 编码前必须选择型号。')
            elif asset_type and product_model.asset_type_code_id != asset_type.id:
                self.add_error('product_model_code', '所选型号不属于所选设备类型。')
            elif product_model and not product_model.sc_prefix:
                self.add_error('product_model_code', '所选型号尚未维护源表 SC 编码前缀。')
        elif code_source == Asset.CodeSource.ADMIN_ASSIGNED:
            if not asset_code:
                self.add_error('asset_code', '选择行政/ERP 编码时必须填写 DZSB 资产编码。')
            elif not asset_code.startswith(('DZSB-', 'ZSB-', 'QTZC-')):
                self.add_error(
                    'asset_code',
                    '行政/ERP 资产编码必须以 DZSB- 开头（历史遗留的 ZSB-、QTZC- 也可接受）。',
                )
        elif code_source == Asset.CodeSource.WAREHOUSE_ASSIGNED:
            if not asset_code.startswith(('SC-', 'DZSB-', 'ZSB-', 'QTZC-')):
                self.add_error(
                    'asset_code',
                    '源表既有资产编码必须以 SC-、DZSB-、ZSB- 或 QTZC- 开头。',
                )
        elif code_source == Asset.CodeSource.PENDING_ADMIN:
            cleaned_data['asset_code'] = None
        sn = normalize_identifier(cleaned_data.get('serial_number'))
        sn_status = cleaned_data.get('sn_status')
        if sn in SN_PLACEHOLDERS:
            self.add_error('serial_number', '请不要用“无、未知、-、暂无”等文字代替 SN，请选择正确的 SN 状态。')
        if sn_status in {Asset.SnStatus.PROVIDED, Asset.SnStatus.MANUAL} and not sn:
            self.add_error('serial_number', '当前 SN 状态要求填写生产厂家 SN。')
        if sn_status in {Asset.SnStatus.NO_SN, Asset.SnStatus.PENDING} and sn:
            self.add_error('serial_number', '当前 SN 状态要求 SN 留空。')
        if sn:
            duplicates = Asset.objects.filter(serial_number__iexact=sn).exclude(pk=self.instance.pk)
            if duplicates.exists():
                same_context = duplicates.filter(
                    manufacturer_ref=cleaned_data.get('manufacturer_ref'),
                    product_model_code=product_model,
                ).exists()
                self.sn_warnings.append(
                    '同生产厂家、同型号下已有相同 SN，请再次核对实物。'
                    if same_context else '系统中已有其他资产使用相同 SN，请确认这是允许的重复编号。'
                )
        work_order = cleaned_data.get('work_order')
        project = cleaned_data.get('project')
        if work_order and project and work_order.project_id and work_order.project_id != project.id:
            self.add_error('work_order', '所选工单不属于所选项目。')
        if self.instance.pk and self.instance.domain_id:
            identity_fields = {'asset_code', 'code_source', 'asset_type_code', 'product_model_code'}
            if identity_fields.intersection(self.changed_data) and not cleaned_data.get('identity_change_reason', '').strip():
                self.add_error('identity_change_reason', '修改资产编码或编码方式时必须填写变更原因。')
        return cleaned_data


class AssetDomainChangeForm(forms.Form):
    to_domain = forms.ModelChoiceField(label='调整后的资产归属', queryset=InventoryDomain.objects.none())
    asset_type_code = forms.ModelChoiceField(label='设备类型代码', queryset=AssetTypeCode.objects.none(), required=False)
    product_model_code = forms.ModelChoiceField(label='型号代码', queryset=ProductModelCode.objects.none(), required=False)
    reason = forms.CharField(label='调整原因', widget=forms.Textarea(attrs={'rows': 4}))
    confirm = forms.BooleanField(label='我已核对实物与编码规则，并确认重新打印资产标签')

    def __init__(self, *args, asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asset = asset
        queryset = InventoryDomain.objects.filter(is_active=True)
        if asset and asset.domain_id:
            queryset = queryset.exclude(pk=asset.domain_id)
        self.fields['to_domain'].queryset = queryset.order_by('code')
        self.fields['asset_type_code'].queryset = AssetTypeCode.objects.filter(is_active=True).order_by('code')
        self.fields['product_model_code'].queryset = ProductModelCode.objects.filter(is_active=True).select_related('asset_type_code').order_by('asset_type_code__code', 'code')

    def clean(self):
        cleaned = super().clean()
        domain = cleaned.get('to_domain')
        asset_type = cleaned.get('asset_type_code')
        product_model = cleaned.get('product_model_code')
        if asset_type and product_model and product_model.asset_type_code_id != asset_type.id:
            self.add_error('product_model_code', '所选型号不属于所选设备类型。')
        return cleaned


class StockItemManagementForm(forms.ModelForm):
    initial_quantity = forms.IntegerField(
        label='初始库存数量', min_value=0, required=False, initial=0,
        help_text='仅新建耗材配件时填写；后续数量变化必须通过出入库、盘点或受控转换处理。',
    )

    class Meta:
        model = StockItem
        fields = [
            'name', 'code', 'item_type', 'unit', 'safety_stock',
            'supplier_ref', 'supplier', 'unit_cost', 'warehouse', 'location',
        ]
        labels = {
            'name': '耗材配件名称', 'code': '耗材配件编码', 'item_type': '物品类型',
            'unit': '计量单位', 'safety_stock': '安全库存',
            'supplier_ref': '供应商资料', 'supplier': '供应商名称（未建档时填写）',
            'unit_cost': '单价（元）', 'warehouse': '所属仓库',
            'location': '所在库位',
        }
        widgets = {
            'safety_stock': forms.NumberInput(attrs={'min': '0'}),
            'unit_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields.pop('initial_quantity')

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.pk:
            instance.quantity = self.cleaned_data.get('initial_quantity') or 0
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean(self):
        cleaned_data = super().clean()
        warehouse = cleaned_data.get('warehouse')
        location = cleaned_data.get('location')
        if warehouse and location and location.warehouse_id != warehouse.id:
            self.add_error('location', '所选库位不属于该仓库。')
        return cleaned_data


class WarehouseManagementForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ['name', 'code', 'location_desc', 'is_active']
        labels = {
            'name': '仓库名称', 'code': '仓库编码', 'location_desc': '位置说明',
            'is_active': '是否启用',
        }
        widgets = {'location_desc': forms.Textarea(attrs={'rows': 3})}


class LocationManagementForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ['warehouse', 'name', 'code', 'description']
        labels = {
            'warehouse': '所属仓库', 'name': '库位名称', 'code': '库位编码',
            'description': '库位说明',
        }
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class CategoryManagementForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'code']
        labels = {'name': '分类名称', 'code': '分类编码'}


class ItemTypeManagementForm(forms.ModelForm):
    class Meta:
        model = ItemType
        fields = ['name', 'code', 'is_serialized', 'unit', 'safety_stock']
        labels = {
            'name': '物品类型名称', 'code': '类型编码',
            'is_serialized': '单件管理', 'unit': '计量单位', 'safety_stock': '安全库存',
        }
        widgets = {'safety_stock': forms.NumberInput(attrs={'min': '0'})}

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.category_id:
            instance.category, _ = Category.objects.get_or_create(
                code='DEFAULT', defaults={'name': '默认分类'},
            )
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class BusinessReferenceForm(forms.ModelForm):
    class Meta:
        fields = ['code', 'name', 'contact_name', 'phone', 'notes', 'is_active']
        labels = {
            'code': '编码', 'name': '名称', 'contact_name': '联系人',
            'phone': '联系电话', 'notes': '备注', 'is_active': '是否启用',
        }
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}


class CustomerManagementForm(BusinessReferenceForm):
    class Meta(BusinessReferenceForm.Meta):
        model = Customer


class SupplierManagementForm(BusinessReferenceForm):
    class Meta(BusinessReferenceForm.Meta):
        model = Supplier


class ManufacturerManagementForm(BusinessReferenceForm):
    class Meta(BusinessReferenceForm.Meta):
        model = Manufacturer


class AssetTypeCodeManagementForm(forms.ModelForm):
    class Meta:
        model = AssetTypeCode
        fields = ['code', 'name', 'description', 'is_active']
        labels = {'code': '设备类型代码', 'name': '设备类型名称', 'description': '说明', 'is_active': '是否启用'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class ProductModelCodeManagementForm(forms.ModelForm):
    class Meta:
        model = ProductModelCode
        fields = [
            'asset_type_code', 'manufacturer', 'code', 'name',
            'sc_prefix', 'sc_serial_width', 'description', 'is_active',
        ]
        labels = {
            'asset_type_code': '所属设备类型', 'manufacturer': '生产厂家',
            'code': '型号代码', 'name': '型号名称',
            'sc_prefix': 'SC 编码完整前缀', 'sc_serial_width': '流水位数',
            'description': '说明', 'is_active': '是否启用',
        }
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def clean_sc_prefix(self):
        value = normalize_identifier(self.cleaned_data.get('sc_prefix'))
        if value and (not value.startswith('SC-') or not all(part.isalnum() for part in value.split('-'))):
            raise forms.ValidationError('SC 前缀必须以 SC- 开头，且只能包含字母、数字和连字符。')
        return value


class ServiceProviderManagementForm(BusinessReferenceForm):
    class Meta(BusinessReferenceForm.Meta):
        model = ServiceProvider


class ProjectManagementForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['code', 'name', 'customer', 'notes', 'is_active']
        labels = {
            'code': '项目编号', 'name': '项目名称', 'customer': '关联客户',
            'notes': '备注', 'is_active': '是否启用',
        }
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}


class WorkOrderManagementForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = ['code', 'name', 'project', 'customer', 'notes', 'is_active']
        labels = {
            'code': '工单编号', 'name': '工单名称', 'project': '所属项目',
            'customer': '关联客户', 'notes': '备注', 'is_active': '是否启用',
        }
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}

    def clean(self):
        cleaned_data = super().clean()
        project = cleaned_data.get('project')
        customer = cleaned_data.get('customer')
        if project and customer and project.customer_id and project.customer_id != customer.id:
            self.add_error('customer', '所选客户与项目关联的客户不一致。')
        return cleaned_data


class MaintenanceCreateForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRecord
        fields = [
            'source', 'reported_date', 'repair_type', 'fault_description', 'service_provider_ref', 'service_provider', 'rma_no',
            'expected_return_date', 'estimated_cost', 'is_under_warranty',
            'responsible_user',
        ]
        labels = {
            'source': '记录来源', 'reported_date': '故障/维修发生日期',
            'repair_type': '维修方式', 'fault_description': '故障现象',
            'service_provider_ref': '维修服务商资料',
            'service_provider': '维修商名称（未建档时填写）', 'rma_no': 'RMA 编号',
            'expected_return_date': '预计返厂日期', 'estimated_cost': '预计维修成本（元）',
            'is_under_warranty': '是否在质保期内', 'responsible_user': '维修负责人',
        }
        widgets = {
            'fault_description': forms.Textarea(attrs={'rows': 4}),
            'reported_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_return_date': forms.DateInput(attrs={'type': 'date'}),
            'estimated_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsible_user'].queryset = User.objects.filter(is_active=True).order_by('username')


class MaintenanceUpdateForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRecord
        fields = [
            'repair_type', 'status', 'fault_description', 'diagnosis', 'resolution',
            'service_provider_ref', 'service_provider', 'rma_no', 'outbound_tracking_no', 'return_tracking_no',
            'sent_date', 'expected_return_date', 'returned_date', 'estimated_cost',
            'actual_cost', 'is_under_warranty', 'responsible_user',
        ]
        labels = {
            'repair_type': '维修方式', 'status': '当前维修进度',
            'fault_description': '故障现象', 'diagnosis': '诊断结果', 'resolution': '处理方案/维修结果',
            'service_provider_ref': '维修服务商资料',
            'service_provider': '维修商名称（未建档时填写）', 'rma_no': 'RMA 编号',
            'outbound_tracking_no': '寄出快递单号', 'return_tracking_no': '返还快递单号',
            'sent_date': '寄出日期', 'expected_return_date': '预计返厂日期',
            'returned_date': '实际返回日期', 'estimated_cost': '预计维修成本（元）',
            'actual_cost': '实际维修成本（元）', 'is_under_warranty': '是否在质保期内',
            'responsible_user': '维修负责人',
        }
        widgets = {
            'fault_description': forms.Textarea(attrs={'rows': 3}),
            'diagnosis': forms.Textarea(attrs={'rows': 3}),
            'resolution': forms.Textarea(attrs={'rows': 3}),
            'sent_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_return_date': forms.DateInput(attrs={'type': 'date'}),
            'returned_date': forms.DateInput(attrs={'type': 'date'}),
            'estimated_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
            'actual_cost': forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = [
            choice for choice in MaintenanceRecord.Status.choices
            if choice[0] not in {MaintenanceRecord.Status.COMPLETED, MaintenanceRecord.Status.CANCELED}
        ]
        self.fields['responsible_user'].queryset = User.objects.filter(is_active=True).order_by('username')


class MaintenanceFinishForm(forms.Form):
    class Destination:
        RESTORE = 'restore'
        WAREHOUSE = 'warehouse'
        choices = (
            (RESTORE, '恢复维修前状态'),
            (WAREHOUSE, '完成后入指定仓库'),
        )

    resolution = forms.CharField(
        label='维修结果', required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    actual_cost = forms.DecimalField(
        label='实际维修成本（元）', required=False, min_value=0, max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={'min': '0', 'step': '0.01'}),
    )
    returned_date = forms.DateField(
        label='实际返回日期', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    destination = forms.ChoiceField(label='完成后资产去向', choices=Destination.choices)
    target_warehouse = forms.ModelChoiceField(
        label='目标仓库', queryset=Warehouse.objects.none(), required=False,
    )
    target_location = forms.ModelChoiceField(
        label='目标库位', queryset=Location.objects.none(), required=False,
    )

    def __init__(self, *args, historical=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.historical = historical
        self.fields['target_warehouse'].queryset = Warehouse.objects.filter(is_active=True).order_by('name')
        self.fields['target_location'].queryset = Location.objects.select_related('warehouse').order_by(
            'warehouse__name', 'code',
        )
        if historical:
            self.initial.setdefault('destination', self.Destination.WAREHOUSE)

    def clean(self):
        cleaned = super().clean()
        if self.historical or cleaned.get('destination') == self.Destination.WAREHOUSE:
            warehouse = cleaned.get('target_warehouse')
            location = cleaned.get('target_location')
            if not warehouse:
                self.add_error('target_warehouse', '请选择维修完成后的目标仓库。')
            if not location:
                self.add_error('target_location', '请选择维修完成后的目标库位。')
            elif warehouse and location.warehouse_id != warehouse.id:
                self.add_error('target_location', '所选库位不属于目标仓库。')
        return cleaned


class AssetStateCorrectionForm(forms.ModelForm):
    class Meta:
        model = AssetStateCorrection
        fields = ['to_status', 'source', 'target_warehouse', 'target_location', 'reason']
        labels = {
            'to_status': '校正后状态',
            'source': '校正来源',
            'target_warehouse': '目标仓库',
            'target_location': '目标库位',
            'reason': '校正原因',
        }
        widgets = {'reason': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.common.permissions import is_system_administrator
        from .state_services import (
            ADMIN_CORRECTION_TARGET_STATUSES,
            CORRECTION_TARGET_STATUSES,
        )
        allowed = ADMIN_CORRECTION_TARGET_STATUSES if is_system_administrator(user) else CORRECTION_TARGET_STATUSES
        self.fields['to_status'].choices = [
            (status, dict(Asset.Status.choices)[status]) for status in Asset.Status.values if status in allowed
        ]
        self.fields['target_warehouse'].queryset = Warehouse.objects.filter(is_active=True).order_by('name')
        self.fields['target_location'].queryset = Location.objects.select_related('warehouse').order_by(
            'warehouse__name', 'code',
        )

    def clean(self):
        cleaned = super().clean()
        warehouse = cleaned.get('target_warehouse')
        location = cleaned.get('target_location')
        if warehouse and location and location.warehouse_id != warehouse.id:
            self.add_error('target_location', '所选库位不属于目标仓库。')
        return cleaned


class AssetHandoverForm(forms.ModelForm):
    class Meta:
        model = AssetHandover
        fields = [
            'from_holder_name', 'to_holder_name', 'to_user', 'handover_date',
            'condition_description', 'accessories', 'note',
        ]
        labels = {
            'from_holder_name': '移交人', 'to_holder_name': '接收人',
            'to_user': '系统内确认账号', 'handover_date': '交接日期',
            'condition_description': '设备外观和运行状态', 'accessories': '随附配件清单',
            'note': '交接备注',
        }
        help_texts = {
            'to_user': '可不选；选择后仅该账号或管理员可以确认接收。',
        }
        widgets = {
            'handover_date': forms.DateInput(attrs={'type': 'date'}),
            'condition_description': forms.Textarea(attrs={'rows': 3}),
            'accessories': forms.Textarea(attrs={'rows': 3}),
            'note': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['to_user'].queryset = User.objects.filter(is_active=True).order_by('username')


class AssetAttachmentForm(forms.ModelForm):
    ALLOWED_SUFFIXES = ALLOWED_ATTACHMENT_SUFFIXES

    class Meta:
        model = AssetAttachment
        fields = ['file', 'display_name', 'category', 'maintenance', 'handover', 'note']
        labels = {
            'file': '选择文件', 'display_name': '资料名称', 'category': '资料类别',
            'maintenance': '关联维修单', 'handover': '关联交接记录', 'note': '说明',
        }

    def __init__(self, *args, asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asset = asset
        self.fields['display_name'].required = False
        self.fields['maintenance'].queryset = MaintenanceRecord.objects.filter(asset=asset) if asset else MaintenanceRecord.objects.none()
        self.fields['handover'].queryset = AssetHandover.objects.filter(asset=asset) if asset else AssetHandover.objects.none()

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        validate_attachment_upload(uploaded_file, self.ALLOWED_SUFFIXES)
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        uploaded_file = cleaned_data.get('file')
        if not cleaned_data.get('display_name') and uploaded_file:
            cleaned_data['display_name'] = Path(uploaded_file.name).name[:160]
        return cleaned_data


class ScrapApprovalForm(forms.Form):
    reason = forms.CharField(
        label='报废原因',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '请说明故障、评估结论及无法继续使用的原因'}),
    )


class DamageApprovalForm(forms.Form):
    reason = forms.CharField(
        label='报损原因',
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': '请说明丢失/损坏经过，例如归还验收时实物缺失且确认无法收回'}),
    )
