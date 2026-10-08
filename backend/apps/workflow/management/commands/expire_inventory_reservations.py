from django.core.management.base import BaseCommand

from apps.workflow.services import expire_pending_reservations


class Command(BaseCommand):
    help = '关闭已超过有效期的待审批申请，并释放其全部库存预约。'

    def handle(self, *args, **options):
        count = expire_pending_reservations()
        self.stdout.write(self.style.SUCCESS(f'预约过期处理完成：关闭 {count} 张申请。'))
