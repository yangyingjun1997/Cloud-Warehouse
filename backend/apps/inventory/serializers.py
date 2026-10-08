from rest_framework import serializers

from .models import (
    Asset,
    AssetLifecycleEvent,
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


class BusinessReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ['id', 'code', 'name', 'contact_name', 'phone', 'notes', 'is_active', 'created_at', 'updated_at']


class CustomerSerializer(BusinessReferenceSerializer):
    class Meta(BusinessReferenceSerializer.Meta):
        model = Customer


class SupplierSerializer(BusinessReferenceSerializer):
    class Meta(BusinessReferenceSerializer.Meta):
        model = Supplier


class ServiceProviderSerializer(BusinessReferenceSerializer):
    class Meta(BusinessReferenceSerializer.Meta):
        model = ServiceProvider


class ProjectSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'code', 'name', 'customer', 'customer_name', 'notes', 'is_active', 'created_at', 'updated_at']


class WorkOrderSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source='project.name', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model = WorkOrder
        fields = ['id', 'code', 'name', 'project', 'project_name', 'customer', 'customer_name', 'notes', 'is_active', 'created_at', 'updated_at']


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = ['id', 'name', 'code', 'location_desc', 'is_active', 'created_at', 'updated_at']


class LocationSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta:
        model = Location
        fields = ['id', 'warehouse', 'warehouse_name', 'name', 'code', 'description', 'created_at', 'updated_at']


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'code', 'created_at', 'updated_at']


class ItemTypeSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = ItemType
        fields = [
            'id',
            'name',
            'code',
            'category',
            'category_name',
            'is_serialized',
            'unit',
            'safety_stock',
            'created_at',
            'updated_at',
        ]


class AssetSerializer(serializers.ModelSerializer):
    item_type_name = serializers.CharField(source='item_type.name', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    location_code = serializers.CharField(source='location.code', read_only=True)
    current_holder_name = serializers.CharField(source='current_holder.username', read_only=True)
    can_request = serializers.SerializerMethodField()
    can_return = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id',
            'system_asset_no',
            'asset_code',
            'name',
            'item_type',
            'item_type_name',
            'manufacturer',
            'model',
            'serial_number',
            'manufacturer_barcode',
            'qr_value',
            'search_aliases',
            'received_date',
            'purchase_order_no',
            'supplier',
            'supplier_ref',
            'purchase_amount',
            'warranty_until',
            'source_import_batch',
            'warehouse',
            'warehouse_name',
            'location',
            'location_code',
            'current_holder',
            'current_holder_name',
            'department',
            'status',
            'location_state',
            'availability_state',
            'quality_state',
            'disposition_state',
            'photo_url',
            'remarks',
            'can_request',
            'can_return',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'system_asset_no', 'qr_value', 'source_import_batch', 'current_holder',
            'department', 'status', 'created_at', 'updated_at',
            'location_state', 'availability_state', 'quality_state', 'disposition_state',
        ]

    def validate(self, attrs):
        controlled_fields = {
            'system_asset_no', 'qr_value', 'source_import_batch', 'current_holder',
            'department', 'status', 'location_state', 'availability_state',
            'quality_state', 'disposition_state',
        }
        attempted = controlled_fields.intersection(self.initial_data)
        if attempted:
            raise serializers.ValidationError({
                field: '该字段只能由审批后的出入库流程或历史导入维护。'
                for field in sorted(attempted)
            })
        return attrs

    def get_can_request(self, obj):
        return (
            obj.status == Asset.Status.IN_STOCK
            and obj.availability_state == Asset.AvailabilityState.AVAILABLE
        )

    def get_can_return(self, obj):
        return obj.status in {Asset.Status.BORROWED, Asset.Status.ISSUED}


class AssetPublicSerializer(serializers.ModelSerializer):
    item_type_name = serializers.CharField(source='item_type.name', read_only=True)
    can_request = serializers.SerializerMethodField()
    can_return = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            'id', 'system_asset_no', 'asset_code', 'name', 'item_type_name', 'manufacturer', 'model',
            'serial_number', 'manufacturer_barcode', 'qr_value', 'status', 'photo_url',
            'location_state', 'availability_state', 'quality_state', 'disposition_state',
            'can_request', 'can_return',
        ]

    def get_can_request(self, obj):
        return (
            obj.status == Asset.Status.IN_STOCK
            and obj.availability_state == Asset.AvailabilityState.AVAILABLE
        )

    def get_can_return(self, obj):
        request = self.context.get('request')
        return bool(
            request
            and request.user.is_authenticated
            and obj.current_holder_id == request.user.id
            and obj.status in {Asset.Status.BORROWED, Asset.Status.ISSUED}
        )


