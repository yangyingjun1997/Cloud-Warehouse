from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel, UUIDModel


class Notification(UUIDModel, TimeStampedModel):
    class Level(models.TextChoices):
        INFO = 'info', '信息'
        SUCCESS = 'success', '成功'
        WARNING = 'warning', '警告'
        DANGER = 'danger', '危险'

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=120)
    content = models.TextField()
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    is_read = models.BooleanField(default=False)
    related_model = models.CharField(max_length=120, blank=True, default='')
    related_object_id = models.CharField(max_length=64, blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        verbose_name = '通知'
        verbose_name_plural = '通知'

    def __str__(self) -> str:
        return self.title


class LowStockAlert(UUIDModel, TimeStampedModel):
    class SourceType(models.TextChoices):
        STOCK_ITEM = 'stock_item', '耗材配件'
        ITEM_TYPE = 'item_type', '单件资产类型'

    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_id = models.UUIDField()
    source_name = models.CharField(max_length=160)
    current_quantity = models.PositiveIntegerField(default=0)
    safety_stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=False)
    active_since = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-is_active', 'source_type', 'source_name']
        constraints = [
            models.UniqueConstraint(
                fields=['source_type', 'source_id'],
                name='unique_low_stock_alert_source',
            ),
        ]
        verbose_name = '安全库存预警状态'
        verbose_name_plural = '安全库存预警状态'

    def __str__(self) -> str:
        return f'{self.get_source_type_display()} - {self.source_name}'
