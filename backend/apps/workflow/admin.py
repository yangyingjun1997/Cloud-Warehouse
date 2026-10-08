from django.contrib import admin

from apps.common.admin import ChineseModelAdmin
from .models import (
    ApprovalLog,
    ApprovalTask,
    InventoryReservation,
    InventoryTransaction,
    LoanExtensionRequest,
    OfflineOperation,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    ReturnReminderLog,
    StockItemLoan,
    WorkflowRequest,
    WorkflowRequestLine,
    WorkflowRequestTemplate,
)


@admin.register(OfflineOperation)
class OfflineOperationAdmin(ChineseModelAdmin):
    list_display = ('client_created_at', 'operator', 'operation_type', 'status', 'reviewed_by', 'applied_at')
    list_filter = ('status', 'operation_type', 'client_created_at')
    search_fields = ('client_operation_id', 'operator__username', 'error_message', 'resulting_request__inventory_no')
    readonly_fields = [field.name for field in OfflineOperation._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class WorkflowRequestLineInline(admin.TabularInline):
    model = WorkflowRequestLine
    extra = 0
    fields = ('asset', 'stock_item', 'quantity', 'note')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PurchaseRequestLineInline(admin.TabularInline):
    model = PurchaseRequestLine
    extra = 0
    fields = (
        'name_snapshot', 'code_snapshot', 'management_mode', 'quantity', 'unit',
        'reference_unit_cost', 'reference_amount', 'supplier_snapshot',
        'warehouse_snapshot', 'location_snapshot', 'note',
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(ChineseModelAdmin):
    field_labels = {
        'purchase_no': '采购草稿编号', 'applicant': '创建人', 'application_date': '申请日期',
        'status': '状态', 'reason': '采购原因', 'note': '补充说明', 'designated_approver': '指定审批人',
        'approved_by': '审批人', 'submitted_at': '提交审批时间', 'decided_at': '审批处理时间',
        'approval_comment': '审批意见',
    }
    list_display = ('purchase_no', 'application_date', 'applicant', 'status', 'line_count', 'created_at')
    list_filter = ('status', 'application_date')
    search_fields = ('purchase_no', 'applicant__username', 'reason', 'note', 'lines__name_snapshot', 'lines__code_snapshot')
    inlines = [PurchaseRequestLineInline]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='物品明细数')
    def line_count(self, obj):
        return obj.lines.count()


@admin.register(PurchaseRequestLine)
class PurchaseRequestLineAdmin(ChineseModelAdmin):
    field_labels = {
        'purchase_request': '采购草稿', 'name_snapshot': '物品名称', 'code_snapshot': '物品编码',
        'management_mode': '管理方式', 'quantity': '计划采购数量', 'unit': '单位',
        'reference_unit_cost': '参考单价（元）', 'reference_amount': '参考金额（元）',
        'supplier_snapshot': '供应商快照', 'warehouse_snapshot': '仓库快照',
        'location_snapshot': '库位快照', 'note': '明细备注',
    }
    list_display = ('purchase_request', 'name_snapshot', 'management_mode', 'quantity', 'unit', 'supplier_snapshot')
    search_fields = ('purchase_request__purchase_no', 'name_snapshot', 'code_snapshot', 'supplier_snapshot')
    readonly_fields = [field.name for field in PurchaseRequestLine._meta.fields]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0
    fields = ('name_snapshot', 'code_snapshot', 'management_mode', 'ordered_quantity', 'received_quantity', 'unit', 'unit_cost', 'supplier_snapshot', 'note')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ChineseModelAdmin):
    field_labels = {
        'order_no': '采购订单编号', 'purchase_request': '采购申请', 'created_by': '创建人',
        'order_date': '下单日期', 'expected_delivery_date': '预计到货日期', 'status': '状态',
        'note': '订单说明', 'ordered_at': '下达时间', 'canceled_at': '取消时间',
    }
    list_display = ('order_no', 'purchase_request', 'order_date', 'expected_delivery_date', 'status', 'created_by')
    list_filter = ('status', 'order_date', 'expected_delivery_date')
    search_fields = ('order_no', 'purchase_request__purchase_no', 'created_by__username', 'lines__name_snapshot', 'lines__code_snapshot')
    readonly_fields = [field.name for field in PurchaseOrder._meta.fields]
    inlines = [PurchaseOrderLineInline]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(ChineseModelAdmin):
    field_labels = {
        'order': '采购订单', 'purchase_request_line': '采购申请明细', 'name_snapshot': '物品名称',
        'code_snapshot': '物品编码', 'management_mode': '管理方式', 'ordered_quantity': '订购数量',
        'received_quantity': '已到货数量', 'unit': '单位', 'unit_cost': '采购单价（元）',
        'supplier_snapshot': '供应商快照', 'note': '明细备注',
    }
    list_display = ('order', 'name_snapshot', 'ordered_quantity', 'received_quantity', 'unit_cost')
    search_fields = ('order__order_no', 'name_snapshot', 'code_snapshot', 'supplier_snapshot')
    readonly_fields = [field.name for field in PurchaseOrderLine._meta.fields]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseReceipt)
