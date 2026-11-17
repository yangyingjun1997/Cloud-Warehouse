from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.template import Context, Template

from apps.accounts.models import Department, UserProfile
from apps.common.models import OperationAuditLog
from apps.common.permissions import PERMISSION_GROUPS
from apps.common.templatetags.person import format_person


User = get_user_model()


class PersonLabelTests(TestCase):
    def test_named_user_shows_name_then_username(self):
        user = User.objects.create_user(username='zhangsan', password='pw')
        user.first_name = '张三'
        user.save(update_fields=['first_name'])
        self.assertEqual(format_person(user), '张三（zhangsan）')

    def test_user_without_display_name_shows_username_only(self):
        user = User.objects.create_user(username='lisi', password='pw')
        self.assertEqual(format_person(user), 'lisi')

    def test_deleted_user_placeholder(self):
        self.assertEqual(format_person(None), '已删除账号')

    def test_display_name_equal_to_username_is_not_duplicated(self):
        user = User.objects.create_user(username='wangwu', password='pw')
        user.first_name = 'wangwu'
        user.save(update_fields=['first_name'])
        self.assertEqual(format_person(user), 'wangwu')

    def test_template_filter_renders(self):
        user = User.objects.create_user(username='zhaoliu', password='pw')
        user.first_name = '赵六'
        user.save(update_fields=['first_name'])
        rendered = Template(
            '{% load person %}{{ person|person_label }}'
        ).render(Context({'person': user}))
        self.assertEqual(rendered, '赵六（zhaoliu）')


class AccountProfileAdminEditTests(TestCase):
    """权限页必须能修改工号与部门（此前只能改权限和钉钉）。"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='account-admin', password='pw', email='',
        )
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.target = User.objects.create_user(username='target-user', password='pw')
        self.department = Department.objects.create(name='售后部', code='AFTER')
        self.other_department = Department.objects.create(name='研发部', code='RND')
        UserProfile.objects.create(
            user=self.target, employee_no='EMP-OLD', department=self.department,
        )
        self.client.force_login(self.admin)

    def test_admin_can_change_employee_no_and_department(self):
        response = self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(self.target.pk),
            'employee_no': 'EMP-NEW',
            'department_id': str(self.other_department.pk),
            'permissions': ['warehouse_outbound'],
        })

        self.assertRedirects(response, '/permissions/')
        profile = UserProfile.objects.get(user=self.target)
        self.assertEqual(profile.employee_no, 'EMP-NEW')
        self.assertEqual(profile.department_id, self.other_department.pk)
        self.assertTrue(self.target.groups.filter(name='warehouse_outbound').exists())
        self.assertTrue(OperationAuditLog.objects.filter(
            action='account_profile_updated',
        ).exists())

    def test_duplicate_employee_no_is_rejected(self):
        other = User.objects.create_user(username='other-user', password='pw')
        UserProfile.objects.create(user=other, employee_no='EMP-TAKEN')

        response = self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(self.target.pk),
            'employee_no': 'EMP-TAKEN',
            'permissions': ['warehouse_outbound'],
        })

        self.assertRedirects(response, '/permissions/')
        profile = UserProfile.objects.get(user=self.target)
        self.assertEqual(profile.employee_no, 'EMP-OLD')

    def test_keeping_own_employee_no_is_allowed(self):
        response = self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(self.target.pk),
            'employee_no': 'EMP-OLD',
            'department_id': str(self.department.pk),
            'permissions': ['warehouse_entry'],
        })

        self.assertRedirects(response, '/permissions/')
        self.assertEqual(UserProfile.objects.get(user=self.target).employee_no, 'EMP-OLD')

    def test_empty_employee_no_is_now_allowed(self):
        """公司没有统一工号体系，工号改为选填后允许清空。"""
        response = self.client.post('/permissions/', {
            'action': 'assign_role',
            'user_id': str(self.target.pk),
            'employee_no': '',
            'permissions': ['warehouse_entry'],
        })

        self.assertRedirects(response, '/permissions/')
        self.assertIsNone(UserProfile.objects.get(user=self.target).employee_no)

    def test_page_renders_employee_no_and_department_inputs(self):
        response = self.client.get('/permissions/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="employee_no"')
        self.assertContains(response, 'name="department_id"')
        self.assertContains(response, 'EMP-OLD')
