from rest_framework.routers import DefaultRouter

from .views import (
    AssetNetworkEndpointViewSet,
    AssetViewSet,
    CategoryViewSet,
    CompositeUnitViewSet,
    CustomerViewSet,
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

urlpatterns = router.urls
