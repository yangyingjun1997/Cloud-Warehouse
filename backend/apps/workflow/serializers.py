from rest_framework import serializers

from .models import ApprovalLog, ApprovalTask, InventoryTransaction, WorkflowRequest, WorkflowRequestLine
from .services import is_operator


class WorkflowRequestLineSerializer(serializers.ModelSerializer):
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    stock_item_name = serializers.CharField(source='stock_item.name', read_only=True)
    asset_status = serializers.CharField(source='asset.status', read_only=True)
    warehouse_name = serializers.SerializerMethodField()
    location_code = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowRequestLine
        fields = [
            'id',
            'request',
            'asset',
            'asset_code',
            'asset_name',
            'asset_status',
            'stock_item',
            'stock_item_name',
            'warehouse_name',
            'location_code',
            'quantity',
            'note',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {'request': {'required': False}}

    def _is_operator(self):
        request = self.context.get('request')
        return bool(request and is_operator(request.user))

    def get_warehouse_name(self, obj):
        if not self._is_operator():
            return ''
        if obj.asset_id:
            return obj.asset.warehouse.name if obj.asset.warehouse_id else ''
        return obj.stock_item.warehouse.name if obj.stock_item_id and obj.stock_item.warehouse_id else ''

    def get_location_code(self, obj):
        if not self._is_operator():
            return ''
        if obj.asset_id:
            return obj.asset.location.code if obj.asset.location_id else ''
        return obj.stock_item.location.code if obj.stock_item_id and obj.stock_item.location_id else ''

    def validate(self, attrs):
        if bool(attrs.get('asset')) == bool(attrs.get('stock_item')):
            raise serializers.ValidationError('每条明细必须且只能选择资产或耗材。')
        return attrs


class ApprovalTaskSerializer(serializers.ModelSerializer):
    request_no = serializers.CharField(source='request.request_no', read_only=True)
    inventory_no = serializers.CharField(source='request.inventory_no', read_only=True)
    application_date = serializers.DateField(source='request.application_date', read_only=True)
    expected_return_date = serializers.DateField(source='request.expected_return_date', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.username', read_only=True)
    approver_name = serializers.CharField(source='approver.username', read_only=True)

    class Meta:
        model = ApprovalTask
        fields = [
            'id', 'request', 'request_no', 'inventory_no', 'application_date', 'expected_return_date',
            'assigned_to', 'assigned_to_name', 'approver', 'approver_name',
            'status', 'comment', 'acted_at', 'created_at', 'updated_at',
        ]


class ApprovalLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(read_only=True)
    actor_name = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = ApprovalLog
        fields = ['id', 'request', 'actor', 'actor_name', 'action', 'action_label', 'comment', 'created_at', 'updated_at']


class InventoryTransactionSerializer(serializers.ModelSerializer):
    inventory_no = serializers.CharField(source='request.inventory_no', read_only=True)
    request_no = serializers.CharField(source='request.request_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    stock_item_name = serializers.CharField(source='stock_item.name', read_only=True)
    actor_name = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = InventoryTransaction
        fields = [
            'id', 'inventory_no', 'request', 'request_no', 'asset', 'asset_code', 'stock_item', 'stock_item_name',
            'actor', 'actor_name', 'action', 'quantity', 'asset_status_before',
            'asset_status_after', 'stock_quantity_before', 'stock_quantity_after',
            'source_warehouse', 'target_warehouse', 'holder_name', 'holder_company',
            'holder_phone', 'project_code', 'work_order_no', 'cost_amount', 'comment', 'business_date', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WorkflowRequestSerializer(serializers.ModelSerializer):
    lines = WorkflowRequestLineSerializer(many=True, required=False)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    designated_approver_name = serializers.CharField(source='designated_approver.username', read_only=True)
    target_warehouse_name = serializers.CharField(source='target_warehouse.name', read_only=True)
    target_location_code = serializers.CharField(source='target_location.code', read_only=True)
    approval_tasks = ApprovalTaskSerializer(many=True, read_only=True)
    approval_logs = ApprovalLogSerializer(many=True, read_only=True)

    class Meta:
        model = WorkflowRequest
        fields = [
            'id',
            'request_no',
            'inventory_no',
            'application_date',
            'request_type',
            'applicant',
            'applicant_name',
            'department',
            'department_name',
            'status',
            'reason',
            'expected_return_date',
            'transaction_date',
            'designated_approver',
            'designated_approver_name',
            'target_warehouse',
            'target_warehouse_name',
            'target_location',
            'target_location_code',
            'recipient_name',
            'recipient_company',
            'recipient_phone',
            'usage_location',
            'customer_ref',
            'project_ref',
            'work_order_ref',
            'project_code',
            'work_order_no',
            'is_quick_process',
            'contact_source',
            'lines',
            'approval_tasks',
            'approval_logs',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['request_no', 'inventory_no', 'applicant', 'department', 'status', 'is_quick_process', 'contact_source']

    def create(self, validated_data):
        lines = validated_data.pop('lines', [])
        request_obj = WorkflowRequest.objects.create(**validated_data)
        WorkflowRequestLine.objects.bulk_create([
            WorkflowRequestLine(request=request_obj, **line_data)
            for line_data in lines
        ])
        return request_obj

    def validate(self, attrs):
        request_type = attrs.get('request_type', getattr(self.instance, 'request_type', None))
        target_warehouse = attrs.get('target_warehouse', getattr(self.instance, 'target_warehouse', None))
        target_location = attrs.get('target_location', getattr(self.instance, 'target_location', None))
        if request_type == WorkflowRequest.RequestType.TRANSFER and not target_warehouse:
            raise serializers.ValidationError({'target_warehouse': '调拨申请必须选择目标仓库。'})
        if target_location and target_warehouse and target_location.warehouse_id != target_warehouse.id:
            raise serializers.ValidationError({'target_location': '目标库位不属于目标仓库。'})
        return attrs
