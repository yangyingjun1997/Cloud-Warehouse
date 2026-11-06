from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel, TimeStampedModel


class Department(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = '部门'
        verbose_name_plural = '部门'

    def __str__(self) -> str:
        return self.name


class UserProfile(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    employee_no = models.CharField(max_length=50, unique=True)
    phone = models.CharField(max_length=32, blank=True, default='')
    title = models.CharField(max_length=100, blank=True, default='')
    wx_union_id = models.CharField(max_length=128, blank=True, default='')
    is_wx_bound = models.BooleanField(default=False)
    dingtalk_user_id = models.CharField(max_length=128, blank=True, default='')
    is_dingtalk_bound = models.BooleanField(default=False)
    dingtalk_bound_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['employee_no']
        verbose_name = '用户档案'
        verbose_name_plural = '用户档案'

    def __str__(self) -> str:
        return f'{self.employee_no} {self.user.username}'


class AccountSession(UUIDModel, TimeStampedModel):
    """可由用户查看和撤销的登录会话登记。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='account_sessions',
        verbose_name='账号',
    )
    session_key = models.CharField('会话标识', max_length=64, unique=True)
    ip_hash = models.CharField('地址摘要', max_length=64)
    ip_hint = models.CharField('地址提示', max_length=64, blank=True, default='')
    user_agent_hash = models.CharField('设备摘要', max_length=64, blank=True, default='')
    device_label = models.CharField('设备说明', max_length=120, blank=True, default='')
    last_seen_at = models.DateTimeField('最近活动时间', default=timezone.now)
    revoked_at = models.DateTimeField('撤销时间', null=True, blank=True)
    revoked_reason = models.CharField('撤销原因', max_length=120, blank=True, default='')

    class Meta:
        ordering = ['-last_seen_at']
        indexes = [
            models.Index(fields=['user', 'revoked_at', 'last_seen_at']),
        ]
        verbose_name = '账号登录会话'
        verbose_name_plural = '账号登录会话'

    def __str__(self) -> str:
        return f'{self.user.get_username()} - {self.device_label or "未知设备"}'
