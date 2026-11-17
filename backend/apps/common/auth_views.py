"""登录页面的安全增强视图。"""

import logging

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import views as auth_views
from django.db import transaction

from apps.accounts.account_notifications import notify_account_request_submitted
from apps.accounts.session_services import register_account_session

logger = logging.getLogger(__name__)

from .auth_throttle import (
    clear_account_failures,
    get_client_ip,
    is_blocked,
    record_failure,
)
from .security_audit import record_login_event


class RateLimitedAuthenticationForm(AuthenticationForm):
    """兼容 Django 登录行为并增加账号/IP 失败限流。"""

    # 统一使用项目文案：模板现在直接渲染表单错误（为了能显示"账号已停用"），
    # 因此默认的 invalid_login 文案必须在这里改，而不是写死在模板里。
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': '用户名或密码不正确，请重新输入。',
        'inactive': '用户名或密码不正确，请重新输入。',
    }

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
            # 密码正确但账号被停用时，给明确提示比"密码不正确"更有用；
            # 密码错误时仍统一提示，避免把"账号是否存在"暴露给猜密码的人。
            if self._is_disabled_account(username, cleaned_data):
                record_login_event(
                    event_type='login_disabled_account',
                    username=username,
                    client_ip=client_ip,
                )
                raise forms.ValidationError(
                    '该账号已停用，无法登录。如需恢复使用请联系系统管理员。'
                ) from None
            record_failure(username=username, client_ip=client_ip)
            record_login_event(
                event_type='login_failed',
                username=username,
                client_ip=client_ip,
            )
            raise

        clear_account_failures(username)
        return cleaned_data

    def _is_disabled_account(self, username, cleaned_data) -> bool:
        """判断"用户名+密码都对，但账号已停用"。

        只在凭据本身正确时才为真，因此不会泄露账号是否存在。
        """
        password = cleaned_data.get('password') or self.data.get('password') or ''
        if not username or not password:
            return False
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None or user.is_active:
            return False
        return user.check_password(password)

    def get_user(self):
        return getattr(self, 'user_cache', None)


class RateLimitedLoginView(auth_views.LoginView):
    authentication_form = RateLimitedAuthenticationForm
    template_name = 'registration/login.html'

    def form_valid(self, form):
        """完成登录并记录不含明文账号/IP 的成功事件。"""

        with transaction.atomic():
            response = super().form_valid(form)
            register_account_session(self.request, form.get_user())
            record_login_event(
                event_type='login_succeeded',
                username=form.get_user().get_username(),
                client_ip=get_client_ip(self.request),
            )
        return response


class RestrictedPasswordChangeView(auth_views.PasswordChangeView):
    """密码修改只接受浏览器表单实际使用的方法。"""

    http_method_names = ['get', 'post', 'head', 'options']


class AccountRequestForm(forms.Form):
    """登录页「申请开通账号」表单。

    不做自助注册：只收集信息交给管理员核对后建号，避免陌生人仅凭内网地址拿到账号。
    部门不在申请人这边填写：申请人并不清楚系统里配置了哪些部门名，填了也是错的，
    建号时由管理员从部门字典里选。
    """

    real_name = forms.CharField(label='姓名', max_length=50)
    phone = forms.CharField(label='联系电话', max_length=32)
    reason = forms.CharField(
        label='申请说明', max_length=500, required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean_real_name(self):
        value = ' '.join(self.cleaned_data.get('real_name', '').split())
        if not value:
            raise forms.ValidationError('请填写姓名。')
        return value

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '').strip()
        if not value:
            raise forms.ValidationError('请填写联系电话。')
        return value


def account_request_create(request):
    """接收账号开通申请。同一姓名+电话不重复创建待处理申请。"""
    from django.contrib import messages
    from django.shortcuts import redirect, render

    from apps.accounts.models import AccountRequest

    if request.method == 'POST':
        form = AccountRequestForm(request.POST)
        if form.is_valid():
            real_name = form.cleaned_data['real_name']
            phone = form.cleaned_data['phone']
            already_pending = AccountRequest.objects.filter(
                real_name=real_name,
                phone=phone,
                status=AccountRequest.Status.PENDING,
            ).exists()
            if already_pending:
                messages.info(request, '您已有待处理的开通申请，管理员会尽快处理，无需重复提交。')
                return redirect('login')
            account_request = AccountRequest.objects.create(
                real_name=real_name,
                phone=phone,
                reason=form.cleaned_data.get('reason', '').strip(),
            )
            # 过去提交后只写库不发通知，管理员不登录就不知道有人申请。
            try:
                notify_account_request_submitted(account_request)
            except Exception:
                # 通知失败不能让申请人看到报错：申请本身已经落库，
                # 审批人下次打开账号页仍能看到待办。
                logger.exception('账号开通申请通知发送失败 request_id=%s', account_request.id)
            messages.success(
                request,
                '申请已提交，请等待管理员核对后开通账号。管理员会通过您留下的电话联系。',
            )
            return redirect('login')
    else:
        form = AccountRequestForm()
    return render(request, 'registration/account_request.html', {'form': form})
