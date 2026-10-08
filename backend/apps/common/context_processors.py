from apps.common.permissions import can_approve_workflow, can_execute_inventory, can_manage_inventory, can_view_reports, is_system_administrator, is_warehouse_operator
from apps.notifications.models import Notification


def web_navigation(request):
    if not request.user.is_authenticated:
        return {}
    return {
        'global_unread_notification_count': Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count(),
        'global_user_is_operator': is_warehouse_operator(request.user),
        'global_user_is_system_admin': is_system_administrator(request.user),
        'global_can_manage_inventory': can_manage_inventory(request.user),
        'global_can_execute_inventory': can_execute_inventory(request.user),
        'global_can_approve_workflow': can_approve_workflow(request.user),
        'global_can_view_reports': can_view_reports(request.user),
    }
