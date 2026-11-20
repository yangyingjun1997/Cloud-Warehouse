import logging

from datetime import date

from django.contrib.auth import get_user_model
from django.db import DatabaseError, OperationalError

from apps.common.models import SiteSettings

logger = logging.getLogger(__name__)
from apps.common.permissions import (
    can_approve_workflow,
    can_execute_inventory,
    can_import_inventory,
    can_manage_code_master,
    can_manage_inventory,
    can_view_financials,
    can_print_asset_labels,
    can_view_reports,
    is_system_administrator,
    is_warehouse_operator,
)
from apps.notifications.models import Notification

# 域名到期提醒阈值：30 天内提示、10 天内升级为警告
DOMAIN_WARN_DAYS = 30
DOMAIN_URGENT_DAYS = 10


def _domain_expiry_alert(site_settings):
    """返回域名到期提醒信息；未到期或无配置时返回 None。"""
    if not site_settings or not site_settings.domain_expiry_date:
        return None
    remaining = (site_settings.domain_expiry_date - date.today()).days
    if remaining > DOMAIN_WARN_DAYS:
        return None
    if remaining <= 0:
        level, text = 'error', f'域名已过期 {abs(remaining)} 天，请立即续费。'
    elif remaining <= DOMAIN_URGENT_DAYS:
        level, text = 'error', f'域名将在 {remaining} 天后到期，请尽快续费。'
    else:
        level, text = 'warning', f'域名将在 {remaining} 天后到期，记得安排续费。'
    return {
        'level': level,
        'remaining_days': remaining,
        'expiry_date': site_settings.domain_expiry_date,
        'domain_name': site_settings.domain_name or '',
        'message': text,
    }


def web_navigation(request):
    site_settings = SiteSettings.load()
    domain_alert = _domain_expiry_alert(site_settings)
    if not request.user.is_authenticated:
        return {'global_site_settings': site_settings, 'global_domain_alert': domain_alert}
    display_name = request.user.get_full_name().strip() or request.user.get_username()
    is_admin = is_system_administrator(request.user)
    return {
        'global_site_settings': site_settings,
        'global_domain_alert': domain_alert,
        # 只有管理员需要处理「账号资料不全」，普通用户看到只会困惑
        'global_incomplete_profile_count': _incomplete_profile_count() if is_admin else 0,
        'global_user_display_name': display_name,
        'global_user_avatar': display_name[:1] or request.user.get_username()[:1],
        'global_unread_notification_count': Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count(),
        'global_user_is_operator': is_warehouse_operator(request.user),
        'global_user_is_system_admin': is_admin,
        'global_can_manage_inventory': can_manage_inventory(request.user),
        'global_can_execute_inventory': can_execute_inventory(request.user),
        'global_can_approve_workflow': can_approve_workflow(request.user),
        'global_can_view_reports': can_view_reports(request.user),
        'global_can_import_inventory': can_import_inventory(request.user),
        'global_can_print_asset_labels': can_print_asset_labels(request.user),
        'global_can_manage_code_master': can_manage_code_master(request.user),
        'global_can_view_financials': can_view_financials(request.user),
    }


def _incomplete_profile_count():
    """统计缺少中文名（first_name）的启用账号数。

    账号由管理员代建，所以提醒对象是管理员而不是用户本人；
    中文名用于「姓名（账号）」展示，缺失时界面上只能看到拼音账号。
    """
    user_model = get_user_model()
    try:
        # 用统一的维护账号清单，而不是再写一遍字面量。
        # 早期这里写的是 'label_print'（下划线），线上实际账号为 'label-printer'
        # （连字符），导致打印账号一直被算进「缺中文名」的提醒里。
        from apps.accounts.system_accounts import exclude_system_accounts

        return (
            exclude_system_accounts(
                user_model.objects.filter(is_active=True, first_name='')
            ).count()
        )
    except (DatabaseError, OperationalError) as exc:
        # 只兜数据库层异常：这个函数在每个页面渲染时都会跑，
        # 数据库迁移未完成或连接中断时不该让整站 500，退化成「不显示提醒」即可。
        # 但其它异常（例如 system_accounts 导入写错）必须冒出来，不能被伪装成「0 个」。
        logger.warning('统计缺中文名账号数失败（本次不显示提醒）: %s', exc)
        return 0

