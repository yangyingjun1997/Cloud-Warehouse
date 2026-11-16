from django.core.management.base import BaseCommand

from apps.notifications.outbox import process_due_outbox


class Command(BaseCommand):
    help = '发送已到期的钉钉 outbox 消息，失败时按指数退避重试。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--max-attempts', type=int, default=8)

    def handle(self, *args, **options):
        result = process_due_outbox(
            limit=options['limit'],
            max_attempts=options['max_attempts'],
        )
        self.stdout.write(
            '钉钉发送队列处理完成：'
            f'认领 {result["claimed"]}，送达 {result["delivered"]}，'
            f'待重试 {result["retrying"]}，终止失败 {result["failed"]}。'
        )
