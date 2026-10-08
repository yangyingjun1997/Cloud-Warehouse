from django.core.management.base import BaseCommand

from apps.notifications.return_reminders import send_return_due_reminders


class Command(BaseCommand):
    help = '发送三天内到期、当天到期及已逾期的借用归还提醒。'

    def handle(self, *args, **options):
        count = send_return_due_reminders()
        self.stdout.write(self.style.SUCCESS(f'归还提醒处理完成：新增 {count} 条提醒。'))
