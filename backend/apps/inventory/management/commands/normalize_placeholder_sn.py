"""把非规范 SN 规范化为纯字母数字，原值留痕到备注。

处理策略按「能否保留真实信息」区分：

  A. 真实 SN + 注释   → 剥离注释，保留原 SN（只做空格→连字符）
       'A2 pro-10642(蓝牙码10902)'  → SN='A2-pro-10642'  备注='原SN：…；蓝牙码10902'
       '3112A1A040C0070（右）'      → SN='3112A1A040C0070' 备注='原SN：…；右'

  B. 研发NNN 占位符   → 重新编号 <前缀>-NNN，原值入备注
  C. IP 地址          → 重新编号 <前缀>-NNN，原 IP 及附加说明入备注
  D. 纯中文           → 重新编号 <前缀>-NNN，原值入备注

不改动的：已经是纯字母数字的正式 SN。

用户要求：任何被改动的资产，原 SN 全文必须写进 remarks，
保证仓管仍能按旧值检索到。

命令默认只预演，加 --apply 才写库。
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.audit import record_operation
from apps.inventory.models import Asset

PLACEHOLDER_RND = re.compile(r'^研发(?P<no>\d{3})$')
IP_HEAD = re.compile(r'^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})(?P<rest>.*)$')
CJK = re.compile(r'[\u4e00-\u9fff]')
# SN 规范：字母数字开头，主体允许字母数字与 - . _ /
VALID_SN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\-/]*$')

PRODUCT_PREFIX = {
    '大华云台': 'DHYT',
    '4目相机OMNI-D': 'SMXJ',
    '4目红外版本全景热成像环视模组': 'SMHW',
    '海塔迷你主机': 'HTZZ',
    'LZJ008A2': 'LZJ8',
    'LZJ010A2': 'LZJ10',
    'LZJ050A2S（3瓦）': 'LZJ50',
    '因时灵巧手': 'YSLQ',
    '宇树A2 pro': 'A2P',
    '宇树A2-pro': 'A2P',
    '宇树A2W': 'A2W',
    '宇树As2-W': 'AS2W',
}
FALLBACK_PREFIX = 'ZZSB'


def _tidy(value: str) -> str:
    """把候选 SN 洗净：去中文、空格转连字符、去掉首尾连字符。"""
    text = CJK.sub('', value or '').strip()
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'[^A-Za-z0-9._\-/]', '', text)
    text = re.sub(r'-{2,}', '-', text).strip('-._/')
    return text


def analyse(sn: str):
    """返回 (kind, new_sn_or_None, annotation)。

    kind:
      'keep'   无需处理
      'strip'  真实 SN + 注释，剥离注释即可
      'rnd'    研发NNN 占位符
      'ip'     IP 地址
      'cjk'    纯中文
    """
    sn = (sn or '').strip()
    if not sn:
        return 'keep', None, ''
    if VALID_SN.fullmatch(sn):
        return 'keep', None, ''
    if PLACEHOLDER_RND.fullmatch(sn):
        return 'rnd', None, ''

    ip_match = IP_HEAD.match(sn)
    if ip_match and not re.search(r'[A-Za-z]{2,}', ip_match.group('ip')):
        return 'ip', None, ip_match.group('rest').strip()

    # 真实 SN + 中文注释：取第一个中文/全角括号之前的部分
    head = re.split(r'[\u4e00-\u9fff（(]', sn, maxsplit=1)[0]
    tail = sn[len(head):].strip(' （）()：:')
    tidy = _tidy(head)
    if len(tidy) >= 6 and re.search(r'\d', tidy):
        return 'strip', tidy, tail
    return 'cjk', None, ''


class Command(BaseCommand):
    help = '把占位符/非规范 SN 规范化，原值写入备注。'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='真正写库')
        parser.add_argument('--user', default='admin', help='操作人账号')
        parser.add_argument(
            '--kinds',
            default='strip,rnd,ip,cjk',
            help='处理类别，逗号分隔：strip,rnd,ip,cjk',
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        kinds = {x.strip() for x in options['kinds'].split(',') if x.strip()}
        try:
            actor = get_user_model().objects.get(username=options['user'])
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f'账号不存在：{options["user"]}') from exc

        taken = {
            s.upper()
            for s in Asset.objects.exclude(serial_number='').values_list(
                'serial_number', flat=True
            )
        }

        candidates = []
        for a in Asset.objects.exclude(serial_number='').order_by('name', 'serial_number'):
            kind, new_sn, annot = analyse(a.serial_number)
            if kind != 'keep' and kind in kinds:
                candidates.append((a, kind, new_sn, annot))

        if not candidates:
            self.stdout.write(self.style.WARNING('没有匹配的占位符 SN。'))
            return

        from collections import defaultdict

        # 'strip' 类不需要重新编号，直接用剥离后的值
        plan = []
        renumber = defaultdict(list)
        for a, kind, new_sn, annot in candidates:
            if kind == 'strip' and new_sn:
                candidate = new_sn
                # 撞号则加序号后缀
                suffix = 2
                base = candidate
                while candidate.upper() in taken:
                    candidate = f'{base}-{suffix}'
                    suffix += 1
                taken.add(candidate.upper())
                plan.append((a, kind, candidate, annot))
            else:
                renumber[(a.name, kind)].append((a, annot))

        for (name, kind), items in sorted(renumber.items()):
            prefix = PRODUCT_PREFIX.get(name) or FALLBACK_PREFIX
            items.sort(key=lambda x: x[0].serial_number)
            next_no = 1
            for a, annot in items:
                while True:
                    candidate = f'{prefix}-{next_no:03d}'
                    next_no += 1
                    if candidate.upper() not in taken:
                        taken.add(candidate.upper())
                        break
                plan.append((a, kind, candidate, annot))

        plan.sort(key=lambda x: (x[0].name, x[0].serial_number))

        self.stdout.write('=' * 76)
        self.stdout.write(f'计划处理 {len(plan)} 件')
        self.stdout.write('=' * 76)
        current = None
        for a, kind, new_sn, annot in plan:
            if a.name != current:
                current = a.name
                self.stdout.write(f'\n【{a.name}】')
            extra = f'  备注+={annot!r}' if annot else ''
            self.stdout.write(
                f'  {a.asset_code or "(待赋码)":<26} [{kind:<5}] '
                f'{a.serial_number!r:<42} → {new_sn}{extra}'
            )
        self.stdout.write(
            f'\n合计：保留真实 SN 只去注释 {sum(1 for p in plan if p[1]=="strip")} 件，'
            f'重新编号 {sum(1 for p in plan if p[1]!="strip")} 件'
        )

        if not options['apply']:
            self.stdout.write(
                self.style.WARNING('\n预演完成，未写库。加 --apply 执行。')
            )
            return

        changed = 0
        with transaction.atomic():
            for a, kind, new_sn, annot in plan:
                old_sn = a.serial_number
                parts = [f'原SN：{old_sn}']
                if annot:
                    parts.append(annot)
                notes = '；'.join(parts)
                remarks = a.remarks or ''
                if notes not in remarks:
                    remarks = f'{remarks}；{notes}'.strip('；') if remarks else notes
                before = {'serial_number': old_sn, 'remarks': a.remarks}
                Asset.objects.filter(pk=a.pk).update(
                    serial_number=new_sn,
                    sn_status=Asset.SnStatus.MANUAL,
                    remarks=remarks,
                )
                a.refresh_from_db()
                record_operation(
                    actor,
                    'asset.sn_placeholder_replaced',
                    a,
                    summary=f'非规范 SN {old_sn} → {new_sn}（原值已存入备注）',
                    before=before,
                    after={
                        'serial_number': new_sn,
                        'sn_status': a.sn_status,
                        'remarks': a.remarks,
                    },
                )
                changed += 1
        self.stdout.write(self.style.SUCCESS(f'\n完成：改写 {changed} 件资产的 SN。'))