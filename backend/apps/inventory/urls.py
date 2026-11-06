from django.urls import path
from rest_framework.routers import DefaultRouter

from .label_views import (
    claim_label_job,
    create_label_job,
    finish_label_job,
    heartbeat_label_job,
    label_job_status,
    retry_label_job,
    cancel_label_job,
)
from .views import (
    AssetNetworkEndpointViewSet,
    AssetViewSet,
    CategoryViewSet,
    CompositeUnitViewSet,
    CustomerViewSet,
    InventoryDomainViewSet,
    ManufacturerViewSet,
    AssetTypeCodeViewSet,
    ProductModelCodeViewSet,
    InventoryCheckAdjustmentViewSet,
    InventoryCheckLineViewSet,
    InventoryCheckScanViewSet,
    InventoryCheckTaskViewSet,
    ItemTypeViewSet,
    LocationViewSet,
    ProjectViewSet,
    ServiceProviderViewSet,
    StockItemViewSet,
    SupplierViewSet,
    WarehouseViewSet,
    WorkOrderViewSet,
)

router = DefaultRouter()
router.register(r'warehouses', WarehouseViewSet, basename='warehouse')
router.register(r'locations', LocationViewSet, basename='location')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'item-types', ItemTypeViewSet, basename='item-type')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'suppliers', SupplierViewSet, basename='supplier')
router.register(r'manufacturers', ManufacturerViewSet, basename='manufacturer')
router.register(r'inventory-domains', InventoryDomainViewSet, basename='inventory-domain')
router.register(r'asset-type-codes', AssetTypeCodeViewSet, basename='asset-type-code')
router.register(r'product-model-codes', ProductModelCodeViewSet, basename='product-model-code')
router.register(r'service-providers', ServiceProviderViewSet, basename='service-provider')
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'work-orders', WorkOrderViewSet, basename='work-order')
router.register(r'assets', AssetViewSet, basename='asset')
router.register(r'composite-units', CompositeUnitViewSet, basename='composite-unit')
router.register(r'network-endpoints', AssetNetworkEndpointViewSet, basename='network-endpoint')
router.register(r'stock-items', StockItemViewSet, basename='stock-item')
router.register(r'inventory-checks', InventoryCheckTaskViewSet, basename='inventory-check')
router.register(r'inventory-check-lines', InventoryCheckLineViewSet, basename='inventory-check-line')
router.register(r'inventory-check-scans', InventoryCheckScanViewSet, basename='inventory-check-scan')
router.register(r'inventory-check-adjustments', InventoryCheckAdjustmentViewSet, basename='inventory-check-adjustment')

urlpatterns = router.urls + [
    # 标签打印：网页端建作业、打印客户端领作业并回报结果
    path('label-jobs/', create_label_job, name='label-job-create'),
    path('label-jobs/claim/', claim_label_job, name='label-job-claim'),
    path('label-jobs/status/', label_job_status, name='label-job-status'),
    path('label-jobs/<uuid:job_id>/finish/', finish_label_job, name='label-job-finish'),
    path('label-jobs/<uuid:job_id>/heartbeat/', heartbeat_label_job, name='label-job-heartbeat'),
    path('label-jobs/<uuid:job_id>/retry/', retry_label_job, name='label-job-retry'),
    path('label-jobs/<uuid:job_id>/cancel/', cancel_label_job, name='label-job-cancel'),
]
