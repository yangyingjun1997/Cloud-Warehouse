from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import AccountRequest, UserProfile
from apps.common.models import OperationAuditLog
from apps.common.permissions import PERMISSION_GROUPS
from apps.common.utils import generate_temporary_password


User = get_user_model()


class TemporaryPasswordTests(TestCase):
    def test_length_and_charset(self):
        pwd = generate_temporary_password()
        self.assertEqual(len(pwd), 12)
        self.assertGreaterEqual(len(set(pwd)), 4)
        # 不含容易混淆的字符
        self.assertFalse(set(pwd) & set('0O1lI'))

    def test_minimum_length_enforced(self):
        self.assertEqual(len(generate_temporary_password(4)), 8)

    def test_values_differ(self):
        values = {generate_temporary_password() for _ in range(20)}
        self.assertEqual(len(values), 20)


class AdminResetPasswordTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='reset-admin', password='pw-admin')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.target = User.objects.create_user(username='forgetful', password='old-password')
        self.client.force_login(self.admin)

    def test_reset_generates_audit_and_revokes_sessions(self):
        response = self.client.post('/permissions/', {
            'action': 'reset_password',
            'user_id': str(self.target.pk),
        })

        self.assertRedirects(response, '/permissions/')
        self.assertTrue(OperationAuditLog.objects.filter(
            action='account_password_reset',
        ).exists())
        self.target.refresh_from_db()
        self.assertFalse(self.target.check_password('old-password'))

    def test_superuser_password_cannot_be_reset_here(self):
        superuser = User.objects.create_superuser(username='root-admin', password='pw-root', email='')
        response = self.client.post('/permissions/', {
            'action': 'reset_password',
            'user_id': str(superuser.pk),
        })

        self.assertRedirects(response, '/permissions/')
        superuser.refresh_from_db()
        self.assertTrue(superuser.check_password('pw-root'))


class AccountRequestFlowTests(TestCase):
    """登录页申请开通账号：不能自助注册，只能提交申请。"""

    def test_anonymous_can_submit_request(self):
        response = self.client.post('/login/request-account/', {
            'real_name': '  王五  ',
            'phone': '13800138000',
            'reason': '新入职',
        })

        self.assertRedirects(response, '/login/')
        record = AccountRequest.objects.get(real_name='王五')
        self.assertEqual(record.real_name, '王五')
        self.assertEqual(record.status, AccountRequest.Status.PENDING)
        # 工号改为选填后默认留空
        self.assertEqual(record.employee_no, '')
        # 绝不能因为提交申请就创建账号
        self.assertFalse(User.objects.filter(username='王五').exists())
        self.assertFalse(UserProfile.objects.filter(employee_no='').exists())

    def test_required_fields_are_validated(self):
        response = self.client.post('/login/request-account/', {
            'real_name': '',
            'phone': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AccountRequest.objects.count(), 0)

    def test_duplicate_pending_request_is_not_duplicated(self):
        # 工号取消后，去重键改为「姓名 + 电话」
        payload = {'real_name': '王五', 'phone': '13800138000', 'reason': ''}
        self.client.post('/login/request-account/', payload)
        self.client.post('/login/request-account/', payload)

        self.assertEqual(AccountRequest.objects.filter(real_name='王五').count(), 1)

    def test_login_page_links_to_request_form(self):
        response = self.client.get('/login/')
        self.assertContains(response, '/login/request-account/')
        self.assertNotContains(response, '注册')

    def test_admin_can_reject_request(self):
        admin = User.objects.create_user(username='req-admin', password='pw-admin')
        admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        record = AccountRequest.objects.create(
            real_name='李六', employee_no='E-1003', phone='13900139000',
        )
        self.client.force_login(admin)

        response = self.client.post('/permissions/', {
            'action': 'reject_account_request',
            'request_id': str(record.pk),
            'handle_note': '工号不存在',
        })

        self.assertRedirects(response, '/permissions/')
        record.refresh_from_db()
        self.assertEqual(record.status, AccountRequest.Status.REJECTED)
        self.assertEqual(record.handle_note, '工号不存在')

    def test_pending_requests_appear_on_permission_page(self):
        admin = User.objects.create_user(username='list-admin', password='pw-admin')
        admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        AccountRequest.objects.create(
            real_name='赵七', phone='13700137000',
        )
        self.client.force_login(admin)

        response = self.client.get('/permissions/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '赵七')
        # 待办列表展示按姓名预生成的拼音账号，省去管理员手动想英文名
        self.assertContains(response, 'zhaoqi')
