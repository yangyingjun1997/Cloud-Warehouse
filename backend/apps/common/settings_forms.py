from django import forms
from django.core.files.uploadedfile import InMemoryUploadedFile
from io import BytesIO

from .models import SiteSettings

MAX_LOGO_SIZE = 2 * 1024 * 1024

# 浏览器里 logo 最大只显示到约 34px 高，标签 logo 约 9mm（300dpi 下不足 110px）。
# 原图 566×530 却要 79 KB，每页都要下载，是首屏变慢的主要原因。
MAX_LOGO_EDGE_PX = 320


def _validate_logo(file_obj):
    if not file_obj:
        return file_obj
    if file_obj.size > MAX_LOGO_SIZE:
        raise forms.ValidationError('Logo 文件不能超过 2 MB。')
    if not file_obj.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        raise forms.ValidationError('Logo 只支持 PNG、JPG 或 JPEG。')
    return file_obj


def _shrink_logo(file_obj):
    """把上传的 Logo 缩到展示所需尺寸，并做调色板量化。

    展示位最大约 320px，超过这个尺寸对肉眼没有区别，只会拖慢每页加载。
    实测 566×530 的 79 KB 图，缩到 320px + 128 色量化后约 17 KB（降 79%），
    肉眼几乎看不出差别。处理失败时原样返回，不能让压缩问题挡住保存。
    """
    if not file_obj:
        return file_obj
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow 是必装依赖
        return file_obj
    try:
        file_obj.seek(0)
        image = Image.open(file_obj)
        image.load()
    except Exception:  # noqa: BLE001 - 无法解码就交给后续流程
        file_obj.seek(0)
        return file_obj

    working = image.convert('RGBA') if image.mode in ('RGBA', 'LA', 'P') else image.convert('RGB')
    if max(working.size) > MAX_LOGO_EDGE_PX:
        working.thumbnail((MAX_LOGO_EDGE_PX, MAX_LOGO_EDGE_PX), Image.LANCZOS)

    buffer = BytesIO()
    try:
        # 有透明通道时保留它（LOGO 常见），否则量化成 128 色足以展示。
        if working.mode == 'RGBA':
            working.save(buffer, format='PNG', optimize=True)
        else:
            working.quantize(colors=128, method=Image.MEDIANCUT).save(
                buffer, format='PNG', optimize=True,
            )
    except Exception:  # noqa: BLE001
        file_obj.seek(0)
        return file_obj

    data = buffer.getvalue()
    if len(data) >= getattr(file_obj, 'size', 0):
        # 压缩后反而更大（例如很小的图）就保留原文件
        file_obj.seek(0)
        return file_obj
    name = (getattr(file_obj, 'name', 'logo') or 'logo').rsplit('.', 1)[0] + '.png'
    return InMemoryUploadedFile(
        BytesIO(data), 'logo', name, 'image/png', len(data), None,
    )


class SiteSettingsForm(forms.ModelForm):
    clear_logo = forms.BooleanField(label='清除站点 Logo', required=False)
    clear_label_logo = forms.BooleanField(label='清除标签彩色 Logo', required=False)
    clear_label_logo_mono = forms.BooleanField(label='清除标签单色 Logo', required=False)

    class Meta:
        model = SiteSettings
        fields = ['site_name', 'subtitle', 'logo', 'label_logo', 'label_logo_mono']

    def clean_logo(self):
        return _shrink_logo(_validate_logo(self.cleaned_data.get('logo')))

    def clean_label_logo(self):
        return _shrink_logo(_validate_logo(self.cleaned_data.get('label_logo')))

    def clean_label_logo_mono(self):
        return _shrink_logo(_validate_logo(self.cleaned_data.get('label_logo_mono')))

    def save(self, commit=True):
        instance = super().save(commit=False)
        for field, clear_field in (
            ('logo', 'clear_logo'),
            ('label_logo', 'clear_label_logo'),
            ('label_logo_mono', 'clear_label_logo_mono'),
        ):
            if self.cleaned_data.get(clear_field):
                setattr(instance, field, None)
        if commit:
            instance.save()
        return instance