"""资产质保到期提醒。

背景（2026-11-23）：636 件资产里带 warranty_until 的不少，但系统里没有任何
「即将过保」的视图。到期日只是一列静态数据，不产生任何行动提示——
而设备过保后维修费用从「供应商承担」变成「自己承担」，这是资产管理的刚需。

设计取舍：
- 不建新模型。质保到期是「日期比较」，实时算即可，不需要 alert 状态表。
  理由：LowStockAlert 之所以需要表，是因为「库存从充足变不足」是事件，
  需要记录转变时刻以免重复通知；而「还有 30 天过保」每天都能重新算出来，
  存表反而要维护状态同步。
- 阈值：30 / 60 / 90 天可选，默认 60 天。分档是因为 30 天太临时、
  90 天太早，60 天够走完一次采购或续保流程。
- 已过保的单独一组，按过期时间倒序，优先处理最久的。
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.inventory.models import Asset

# 可选的提前提醒天数。
# 2026-11-23 实测：线上 29 件带质保日的资产最早也要 321 天后才到期，
# 只给 30/60/90 天窗口的话页面永远是空的，用户会以为功能坏了。
# 因此把窗口上限拉到 365 天，默认 90 天（够走完一次续保或采购流程）。
WARRANTY_WARNING_DAYS = (30, 60, 90, 180, 365)
DEFAULT_WARNING_DAYS = 90


def warranty_expiring_queryset(days=DEFAULT_WARNING_DAYS, *, include_expired=True):
    """返回即将过保（以及已过保）的资产查询集。

    days：距今天多少天内到期算「即将过保」
    include_expired：是否包含已经过期的资产
    """
    today = timezone.localdate()
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = DEFAULT_WARNING_DAYS
    if days not in WARRANTY_WARNING_DAYS:
        days = DEFAULT_WARNING_DAYS

    deadline = today + timedelta(days=days)
    base = Asset.objects.filter(warranty_until__isnull=False)

    if include_expired:
        condition = Q(warranty_until__lte=deadline)
    else:
        condition = Q(warranty_until__gte=today, warranty_until__lte=deadline)

    # 已报废/已售出/已退供应商的资产不再关心质保
    useless_statuses = {
        Asset.Status.SCRAPPED,
        Asset.Status.SOLD,
        Asset.Status.RETURNED_TO_VENDOR,
        Asset.Status.DISABLED,
    }
    return (
        base.filter(condition)
        .exclude(status__in=useless_statuses)
        .select_related('item_type', 'warehouse', 'location', 'current_holder')
        .order_by('warranty_until')
    )


def warranty_summary(days=DEFAULT_WARNING_DAYS):
    """汇总即将过保与已过保的数量，用于工作台指标卡。

    用 values_list 只取日期字段做聚合，不实例化 Asset 对象。
    早期写法用 .only('warranty_until') 遍历，与 queryset 里的
    select_related 冲突（Django 报 item_type 同时被 defer 和 traverse）。
    """
    today = timezone.localdate()
    dates = list(
        warranty_expiring_queryset(days).values_list('warranty_until', flat=True)
    )
    expired = sum(1 for value in dates if value and value < today)
    return {
        'expired': expired,
        'expiring': len(dates) - expired,
        'days': days,
    }


def days_left(asset) -> int | None:
    """距过保还有多少天；已过保返回负数；无质保日返回 None。"""
    if not asset.warranty_until:
        return None
    return (asset.warranty_until - timezone.localdate()).days