class PurchaseReceiptAdmin(ChineseModelAdmin):
    field_labels = {'receipt_no': '到货登记编号', 'order': '采购订单', 'inventory_request': '采购入库单', 'received_by': '登记人', 'received_date': '到货日期', 'note': '到货说明'}
    list_display = ('receipt_no', 'order', 'inventory_request', 'received_date', 'received_by')
    list_filter = ('received_date',)
    search_fields = ('receipt_no', 'order__order_no', 'inventory_request__inventory_no', 'received_by__username')
    readonly_fields = [field.name for field in PurchaseReceipt._meta.fields]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseReceiptLine)
class PurchaseReceiptLineAdmin(ChineseModelAdmin):
    field_labels = {'receipt': '到货登记', 'order_line': '采购订单明细', 'received_quantity': '本次到货数量', 'destination_warehouse': '入库仓库', 'destination_location': '入库库位', 'created_asset_ids': '本次生成资产编号', 'note': '明细备注'}
    list_display = ('receipt', 'order_line', 'received_quantity', 'destination_warehouse', 'destination_location')
    readonly_fields = [field.name for field in PurchaseReceiptLine._meta.fields]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowRequest)
class WorkflowRequestAdmin(ChineseModelAdmin):
    field_labels = {'request_no': '申请单号', 'inventory_no': '出入库单号', 'application_date': '申请日期', 'transaction_date': '出入库日期', 'request_type': '申请类型', 'applicant': '申请人', 'department': '申请部门', 'status': '申请状态', 'reason': '申请原因', 'expected_return_date': '预计归还日期', 'recipient_name': '实际使用人/接收人', 'recipient_company': '使用部门/单位', 'recipient_phone': '联系电话', 'usage_location': '使用地点', 'designated_approver': '指定审批人'}
    list_display = ('request_no', 'inventory_no', 'application_date', 'expected_return_date', 'request_type', 'applicant', 'department', 'line_count', 'status', 'created_at')
    search_fields = ('request_no', 'inventory_no', 'applicant__username', 'reason', 'recipient_name', 'project_code', 'work_order_no')
    list_filter = ('request_type', 'status', 'department', 'application_date', 'expected_return_date')
    inlines = [WorkflowRequestLineInline]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='物品明细数')
    def line_count(self, obj):
        return obj.lines.count()


@admin.register(WorkflowRequestLine)
class WorkflowRequestLineAdmin(ChineseModelAdmin):
    field_labels = {'request': '出入库申请单', 'asset': '资产', 'stock_item': '耗材配件', 'quantity': '数量', 'note': '物品明细备注'}
    list_display = ('request', 'asset', 'stock_item', 'quantity')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApprovalTask)
