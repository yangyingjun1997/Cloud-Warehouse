from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from apps.common.settings_forms import MAX_LOGO_EDGE_PX, SiteSettingsForm

User = get_user_model()

FORM_DATA = {'site_name': 'Test Site', 'subtitle': 'Subtitle'}


def _png_bytes(width, height, color=(20, 100, 90)):
    image = Image.new('RGB', (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class LogoShrinkTests(TestCase):
    def test_large_logo_is_downscaled(self):
        raw = _png_bytes(1200, 1100)
        upload = SimpleUploadedFile('big.png', raw, content_type='image/png')

        form = SiteSettingsForm(data=FORM_DATA, files={'logo': upload})
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data['logo']

        image = Image.open(cleaned)
        self.assertLessEqual(max(image.size), MAX_LOGO_EDGE_PX)
        self.assertLess(cleaned.size, len(raw))

    def test_small_logo_is_kept(self):
        raw = _png_bytes(64, 64)
        upload = SimpleUploadedFile('small.png', raw, content_type='image/png')

        form = SiteSettingsForm(data=FORM_DATA, files={'logo': upload})
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data['logo']
        image = Image.open(cleaned)
        self.assertLessEqual(max(image.size), MAX_LOGO_EDGE_PX)

    def test_non_image_with_png_suffix_does_not_crash(self):
        upload = SimpleUploadedFile(
            'fake.png', b'not-an-image', content_type='image/png',
        )
        form = SiteSettingsForm(data=FORM_DATA, files={'logo': upload})
        # 压缩失败不能抛异常；合法性由校验层负责
        form.is_valid()
