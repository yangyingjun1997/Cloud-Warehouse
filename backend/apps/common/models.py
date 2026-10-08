import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AuditMixin(TimeStampedModel):
    created_by = models.CharField(max_length=64, blank=True, default='')
    updated_by = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        abstract = True


class UserListPreference(UUIDModel, TimeStampedModel):
    """Per-user column choices for dense management lists."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='list_preferences')
    list_key = models.CharField(max_length=80)
    columns = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = ('user', 'list_key')
        verbose_name = '列表展示偏好'
        verbose_name_plural = '列表展示偏好'


class ImmutableAuditMixin(models.Model):
    """审计留痕记录的应用层防篡改保护。

    只增不改：禁止通过 ORM 更新或删除既有记录；如需修正必须走数据库层
    并留下独立说明。仅拦截 save()/delete()，不影响 raw SQL 与批量操作，
    日常使用（管理员后台、脚本、服务层）会立即抛出 ValidationError。
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('审计留痕记录只允许新增，不允许修改。')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('审计留痕记录不允许删除。')


class OperationAuditLog(ImmutableAuditMixin, UUIDModel, TimeStampedModel):
    """Append-only audit trail for changes outside the workflow transaction ledger."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operation_audit_logs',
    )
    action = models.CharField(max_length=80)
    target_model = models.CharField(max_length=120)
    target_id = models.UUIDField(null=True, blank=True)
    summary = models.CharField(max_length=255, blank=True, default='')
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '业务操作审计日志'
        verbose_name_plural = '业务操作审计日志'


class DailySequence(UUIDModel, TimeStampedModel):
    """Daily counters used by human-readable business document numbers."""

    prefix = models.CharField(max_length=16)
    sequence_date = models.DateField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['prefix', 'sequence_date'],
                name='unique_daily_sequence_prefix_date',
            ),
        ]
        verbose_name = '单据日序号'
        verbose_name_plural = '单据日序号'


class LoginThrottleState(UUIDModel, TimeStampedModel):
    """登录失败限流状态。

    key 只保存账号/IP 的 SHA-256 摘要，不保存可直接识别的登录账号或地址。
    该表使用数据库保存状态，保证 Gunicorn 多进程之间共享计数。
    """

    key = models.CharField(max_length=128, unique=True)
    failure_count = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = '登录限流状态'
        verbose_name_plural = '登录限流状态'


class ApiThrottleState(UUIDModel, TimeStampedModel):
    """匿名 API 请求限流状态，不保存明文账号或 IP。"""

    key = models.CharField(max_length=128, unique=True)
    request_count = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()

    class Meta:
        ordering = ['-updated_at']
        verbose_name = '接口限流状态'
        verbose_name_plural = '接口限流状态'


class ApiAccessToken(UUIDModel, TimeStampedModel):
    """可过期、可撤销、可轮换的 API 访问令牌。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='api_access_tokens',
    )
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=16)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replaced_tokens',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'API 访问令牌'
        verbose_name_plural = 'API 访问令牌'

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()


class SecurityAuditEvent(ImmutableAuditMixin, UUIDModel, TimeStampedModel):
    """认证和安全相关事件的只增不改记录。"""

    event_type = models.CharField(max_length=48)
    subject_hash = models.CharField(max_length=64, blank=True, default='')
    ip_hash = models.CharField(max_length=64, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_type', 'created_at']),
        ]
        verbose_name = '安全审计事件'
        verbose_name_plural = '安全审计事件'
