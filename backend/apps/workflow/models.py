from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounts.models import Department
from apps.common.models import TimeStampedModel, UUIDModel
from apps.common.utils import build_daily_code
from apps.inventory.models import (
    Asset,
    CompositeUnit,
    Customer,
    Location,
    Project,
    StockItem,
    Warehouse,
    WorkOrder,
)


class WorkflowRequest(UUIDModel, TimeStampedModel):
    class RequestType(models.TextChoices):
        BORROW = 'borrow', '借用'
        RETURN = 'return', '归还'
        ISSUE = 'issue', '领用'
        TRANSFER = 'transfer', '调拨'
        REPAIR = 'repair', '维修'
        DAMAGE = 'damage', '报损'
        SALE = 'sale', '出售'
        RETURN_TO_VENDOR = 'return_to_vendor', '退货给供应商'
        SCRAP = 'scrap', '报废'
        PURCHASE_RECEIPT = 'purchase_receipt', '采购入库'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PENDING = 'pending', '待审批'
        REJECTED = 'rejected', '已驳回'
        APPROVED = 'approved', '已通过'
        WAITING_WAREHOUSE = 'waiting_warehouse', '待仓库处理'
        DONE = 'done', '已完成'
        CANCELED = 'canceled', '已取消'
        CLOSED = 'closed', '异常关闭'

    request_no = models.CharField(max_length=80, unique=True, default='')
    inventory_no = models.CharField(max_length=80, unique=True, null=True, blank=True)
    application_date = models.DateField(default=timezone.localdate)
    request_type = models.CharField(max_length=32, choices=RequestType.choices)
    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='workflow_requests')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    reason = models.TextField(blank=True, default='')
    expected_return_date = models.DateField(default=timezone.localdate, null=True, blank=True)
    transaction_date = models.DateField(default=timezone.localdate)
    designated_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='designated_workflow_requests',
    )
    target_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='incoming_requests')
    target_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='incoming_requests')
    recipient_name = models.CharField(max_length=120, blank=True, default='')
    recipient_company = models.CharField(max_length=120, blank=True, default='')
    recipient_phone = models.CharField(max_length=32, blank=True, default='')
    usage_location = models.CharField(max_length=160, blank=True, default='')
    project_code = models.CharField(max_length=80, blank=True, default='')
    work_order_no = models.CharField(max_length=80, blank=True, default='')
    customer_ref = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_requests',
    )
    project_ref = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_requests',
    )
    work_order_ref = models.ForeignKey(
        WorkOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_requests',
    )
    composite_unit = models.ForeignKey(
        CompositeUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_requests',
        verbose_name='成品设备',
    )
    is_quick_process = models.BooleanField(default=False)
    contact_source = models.CharField(max_length=32, blank=True, default='')
    is_hidden_by_applicant = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '申请单审计记录'
        verbose_name_plural = '申请单审计记录'
        indexes = [
            models.Index(fields=['application_date']),
            models.Index(fields=['transaction_date']),
            models.Index(fields=['status', 'application_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.request_no:
            self.request_no = build_daily_code('REQ', self.application_date)
        if self.project_ref_id:
            self.project_code = self.project_ref.code
        if self.work_order_ref_id:
            self.work_order_no = self.work_order_ref.code
        if self.customer_ref_id and not self.recipient_company:
            self.recipient_company = self.customer_ref.name
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'project_code', 'work_order_no', 'recipient_company'}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.request_no


class PurchaseRequest(UUIDModel, TimeStampedModel):
    """仓管根据安全库存生成的采购申请草稿。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '采购草稿'
        PENDING_APPROVAL = 'pending_approval', '待采购审批'
        APPROVED = 'approved', '采购已通过'
        REJECTED = 'rejected', '采购已驳回'
        ARCHIVED = 'archived', '已归档'

    purchase_no = models.CharField('采购草稿编号', max_length=80, unique=True, default='')
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_request_drafts',
    )
    application_date = models.DateField('申请日期', default=timezone.localdate)
    status = models.CharField('状态', max_length=16, choices=Status.choices, default=Status.DRAFT)
    reason = models.TextField('采购原因', blank=True, default='')
    note = models.TextField('补充说明', blank=True, default='')
    designated_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='指定审批人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='designated_purchase_requests',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='审批人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_purchase_requests',
    )
    submitted_at = models.DateTimeField('提交审批时间', null=True, blank=True)
    decided_at = models.DateTimeField('审批处理时间', null=True, blank=True)
    approval_comment = models.TextField('审批意见', blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '采购申请草稿'
        verbose_name_plural = '采购申请草稿'
        indexes = [
            models.Index(fields=['application_date']),
            models.Index(fields=['status', 'application_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.purchase_no:
            self.purchase_no = build_daily_code('PUR', self.application_date)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.purchase_no


class PurchaseRequestLine(UUIDModel, TimeStampedModel):
    """采购草稿的物品快照，来源可以是资产类型或耗材配件。"""

    purchase_request = models.ForeignKey(
        PurchaseRequest,
        verbose_name='采购草稿',
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item_type = models.ForeignKey(
        'inventory.ItemType',
        verbose_name='资产物品类型来源',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_request_lines',
    )
    stock_item = models.ForeignKey(
        StockItem,
        verbose_name='耗材配件来源',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_request_lines',
    )
    name_snapshot = models.CharField('物品名称', max_length=120)
    code_snapshot = models.CharField('物品编码', max_length=80, blank=True, default='')
    management_mode = models.CharField('管理方式', max_length=32, default='')
    quantity = models.PositiveIntegerField('计划采购数量', default=1)
    unit = models.CharField('单位', max_length=20, default='个')
    reference_unit_cost = models.DecimalField('参考单价（元）', max_digits=12, decimal_places=2, null=True, blank=True)
    reference_amount = models.DecimalField('参考金额（元）', max_digits=12, decimal_places=2, null=True, blank=True)
    supplier_snapshot = models.CharField('供应商快照', max_length=120, blank=True, default='')
    warehouse_snapshot = models.CharField('仓库快照', max_length=255, blank=True, default='')
    location_snapshot = models.CharField('库位快照', max_length=255, blank=True, default='')
    note = models.CharField('明细备注', max_length=255, blank=True, default='')

    class Meta:
        ordering = ['created_at']
        verbose_name = '采购申请明细'
        verbose_name_plural = '采购申请明细'
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(item_type__isnull=False) | models.Q(stock_item__isnull=False)),
                name='purchase_line_has_source',
            ),
            models.CheckConstraint(
                condition=~(models.Q(item_type__isnull=False) & models.Q(stock_item__isnull=False)),
                name='purchase_line_one_source',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.purchase_request.purchase_no} - {self.name_snapshot}'


class PurchaseOrder(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', '待下达'
        ORDERED = 'ordered', '已下达'
        PARTIAL_RECEIVED = 'partial_received', '部分到货'
        RECEIVED = 'received', '已全部到货'
        CANCELED = 'canceled', '已取消'

    order_no = models.CharField('采购订单编号', max_length=80, unique=True, default='')
    purchase_request = models.OneToOneField(
        PurchaseRequest,
        verbose_name='采购申请',
        on_delete=models.PROTECT,
        related_name='purchase_order',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='创建人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_purchase_orders',
    )
    order_date = models.DateField('下单日期', default=timezone.localdate)
    expected_delivery_date = models.DateField('预计到货日期', null=True, blank=True)
    status = models.CharField('状态', max_length=24, choices=Status.choices, default=Status.DRAFT)
    note = models.TextField('订单说明', blank=True, default='')
    ordered_at = models.DateTimeField('下达时间', null=True, blank=True)
    canceled_at = models.DateTimeField('取消时间', null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '采购订单'
        verbose_name_plural = '采购订单'
        indexes = [
            models.Index(fields=['order_date']),
            models.Index(fields=['status', 'order_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.order_no:
            self.order_no = build_daily_code('PO', self.order_date)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.order_no


class PurchaseOrderLine(UUIDModel, TimeStampedModel):
    order = models.ForeignKey(PurchaseOrder, verbose_name='采购订单', on_delete=models.CASCADE, related_name='lines')
    purchase_request_line = models.OneToOneField(
        PurchaseRequestLine,
        verbose_name='采购申请明细',
        on_delete=models.PROTECT,
        related_name='purchase_order_line',
    )
    item_type = models.ForeignKey(
        'inventory.ItemType', verbose_name='资产物品类型来源', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='purchase_order_lines',
    )
    stock_item = models.ForeignKey(
        StockItem, verbose_name='耗材配件来源', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='purchase_order_lines',
    )
    name_snapshot = models.CharField('物品名称', max_length=120)
    code_snapshot = models.CharField('物品编码', max_length=80, blank=True, default='')
    management_mode = models.CharField('管理方式', max_length=32, default='')
    ordered_quantity = models.PositiveIntegerField('订购数量', default=1)
    received_quantity = models.PositiveIntegerField('已到货数量', default=0)
    unit = models.CharField('单位', max_length=20, default='个')
    unit_cost = models.DecimalField('采购单价（元）', max_digits=12, decimal_places=2, null=True, blank=True)
    supplier_snapshot = models.CharField('供应商快照', max_length=120, blank=True, default='')
    note = models.CharField('明细备注', max_length=255, blank=True, default='')

    class Meta:
        ordering = ['created_at']
        verbose_name = '采购订单明细'
        verbose_name_plural = '采购订单明细'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(received_quantity__lte=models.F('ordered_quantity')),
                name='purchase_order_received_lte_ordered',
            ),
        ]

    @property
    def remaining_quantity(self):
        return max(self.ordered_quantity - self.received_quantity, 0)

    def __str__(self) -> str:
        return f'{self.order.order_no} - {self.name_snapshot}'


class PurchaseReceipt(UUIDModel, TimeStampedModel):
    receipt_no = models.CharField('到货登记编号', max_length=80, unique=True, default='')
    order = models.ForeignKey(PurchaseOrder, verbose_name='采购订单', on_delete=models.PROTECT, related_name='receipts')
    inventory_request = models.OneToOneField(
        WorkflowRequest,
        verbose_name='采购入库单',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='purchase_receipt',
    )
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='登记人', on_delete=models.PROTECT, related_name='purchase_receipts')
    received_date = models.DateField('到货日期', default=timezone.localdate)
    note = models.TextField('到货说明', blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '采购到货登记'
        verbose_name_plural = '采购到货登记'

    def save(self, *args, **kwargs):
        if not self.receipt_no:
            self.receipt_no = build_daily_code('GRN', self.received_date)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.receipt_no


class PurchaseReceiptLine(UUIDModel, TimeStampedModel):
    receipt = models.ForeignKey(PurchaseReceipt, verbose_name='到货登记', on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(PurchaseOrderLine, verbose_name='采购订单明细', on_delete=models.PROTECT, related_name='receipt_lines')
    received_quantity = models.PositiveIntegerField('本次到货数量', default=1)
    destination_warehouse = models.ForeignKey(Warehouse, verbose_name='入库仓库', on_delete=models.PROTECT)
    destination_location = models.ForeignKey(Location, verbose_name='入库库位', on_delete=models.PROTECT)
    created_asset_ids = models.JSONField('本次生成资产编号', default=list, blank=True)
    note = models.CharField('明细备注', max_length=255, blank=True, default='')

    class Meta:
        ordering = ['created_at']
        verbose_name = '采购到货明细'
        verbose_name_plural = '采购到货明细'

    def __str__(self) -> str:
        return f'{self.receipt.receipt_no} - {self.order_line.name_snapshot}'


class WorkflowRequestLine(UUIDModel, TimeStampedModel):
    request = models.ForeignKey(WorkflowRequest, on_delete=models.CASCADE, related_name='lines')
    asset = models.ForeignKey(Asset, on_delete=models.SET_NULL, null=True, blank=True)
    stock_item = models.ForeignKey(StockItem, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        verbose_name = '申请物品行（随申请单）'
        verbose_name_plural = '申请物品行（随申请单）'

    def __str__(self) -> str:
        return f'{self.request.request_no}-{self.quantity}'


class WorkflowRequestTemplate(UUIDModel, TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workflow_request_templates',
    )
    name = models.CharField(max_length=80)
    request_type = models.CharField(max_length=32, choices=WorkflowRequest.RequestType.choices)
    recipient_name = models.CharField(max_length=120, blank=True, default='')
    recipient_company = models.CharField(max_length=120, blank=True, default='')
    recipient_phone = models.CharField(max_length=32, blank=True, default='')
    usage_location = models.CharField(max_length=160, blank=True, default='')
    project_code = models.CharField(max_length=80, blank=True, default='')
    work_order_no = models.CharField(max_length=80, blank=True, default='')
    customer_ref = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='workflow_request_templates')
    project_ref = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='workflow_request_templates')
    work_order_ref = models.ForeignKey(WorkOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name='workflow_request_templates')
    reason = models.TextField(blank=True, default='')
    default_borrow_days = models.PositiveSmallIntegerField(default=7)
    designated_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='designated_request_templates',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'name'], name='unique_workflow_template_owner_name'),
        ]
        ordering = ['name']
        verbose_name = '常用申请模板'
        verbose_name_plural = '常用申请模板'

    def __str__(self) -> str:
        return f'{self.owner.get_username()} - {self.name}'

    def save(self, *args, **kwargs):
        if self.project_ref_id:
            self.project_code = self.project_ref.code
        if self.work_order_ref_id:
            self.work_order_no = self.work_order_ref.code
        if self.customer_ref_id and not self.recipient_company:
            self.recipient_company = self.customer_ref.name
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = set(update_fields) | {'project_code', 'work_order_no', 'recipient_company'}
        super().save(*args, **kwargs)


def sync_asset_availability_states(asset_ids):
    asset_ids = {asset_id for asset_id in asset_ids if asset_id}
    if not asset_ids:
        return
    active_asset_ids = set(
        Asset.objects.filter(
            pk__in=asset_ids,
            reservations__status='active',
        ).values_list('pk', flat=True)
    )
    for asset in Asset.objects.filter(pk__in=asset_ids).only('pk', 'status', 'availability_state'):
        dimensions = Asset.LEGACY_STATUS_DIMENSIONS.get(asset.status, {})
        expected_state = (
            Asset.AvailabilityState.RESERVED
            if asset.pk in active_asset_ids
            else dimensions.get('availability_state', Asset.AvailabilityState.UNAVAILABLE)
        )
        if asset.availability_state != expected_state:
            Asset.objects.filter(pk=asset.pk).update(
                availability_state=expected_state,
                updated_at=timezone.now(),
            )


class InventoryReservation(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', '预占中'
        RELEASED = 'released', '已释放'
        CONSUMED = 'consumed', '已执行'

    request_line = models.OneToOneField(
        WorkflowRequestLine,
        on_delete=models.PROTECT,
        related_name='reservation',
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reservations',
    )
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reservations',
    )
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='released_inventory_reservations',
    )
    release_reason = models.CharField(max_length=255, blank=True, default='')
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(asset__isnull=False, stock_item__isnull=True)
                    | models.Q(asset__isnull=True, stock_item__isnull=False)
                ),
                name='reservation_exactly_one_inventory_target',
            ),
            models.UniqueConstraint(
                fields=['asset'],
                condition=models.Q(status='active', asset__isnull=False),
                name='unique_active_asset_reservation',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '库存预占记录'
        verbose_name_plural = '库存预占记录'

    def __str__(self) -> str:
        return f'{self.request_line.request.request_no} - {self.get_status_display()}'

    def save(self, *args, **kwargs):
        previous_asset_id = None
        if self.pk:
            previous_asset_id = type(self).objects.filter(pk=self.pk).values_list('asset_id', flat=True).first()
        super().save(*args, **kwargs)
        sync_asset_availability_states({previous_asset_id, self.asset_id})

    def delete(self, *args, **kwargs):
        asset_id = self.asset_id
        result = super().delete(*args, **kwargs)
        sync_asset_availability_states({asset_id})
        return result


class StockItemLoan(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'active', '借用中'
        RETURNED = 'returned', '已归还'

    borrower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='stock_item_loans',
    )
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name='loans')
    source_line = models.OneToOneField(
        WorkflowRequestLine,
        on_delete=models.PROTECT,
        related_name='stock_loan',
    )
    original_quantity = models.PositiveIntegerField()
    outstanding_quantity = models.PositiveIntegerField()
    expected_return_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['expected_return_date', 'created_at']
        verbose_name = '耗材借用明细'
        verbose_name_plural = '耗材借用明细'

    def __str__(self) -> str:
        return f'{self.borrower.get_username()} - {self.stock_item.name}: {self.outstanding_quantity}'


class LoanExtensionRequest(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待审批'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'
        CANCELED = 'canceled', '已撤回'

    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='loan_extension_requests',
    )
    source_request = models.ForeignKey(
        WorkflowRequest,
        on_delete=models.PROTECT,
        related_name='loan_extension_requests',
    )
    original_return_date = models.DateField()
    requested_return_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_loan_extension_requests',
    )
    review_comment = models.TextField(blank=True, default='')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['source_request'],
                condition=models.Q(status='pending'),
                name='unique_pending_extension_per_source_request',
            ),
        ]
        verbose_name = '借用延期申请'
        verbose_name_plural = '借用延期申请'

    def __str__(self) -> str:
        return f'{self.source_request.request_no} 延期至 {self.requested_return_date:%Y-%m-%d}'


class ReturnReminderLog(UUIDModel, TimeStampedModel):
    business_key = models.CharField(max_length=180, unique=True)
    reminder_date = models.DateField(default=timezone.localdate)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='return_reminder_logs',
    )
    request_line = models.ForeignKey(
        WorkflowRequestLine,
        on_delete=models.PROTECT,
        related_name='return_reminder_logs',
    )
    stage = models.CharField(max_length=16)

    class Meta:
        ordering = ['-reminder_date', '-created_at']
        verbose_name = '归还提醒发送记录'
        verbose_name_plural = '归还提醒发送记录'

    def __str__(self) -> str:
        return self.business_key


class ApprovalTask(UUIDModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', '待处理'
        APPROVED = 'approved', '已通过'
        REJECTED = 'rejected', '已驳回'
        CANCELED = 'canceled', '已取消'

    request = models.ForeignKey(WorkflowRequest, on_delete=models.CASCADE, related_name='approval_tasks')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_approval_tasks')
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='approval_tasks')
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    comment = models.TextField(blank=True, default='')
    acted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '审批任务审计记录'
        verbose_name_plural = '审批任务审计记录'


class ApprovalLog(UUIDModel, TimeStampedModel):
    ACTION_LABELS = {
        'submit': '提交申请',
        'approve': '同意申请',
        'reject': '拒绝申请',
        'process': '执行出入库',
        'cancel': '取消申请',
        'withdraw': '申请人撤回',
        'hide': '申请人删除记录',
        'reservation_expire': '预约超时关闭',
        'reservation_close': '人工关闭并释放预约',
        'reservation_extend': '延长预约有效期',
        'loan_extension_submit': '提交借用延期',
        'loan_extension_approve': '同意借用延期',
        'loan_extension_reject': '拒绝借用延期',
    }

    request = models.ForeignKey(WorkflowRequest, on_delete=models.CASCADE, related_name='approval_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    comment = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = '流程操作日志'
        verbose_name_plural = '流程操作日志'

    @property
    def action_label(self):
        return self.ACTION_LABELS.get(self.action, self.action)


class InventoryTransaction(UUIDModel, TimeStampedModel):
    request = models.ForeignKey(WorkflowRequest, on_delete=models.PROTECT, related_name='inventory_transactions')
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, null=True, blank=True, related_name='transactions')
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='transactions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=32, choices=WorkflowRequest.RequestType.choices)
    business_date = models.DateField(default=timezone.localdate)
    quantity = models.PositiveIntegerField(default=1)
    asset_status_before = models.CharField(max_length=32, blank=True, default='')
    asset_status_after = models.CharField(max_length=32, blank=True, default='')
    stock_quantity_before = models.PositiveIntegerField(null=True, blank=True)
    stock_quantity_after = models.PositiveIntegerField(null=True, blank=True)
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='outgoing_transactions')
    target_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='incoming_transactions')
    holder_name = models.CharField(max_length=120, blank=True, default='')
    holder_company = models.CharField(max_length=120, blank=True, default='')
    holder_phone = models.CharField(max_length=32, blank=True, default='')
    project_code = models.CharField(max_length=80, blank=True, default='')
    work_order_no = models.CharField(max_length=80, blank=True, default='')
    cost_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    comment = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '库存动作台账'
        verbose_name_plural = '库存动作台账'
        indexes = [
            models.Index(fields=['business_date']),
            models.Index(fields=['action', 'business_date']),
        ]

    def __str__(self) -> str:
        return f'{self.get_action_display()} {self.created_at:%Y-%m-%d %H:%M}'


class OfflineOperation(UUIDModel, TimeStampedModel):
    class OperationType(models.TextChoices):
        QUICK_INVENTORY = 'quick_inventory', '离线快速出入库'

    class Status(models.TextChoices):
        PENDING = 'pending', '待复核'
        APPLIED = 'applied', '已执行'
        CONFLICT = 'conflict', '存在冲突'
        REJECTED = 'rejected', '已拒绝'

    client_operation_id = models.UUIDField('客户端操作编号', unique=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='登记人',
        on_delete=models.PROTECT,
        related_name='offline_operations',
    )
    operation_type = models.CharField(
        '操作类型',
        max_length=32,
        choices=OperationType.choices,
        default=OperationType.QUICK_INVENTORY,
    )
    status = models.CharField('状态', max_length=16, choices=Status.choices, default=Status.PENDING)
    payload = models.JSONField('离线数据', default=dict)
    conflict_details = models.JSONField('冲突详情', default=dict, blank=True)
    error_message = models.TextField('错误说明', blank=True, default='')
    client_created_at = models.DateTimeField('客户端登记时间')
    received_at = models.DateTimeField('服务器接收时间', default=timezone.now)
    applied_at = models.DateTimeField('执行时间', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='复核人',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_offline_operations',
    )
    reviewed_at = models.DateTimeField('复核时间', null=True, blank=True)
    retry_count = models.PositiveIntegerField('同步/执行次数', default=0)
    resulting_request = models.OneToOneField(
        WorkflowRequest,
        verbose_name='生成的出入库单',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='offline_operation',
    )

    class Meta:
        ordering = ['-client_created_at', '-created_at']
        verbose_name = '离线操作'
        verbose_name_plural = '离线操作'
        indexes = [
            models.Index(fields=['status', 'received_at']),
            models.Index(fields=['operator', 'client_created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.get_operation_type_display()} - {self.get_status_display()}'
