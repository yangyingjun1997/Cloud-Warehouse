from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.common.models import OperationAuditLog


User = get_user_model()


class LabelPrinterAccountProtectionTests(TestCase):
    """label-printer 是打印客户端专用接口账号，不允许在权限页误删。"""

    def setUp(self):
        self.admin = User.objects.create_user(username='protect-admin', password='pw')
        self.admin.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.printer = User.objects.create_user(username='label-printer', password='pw')
        self.printer.groups.add(Group.objects.get_or_create(name='warehouse_label_print')[0])
        self.client.force_login(self.admin)

    def test_label_printer_account_cannot_be_deleted(self):
        response = self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': str(self.printer.pk),
        })

        self.assertRedirects(response, '/permissions/')
        self.assertTrue(User.objects.filter(username='label-printer').exists())

    def test_other_print_account_can_still_be_deleted(self):
        other = User.objects.create_user(username='label-printer-2', password='pw')
        other.groups.add(Group.objects.get(name='warehouse_label_print'))
        response = self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': str(other.pk),
        })

        self.assertRedirects(response, '/permissions/')
        self.assertFalse(User.objects.filter(username='label-printer-2').exists())

    def test_ordinary_account_can_still_be_deleted(self):
        plain = User.objects.create_user(username='plain-user', password='pw')
        response = self.client.post('/permissions/', {
            'action': 'delete_user',
            'user_id': str(plain.pk),
        })

        self.assertRedirects(response, '/permissions/')
        self.assertFalse(User.objects.filter(username='plain-user').exists())
