"""登录页面的安全增强视图。"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import views as auth_views

from .auth_throttle import (
    clear_account_failures,
    get_client_ip,
    is_blocked,
    record_failure,
)
from .security_audit import record_login_event


class RateLimitedAuthenticationForm(AuthenticationForm):
    """兼容 Django 登录行为并增加账号/IP 失败限流。"""

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def clean(self):
        # 先执行字段级清理，确保锁定账号不会触发认证后端查询。
        cleaned_data = forms.Form.clean(self)
        username = cleaned_data.get('username', '')
        client_ip = get_client_ip(self.request) if self.request else 'unknown'
        if is_blocked(username=username, client_ip=client_ip):
            record_login_event(
                event_type='login_blocked',
                username=username,
                client_ip=client_ip,
                metadata={'reason': 'rate_limit'},
            )
            raise forms.ValidationError('登录失败次数过多，请稍后再试。')

        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            record_failure(username=username, client_ip=client_ip)
            record_login_event(
                event_type='login_failed',
                username=username,
                client_ip=client_ip,
            )
            raise

        clear_account_failures(username)
        return cleaned_data

    def get_user(self):
        return getattr(self, 'user_cache', None)


class RateLimitedLoginView(auth_views.LoginView):
    authentication_form = RateLimitedAuthenticationForm
    template_name = 'registration/login.html'
