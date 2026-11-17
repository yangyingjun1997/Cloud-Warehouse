from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import AccountRequest
from apps.accounts.username_services import suggest_username, unique_username
from apps.common.audit import record_operation
from apps.common.models import OperationAuditLog


User = get_user_model()


class UsernameSuggestionTests(TestCase):
    def test_chinese_name_becomes_pinyin(self):
        self.assertEqual(suggest_username('杨俊杰'), 'yangjunjie')
        self.assertEqual(suggest_username('张三'), 'zhangsan')

    def test_english_name_passes_through(self):
        self.assertEqual(suggest_username('Tom Lee'), 'tomlee')

    def test_empty_falls_back(self):
        self.assertEqual(suggest_username(''), 'user')
        self.assertEqual(suggest_username('   '), 'user')

    def test_duplicate_gets_numeric_suffix(self):
        User.objects.create_user(username='yangjunjie', password='pw')
        self.assertEqual(unique_username('杨俊杰'), 'yangjunjie2')
        User.objects.create_user(username='yangjunjie2', password='pw')
        self.assertEqual(unique_username('杨俊杰'), 'yangjunjie3')

    def test_no_conflict_returns_base(self):
        self.assertEqual(unique_username('李四'), 'lisi')


class AccountRequestSimplifiedFormTests(TestCase):
    """申请表单不再要求工号和部门。"""

    def test_request_only_needs_name_and_phone(self):
        response = self.client.post('/login/request-account/', {
            'real_name': '王五',
            'phone': '13800138000',
            'reason': '外包同事，没有工号',
        })

        self.assertRedirects(response, '/login/')
        record = AccountRequest.objects.get(real_name='王五')
        self.assertEqual(record.employee_no, '')
        self.assertEqual(record.department_text, '')

    def test_department_field_removed_from_form_page(self):
        response = self.client.get('/login/request-account/')
        self.assertNotContains(response, 'name="department_text"')
        self.assertNotContains(response, 'name="employee_no"')

    def test_duplicate_pending_by_name_and_phone_blocked(self):
        payload = {'real_name': '赵六', 'phone': '13900139000'}
        self.client.post('/login/request-account/', payload)
        self.client.post('/login/request-account/', payload)
        self.assertEqual(AccountRequest.objects.filter(real_name='赵六').count(), 1)

    def test_same_name_different_phone_allowed(self):
        self.client.post('/login/request-account/', {'real_name': '同名', 'phone': '111'})
        self.client.post('/login/request-account/', {'real_name': '同名', 'phone': '222'})
        self.assertEqual(AccountRequest.objects.filter(real_name='同名').count(), 2)


class EmployeeNoOptionalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='opt-admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.client.force_login(self.admin)

    def test_create_account_without_employee_no(self):
        response = self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'no-emp-no',
            'password': 'password123',
            'employee_no': '',
            'permissions': ['warehouse_entry'],
        })

        self.assertRedirects(response, '/permissions/')
        user = User.objects.get(username='no-emp-no')
        self.assertIsNone(user.profile.employee_no)

    def test_multiple_accounts_without_employee_no_allowed(self):
        for index in range(2):
            self.client.post('/permissions/', {
                'action': 'create_user',
                'username': f'no-emp-{index}',
                'password': 'password123',
                'employee_no': '',
                'permissions': ['warehouse_entry'],
            })
        self.assertEqual(
            User.objects.filter(username__startswith='no-emp-').count(), 2,
        )


class AccountLifecycleAuditTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='audit-admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.client.force_login(self.admin)

    def test_creating_account_is_audited(self):
        self.client.post('/permissions/', {
            'action': 'create_user',
            'username': 'created-audit',
            'password': 'password123',
            'permissions': ['warehouse_outbound'],
        })
        log = OperationAuditLog.objects.filter(action='account_created').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_username, 'audit-admin')
        self.assertIn('warehouse_outbound', log.after_data['permissions'])

    def test_permission_change_is_audited(self):
        target = User.objects.create_user(username='promote-me', password='pw')
        target.groups.add(Group.objects.get(name='warehouse_entry'))
        self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(target.pk),
            'employee_no': '',
            # warehouse_admin 不是 PERMISSION_GROUPS 成员，单独用 is_admin 勾选
            'permissions': ['warehouse_entry', 'warehouse_outbound'],
            'is_admin': '1',
        })
        log = OperationAuditLog.objects.filter(
            action='account_permissions_updated',
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.after_data['is_admin'], True)
        self.assertIn('warehouse_outbound', log.after_data['permissions'])

    def test_disable_account_keeps_record_and_writes_audit(self):
        target = User.objects.create_user(username='leaver', password='pw')
        response = self.client.post('/permissions/', {
            'action': 'toggle_active',
            'user_id': str(target.pk),
        })

        self.assertRedirects(response, '/permissions/')
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertTrue(User.objects.filter(username='leaver').exists())
        self.assertTrue(OperationAuditLog.objects.filter(
            action='account_disabled',
        ).exists())

    def test_disabled_account_can_be_reenabled(self):
        target = User.objects.create_user(username='returner', password='pw')
        target.is_active = False
        target.save(update_fields=['is_active'])
        self.client.post('/permissions/', {
            'action': 'toggle_active',
            'user_id': str(target.pk),
        })
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_audit_snapshot_survives_account_deletion(self):
        """审计行在删号后仍能看出是谁做的。"""
        target = User.objects.create_user(username='deleted-later', password='pw')
        target.groups.add(Group.objects.get(name='warehouse_entry'))
        self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(target.pk),
            'employee_no': '',
            'permissions': ['warehouse_outbound'],
        })
        log = OperationAuditLog.objects.filter(
            action='account_permissions_updated',
        ).first()
        self.assertEqual(log.actor_username, 'audit-admin')

    def test_audit_row_records_actor_snapshot(self):
        """审计行必须带操作人快照，便于展示与追溯。"""
        target = User.objects.create_user(username='snapshot-target', password='pw')
        self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(target.pk),
            'employee_no': '',
            'permissions': ['warehouse_outbound'],
        })
        log = OperationAuditLog.objects.filter(
            action='account_permissions_updated',
        ).first()
        self.assertEqual(log.actor_username, 'audit-admin')

    def test_account_with_audit_cannot_be_hard_deleted(self):
        """有审计记录的账号不允许硬删（数据库层也禁止改审计表），应改用停用。"""
        target = User.objects.create_user(username='has-audit', password='pw')
        target.groups.add(Group.objects.get(name='warehouse_entry'))
        # 让目标账号自己产生一条审计（他作为 actor 操作了自己）
        record_operation(
            target, 'self_action', target, summary='目标账号自己的操作记录',
        )
        self.assertTrue(OperationAuditLog.objects.filter(actor=target).exists())

        response = self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': str(target.pk),
        })

        self.assertRedirects(response, '/permissions/')
        # 账号仍在，且可用「停用」处理
        self.assertTrue(User.objects.filter(username='has-audit').exists())
