import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
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


class ImmutableAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('审计留痕记录只允许新增，不允许批量修改。')

    def delete(self):
        raise ValidationError('审计留痕记录不允许批量删除。')


class ImmutableAuditManager(models.Manager.from_queryset(ImmutableAuditQuerySet)):
    pass


class ImmutableAuditMixin(models.Model):
    """审计留痕记录的应用层防篡改保护。

    只增不改：禁止通过实例或 QuerySet 更新、删除既有记录。
    数据库原始 SQL 仍需由 PostgreSQL 权限或触发器提供最终保护。
    """

    objects = ImmutableAuditManager()

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
        # 保持 PROTECT：数据库触发器禁止 UPDATE 审计表，SET_NULL 会在删号时
        # 发出 UPDATE 而被拒绝。有审计记录的账号不允许硬删，离职改用「停用」。
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='operation_audit_logs',
    )
    # 冗余快照：用于展示，以及在将来需要松绑外键时仍能追溯操作人。
    actor_username = models.CharField(
        '操作人账号快照', max_length=150, blank=True, default='',
    )
    actor_display_name = models.CharField(
        '操作人姓名快照', max_length=150, blank=True, default='',
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
    # 为空表示长期令牌，不会自然过期；只能通过撤销或轮换失效。
    expires_at = models.DateTimeField(null=True, blank=True)
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
        if self.revoked_at is not None:
            return False
        # expires_at 为空 = 长期令牌，永不自然过期。
        return self.expires_at is None or self.expires_at > timezone.now()

    @property
    def is_never_expiring(self):
        return self.expires_at is None


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


class SiteSettings(models.Model):
    """单例站点品牌设置，只有系统管理员可以修改。"""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    site_name = models.CharField('系统名称', max_length=60, default='售后仓库')
    subtitle = models.CharField('副标题', max_length=80, default='资产与出入库管理')
    logo = models.ImageField('站点 Logo', upload_to='branding/', blank=True, null=True)
    label_logo = models.ImageField('标签彩色 Logo', upload_to='branding/', blank=True, null=True)
    label_logo_mono = models.ImageField('标签单色 Logo', upload_to='branding/', blank=True, null=True)
    # 域名到期提醒：域名每年续费一次，容易忘。填了到期日之后，
    # 剩余 30 天起在页面顶部提示，10 天内升级为醒目警告。
    domain_expiry_date = models.DateField(
        '域名到期日', blank=True, null=True,
        help_text='填写后，到期前 30 天会在页面顶部提醒续费。',
    )
    domain_name = models.CharField('域名', max_length=120, blank=True, default='')
    updated_by = models.CharField('最后修改人', max_length=64, blank=True, default='')

    class Meta:
        verbose_name = '站点设置'
        verbose_name_plural = '站点设置'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        try:
            return cls.objects.filter(pk=1).first()
        except (OperationalError, ProgrammingError):
            # 迁移尚未应用或数据库只读时，页面继续使用默认品牌。
            return None
