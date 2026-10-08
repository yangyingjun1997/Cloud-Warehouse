from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.inventory.models import Asset
from apps.common.permissions import PERMISSION_GROUPS
from apps.workflow.models import ReturnReminderLog, StockItemLoan, WorkflowRequest, WorkflowRequestLine

from .dingtalk import send_group_message, send_user_message
from .models import Notification

User = get_user_model()


def _stage(due_date, today):
    days = (due_date - today).days
    if days < 0:
        return 'overdue', f'已逾期 {abs(days)} 天'
    if days == 0:
        return 'due', '今天到期'
    if days <= 3:
        return 'upcoming', f'{days} 天后到期'
    return '', ''


def _operator_users():
    return User.objects.filter(
        Q(is_superuser=True) | Q(groups__name__in=['warehouse_staff', 'warehouse_admin', *PERMISSION_GROUPS]),
        is_active=True,
    ).distinct()


def _asset_due_rows(today):
    assets = Asset.objects.filter(
        status=Asset.Status.BORROWED,
        current_holder__isnull=False,
    ).select_related('current_holder')
    for asset in assets.iterator():
        line = WorkflowRequestLine.objects.select_related('request').filter(
            asset=asset,
            request__applicant=asset.current_holder,
            request__request_type=WorkflowRequest.RequestType.BORROW,
            request__status=WorkflowRequest.Status.DONE,
            request__expected_return_date__isnull=False,
        ).order_by('-request__transaction_date', '-created_at').first()
        if not line:
            continue
        stage, stage_label = _stage(line.request.expected_return_date, today)
        if stage:
            yield {
                'line': line,
                'recipient': asset.current_holder,
                'stage': stage,
                'stage_label': stage_label,
                'due_date': line.request.expected_return_date,
                'item_label': f'{asset.name}（{asset.asset_code}）',
                'quantity_label': '1 件',
            }


def _stock_due_rows(today):
    loans = StockItemLoan.objects.filter(
        status=StockItemLoan.Status.ACTIVE,
        outstanding_quantity__gt=0,
    ).select_related('borrower', 'stock_item', 'source_line__request')
    for loan in loans.iterator():
        stage, stage_label = _stage(loan.expected_return_date, today)
        if stage:
            yield {
                'line': loan.source_line,
                'recipient': loan.borrower,
                'stage': stage,
                'stage_label': stage_label,
                'due_date': loan.expected_return_date,
                'item_label': f'{loan.stock_item.name}（{loan.stock_item.code}）',
                'quantity_label': f'{loan.outstanding_quantity} {loan.stock_item.unit}',
            }


@transaction.atomic
def send_return_due_reminders(today=None):
    today = today or timezone.localdate()
    new_rows = []
    for row in [*_asset_due_rows(today), *_stock_due_rows(today)]:
        business_key = f'{today.isoformat()}:{row["stage"]}:{row["line"].id}'
        _, created = ReturnReminderLog.objects.get_or_create(
            business_key=business_key,
            defaults={
                'reminder_date': today,
                'recipient': row['recipient'],
                'request_line': row['line'],
                'stage': row['stage'],
            },
        )
        if created:
            new_rows.append(row)
    if not new_rows:
        return 0

    by_recipient = defaultdict(list)
    for row in new_rows:
        by_recipient[row['recipient']].append(row)
    notifications = []
    for recipient, rows in by_recipient.items():
        first = rows[0]
        detail = '；'.join(
            f'{row["item_label"]} {row["quantity_label"]}，{row["stage_label"]}'
            for row in rows
        )
        notifications.append(Notification(
            recipient=recipient,
            title='借用归还提醒',
            content=detail,
            level=Notification.Level.DANGER if any(row['stage'] == 'overdue' for row in rows) else Notification.Level.WARNING,
            related_model='WorkflowRequest',
            related_object_id=str(first['line'].request_id),
        ))
    operator_detail = '\n'.join(
        f'{index}. {row["recipient"].get_username()}：{row["item_label"]} {row["quantity_label"]}，'
        f'{row["stage_label"]}（{row["due_date"]:%Y-%m-%d}）'
        for index, row in enumerate(new_rows, start=1)
    )
    for operator in _operator_users():
        if operator not in by_recipient:
            notifications.append(Notification(
                recipient=operator,
                title='借用到期汇总',
                content=operator_detail,
                level=Notification.Level.WARNING,
            ))
    Notification.objects.bulk_create(notifications)

    def send_dingtalk_notifications():
        for recipient, rows in by_recipient.items():
            detail = '；'.join(
                f'{row["item_label"]} {row["quantity_label"]}，{row["stage_label"]}'
                for row in rows
            )
            send_user_message(recipient, '售后仓库：借用归还提醒', detail)
        send_group_message('售后仓库：借用归还提醒', operator_detail)

    transaction.on_commit(send_dingtalk_notifications)
    return len(new_rows)
