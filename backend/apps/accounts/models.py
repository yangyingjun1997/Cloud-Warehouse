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
    # 公司目前没有统一工号体系，外包同事可能完全没有工号：
    # 这里放开为可空，但保留唯一性（用于对接 HR/钉钉时的匹配键）。
    employee_no = models.CharField(max_length=50, unique=True, null=True, blank=True)
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


class AccountRequest(UUIDModel, TimeStampedModel):
    """登录页提交的「申请开通账号」。

    系统不开放自助注册：这里只收集申请信息，由管理员在权限页核对后建号。
    这样既避免管理员逐个口头收集信息，也不会让陌生人凭内网地址直接拿到账号。
    """

    class Status(models.TextChoices):
        PENDING = 'pending', '待处理'
        CREATED = 'created', '已建号'
        REJECTED = 'rejected', '已驳回'

    real_name = models.CharField('姓名', max_length=50)
    # 公司没有统一工号体系，申请人可能完全没有工号：改为可空，
    # 建号时由管理员按需补充。去重改用"姓名+电话"。
    employee_no = models.CharField('员工工号', max_length=50, blank=True, default='')
    department_text = models.CharField('部门', max_length=100, blank=True, default='')
    phone = models.CharField('联系电话', max_length=32)
    reason = models.TextField('申请说明', blank=True, default='')
    status = models.CharField(
        '处理状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handled_account_requests', verbose_name='处理人',
    )
    handled_at = models.DateTimeField('处理时间', null=True, blank=True)
    handle_note = models.CharField('处理说明', max_length=255, blank=True, default='')
    created_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_from_account_requests', verbose_name='建号结果',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = '账号开通申请'
        verbose_name_plural = '账号开通申请'
        constraints = [
            models.UniqueConstraint(
                fields=['real_name', 'phone'],
                condition=models.Q(status='pending'),
                name='unique_pending_account_request_per_person',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.real_name}（{self.employee_no}）{self.get_status_display()}'
