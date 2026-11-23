"""按母版批量补录资产持有人。

用法：
    # 先预览，不写库
    python manage.py import_asset_holders holders.csv --dry-run

    # 确认无误后写入
    python manage.py import_asset_holders holders.csv

CSV 表头（列名可用别名，见 holder_import.COLUMN_ALIASES）：
    系统编号,持有人账号,持有人姓名,部门,外部持有人

约束：
- 只按「系统编号」(AST-xxxx) 匹配，不猜身份
- 只改持有人与部门，不改状态/仓库/库位/金额
- 姓名匹配要求全库唯一，有歧义会拒绝并提示改用账号
- 每行独立事务 + 逐行审计
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.common.permissions import is_system_administrator
from apps.inventory.holder_import import (
    HolderImportError,
    apply_holder_rows,
    parse_holder_csv,
)

User = get_user_model()


class Command(BaseCommand):
    help = '按母版 CSV 批量补录资产持有人（只按系统编号匹配，不猜身份）。'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', help='母版 CSV 文件路径')
        parser.add_argument(
            '--actor', default='',
            help='执行人账号（写入审计用）。默认为第一个超级管理员。',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只预览将发生的变更，不写数据库。',
        )
        parser.add_argument(
            '--limit-preview', type=int, default=30,
            help='预览最多打印多少行，默认 30。',
        )

    def handle(self, *args, **options):
        path = options['csv_file']
        try:
            with open(path, 'rb') as handle:
                content = handle.read()
        except OSError as exc:
            raise CommandError('无法读取文件 %s：%s' % (path, exc))

        try:
            rows, parse_errors = parse_holder_csv(content)
        except HolderImportError as exc:
            raise CommandError(str(exc))

        if parse_errors:
            self.stdout.write(self.style.WARNING(
                '有 %d 行在解析阶段被跳过：' % len(parse_errors)
            ))
            for line_no, reason in parse_errors[:20]:
                self.stdout.write('  第 %d 行：%s' % (line_no, reason))
            if len(parse_errors) > 20:
                self.stdout.write('  ... 另有 %d 行' % (len(parse_errors) - 20))

        if not rows:
            raise CommandError('没有可处理的数据行。')

        actor_name = options['actor'].strip()
        if actor_name:
            actor = User.objects.filter(username=actor_name).first()
        else:
            actor = User.objects.filter(is_superuser=True, is_active=True).first()
        if not actor:
            raise CommandError('找不到执行人账号，请用 --actor 指定。')
        if not (is_system_administrator(actor) or actor.is_superuser):
            raise CommandError('执行人必须是系统管理员。')

        dry_run = options['dry_run']
        self.stdout.write(
            '共 %d 行有效数据，执行人 %s，模式：%s'
            % (len(rows), actor.get_username(), '预览（不写库）' if dry_run else '写入')
        )

        result = apply_holder_rows(rows, actor=actor, dry_run=dry_run)

        limit = options['limit_preview']
        for item in result['preview'][:limit]:
            self.stdout.write(
                '  第 %s 行 %s %s -> %s（%s）'
                % (item['line'], item['code'], item['action'],
                   item.get('holder', ''), item.get('fields', '无'))
            )
        if len(result['preview']) > limit:
            self.stdout.write('  ... 另有 %d 行未显示' % (len(result['preview']) - limit))

        if result['failed']:
            self.stdout.write(self.style.ERROR(
                '失败 %d 行：' % len(result['failed'])
            ))
            for line_no, reason in result['failed'][:30]:
                self.stdout.write('  第 %d 行：%s' % (line_no, reason))

        verb = '将更新' if dry_run else '已更新'
        self.stdout.write(self.style.SUCCESS(
            '%s %d 行，无变化 %d 行，失败 %d 行。'
            % (verb, result['updated'], result['skipped'], len(result['failed']))
        ))
        if dry_run:
            self.stdout.write(
                '这是预览结果，未写入数据库。确认无误后去掉 --dry-run 再执行一次。'
            )
