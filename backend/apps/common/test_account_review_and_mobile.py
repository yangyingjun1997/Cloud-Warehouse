"""本轮新增能力回归：库位停用、账号审批权限、账号申请通知、移动端精简骨架。"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.common.permissions import PERMISSION_GROUPS, can_review_accounts, is_warehouse_operator
from apps.inventory.forms import LocationManagementForm
from apps.inventory.models import Location, Warehouse
from apps.notifications.models import Notification

User = get_user_model()


def _group(name):
    return Group.objects.get_or_create(name=name)[0]


class LocationActiveFlagTests(TestCase):
    """库位必须能停用：历史资产仍引用它，硬删会破坏台账追溯。"""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(name='库位测试仓', code='LOC-WH')

    def test_model_has_is_active_default_true(self):
        location = Location.objects.create(warehouse=self.warehouse, code='L-01', name='L-01')
        self.assertTrue(location.is_active)

    def test_form_exposes_is_active(self):
        self.assertIn('is_active', LocationManagementForm().fields)

    def test_inactive_location_can_be_saved(self):
        location = Location.objects.create(warehouse=self.warehouse, code='L-02', name='L-02')
        form = LocationManagementForm(
            data={
                'warehouse': self.warehouse.pk,
                'code': 'L-02',
                'name': 'L-02',
                'description': '',
                'is_active': '',
                # VersionedModelForm 用该字段做并发编辑冲突检测
                'expected_updated_at': location.updated_at.isoformat(),
            },
            instance=location,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertFalse(saved.is_active)

    def test_location_list_shows_status_column_like_warehouse(self):
        """库位列表必须和仓库一样展示启用/停用状态，否则用户看不出哪些已停用。"""
        admin = User.objects.create_user(username='loc_col_admin', password='Pw#2026aa')
        admin.is_staff = True
        admin.groups.add(_group('warehouse_admin'))

        Location.objects.create(warehouse=self.warehouse, code='L-ON', name='启用库位', is_active=True)
        Location.objects.create(warehouse=self.warehouse, code='L-OFF', name='停用库位', is_active=False)

        self.client.force_login(admin)
        html = self.client.get('/warehouse/setup/locations/').content.decode('utf-8')

        self.assertIn('<th>状态</th>', html)
        self.assertIn('>启用<', html)
        self.assertIn('>停用<', html)


class AccountReviewPermissionTests(TestCase):
    """账号开通与建号：显式授权，不继承 warehouse_staff 的宽泛兜底。"""

    def setUp(self):
        for name in [*PERMISSION_GROUPS, 'warehouse_admin', 'warehouse_staff']:
            _group(name)

    def _user(self, username, groups):
        user = User.objects.create_user(username=username, password='Pw#2026aa')
        user.groups.set([_group(g) for g in groups])
        return user

    def test_permission_registered(self):
        self.assertIn('warehouse_account_review', PERMISSION_GROUPS)

    def test_admin_and_reviewer_allowed(self):
        self.assertTrue(can_review_accounts(self._user('ar_admin', ['warehouse_admin'])))
        self.assertTrue(can_review_accounts(self._user('ar_reviewer', ['warehouse_account_review'])))

    def test_other_permissions_denied(self):
        self.assertFalse(can_review_accounts(self._user('ar_op', ['warehouse_outbound'])))
        self.assertFalse(can_review_accounts(self._user('ar_plain', [])))

    def test_legacy_staff_group_does_not_grant_account_review(self):
        """warehouse_staff 是历史全权限组，但账号治理必须显式授权。"""
        self.assertFalse(can_review_accounts(self._user('ar_legacy', ['warehouse_staff'])))

    def test_reviewer_can_open_permission_page(self):
        client = self.client
        client.force_login(self._user('ar_page', ['warehouse_account_review']))
        self.assertEqual(client.get('/permissions/').status_code, 200)

    def test_operator_cannot_open_permission_page(self):
        client = self.client
        client.force_login(self._user('ar_page_denied', ['warehouse_outbound']))
        self.assertEqual(client.get('/permissions/').status_code, 302)

    def test_reviewer_cannot_delete_account(self):
        """仅有开通权限的账号不能删号，否则权限会被静默放大。"""
        reviewer = self._user('ar_del_reviewer', ['warehouse_account_review'])
        target = self._user('ar_del_target', [])
        self.client.force_login(reviewer)
        self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': target.pk,
        })
        self.assertTrue(User.objects.filter(pk=target.pk).exists())


class AccountRequestNotificationTests(TestCase):
    """开通申请提交后必须通知审批人，否则管理员不登录就不知道有人申请。"""

    def setUp(self):
        for name in [*PERMISSION_GROUPS, 'warehouse_admin']:
            _group(name)
        self.admin = User.objects.create_user(username='arn_admin', password='Pw#2026aa')
        self.admin.groups.add(_group('warehouse_admin'))
        self.reviewer = User.objects.create_user(username='arn_reviewer', password='Pw#2026aa')
        self.reviewer.groups.add(_group('warehouse_account_review'))

    def test_submission_notifies_reviewers(self):
        response = self.client.post('/login/request-account/', {
            'real_name': '测试申请人',
            'phone': '13800138000',
            'reason': '需要查询资产',
        })
        self.assertEqual(response.status_code, 302)

        notified = set(
            Notification.objects.filter(title='新的账号开通申请')
            .values_list('recipient__username', flat=True)
        )
        self.assertIn('arn_admin', notified)
        self.assertIn('arn_reviewer', notified)

    def test_duplicate_pending_does_not_spam(self):
        payload = {'real_name': '重复申请', 'phone': '13900139000', 'reason': ''}
        self.client.post('/login/request-account/', payload)
        first = Notification.objects.filter(title='新的账号开通申请').count()
        self.client.post('/login/request-account/', payload)
        second = Notification.objects.filter(title='新的账号开通申请').count()
        self.assertEqual(first, second, '重复提交不应重复通知')


class MePermissionConsistencyTests(TestCase):
    """/me 返回的 is_warehouse_operator 必须与实际权限谓词一致。"""

    def setUp(self):
        for name in PERMISSION_GROUPS:
            _group(name)

    def test_me_matches_actual_permission(self):
        cases = [
            ('me_op', ['warehouse_outbound']),
            ('me_appr', ['warehouse_approval']),
            ('me_plain', []),
        ]
        for username, groups in cases:
            with self.subTest(username=username):
                user = User.objects.create_user(username=username, password='Pw#2026aa')
                user.groups.set([_group(g) for g in groups])
                self.client.force_login(user)
                payload = self.client.get('/api/accounts/me/').json()
                self.assertEqual(payload['is_warehouse_operator'], is_warehouse_operator(user))


class MobileLayoutTests(TestCase):
    """移动端精简：次要面板可折叠、hero 按钮在手机上隐藏。"""

    def setUp(self):
        _group('warehouse_outbound')
        self.user = User.objects.create_user(username='mob_user', password='Pw#2026aa')
        self.user.groups.add(_group('warehouse_outbound'))
        self.client.force_login(self.user)

    def test_home_has_collapsible_sections(self):
        html = self.client.get('/').content.decode('utf-8')
        self.assertIn('mobile-collapse', html)
        self.assertIn('<details', html)

    def test_mobile_css_hides_hero_actions(self):
        html = self.client.get('/').content.decode('utf-8')
        self.assertIn('.hero-actions { display: none; }', html)
        # 桌面端用 display:contents 让折叠容器透明化，不影响原版式
        self.assertIn('display: contents', html)
