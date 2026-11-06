from django import forms

from .models import SiteSettings

MAX_LOGO_SIZE = 2 * 1024 * 1024


def _validate_logo(file_obj):
    if not file_obj:
        return file_obj
    if file_obj.size > MAX_LOGO_SIZE:
        raise forms.ValidationError('Logo 文件不能超过 2 MB。')
    if not file_obj.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        raise forms.ValidationError('Logo 只支持 PNG、JPG 或 JPEG。')
    return file_obj


class SiteSettingsForm(forms.ModelForm):
    clear_logo = forms.BooleanField(label='清除站点 Logo', required=False)
    clear_label_logo = forms.BooleanField(label='清除标签彩色 Logo', required=False)
    clear_label_logo_mono = forms.BooleanField(label='清除标签单色 Logo', required=False)

    class Meta:
        model = SiteSettings
        fields = ['site_name', 'subtitle', 'logo', 'label_logo', 'label_logo_mono']

    def clean_logo(self):
        return _validate_logo(self.cleaned_data.get('logo'))

    def clean_label_logo(self):
        return _validate_logo(self.cleaned_data.get('label_logo'))

    def clean_label_logo_mono(self):
        return _validate_logo(self.cleaned_data.get('label_logo_mono'))

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