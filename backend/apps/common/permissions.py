from rest_framework.permissions import BasePermission

PERMISSION_GROUPS = {
    'warehouse_entry': '录入与基础资料',
    'warehouse_outbound': '出入库执行',
    'warehouse_approval': '审批处理',
    'warehouse_reports': '报表与导出',
}
ADMIN_GROUP = 'warehouse_admin'
LEGACY_GROUP = 'warehouse_staff'


def _has_group(user, *names) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=names).exists()))


def is_system_administrator(user) -> bool:
    return _has_group(user, ADMIN_GROUP)


def has_warehouse_permission(user, permission_name: str) -> bool:
    return _has_group(user, ADMIN_GROUP, LEGACY_GROUP, permission_name)


def is_warehouse_operator(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=[ADMIN_GROUP, LEGACY_GROUP, *PERMISSION_GROUPS]).exists()))


def can_manage_inventory(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_entry')


def can_execute_inventory(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_outbound')


def can_approve_workflow(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_approval')


def can_view_reports(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_reports')


class IsWarehouseOperator(BasePermission):
    message = '当前账号没有仓库业务权限。'
    def has_permission(self, request, view): return is_warehouse_operator(request.user)


class IsInventoryEditor(BasePermission):
    message = '当前账号没有录入与基础资料权限。'
    def has_permission(self, request, view): return can_manage_inventory(request.user)


class IsInventoryExecutor(BasePermission):
    message = '当前账号没有出入库执行权限。'
    def has_permission(self, request, view): return can_execute_inventory(request.user)


class IsWorkflowApprover(BasePermission):
    message = '当前账号没有审批权限。'
    def has_permission(self, request, view): return can_approve_workflow(request.user)


class IsReportViewer(BasePermission):
    message = '当前账号没有报表与导出权限。'
    def has_permission(self, request, view): return can_view_reports(request.user)


class IsSystemAdministrator(BasePermission):
    message = '只有仓库管理员或超级管理员可以维护系统设置。'
    def has_permission(self, request, view): return is_system_administrator(request.user)
