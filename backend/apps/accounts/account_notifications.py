"""账号治理相关通知。

账号开通申请过去只写数据库、不通知任何人，管理员不登录就完全不知道有人申请。
这里补齐两层通知：站内通知（进通知中心）+ 钉钉 outbox（异步投递，失败重试）。
"""

from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.notifications.models import Notification
from apps.notifications.outbox import enqueue_users_or_group, outbox_key


def account_reviewers():
    """所有有账号审批权限的人：系统管理员 + 持有 warehouse_account_review 的账号。"""
    user_model = get_user_model()
    return list(
        user_model.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name__in=['warehouse_admin', 'warehouse_account_review']))
        .distinct()
        .order_by('username')
    )


def notify_account_request_submitted(account_request, *, requested_by=None):
    """新开通申请：通知所有审批人。

    返回实际通知到的账号数，便于测试与排查。
    """
    reviewers = [
        user for user in account_reviewers()
        # 自己申请自己的情况（理论上不会发生）不通知本人
        if requested_by is None or user.pk != requested_by.pk
    ]
    if not reviewers:
        return 0

    title = '新的账号开通申请'
    # 站内通知保留完整信息：只有具备账号审批权限的人能看到，且需要登录。
    content = (
        f'{account_request.real_name} 提交了账号开通申请。\n'
        f'联系电话：{account_request.phone or "未填写"}\n'
        f'申请说明：{account_request.reason or "未填写"}\n'
        f'请登录「账号与权限」页面核对后建号或驳回。'
    )

    # 钉钉只发概要，不带手机号与自由文本说明（2026-11-23，P2-16）。
    # 原因：钉钉消息会经过钉钉服务器，且可能落在群会话里被更多成员看到；
    # 手机号属于个人敏感信息，申请人填写时并未预期它会被转发到第三方 IM。
    # 审批人收到概要后点进系统即可看到完整信息，业务上不需要在钉钉里看手机号。
    dingtalk_content = (
        f'{account_request.real_name} 提交了账号开通申请。\n'
        f'请登录「账号与权限」页面查看联系方式与申请说明，核对后建号或驳回。'
    )

    Notification.objects.bulk_create([
        Notification(
            recipient=user,
            title=title,
            content=content,
            level=Notification.Level.INFO,
            related_model='AccountRequest',
            related_object_id=str(account_request.id),
        )
        for user in reviewers
    ])

    enqueue_users_or_group(
        reviewers,
        title,
        dingtalk_content,
        idempotency_key=outbox_key('account_request', account_request.id, title),
    )
    return len(reviewers)
