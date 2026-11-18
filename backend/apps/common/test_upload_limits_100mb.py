"""附件上传上限回归：100 MB 边界与后缀白名单。"""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.common.upload_validation import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    MAX_ATTACHMENT_SIZE,
    validate_attachment_upload,
)

MB = 1024 * 1024


def _pdf(size_bytes):
    return SimpleUploadedFile(
        'manual.pdf', b'%PDF-1.4\n' + b'0' * max(size_bytes - 9, 0),
        content_type='application/pdf',
    )


class AttachmentSizeLimitTests(SimpleTestCase):
    def test_limit_is_100mb(self):
        self.assertEqual(MAX_ATTACHMENT_SIZE, 100 * MB)

    def test_under_limit_passes(self):
        validate_attachment_upload(_pdf(50 * MB), ALLOWED_ATTACHMENT_SUFFIXES)

    def test_exactly_at_limit_passes(self):
        validate_attachment_upload(_pdf(MAX_ATTACHMENT_SIZE), ALLOWED_ATTACHMENT_SUFFIXES)

    def test_over_limit_rejected_with_clear_message(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_attachment_upload(_pdf(MAX_ATTACHMENT_SIZE + 1), ALLOWED_ATTACHMENT_SUFFIXES)
        self.assertIn('100 MB', str(ctx.exception))

    def test_image_limit_stays_at_10mb(self):
        """图片仍限 10 MB：图片会被 Pillow 解码，放大上限有内存风险。"""
        from apps.common.upload_validation import MAX_IMAGE_SIZE

        self.assertEqual(MAX_IMAGE_SIZE, 10 * MB)


class AttachmentSuffixTests(SimpleTestCase):
    def test_common_documents_allowed(self):
        for name in ('manual.pdf', 'contract.docx', 'sheet.xlsx', 'archive.zip', 'log.txt'):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b'x' * 100)
                validate_attachment_upload(upload, ALLOWED_ATTACHMENT_SUFFIXES)

    def test_executables_rejected(self):
        for name in ('setup.exe', 'run.bat', 'lib.dll'):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b'x' * 100)
                with self.assertRaises(ValidationError):
                    validate_attachment_upload(upload, ALLOWED_ATTACHMENT_SUFFIXES)
