from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from .upload_validation import MAX_IMAGE_PIXELS, sanitize_image_upload, validate_image_upload


def image_upload(*, image_format='PNG'):
    output = BytesIO()
    Image.new('RGB', (8, 8), color='white').save(output, format=image_format)
    return SimpleUploadedFile(
        f'test.{image_format.lower()}',
        output.getvalue(),
        content_type=f'image/{image_format.lower()}',
    )


class UploadValidationTests(SimpleTestCase):
    def test_supported_image_is_reencoded_as_png(self):
        sanitized = sanitize_image_upload(image_upload())

        self.assertTrue(sanitized.name.endswith('.png'))
        with Image.open(sanitized) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (8, 8))

    def test_unsupported_decoded_format_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, '只支持 JPEG、PNG 或 WebP'):
            validate_image_upload(image_upload(image_format='GIF'))

    @patch('apps.common.upload_validation.Image.open')
    def test_excessive_pixel_count_is_rejected_before_decode(self, image_open):
        image = MagicMock()
        image.__enter__.return_value = image
        image.format = 'PNG'
        image.width = MAX_IMAGE_PIXELS + 1
        image.height = 1
        image_open.return_value = image
        upload = SimpleUploadedFile('large.png', b'placeholder', content_type='image/png')

        with self.assertRaisesMessage(ValidationError, '图片像素过大'):
            validate_image_upload(upload)

        image.verify.assert_not_called()
