from rest_framework.permissions import BasePermission

# ── 细粒度能力组（保留）────────────────────────────────────────────
# 这些组是「能力标签」，谓词函数按它们判断。历史账号直接持有这些组。
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
    # 账号治理：让库管不必等管理员上线就能处理开通申请与建号。
    # 拥有此权限即可进入账号与权限页、审批开通申请、创建账号、重置密码；
    # 但「删除账号」和「修改他人权限」仍只对系统管理员开放（见视图内二次判断）。
    'warehouse_account_review': '账号开通与建号',
}

# ── 角色组（2026-11-23 新增，13 组归并为 5 个）──────────────────────
#
# 为什么这样做：13 个细粒度组对「4 个账号」的系统是纯负担——给一个仓库员
# 分配权限要勾 5-8 个组，容易漏勾导致功能不可用。
#
# 但直接把 13 个组从代码里删掉风险很高：它们分散在 10 个谓词函数、
# 若干视图和模板条件里，删完必然遗漏。
#
# 采用「角色组聚合」而不是「重命名」：
#   1. 新增 5 个角色组，每组代表一类工作职责；
#   2. 谓词函数改为「角色组 或 原细粒度组」任一命中即通过；
#   3. 老账号持有的细粒度组继续有效（零数据迁移），
#      新账号只分配角色组即可。
#
# 这样权限语义完全不变，只是多了一个更好用的入口。
ROLE_GROUPS = {
    # 只读：能看台账、报表、财务金额，不能改任何数据
    'warehouse_role_viewer': '角色·只读',
    # 录入：建档、导入、打标签，不能执行出入库
    'warehouse_role_editor': '角色·录入',
    # 执行：出入库、台账校正
    'warehouse_role_operator': '角色·执行',
    # 审批：审批单据、处理账号开通申请
    'warehouse_role_approver': '角色·审批',
}

# 角色组 -> 它聚合的细粒度能力组
ROLE_GROUP_CAPABILITIES = {
    'warehouse_role_viewer': frozenset({
        'warehouse_reports', 'warehouse_finance',
    }),
    'warehouse_role_editor': frozenset({
        'warehouse_entry', 'warehouse_import', 'warehouse_label_print',
        'warehouse_code_master', 'warehouse_admin_code',
        'warehouse_domain_change',
    }),
    'warehouse_role_operator': frozenset({
        'warehouse_outbound', 'warehouse_state_correction',
    }),
    'warehouse_role_approver': frozenset({
        'warehouse_approval', 'warehouse_account_review',
    }),
}

ADMIN_GROUP = 'warehouse_admin'
# 历史遗留的全权限组，线上已无人使用（2026-11-23 实测 0 人）。
# 保留常量是为了让仍在代码里的引用不报错，但不再对外宣传。
LEGACY_GROUP = 'warehouse_staff'


def _has_group(user, *names) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=names).exists()))


def _role_covers(user, capability: str) -> bool:
    """角色组是否覆盖某个细粒度能力。

    例如用户属于 warehouse_role_operator，则该函数对
    warehouse_outbound 与 warehouse_state_correction 都返回 True。
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    held = set(user.groups.values_list('name', flat=True))
    for role, capabilities in ROLE_GROUP_CAPABILITIES.items():
        if role in held and capability in capabilities:
            return True
    return False


def is_system_administrator(user) -> bool:
    return _has_group(user, ADMIN_GROUP)


def has_warehouse_permission(user, permission_name: str) -> bool:
    """细粒度权限判定：管理员 / 遗留全权限组 / 该能力组 / 覆盖它的角色组。"""
    if _has_group(user, ADMIN_GROUP, LEGACY_GROUP, permission_name):
        return True
    return _role_covers(user, permission_name)


def is_warehouse_operator(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    held = set(user.groups.values_list('name', flat=True))
    return bool(held & ({ADMIN_GROUP, LEGACY_GROUP, *PERMISSION_GROUPS, *ROLE_GROUPS}))


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
    """资产台账校正入口权限：必须显式持有 warehouse_state_correction。

    刻意不继承 has_warehouse_permission 的宽泛兜底：该函数会把 warehouse_admin
    与 warehouse_staff 也放行，等于「有仓库权限就能改台账」，与
    warehouse_state_correction 这个细粒度组的存在意义相矛盾。

    台账校正直接改资产状态、不产生业务单据，属于敏感操作。
    因此这里只认 warehouse_state_correction 或覆盖它的角色组
    （warehouse_role_operator），不因拥有 warehouse_admin 而自动获得入口。
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(name='warehouse_state_correction').exists():
        return True
    return _role_covers(user, 'warehouse_state_correction')


def can_review_accounts(user) -> bool:
    """账号开通与建号：可处理开通申请、创建账号、重置密码。

    刻意**不**继承 warehouse_staff 的宽泛兜底：账号治理是敏感操作，
    必须显式授权，避免历史遗留的全权限组自动获得建号能力。
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(name__in=[ADMIN_GROUP, 'warehouse_account_review']).exists():
        return True
    return _role_covers(user, 'warehouse_account_review')


def can_view_financials(user) -> bool:
    # 财务字段不继承 warehouse_staff 的全权限兼容口径，
    # 必须显式加入财务组；管理员与超管默认可见。
    # 角色侧：warehouse_role_viewer 聚合了 warehouse_finance。
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.groups.filter(name__in=[ADMIN_GROUP, 'warehouse_finance']).exists():
        return True
    return _role_covers(user, 'warehouse_finance')


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
