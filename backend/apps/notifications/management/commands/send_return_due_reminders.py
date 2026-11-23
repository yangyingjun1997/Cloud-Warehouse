from django.core.management.base import BaseCommand

from apps.inventory.borrow_health import orphan_borrowed_assets, summarize_borrow_health
from apps.notifications.return_reminders import send_return_due_reminders


class Command(BaseCommand):
    help = '发送三天内到期、当天到期及已逾期的借用归还提醒。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--show-orphans',
            action='store_true',
            help='列出「已借出但没有任何持有人信息」的资产（无法催办，需人工补录）。',
        )

    def handle(self, *args, **options):
        count = send_return_due_reminders()
        self.stdout.write(self.style.SUCCESS(f'归还提醒处理完成：新增 {count} 条提醒。'))

        # 2026-11-23 新增：暴露「借出但不知道在谁手上」的孤儿数据。
        # 线上实测 48 件借出资产的 current_holder 与 external_holder_name 全为空，
        # 都是导入时只填状态的历史数据。这类资产永远不会被催办，
        # 但此前没有任何地方提示，管理者看不到这个盲区。
        health = summarize_borrow_health()
        if health['orphan']:
            self.stdout.write(
                self.style.WARNING(
                    f"借用台账存在盲区：共 {health['total']} 件在外资产，"
                    f"其中 {health['orphan']} 件没有任何持有人信息，无法催办。"
                    f"请在「资产台账」中补录持有人，或用库位/部门字段回溯确认。"
                )
            )
            if options.get('show_orphans'):
                for asset in orphan_borrowed_assets()[:50]:
                    self.stdout.write(
                        f"  {asset.system_asset_no}  {asset.name}"
                        f"  （{asset.get_status_display()}）"
                    )
                remaining = health['orphan'] - 50
                if remaining > 0:
                    self.stdout.write(f'  ... 另有 {remaining} 件未列出')
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"借用台账完整：共 {health['total']} 件在外资产，均有持有人信息。"
                )
            )