class AssetNetworkEndpointSerializer(serializers.ModelSerializer):
    system_asset_no = serializers.CharField(source='asset.system_asset_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)

    class Meta:
        model = AssetNetworkEndpoint
        fields = [
            'id',
            'asset',
            'system_asset_no',
            'asset_code',
            'interface_name',
            'ip_address',
            'subnet_mask',
            'gateway',
            'mac_address',
            'hostname',
            'vlan_id',
            'is_primary',
            'notes',
            'created_at',
            'updated_at',
        ]


class StockItemSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    item_type_name = serializers.CharField(source='item_type.name', read_only=True)
    initial_quantity = serializers.IntegerField(write_only=True, min_value=0, required=False)

    class Meta:
        model = StockItem
        fields = [
            'id',
            'name',
            'code',
            'unit',
            'quantity',
            'initial_quantity',
            'safety_stock',
            'supplier',
            'supplier_ref',
            'unit_cost',
            'item_type',
            'item_type_name',
            'source_import_batch',
            'warehouse',
            'warehouse_name',
            'location',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['quantity', 'source_import_batch', 'created_at', 'updated_at']

    def validate(self, attrs):
        if self.instance and 'quantity' in self.initial_data:
            raise serializers.ValidationError({'quantity': '当前库存不能直接修改；请通过出入库或盘点调整流程变更。'})
        if self.instance and 'initial_quantity' in attrs:
            raise serializers.ValidationError({'initial_quantity': '初始库存只能在新建物料时填写；后续数量请通过出入库或盘点调整流程变更。'})
        return attrs

    def create(self, validated_data):
        initial_quantity = validated_data.pop('initial_quantity', 0)
        return super().create({**validated_data, 'quantity': initial_quantity})


class StockItemPublicSerializer(serializers.ModelSerializer):
    item_type_name = serializers.CharField(source='item_type.name', read_only=True)
    can_request = serializers.SerializerMethodField()

    class Meta:
        model = StockItem
        fields = ['id', 'name', 'code', 'unit', 'item_type_name', 'can_request']

    def get_can_request(self, obj):
        return obj.quantity > 0


class InventoryCheckLineSerializer(serializers.ModelSerializer):
    item_type_name = serializers.CharField(source='item_type.name', read_only=True)
    system_asset_no = serializers.CharField(source='asset.system_asset_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    stock_item_name = serializers.CharField(source='stock_item.name', read_only=True)
    result_label = serializers.CharField(source='get_result_display', read_only=True)
    expected_warehouse_name = serializers.CharField(source='expected_warehouse.name', read_only=True)
    expected_location_code = serializers.CharField(source='expected_location.code', read_only=True)
    observed_warehouse_name = serializers.CharField(source='observed_warehouse.name', read_only=True)
    observed_location_code = serializers.CharField(source='observed_location.code', read_only=True)
    counted_by_name = serializers.CharField(source='counted_by.username', read_only=True)

    class Meta:
        model = InventoryCheckLine
        fields = [
            'id', 'task', 'item_type', 'item_type_name', 'asset', 'system_asset_no', 'asset_code', 'asset_name',
            'stock_item', 'stock_item_name', 'expected_quantity', 'counted_quantity',
            'expected_asset_code', 'expected_serial_number', 'expected_status',
            'expected_warehouse', 'expected_warehouse_name', 'expected_location', 'expected_location_code',
            'observed_warehouse', 'observed_warehouse_name', 'observed_location', 'observed_location_code',
            'result', 'result_label', 'counted_by', 'counted_by_name', 'counted_at', 'note',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class InventoryCheckTaskSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    location_code = serializers.CharField(source='location.code', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.username', read_only=True)
    reopened_by_name = serializers.CharField(source='reopened_by.username', read_only=True)
    line_total = serializers.IntegerField(source='lines.count', read_only=True)
    lines = InventoryCheckLineSerializer(many=True, read_only=True)

    class Meta:
        model = InventoryCheckTask
        fields = [
            'id', 'check_no', 'name', 'warehouse', 'warehouse_name', 'location', 'location_code',
            'status', 'status_label', 'note', 'created_by', 'created_by_name', 'started_by',
            'reviewed_by', 'reviewed_by_name', 'reopened_by', 'reopened_by_name', 'reopened_at', 'reopen_note',
            'started_at', 'submitted_at', 'completed_at',
            'line_total', 'lines', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class InventoryCheckScanSerializer(serializers.ModelSerializer):
    result_label = serializers.CharField(source='get_result_display', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    system_asset_no = serializers.CharField(source='asset.system_asset_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    stock_item_name = serializers.CharField(source='stock_item.name', read_only=True)

    class Meta:
        model = InventoryCheckScan
        fields = [
            'id', 'task', 'line', 'scanned_value', 'asset', 'system_asset_no', 'asset_code', 'stock_item', 'stock_item_name',
            'quantity', 'result', 'result_label', 'operator', 'operator_name', 'observed_warehouse',
            'observed_location', 'note', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class InventoryCheckAdjustmentSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source='get_action_display', read_only=True)
    reviewed_by_name = serializers.CharField(source='reviewed_by.username', read_only=True)
    system_asset_no = serializers.CharField(source='asset.system_asset_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    stock_item_name = serializers.CharField(source='stock_item.name', read_only=True)

    class Meta:
        model = InventoryCheckAdjustment
        fields = [
            'id', 'task', 'line', 'action', 'action_label', 'asset', 'system_asset_no', 'asset_code', 'stock_item',
            'stock_item_name', 'before_quantity', 'after_quantity', 'before_status', 'after_status',
            'before_warehouse', 'after_warehouse', 'before_location', 'after_location', 'reviewed_by',
            'reviewed_by_name', 'review_note', 'executed_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ComponentLinkSerializer(serializers.ModelSerializer):
    system_asset_no = serializers.CharField(source='asset.system_asset_no', read_only=True)
    asset_code = serializers.CharField(source='asset.asset_code', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    serial_number = serializers.CharField(source='asset.serial_number', read_only=True)
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = ComponentLink
        fields = [
            'id', 'asset', 'system_asset_no', 'asset_code', 'asset_name', 'serial_number',
            'assembled_at', 'disassembled_at', 'assemble_request_no',
            'disassemble_request_no', 'note', 'is_active',
        ]

    def get_is_active(self, obj):
        return obj.disassembled_at is None


class CompositeUnitSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    components = serializers.SerializerMethodField()
    component_count = serializers.SerializerMethodField()

    class Meta:
        model = CompositeUnit
        fields = [
            'id', 'unit_code', 'name', 'model', 'qr_value', 'status', 'status_label',
            'customer', 'customer_name', 'remarks',
            'components', 'component_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['qr_value', 'status', 'created_at', 'updated_at']

    def get_components(self, obj):
        links = obj.component_links.filter(disassembled_at__isnull=True).select_related('asset')
        return ComponentLinkSerializer(links, many=True).data

    def get_component_count(self, obj):
        return obj.component_links.filter(disassembled_at__isnull=True).count()


class CompositeUnitHistorySerializer(serializers.ModelSerializer):
    components = serializers.SerializerMethodField()

    class Meta:
        model = CompositeUnit
        fields = ['id', 'unit_code', 'name', 'model', 'status', 'components', 'created_at']

    def get_components(self, obj):
        links = obj.component_links.select_related('asset').all()
        return ComponentLinkSerializer(links, many=True).data


class AssetLifecycleEventSerializer(serializers.ModelSerializer):
    """资产全生命周期事件（小程序/外部系统只读消费）。"""

    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)
    actor_name = serializers.CharField(source='actor.username', read_only=True, default='')

    class Meta:
        model = AssetLifecycleEvent
        fields = [
            'id',
            'event_type',
            'event_type_display',
            'title',
            'description',
            'event_at',
            'actor_name',
            'related_model',
            'related_object_id',
            'created_at',
        ]
