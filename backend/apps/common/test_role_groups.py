"""角色组聚合的等价性回归（2026-11-23，权限组 13 → 4 角色）。

设计：细粒度能力组保留为「能力标签」，新增 4 个角色组聚合它们；
谓词函数对「角色组 或 原能力组」任一命中即通过。
必须保证：持有角色组 == 持有它聚合的全部能力组，不多也不少。
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.common.permissions import (
    PERMISSION_GROUPS,
    ROLE_GROUP_CAPABILITIES,
    ROLE_GROUPS,
    can_approve_workflow,
    can_change_asset_domain,
    can_correct_asset_state,
    can_execute_inventory,
    can_import_inventory,
    can_manage_admin_code,
    can_manage_code_master,
    can_manage_inventory,
    can_print_asset_labels,
    can_review_accounts,
    can_view_financials,
    can_view_reports,
    has_warehouse_permission,
    is_warehouse_operator,
)

User = get_user_model()

# 能力名 -> 谓词函数
PREDICATES = {
    'warehouse_entry': can_manage_inventory,
    'warehouse_outbound': can_execute_inventory,
    'warehouse_approval': can_approve_workflow,
    'warehouse_reports': can_view_reports,
    'warehouse_code_master': can_manage_code_master,
    'warehouse_admin_code': can_manage_admin_code,
    'warehouse_domain_change': can_change_asset_domain,
    'warehouse_import': can_import_inventory,
    'warehouse_label_print': can_print_asset_labels,
    'warehouse_state_correction': can_correct_asset_state,
    'warehouse_finance': can_view_financials,
    'warehouse_account_review': can_review_accounts,
}


def make_user(username, groups=()):
    user = User.objects.create_user(username=username, password='Pw#2026aa')
    for name in groups:
        user.groups.add(Group.objects.get_or_create(name=name)[0])
    return user


class RoleGroupCoverageTests(TestCase):
    def test_every_capability_has_a_predicate(self):
        missing = set(PERMISSION_GROUPS) - set(PREDICATES)
        self.assertEqual(missing, set(), f'以下能力缺少谓词映射: {missing}')

    def test_all_capabilities_covered_by_some_role(self):
        covered = set()
        for capabilities in ROLE_GROUP_CAPABILITIES.values():
            covered |= set(capabilities)
        uncovered = set(PERMISSION_GROUPS) - covered
        self.assertEqual(uncovered, set(), f'以下能力没有任何角色组覆盖: {uncovered}')

    def test_role_groups_have_labels(self):
        self.assertEqual(set(ROLE_GROUPS), set(ROLE_GROUP_CAPABILITIES),
                         '角色组定义与能力映射必须一一对应')


class RoleGroupEquivalenceTests(TestCase):
    """持有角色组 vs 持有细粒度组，两种方式的权限结果必须一致。"""

    def test_viewer_role_matches_capabilities(self):
        role_user = make_user('rg_viewer_role', ['warehouse_role_viewer'])
        for capability in ROLE_GROUP_CAPABILITIES['warehouse_role_viewer']:
            with self.subTest(capability=capability):
                direct = make_user(f'rg_viewer_{capability}', [capability])
                self.assertEqual(
                    PREDICATES[capability](role_user),
                    PREDICATES[capability](direct),
                    f'{capability} 在角色组与细粒度组下结果不一致',
                )

    def test_editor_role_matches_capabilities(self):
        role_user = make_user('rg_editor_role', ['warehouse_role_editor'])
        for capability in ROLE_GROUP_CAPABILITIES['warehouse_role_editor']:
            with self.subTest(capability=capability):
                direct = make_user(f'rg_editor_{capability}', [capability])
                self.assertEqual(
                    PREDICATES[capability](role_user),
                    PREDICATES[capability](direct),
                )

    def test_operator_role_matches_capabilities(self):
        role_user = make_user('rg_operator_role', ['warehouse_role_operator'])
        for capability in ROLE_GROUP_CAPABILITIES['warehouse_role_operator']:
            with self.subTest(capability=capability):
                direct = make_user(f'rg_operator_{capability}', [capability])
                self.assertEqual(
                    PREDICATES[capability](role_user),
                    PREDICATES[capability](direct),
                )

    def test_approver_role_matches_capabilities(self):
        role_user = make_user('rg_approver_role', ['warehouse_role_approver'])
        for capability in ROLE_GROUP_CAPABILITIES['warehouse_role_approver']:
            with self.subTest(capability=capability):
                direct = make_user(f'rg_approver_{capability}', [capability])
                self.assertEqual(
                    PREDICATES[capability](role_user),
                    PREDICATES[capability](direct),
                )

    def test_role_does_not_grant_unrelated_capability(self):
        """角色组不得越权：只读角色不应获得出入库执行权。"""
        viewer = make_user('rg_viewer_only', ['warehouse_role_viewer'])
        self.assertFalse(can_execute_inventory(viewer))
        self.assertFalse(can_manage_inventory(viewer))
        self.assertFalse(can_correct_asset_state(viewer))

    def test_editor_role_cannot_execute_outbound(self):
        editor = make_user('rg_editor_only', ['warehouse_role_editor'])
        self.assertFalse(can_execute_inventory(editor),
                         '录入角色不应能执行出入库')
        self.assertTrue(can_manage_inventory(editor))

    def test_operator_role_cannot_approve(self):
        operator = make_user('rg_operator_only', ['warehouse_role_operator'])
        self.assertFalse(can_approve_workflow(operator),
                         '执行角色不应能审批')
        self.assertTrue(can_execute_inventory(operator))

    def test_approver_role_cannot_execute(self):
        approver = make_user('rg_approver_only', ['warehouse_role_approver'])
        self.assertFalse(can_execute_inventory(approver),
                         '审批角色不应能执行出入库')
        self.assertTrue(can_approve_workflow(approver))


class RoleGroupOperatorTests(TestCase):
    def test_role_group_counts_as_operator(self):
        """角色组必须被识别为「仓库业务角色」，否则页面入口会消失。"""
        for role in ROLE_GROUPS:
            with self.subTest(role=role):
                user = make_user(f'rg_op_{role}', [role])
                self.assertTrue(is_warehouse_operator(user))

    def test_plain_user_is_not_operator(self):
        user = make_user('rg_plain')
        self.assertFalse(is_warehouse_operator(user))

    def test_superuser_is_operator(self):
        user = User.objects.create_user(
            username='rg_super', password='Pw#2026aa', is_superuser=True,
        )
        self.assertTrue(is_warehouse_operator(user))

    def test_no_group_user_has_no_capability(self):
        user = make_user('rg_nobody')
        for capability, predicate in PREDICATES.items():
            with self.subTest(capability=capability):
                self.assertFalse(predicate(user))


class LegacyGroupTests(TestCase):
    def test_legacy_group_still_works_for_compatibility(self):
        """warehouse_staff 已不再分配，但历史账号若持有它仍应保留权限。"""
        user = make_user('rg_legacy', ['warehouse_staff'])
        self.assertTrue(has_warehouse_permission(user, 'warehouse_entry'))
        self.assertTrue(is_warehouse_operator(user))
