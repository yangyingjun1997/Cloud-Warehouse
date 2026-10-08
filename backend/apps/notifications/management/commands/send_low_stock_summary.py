from django.core.management.base import BaseCommand

from apps.notifications.low_stock import send_weekly_summary


class Command(BaseCommand):
    help = '同步安全库存预警状态，并向钉钉发送仍未恢复的每周汇总。'

    def handle(self, *args, **options):
        count = send_weekly_summary()
        if count:
            self.stdout.write(self.style.SUCCESS(f'已发送安全库存汇总，共 {count} 项。'))
        else:
            self.stdout.write(self.style.SUCCESS('当前没有低库存项目，无需发送汇总。'))
