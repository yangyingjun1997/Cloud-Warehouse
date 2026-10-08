from rest_framework.routers import DefaultRouter

from .views import ApprovalLogViewSet, ApprovalTaskViewSet, InventoryTransactionViewSet, WorkflowRequestLineViewSet, WorkflowRequestViewSet

router = DefaultRouter()
router.register(r'requests', WorkflowRequestViewSet, basename='workflow-request')
router.register(r'request-lines', WorkflowRequestLineViewSet, basename='workflow-request-line')
router.register(r'approval-tasks', ApprovalTaskViewSet, basename='approval-task')
router.register(r'approval-logs', ApprovalLogViewSet, basename='approval-log')
router.register(r'inventory-transactions', InventoryTransactionViewSet, basename='inventory-transaction')

urlpatterns = router.urls
