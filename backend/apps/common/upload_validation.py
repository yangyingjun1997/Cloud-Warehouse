"""Shared upload validation and image sanitization helpers."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_SIZE = 10 * 1024 * 1024
# 2026-11-18 从 20 MB 提高到 100 MB：设备说明书、项目合同/图纸、CAD 导出件
# 经常超过 20 MB。注意 Nginx 的 client_max_body_size 必须同步调整，否则
# 请求会被反代层直接 413，用户只看到「上传失败」而不知道是大小限制。
MAX_ATTACHMENT_SIZE = 100 * 1024 * 1024
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
ALLOWED_IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_ATTACHMENT_SUFFIXES = IMAGE_SUFFIXES | {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt', '.log', '.csv', '.zip',
}


def validate_image_upload(uploaded_file, *, max_size=MAX_IMAGE_SIZE):
    """Validate image bytes instead of trusting the browser MIME type or suffix."""
    if not uploaded_file:
        raise ValidationError('请上传图片文件。')
    if uploaded_file.size > max_size:
        raise ValidationError(f'图片不能超过 {max_size // (1024 * 1024)} MB。')
    try:
        uploaded_file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(uploaded_file) as image:
                if image.format not in ALLOWED_IMAGE_FORMATS:
                    raise ValidationError('只支持 JPEG、PNG 或 WebP 图片。')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValidationError('图片像素过大，请缩小后重试。')
                image.verify()
    except ValidationError:
        raise
    except Image.DecompressionBombWarning as exc:
        raise ValidationError('图片像素过大，请缩小后重试。') from exc
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ValidationError('上传文件不是有效的图片。') from exc
    finally:
        uploaded_file.seek(0)


def sanitize_image_upload(uploaded_file, *, max_size=MAX_IMAGE_SIZE):
    """Decode and re-encode an image to remove payloads hidden in the original file."""
    validate_image_upload(uploaded_file, max_size=max_size)
    uploaded_file.seek(0)
    try:
        with Image.open(uploaded_file) as source:
            source.load()
            mode = 'RGBA' if 'A' in source.getbands() else 'RGB'
            image = source.convert(mode)
            output = BytesIO()
            image.save(output, format='PNG', optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ValidationError('图片无法重新编码，请更换文件后重试。') from exc
    finally:
        uploaded_file.seek(0)
    return ContentFile(output.getvalue(), name=f'{uuid4().hex}.png')


def validate_attachment_upload(uploaded_file, allowed_suffixes, *, max_size=MAX_ATTACHMENT_SIZE):
    """Validate generic attachments and inspect image files when applicable."""
    if not uploaded_file:
        raise ValidationError('请选择要上传的文件。')
    if uploaded_file.size > max_size:
        raise ValidationError(f'单个附件不能超过 {max_size // (1024 * 1024)} MB。')
    suffix = Path(uploaded_file.name or '').suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValidationError('不支持该文件格式，请上传允许的图片、文档、表格、日志或 ZIP 文件。')
    if suffix in IMAGE_SUFFIXES:
        validate_image_upload(uploaded_file, max_size=max_size)
    uploaded_file.seek(0)
