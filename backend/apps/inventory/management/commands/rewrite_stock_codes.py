"""把历史导入的 STOCK-<hash> 耗材编码改成可读编码。

编码规则参考《编码规则.yaml》的 ERP 商品编码：
    商品大类(1) + 商品子类(1) + 品牌(2) + 计数(4)

当前阶段先落地「可读 + 稳定 + 可追溯」，格式为：
    STK-<仓库码>-<4位流水>
例如 STK-RND-0001 / STK-MKT-0001

之所以不直接套用 ERP 的「大类+子类+品牌」四段式：32 个耗材组里有相当一部分
（自研电源板、浪潮自组网、消防水炮、umi 数采设备等）在 yaml 里找不到唯一对应
分类，硬猜会引入错误编码。等运营侧确认分类后再迁移到完整 ERP 格式。

命令默认只预演，加 --apply 才写库。
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.audit import record_operation
from apps.inventory.models import StockItem

HASH_STOCK = re.compile(r'^STOCK-[0-9A-F]{12}$')
NEW_STOCK = re.compile(r'^STK-(?P<wh>[A-Z]{3})-(?P<no>\d{4})$')


class Command(BaseCommand):
    help = '把 STOCK-<hash> 形式的耗材编码改成可读的 STK-<仓库>-<流水> 格式。'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='真正写库')
        parser.add_argument('--user', default='admin', help='操作人账号')
        parser.add_argument(
            '--dry-run-count',
            action='store_true',
            help='只打印统计，不输出逐条对照',
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        try:
            actor = get_user_model().objects.get(username=options['user'])
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f'账号不存在：{options["user"]}') from exc

        targets = list(
            StockItem.objects.select_related('warehouse')
            .order_by('warehouse__code', 'name')
        )
        todo = [s for s in targets if HASH_STOCK.match(s.code or '')]
        keep = [s for s in targets if not HASH_STOCK.match(s.code or '')]

        self.stdout.write('=' * 70)
        self.stdout.write(f'耗材组共 {len(targets)} 个：待改 {len(todo)}，已是可读编码 {len(keep)}')
        self.stdout.write('=' * 70)
        for s in keep:
            self.stdout.write(f'  跳过 {s.code:<24} {s.name[:24]}')
        self.stdout.write('')

        # 按仓库分组生成新编码
        from collections import defaultdict

        by_wh = defaultdict(list)
        for s in todo:
            code = (s.warehouse.code if s.warehouse else 'UNK')[:3].upper()
            by_wh[code].append(s)

        # 已被占用的新式编码号段
        used = defaultdict(set)
        for s in keep:
            m = NEW_STOCK.match(s.code or '')
            if m:
                used[m.group('wh')].add(int(m.group('no')))

        plan = []
        for wh, items in sorted(by_wh.items()):
            items.sort(key=lambda x: x.name)
            counter = 1
            for s in items:
                while counter in used[wh]:
                    counter += 1
                new_code = f'STK-{wh}-{counter:04d}'
                used[wh].add(counter)
                counter += 1
                plan.append((s, new_code))

        self.stdout.write('-' * 70)
        self.stdout.write('对照表')
        self.stdout.write('-' * 70)
        for s, new_code in plan:
            if options['dry_run_count']:
                continue
            self.stdout.write(
                f'  {s.code:<22} → {new_code:<14} {s.name[:26]:<28} qty={s.quantity}'
            )
        if options['dry_run_count']:
            self.stdout.write(f'  （共 {len(plan)} 条，已省略明细）')

        if not options['apply']:
            self.stdout.write(
                self.style.WARNING('\n预演完成，未写库。加 --apply 执行。')
            )
            return

        # 两阶段写入：先全部改成临时唯一值，避免 unique 冲突
        with transaction.atomic():
            for index, (s, _new) in enumerate(plan):
                tmp = f'TMP-{index:04d}-{s.pk.hex[:8].upper()}'
                StockItem.objects.filter(pk=s.pk).update(code=tmp)
            for s, new_code in plan:
                before = s.code
                StockItem.objects.filter(pk=s.pk).update(code=new_code)
                s.refresh_from_db()
                record_operation(
                    actor,
                    'stock_item.code_rewritten',
                    s,
                    summary=f'耗材编码 {before} → {new_code}',
                    before={'code': before},
                    after={'code': new_code},
                )
                self.stdout.write(self.style.SUCCESS(f'  ✓ {new_code}  {s.name[:24]}'))
        self.stdout.write(self.style.SUCCESS(f'\n完成：改写 {len(plan)} 个耗材编码。'))