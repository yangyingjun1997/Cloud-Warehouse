from rest_framework.permissions import BasePermission

PERMISSION_GROUPS = {
    'warehouse_entry': '录入与基础资料',
    'warehouse_outbound': '出入库执行',
    'warehouse_approval': '审批处理',
    'warehouse_reports': '报表与导出',
    'warehouse_code_master': '资产编码基础资料',
    'warehouse_admin_code': '行政资产编码维护',
    'warehouse_domain_change': '资产归属调整',
    'warehouse_import': '批量导入',
    'warehouse_label_print': '资产标签打印',
    'warehouse_state_correction': '资产台账校正',
    'warehouse_finance': '采购金额等财务信息',
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


def can_manage_code_master(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_code_master')


def can_manage_admin_code(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_admin_code')


def can_change_asset_domain(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_domain_change')


def can_import_inventory(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_import')


def can_print_asset_labels(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_label_print')


def can_correct_asset_state(user) -> bool:
    return has_warehouse_permission(user, 'warehouse_state_correction')


def can_view_financials(user) -> bool:
    # 财务字段不继承 warehouse_staff 的全权限兼容口径，
    # 必须显式加入财务组；管理员与超管默认可见。
    return bool(user and user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name__in=[ADMIN_GROUP, 'warehouse_finance']).exists()
    ))


class IsAssetLabelPrinter(BasePermission):
    message = '当前账号没有资产标签打印权限。'

    def has_permission(self, request, view):
        return can_print_asset_labels(request.user)


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