class ApprovalTaskAdmin(ChineseModelAdmin):
    field_labels = {'request': '申请单', 'approver': '审批人', 'status': '审批状态', 'comment': '审批意见', 'acted_at': '处理时间'}
    list_display = ('request', 'request_application_date', 'approver', 'status', 'acted_at')
    list_filter = ('status', 'acted_at')

    def has_module_permission(self, request):
        return request.user.is_superuser

    @admin.display(description='申请日期', ordering='request__application_date')
    def request_application_date(self, obj):
        return obj.request.application_date

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApprovalLog)
class ApprovalLogAdmin(ChineseModelAdmin):
    field_labels = {'request': '申请单', 'actor': '操作人', 'action': '操作动作', 'comment': '操作说明'}
    list_display = ('request', 'actor', 'action', 'created_at')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(ChineseModelAdmin):
    list_display = ('business_date', 'inventory_document_no', 'request', 'action', 'asset', 'stock_item', 'quantity', 'actor', 'source_warehouse', 'target_warehouse')
    list_filter = ('action', 'business_date', 'source_warehouse', 'target_warehouse')
    search_fields = ('request__inventory_no', 'request__request_no', 'asset__asset_code', 'stock_item__name', 'holder_name', 'holder_company', 'project_code', 'work_order_no')
    readonly_fields = ('request', 'asset', 'stock_item', 'actor', 'action', 'quantity', 'business_date', 'asset_status_before', 'asset_status_after', 'stock_quantity_before', 'stock_quantity_after', 'source_warehouse', 'target_warehouse', 'holder_name', 'holder_company', 'holder_phone', 'comment')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='出入库单号', ordering='request__inventory_no')
    def inventory_document_no(self, obj):
        return obj.request.inventory_no or '-'


@admin.register(InventoryReservation)
class InventoryReservationAdmin(ChineseModelAdmin):
    list_display = ('request_line', 'asset', 'stock_item', 'quantity', 'status', 'expires_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('request_line__request__request_no', 'asset__asset_code', 'stock_item__code', 'stock_item__name')
    readonly_fields = ('request_line', 'asset', 'stock_item', 'quantity', 'status', 'expires_at', 'released_at', 'released_by', 'release_reason', 'consumed_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockItemLoan)
class StockItemLoanAdmin(ChineseModelAdmin):
    list_display = ('borrower', 'stock_item', 'outstanding_quantity', 'expected_return_date', 'status')
    list_filter = ('status', 'expected_return_date')
    search_fields = ('borrower__username', 'stock_item__name', 'stock_item__code', 'source_line__request__request_no')
    readonly_fields = ('borrower', 'stock_item', 'source_line', 'original_quantity', 'outstanding_quantity', 'expected_return_date', 'status', 'returned_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoanExtensionRequest)
class LoanExtensionRequestAdmin(ChineseModelAdmin):
    list_display = ('source_request', 'applicant', 'original_return_date', 'requested_return_date', 'status', 'reviewed_by', 'created_at')
    list_filter = ('status', 'requested_return_date')
    search_fields = ('source_request__request_no', 'applicant__username', 'reason', 'review_comment')
    readonly_fields = (
        'applicant', 'source_request', 'original_return_date', 'requested_return_date',
        'reason', 'status', 'reviewed_by', 'review_comment', 'reviewed_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowRequestTemplate)
class WorkflowRequestTemplateAdmin(ChineseModelAdmin):
    list_display = ('name', 'owner', 'request_type', 'default_borrow_days', 'updated_at')
    search_fields = ('name', 'owner__username', 'recipient_name', 'project_code', 'work_order_no')
    list_filter = ('request_type',)


@admin.register(ReturnReminderLog)
class ReturnReminderLogAdmin(ChineseModelAdmin):
    list_display = ('reminder_date', 'recipient', 'request_line', 'stage', 'created_at')
    list_filter = ('stage', 'reminder_date')
    search_fields = ('recipient__username', 'request_line__request__request_no', 'business_key')
    readonly_fields = ('business_key', 'reminder_date', 'recipient', 'request_line', 'stage')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
