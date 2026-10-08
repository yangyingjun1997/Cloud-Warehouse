from django.conf import settings
from django.db import models
from django.utils import timezone
from pypinyin import lazy_pinyin

from apps.accounts.models import Department
from apps.common.models import ImmutableAuditMixin, TimeStampedModel, UUIDModel
from apps.common.utils import build_asset_no, build_code


class BusinessReference(UUIDModel, TimeStampedModel):
    code = models.CharField('编码', max_length=80, unique=True)
    name = models.CharField('名称', max_length=160)
    contact_name = models.CharField('联系人', max_length=120, blank=True, default='')
    phone = models.CharField('联系电话', max_length=32, blank=True, default='')
    notes = models.TextField('备注', blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        abstract = True
        ordering = ['name', 'code']

    def __str__(self) -> str:
        return f'{self.name}（{self.code}）'


class Customer(BusinessReference):
    class Meta(BusinessReference.Meta):
        verbose_name = '客户资料'
        verbose_name_plural = '客户资料'


class Supplier(BusinessReference):
    class Meta(BusinessReference.Meta):
        verbose_name = '供应商资料'
        verbose_name_plural = '供应商资料'


class ServiceProvider(BusinessReference):
    class Meta(BusinessReference.Meta):
        verbose_name = '维修服务商资料'
        verbose_name_plural = '维修服务商资料'


class Project(UUIDModel, TimeStampedModel):
    code = models.CharField('项目编号', max_length=80, unique=True)
    name = models.CharField('项目名称', max_length=160)
    customer = models.ForeignKey(
        Customer,
        verbose_name='客户',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='projects',
    )
    notes = models.TextField('备注', blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        ordering = ['name', 'code']
        verbose_name = '项目资料'
        verbose_name_plural = '项目资料'

    def __str__(self) -> str:
        return f'{self.name}（{self.code}）'


class WorkOrder(UUIDModel, TimeStampedModel):
    code = models.CharField('工单编号', max_length=80, unique=True)
    name = models.CharField('工单名称', max_length=160)
    project = models.ForeignKey(
        Project,
        verbose_name='所属项目',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='work_orders',
    )
    customer = models.ForeignKey(
        Customer,
        verbose_name='客户',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='work_orders',
    )
    notes = models.TextField('备注', blank=True, default='')
    is_active = models.BooleanField('是否启用', default=True)

    class Meta:
        ordering = ['name', 'code']
        verbose_name = '工单资料'
        verbose_name_plural = '工单资料'

    def save(self, *args, **kwargs):
        if self.project_id and not self.customer_id:
            self.customer_id = self.project.customer_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.name}（{self.code}）'


class Warehouse(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=50, unique=True)
    location_desc = models.CharField(max_length=255, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = '仓库'
        verbose_name_plural = '仓库'

    def __str__(self) -> str:
        return self.name


class Location(UUIDModel, TimeStampedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='locations')
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        unique_together = ('warehouse', 'code')
        ordering = ['warehouse__name', 'code']
        verbose_name = '库位'
        verbose_name_plural = '库位'

    def __str__(self) -> str:
        return f'{self.warehouse.code}-{self.code}'


class Category(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = '物品分类'
        verbose_name_plural = '物品分类'

    def __str__(self) -> str:
        return self.name


class ItemType(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=50, unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='item_types')
    is_serialized = models.BooleanField(
        default=True,
        help_text='启用后每个实物需单独建立资产台账、资产编号和二维码。',
    )
    unit = models.CharField(max_length=20, default='pcs')
    safety_stock = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('category', 'name')
        ordering = ['category__name', 'name']
        verbose_name = '物品类型'
        verbose_name_plural = '物品类型'

    def __str__(self) -> str:
        return self.name


class Asset(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        IN_STOCK = 'in_stock', '在库'
        BORROWED = 'borrowed', '已借出'
        ISSUED = 'issued', '已领用'
        PENDING_OUT = 'pending_out', '待出库'
        PENDING_RETURN = 'pending_return', '待归还验收'
        PENDING_INSPECTION = 'pending_inspection', '待质检'
        MAINTENANCE = 'maintenance', '待维修'
        REPAIRING = 'repairing', '维修中'
        SOLD = 'sold', '已售出'
        INTERNAL_BORROW = 'internal_borrow', '内部借用库'
        SHIPPED_OUT = 'shipped_out', '已出库'
        ASSEMBLED = 'assembled', '已组装'
        RETURNED_TO_VENDOR = 'returned_to_vendor', '已退供应商'
        SCRAPPED = 'scrapped', '已报废'
        DAMAGED = 'damaged', '已报损'
        LOST = 'lost', '已丢失'
        DISABLED = 'disabled', '已停用'
        CONVERTED_TO_STOCK = 'converted_to_stock', '已转为耗材配件'

    class LocationState(models.TextChoices):
        IN_WAREHOUSE = 'in_warehouse', '在仓'
        OUT_ON_LOAN = 'out_on_loan', '外借中'
        OUT_ISSUED = 'out_issued', '已领用'
        PENDING_OUT = 'pending_out', '待出库'
        PENDING_RETURN = 'pending_return', '待归还验收'
        EXTERNAL_REPAIR = 'external_repair', '外部维修中'
        IN_TRANSIT = 'in_transit', '运输中'
        DELIVERED = 'delivered', '已交付'
        VENDOR = 'vendor', '供应商处'

    class AvailabilityState(models.TextChoices):
        AVAILABLE = 'available', '可申请'
        RESERVED = 'reserved', '已预占'
        UNAVAILABLE = 'unavailable', '暂不可用'
        FROZEN = 'frozen', '已冻结'

    class QualityState(models.TextChoices):
        NORMAL = 'normal', '正常'
        PENDING_INSPECTION = 'pending_inspection', '待质检'
        MAINTENANCE = 'maintenance', '待维修'
        REPAIRING = 'repairing', '维修中'
        DAMAGED = 'damaged', '损坏'
        LOST = 'lost', '丢失'

    class DispositionState(models.TextChoices):
        INTERNAL = 'internal', '内部使用'
        SOLD = 'sold', '已售出'
        RETURNED_TO_VENDOR = 'returned_to_vendor', '已退供应商'
        SCRAPPED = 'scrapped', '已报废'
        DISABLED = 'disabled', '已停用'
        CONVERTED_TO_STOCK = 'converted_to_stock', '已转为耗材配件'

    # Legacy status remains authoritative during the compatibility period.
    # These dimensions provide a stable reporting vocabulary without changing
    # existing workflow queries in one risky migration.
    LEGACY_STATUS_DIMENSIONS = {
        Status.IN_STOCK: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.AVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.BORROWED: {
            'location_state': LocationState.OUT_ON_LOAN,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.ISSUED: {
            'location_state': LocationState.OUT_ISSUED,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.PENDING_OUT: {
            'location_state': LocationState.PENDING_OUT,
            'availability_state': AvailabilityState.RESERVED,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.PENDING_RETURN: {
            'location_state': LocationState.PENDING_RETURN,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.PENDING_INSPECTION,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.PENDING_INSPECTION: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.PENDING_INSPECTION,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.MAINTENANCE: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.MAINTENANCE,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.REPAIRING: {
            'location_state': LocationState.EXTERNAL_REPAIR,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.REPAIRING,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.SOLD: {
            'location_state': LocationState.DELIVERED,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.SOLD,
        },
        Status.INTERNAL_BORROW: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.SHIPPED_OUT: {
            'location_state': LocationState.IN_TRANSIT,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.ASSEMBLED: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.RETURNED_TO_VENDOR: {
            'location_state': LocationState.VENDOR,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.RETURNED_TO_VENDOR,
        },
        Status.SCRAPPED: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.SCRAPPED,
        },
        Status.DAMAGED: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.DAMAGED,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.LOST: {
            'location_state': LocationState.IN_TRANSIT,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.LOST,
            'disposition_state': DispositionState.INTERNAL,
        },
        Status.DISABLED: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.FROZEN,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.DISABLED,
        },
        Status.CONVERTED_TO_STOCK: {
            'location_state': LocationState.IN_WAREHOUSE,
            'availability_state': AvailabilityState.UNAVAILABLE,
            'quality_state': QualityState.NORMAL,
            'disposition_state': DispositionState.CONVERTED_TO_STOCK,
        },
    }

    system_asset_no = models.CharField(
        '仓库系统资产编号', max_length=32, unique=True, editable=False,
    )
    # 公司行政/ERP 分配的资产编码（如 DZSB-...），允许暂时为空。
    asset_code = models.CharField(max_length=80, unique=True, null=True, blank=True, default=None)
    name = models.CharField(max_length=120)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT, related_name='assets')
    manufacturer = models.CharField(max_length=120, blank=True, default='')
    model = models.CharField(max_length=120, blank=True, default='')
    serial_number = models.CharField(max_length=120, blank=True, default='')
    manufacturer_barcode = models.CharField(max_length=255, blank=True, default='')
    qr_value = models.CharField(max_length=255, blank=True, default='')
    search_aliases = models.CharField(max_length=500, blank=True, default='')
    search_index = models.TextField(blank=True, default='', editable=False)
    received_date = models.DateField(null=True, blank=True)
    purchase_order_no = models.CharField(max_length=120, blank=True, default='')
    supplier = models.CharField('供应商', max_length=120, blank=True, default='')
    supplier_ref = models.ForeignKey(
        Supplier,
        verbose_name='供应商资料',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
    )
    purchase_amount = models.DecimalField('采购金额（元）', max_digits=12, decimal_places=2, null=True, blank=True)
    warranty_until = models.DateField('质保到期日', null=True, blank=True)
    source_import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)
    current_holder = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    external_holder_name = models.CharField(max_length=120, blank=True, default='')
    external_holder_company = models.CharField(max_length=120, blank=True, default='')
    external_holder_phone = models.CharField(max_length=32, blank=True, default='')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.IN_STOCK)
    location_state = models.CharField(
        '实物位置状态', max_length=32, choices=LocationState.choices,
        default=LocationState.IN_WAREHOUSE,
    )
    availability_state = models.CharField(
        '可用性状态', max_length=24, choices=AvailabilityState.choices,
        default=AvailabilityState.AVAILABLE,
    )
    quality_state = models.CharField(
        '质量状态', max_length=24, choices=QualityState.choices,
        default=QualityState.NORMAL,
    )
    disposition_state = models.CharField(
        '处置状态', max_length=32, choices=DispositionState.choices,
        default=DispositionState.INTERNAL,
    )
    photo_url = models.CharField(max_length=255, blank=True, default='')
    remarks = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['asset_code']
        verbose_name = '资产'
        verbose_name_plural = '资产'

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        dimension_fields = {
            'location_state', 'availability_state', 'quality_state', 'disposition_state',
        }
        should_sync_dimensions = self._state.adding
        if not should_sync_dimensions:
            previous_values = type(self).objects.filter(pk=self.pk).values(
                'status', *dimension_fields,
            ).first()
            if update_fields is not None:
                should_sync_dimensions = 'status' in update_fields
            else:
                should_sync_dimensions = bool(previous_values and previous_values['status'] != self.status)
            if previous_values and not should_sync_dimensions:
                for field_name in dimension_fields:
                    setattr(self, field_name, previous_values[field_name])
        if not self.system_asset_no:
            self.system_asset_no = build_asset_no()
        if self.asset_code == '':
            self.asset_code = None
        if not self.qr_value:
            self.qr_value = self.system_asset_no
        if self.supplier_ref_id:
            self.supplier = self.supplier_ref.name
        dimensions = self.LEGACY_STATUS_DIMENSIONS.get(self.status) if should_sync_dimensions else None
        if dimensions is not None:
            for field_name, value in dimensions.items():
                setattr(self, field_name, value)
        search_source = ' '.join(filter(None, [
            self.asset_code,
            self.system_asset_no,
            self.name,
            self.manufacturer,
            self.model,
            self.serial_number,
            self.manufacturer_barcode,
            self.qr_value,
            self.search_aliases,
            self.remarks,
            self.item_type.name if self.item_type_id else '',
        ]))
        pinyin_parts = lazy_pinyin(search_source)
        self.search_index = ' '.join([
            search_source.lower(),
            ' '.join(pinyin_parts),
            ''.join(pinyin_parts),
            ''.join(part[0] for part in pinyin_parts if part),
        ])
        if update_fields is not None:
            persisted_fields = {'system_asset_no', 'qr_value', 'search_index', 'supplier'}
            if should_sync_dimensions:
                persisted_fields.update(dimension_fields)
            kwargs['update_fields'] = set(update_fields) | persisted_fields
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.system_asset_no or self.asset_code or f'未编号·{self.name}'


class CompositeUnit(UUIDModel, TimeStampedModel):
    """成品设备：由多个子资产临时组装而成的整机。

    组装/换装/拆散为免审批直接操作（留审计与履历）；出库/归还走审批流。
    拆散后编码作废留档（status=DISASSEMBLED），再次组装时由行政赋新码，不复用旧码。
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', '待组装'
        ASSEMBLED = 'assembled', '已组装'
        BORROWED = 'borrowed', '已借出'
        SOLD = 'sold', '已售出'
        DISASSEMBLED = 'disassembled', '已拆散'

    # 成品编码由行政在出货/赋码时给予，系统不自动生成；未赋码为 NULL。
    unit_code = models.CharField('成品编码', max_length=80, unique=True, null=True, blank=True, default=None)
    name = models.CharField('成品名称', max_length=120)
    model = models.CharField('型号', max_length=120, blank=True, default='')
    qr_value = models.CharField('系统二维码内容', max_length=255, blank=True, default='')
    status = models.CharField('状态', max_length=32, choices=Status.choices, default=Status.ASSEMBLED)
    customer = models.ForeignKey(
        Customer,
        verbose_name='客户',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='composite_units',
    )
    remarks = models.TextField('备注', blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '成品设备'
        verbose_name_plural = '成品设备'

    def save(self, *args, **kwargs):
        if self.unit_code == '':
            self.unit_code = None
        if not self.qr_value and self.unit_code:
            self.qr_value = self.unit_code
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.unit_code or f'未编号·{self.name}'


class ComponentLink(UUIDModel, TimeStampedModel):
    """成品设备与子资产的组装履历；拆散不删记录，只标记 disassembled_at。"""

    composite_unit = models.ForeignKey(CompositeUnit, on_delete=models.CASCADE, related_name='component_links')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='component_links')
    assembled_at = models.DateTimeField('组装时间', default=timezone.now)
    disassembled_at = models.DateTimeField('拆散时间', null=True, blank=True)
    assemble_request_no = models.CharField('组装申请单号', max_length=80, blank=True, default='')
    disassemble_request_no = models.CharField('拆散申请单号', max_length=80, blank=True, default='')
    note = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-assembled_at']
        verbose_name = '成品组装履历'
        verbose_name_plural = '成品组装履历'
        constraints = [
            # 同一资产同一时间只能组装进一台成品设备（未拆散的记录唯一）。
            models.UniqueConstraint(
                fields=['asset'],
                condition=models.Q(disassembled_at__isnull=True),
                name='unique_active_component_link_asset',
            ),
        ]
        indexes = [models.Index(fields=['composite_unit', 'disassembled_at'])]

    def __str__(self) -> str:
        return f'{self.composite_unit} ← {self.asset}'


class AssetNetworkEndpoint(UUIDModel, TimeStampedModel):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='network_endpoints')
    interface_name = models.CharField(max_length=80, blank=True, default='')
    ip_address = models.GenericIPAddressField(protocol='both')
    subnet_mask = models.GenericIPAddressField(protocol='IPv4', null=True, blank=True)
    gateway = models.GenericIPAddressField(protocol='both', null=True, blank=True)
    mac_address = models.CharField(max_length=32, blank=True, default='')
    hostname = models.CharField(max_length=120, blank=True, default='')
    vlan_id = models.PositiveIntegerField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['asset__asset_code', '-is_primary', 'interface_name', 'ip_address']
        verbose_name = '网络接口信息'
        verbose_name_plural = '网络接口信息'

    def __str__(self) -> str:
        return f'{self.asset.asset_code} - {self.ip_address}'


class AssetLifecycleEvent(ImmutableAuditMixin, UUIDModel, TimeStampedModel):
    class EventType(models.TextChoices):
        MAINTENANCE = 'maintenance', '维修'
        HANDOVER = 'handover', '交接'
        ATTACHMENT = 'attachment', '附件归档'
        SCRAP_REQUEST = 'scrap_request', '报废申请'
        NOTE = 'note', '其他记录'

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='lifecycle_events')
    event_type = models.CharField(max_length=24, choices=EventType.choices)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True, default='')
    event_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_lifecycle_events',
    )
    related_model = models.CharField(max_length=120, blank=True, default='')
    related_object_id = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-event_at', '-created_at']
        verbose_name = '资产生命周期事件'
        verbose_name_plural = '资产生命周期事件'
        indexes = [models.Index(fields=['asset', 'event_at'])]

    def __str__(self) -> str:
        return f'{self.asset.asset_code} - {self.title}'


class MaintenanceRecord(UUIDModel, TimeStampedModel):
    class RepairType(models.TextChoices):
        INTERNAL = 'internal', '内部维修'
        EXTERNAL = 'external', '外部维修'
        WARRANTY = 'warranty', '供应商质保'

    class Status(models.TextChoices):
        OPEN = 'open', '待处理'
        DIAGNOSING = 'diagnosing', '故障诊断中'
        SENT_OUT = 'sent_out', '已寄出'
        WAITING_PARTS = 'waiting_parts', '等待配件'
        REPAIRING = 'repairing', '维修中'
        COMPLETED = 'completed', '已完成'
        CANCELED = 'canceled', '已取消'

    maintenance_no = models.CharField(max_length=80, unique=True, default='')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='maintenance_records')
    source_request = models.ForeignKey(
        'workflow.WorkflowRequest',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='maintenance_records',
    )
    repair_type = models.CharField(max_length=20, choices=RepairType.choices, default=RepairType.INTERNAL)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.OPEN)
    fault_description = models.TextField()
    diagnosis = models.TextField(blank=True, default='')
    resolution = models.TextField(blank=True, default='')
    service_provider = models.CharField(max_length=160, blank=True, default='')
    service_provider_ref = models.ForeignKey(
        ServiceProvider,
        verbose_name='维修服务商资料',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_records',
    )
    rma_no = models.CharField(max_length=120, blank=True, default='')
    outbound_tracking_no = models.CharField(max_length=120, blank=True, default='')
    return_tracking_no = models.CharField(max_length=120, blank=True, default='')
    sent_date = models.DateField(null=True, blank=True)
    expected_return_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    actual_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_under_warranty = models.BooleanField(default=False)
    previous_asset_status = models.CharField(max_length=32, choices=Asset.Status.choices, blank=True, default='')
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responsible_maintenance_records',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_maintenance_records',
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_maintenance_records',
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '资产维修台账'
        verbose_name_plural = '资产维修台账'
        constraints = [
            models.UniqueConstraint(
                fields=['asset', 'source_request'],
                condition=models.Q(source_request__isnull=False),
                name='unique_asset_workflow_maintenance',
            ),
        ]
        indexes = [
            models.Index(fields=['asset', 'status']),
            models.Index(fields=['status', 'expected_return_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.maintenance_no:
            self.maintenance_no = build_code('MAINT')
        if self.service_provider_ref_id:
            self.service_provider = self.service_provider_ref.name
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'service_provider'}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.maintenance_no


class AssetHandover(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待确认'
        CONFIRMED = 'confirmed', '已确认'
        CANCELED = 'canceled', '已取消'

    handover_no = models.CharField(max_length=80, unique=True, default='')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='handovers')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    from_holder_name = models.CharField(max_length=120, blank=True, default='')
    to_holder_name = models.CharField(max_length=120)
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='asset_handovers_to_confirm',
    )
    handover_date = models.DateField()
    condition_description = models.TextField(blank=True, default='')
    accessories = models.TextField(blank=True, default='')
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_asset_handovers',
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_asset_handovers',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-handover_date', '-created_at']
        verbose_name = '资产交接记录'
        verbose_name_plural = '资产交接记录'
        indexes = [models.Index(fields=['asset', 'status'])]

    def save(self, *args, **kwargs):
        if not self.handover_no:
            self.handover_no = build_code('HANDOVER')
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.handover_no


def asset_attachment_upload_to(instance, filename):
    return f'assets/{instance.asset.system_asset_no}/archive/{instance.id}/{filename}'


class AssetAttachment(UUIDModel, TimeStampedModel):
    class Category(models.TextChoices):
        PHOTO = 'photo', '现场照片'
        REPAIR_REPORT = 'repair_report', '维修报告'
        INVOICE = 'invoice', '发票或费用凭证'
        SHIPPING = 'shipping', '物流凭证'
        LOG = 'log', '设备日志'
        OTHER = 'other', '其他资料'

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='attachments')
    maintenance = models.ForeignKey(
        MaintenanceRecord,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attachments',
    )
    handover = models.ForeignKey(
        AssetHandover,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='attachments',
    )
    file = models.FileField(upload_to=asset_attachment_upload_to)
    display_name = models.CharField(max_length=160)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.OTHER)
    note = models.CharField(max_length=255, blank=True, default='')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_asset_attachments',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = '资产归档附件'
        verbose_name_plural = '资产归档附件'

    def __str__(self) -> str:
        return self.display_name


class ImportBatch(UUIDModel, TimeStampedModel):
    class Mode(models.TextChoices):
        INCREMENTAL = 'incremental', '增量导入'
        BASELINE = 'baseline', '全量基准导入'

    class Status(models.TextChoices):
        PREVIEWED = 'previewed', '待复核'
        IMPORTED = 'imported', '已导入'
        FAILED = 'failed', '导入失败'

    source_filename = models.CharField(max_length=255)
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.INCREMENTAL)
    source_sha256 = models.CharField(max_length=64, blank=True, default='')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREVIEWED)
    summary = models.JSONField(default=dict, blank=True)
    imported_asset_count = models.PositiveIntegerField(default=0)
    imported_stock_item_count = models.PositiveIntegerField(default=0)
    imported_network_endpoint_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_import_batches',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = '历史数据导入批次'
        verbose_name_plural = '历史数据导入批次'

    def __str__(self) -> str:
        return f'{self.source_filename} ({self.get_status_display()})'


class ImportIssue(UUIDModel, TimeStampedModel):
    class Severity(models.TextChoices):
        INFO = 'info', '提示'
        WARNING = 'warning', '需复核'
        ERROR = 'error', '无法导入'

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='issues')
    sheet_name = models.CharField(max_length=120)
    row_number = models.PositiveIntegerField()
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.WARNING)
    code = models.CharField(max_length=80)
    message = models.CharField(max_length=255)
    source_data = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    resolution_note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['batch', 'sheet_name', 'row_number', 'severity']
        verbose_name = '导入复核问题'
        verbose_name_plural = '导入复核问题'

    def __str__(self) -> str:
        return f'{self.sheet_name} 第 {self.row_number} 行: {self.message}'


class StockItem(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=50, unique=True)
    unit = models.CharField(max_length=20, default='pcs')
    quantity = models.PositiveIntegerField(default=0)
    safety_stock = models.PositiveIntegerField(default=0)
    supplier = models.CharField('供应商', max_length=120, blank=True, default='')
    supplier_ref = models.ForeignKey(
        Supplier,
        verbose_name='供应商资料',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_items',
    )
    unit_cost = models.DecimalField('单价（元）', max_digits=12, decimal_places=2, null=True, blank=True)
    item_type = models.ForeignKey(ItemType, on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_items')
    source_import_batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_items',
    )
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = '耗材配件'
        verbose_name_plural = '耗材配件'

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if self.supplier_ref_id:
            self.supplier = self.supplier_ref.name
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'supplier'}
        super().save(*args, **kwargs)


class StockItemHolding(UUIDModel, TimeStampedModel):
    """Outstanding quantity borrowed by a system user."""

    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name='user_holdings',
    )
    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='stock_item_holdings',
    )
    quantity = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['stock_item', 'holder'],
                name='unique_stock_item_holder',
            ),
        ]
        ordering = ['holder__username', 'stock_item__name']
        verbose_name = '个人耗材借用余额'
        verbose_name_plural = '个人耗材借用余额'

    def __str__(self) -> str:
        return f'{self.holder.get_username()} - {self.stock_item.name}: {self.quantity}'


class InventoryConversion(UUIDModel, TimeStampedModel):
    class Direction(models.TextChoices):
        STOCK_TO_ASSET = 'stock_to_asset', '耗材配件转单件资产'
        ASSET_TO_STOCK = 'asset_to_stock', '单件资产转耗材配件'

    conversion_no = models.CharField(max_length=80, unique=True)
    direction = models.CharField(max_length=24, choices=Direction.choices)
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT, related_name='inventory_conversions')
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name='inventory_conversions',
    )
    assets = models.ManyToManyField(Asset, related_name='inventory_conversions')
    quantity = models.PositiveIntegerField()
    note = models.CharField(max_length=255, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='inventory_conversions',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = '库存管理方式转换记录'
        verbose_name_plural = '库存管理方式转换记录'

    def __str__(self) -> str:
        return self.conversion_no


class InventoryCheckTask(UUIDModel, TimeStampedModel):
    """A frozen inventory snapshot that is counted and reviewed separately."""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        IN_PROGRESS = 'in_progress', '盘点中'
        PENDING_REVIEW = 'pending_review', '待复核'
        COMPLETED = 'completed', '已完成'
        CANCELED = 'canceled', '已取消'

    check_no = models.CharField(max_length=80, unique=True, default='')
    name = models.CharField(max_length=160)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='inventory_check_tasks')
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_check_tasks',
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_inventory_check_tasks',
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='started_inventory_check_tasks',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_inventory_check_tasks',
    )
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reopened_inventory_check_tasks',
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_note = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '盘点任务'
        verbose_name_plural = '盘点任务'

    def save(self, *args, **kwargs):
        if not self.check_no:
            self.check_no = build_code('CHECK')
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.check_no


class InventoryCheckLine(UUIDModel, TimeStampedModel):
    class Result(models.TextChoices):
        PENDING = 'pending', '待盘点'
        MATCHED = 'matched', '账实相符'
        SHORTAGE = 'shortage', '盘亏'
        SURPLUS = 'surplus', '盘盈'
        LOCATION_MISMATCH = 'location_mismatch', '库位不符'
        STATUS_MISMATCH = 'status_mismatch', '状态不符'

    task = models.ForeignKey(InventoryCheckTask, on_delete=models.CASCADE, related_name='lines')
    item_type = models.ForeignKey(ItemType, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_check_lines')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_check_lines')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_check_lines')
    expected_quantity = models.PositiveIntegerField(default=0)
    counted_quantity = models.PositiveIntegerField(null=True, blank=True)
    expected_asset_code = models.CharField(max_length=80, blank=True, default='')
    expected_serial_number = models.CharField(max_length=120, blank=True, default='')
    expected_status = models.CharField(max_length=32, blank=True, default='')
    expected_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expected_inventory_check_lines',
    )
    expected_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expected_inventory_check_lines',
    )
    observed_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='observed_inventory_check_lines',
    )
    observed_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='observed_inventory_check_lines',
    )
    result = models.CharField(max_length=24, choices=Result.choices, default=Result.PENDING)
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='counted_inventory_check_lines',
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['item_type__name', 'expected_asset_code', 'stock_item__name']
        verbose_name = '盘点明细'
        verbose_name_plural = '盘点明细'
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'asset'],
                condition=models.Q(asset__isnull=False),
                name='unique_inventory_check_asset_line',
            ),
            models.UniqueConstraint(
                fields=['task', 'stock_item'],
                condition=models.Q(stock_item__isnull=False),
                name='unique_inventory_check_stock_line',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.task.check_no} - {self.expected_asset_code or self.stock_item or self.item_type}'


class InventoryCheckScan(UUIDModel, TimeStampedModel):
    class Result(models.TextChoices):
        RESOLVED = 'resolved', '已识别'
        DUPLICATE = 'duplicate', '重复扫描'
        NOT_FOUND = 'not_found', '未找到'
        OUT_OF_SCOPE = 'out_of_scope', '不在盘点范围'

    task = models.ForeignKey(InventoryCheckTask, on_delete=models.CASCADE, related_name='scans')
    line = models.ForeignKey(InventoryCheckLine, on_delete=models.SET_NULL, null=True, blank=True, related_name='scans')
    scanned_value = models.CharField(max_length=255)
    asset = models.ForeignKey(Asset, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_check_scans')
    stock_item = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_check_scans')
    quantity = models.PositiveIntegerField(default=1)
    result = models.CharField(max_length=24, choices=Result.choices)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='inventory_check_scans')
    observed_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_check_scans')
    observed_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='inventory_check_scans')
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '盘点扫描记录'
        verbose_name_plural = '盘点扫描记录'


class InventoryCheckAdjustment(UUIDModel, TimeStampedModel):
    class Action(models.TextChoices):
        SURPLUS = 'surplus', '盘盈调整'
        SHORTAGE = 'shortage', '盘亏调整'
        LOCATION = 'location', '盘点位置调整'
        STATUS = 'status', '盘点状态修正'

    task = models.ForeignKey(InventoryCheckTask, on_delete=models.PROTECT, related_name='adjustments')
    line = models.OneToOneField(InventoryCheckLine, on_delete=models.PROTECT, related_name='adjustment')
    action = models.CharField(max_length=24, choices=Action.choices)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_check_adjustments')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='inventory_check_adjustments')
    before_quantity = models.PositiveIntegerField(null=True, blank=True)
    after_quantity = models.PositiveIntegerField(null=True, blank=True)
    before_status = models.CharField(max_length=32, blank=True, default='')
    after_status = models.CharField(max_length=32, blank=True, default='')
    before_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='before_inventory_check_adjustments')
    after_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='after_inventory_check_adjustments')
    before_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='before_inventory_check_adjustments')
    after_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='after_inventory_check_adjustments')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='inventory_check_adjustments')
    review_note = models.TextField(blank=True, default='')
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '盘点差异调整台账'
        verbose_name_plural = '盘点差异调整台账'
