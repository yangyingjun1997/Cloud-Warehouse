from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from io import BytesIO

from openpyxl import Workbook

User = get_user_model()


class ImportUnknownTemplateTests(TestCase):
    """未知模板必须给出明确提示，而不是 500。"""

    def setUp(self):
        self.user = User.objects.create_user(username='import-user', password='pw')
        self.user.groups.add(Group.objects.get_or_create(name='warehouse_admin')[0])
        self.user.groups.add(Group.objects.get_or_create(name='warehouse_import')[0])
        self.client.force_login(self.user)

    def _xlsx(self, sheet_name):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_name
        sheet.append(['随便一列', '另一列'])
        sheet.append(['a', 'b'])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def test_unknown_template_returns_200_with_message(self):
        upload = SimpleUploadedFile(
            'unknown.xlsx',
            self._xlsx('无关工作表'),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        response = self.client.post('/warehouse/import/', {
            'workbook': upload,
            'mode': 'incremental',
        })

        # 关键：不能是 500
        self.assertEqual(response.status_code, 200)
        messages = [str(message) for message in response.context['messages']]
        self.assertTrue(
            any('无法识别' in text for text in messages),
            f'应提示模板无法识别，实际消息：{messages}',
        )

    def test_import_page_still_loads(self):
        response = self.client.get('/warehouse/import/')
        self.assertEqual(response.status_code, 200)
