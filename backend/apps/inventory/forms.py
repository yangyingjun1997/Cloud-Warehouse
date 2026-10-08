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
    Category,
    Customer,
    ItemType,
    Location,
    MaintenanceRecord,
    Project,
    ServiceProvider,
    StockItem,
    Supplier,
    Warehouse,
    WorkOrder,
)

User = get_user_model()


class AssetManagementForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'name', 'item_type', 'asset_code', 'manufacturer', 'model', 'serial_number',
            'manufacturer_barcode', 'search_aliases', 'received_date',
            'purchase_order_no', 'supplier_ref', 'supplier', 'purchase_amount', 'warranty_until',
            'warehouse', 'location', 'photo_url', 'remarks',
        ]
        labels = {
            'name': '资产名称', 'item_type': '物品类型', 'asset_code': '公司 ERP 资产编码', 'manufacturer': '生产厂家',
            'model': '型号', 'serial_number': '生产厂家 SN / 序列号',
            'manufacturer_barcode': '生产厂家条码/二维码内容', 'search_aliases': '别名/搜索标签',
            'received_date': '到货日期', 'purchase_order_no': '采购订单号',
            'supplier_ref': '供应商资料', 'supplier': '供应商名称（未建档时填写）',
            'purchase_amount': '采购金额（元）',
            'warranty_until': '质保到期日', 'warehouse': '所属仓库',
            'location': '所在库位', 'photo_url': '资产照片地址', 'remarks': '备注',
        }
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
        return cleaned_data


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
            'repair_type', 'fault_description', 'service_provider_ref', 'service_provider', 'rma_no',
            'expected_return_date', 'estimated_cost', 'is_under_warranty',
            'responsible_user',
        ]
        labels = {
            'repair_type': '维修方式', 'fault_description': '故障现象',
            'service_provider_ref': '维修服务商资料',
            'service_provider': '维修商名称（未建档时填写）', 'rma_no': 'RMA 编号',
            'expected_return_date': '预计返厂日期', 'estimated_cost': '预计维修成本（元）',
            'is_under_warranty': '是否在质保期内', 'responsible_user': '维修负责人',
        }
        widgets = {
            'fault_description': forms.Textarea(attrs={'rows': 4}),
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
