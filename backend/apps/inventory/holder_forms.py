"""资产持有人登记表单。

与状态校正分开：这里只补录「谁在用这件资产」，不改资产状态。
放在独立模块便于复用与测试。
"""

from django import forms
from django.contrib.auth import get_user_model

from .holder_services import HOLDER_REGISTRABLE_STATUSES
from .models import Asset


class AssetHolderRegistrationForm(forms.Form):
    expected_asset_updated_at = forms.DateTimeField(
        label='',
        widget=forms.HiddenInput,
        error_messages={
            'required': '资产版本信息缺失，请刷新页面后重新填写。',
            'invalid': '资产版本信息无效，请刷新页面后重新填写。',
        },
    )
    holder = forms.ModelChoiceField(
        label='系统内账号',
        queryset=get_user_model().objects.none(),
        required=False,
        help_text='从系统账号中选择持有人；与下面的外部持有人二选一。',
    )
    external_holder_name = forms.CharField(
        label='外部持有人姓名', max_length=120, required=False,
        help_text='持有人不在系统内时填写姓名，并在下方补充单位与电话。',
    )
    external_holder_company = forms.CharField(
        label='外部持有人单位', max_length=120, required=False,
    )
    external_holder_phone = forms.CharField(
        label='外部持有人电话', max_length=32, required=False,
    )
    reason = forms.CharField(
        label='登记原因', widget=forms.Textarea(attrs={'rows': 3}),
        help_text='例如：历史台账补录、现场交接未走流程等。会写入审计与履历。',
    )

    def __init__(self, *args, asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asset = asset
        user_model = get_user_model()
        self.fields['holder'].queryset = (
            user_model.objects.filter(is_active=True).order_by('first_name', 'username')
        )
        if asset and not self.is_bound:
            self.initial['expected_asset_updated_at'] = asset.updated_at

    def clean(self):
        cleaned = super().clean()
        holder = cleaned.get('holder')
        external_name = (cleaned.get('external_holder_name') or '').strip()

        if holder and external_name:
            raise forms.ValidationError('系统内账号与外部持有人只能二选一。')
        if not holder and not external_name:
            raise forms.ValidationError('请选择系统内账号，或填写外部持有人姓名。')

        if self.asset and self.asset.status not in HOLDER_REGISTRABLE_STATUSES:
            raise forms.ValidationError(
                f'当前状态为「{self.asset.get_status_display()}」，不能登记持有人。'
                '只有在资产已离开仓库时才需要登记持有人；在库资产请走借出或领用流程。'
            )
        return cleaned
