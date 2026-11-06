"""补录源表中被导入器跳过的实物。

目前只覆盖消防水炮：源表 5 台，只有第 1 台有 QTZC 编码，其余 4 台按同一规则续号。

中车盒子不在此处理：它在源表里已经作为耗材组导入（qty=3），
其"编码"列的 3-3/2-3 是内部批次号，耗材模型没有备注字段可承载，
留待运营侧决定是否改录模型。

命令默认只预演，加 --apply 才写库。
"""
from __future__ import annotations

import datetime
import hashlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from apps.common.audit import record_operation
from apps.inventory.code_services import apply_asset_identity
from apps.inventory.models import (
    Asset,
    Category,
    InventoryDomain,
    ItemType,
    Warehouse,
)

SHEET_RND = '研发库存'
LEGACY_CATEGORY = '历史导入'
FIRE_PREFIX = 'QTZC-Q2-01-0002-'
FIRE_BASE = 'QTZC-Q2-01-0002-600001'


def _text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _date(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(_text(value)[:10])
    except ValueError:
        return None


class Command(BaseCommand):
    help = '补录源表中无编码的实物（目前仅消防水炮）。'

    def add_arguments(self, parser):
        parser.add_argument('workbook', help='源台账 .xlsx 路径')
        parser.add_argument('--apply', action='store_true', help='真正写入数据库')
        parser.add_argument('--user', default='admin', help='操作人账号，用于审计')

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        try:
            actor = get_user_model().objects.get(username=options['user'])
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f'账号不存在：{options["user"]}') from exc

        workbook = load_workbook(options['workbook'], data_only=True)
        planned = self._plan_fire(workbook)

        self.stdout.write('=' * 68)
        self.stdout.write(f'计划补录 {len(planned)} 件（消防水炮）')
        self.stdout.write('=' * 68)
        for item in planned:
            self.stdout.write(
                f'  {item["asset_code"]:<26} 状态={item["status"]:<12} '
                f'行{item["row_number"]:<5} 备注={item["remarks"][:40]}'
            )
        if not options['apply']:
            self.stdout.write(self.style.WARNING('\n预演完成，未写库。加 --apply 执行。'))
            return

        with transaction.atomic():
            for item in planned:
                asset = self._create_asset(item, actor)
                self.stdout.write(self.style.SUCCESS(f'  ✓ {asset.asset_code} {asset.name}'))
        self.stdout.write(self.style.SUCCESS(f'\n补录完成：新建 {len(planned)} 件资产。'))

    # ---------------- 消防水炮 ----------------

    def _plan_fire(self, workbook):
        """源表第 483 行是组标题（有产品名称+库存数量、无状态），不是实物。"""
        rows = self._rows(workbook, SHEET_RND)
        items = []
        for row_no, row, header in rows:
            if '产品名称' in header and _text(row[header['产品名称']]):
                continue
            model = _text(row[header['型号']]) if '型号' in header else ''
            if model != '轻量化水炮':
                continue
            code = _text(row[header['八维通SN']]) if '八维通SN' in header else ''
            items.append((row_no, row, header, code))
        if not items:
            return []

        existing = set(
            Asset.objects.filter(asset_code__startswith=FIRE_PREFIX)
            .values_list('asset_code', flat=True)
        )
        planned = []
        next_no = 2  # 600001 已存在
        for row_no, row, header, code in items:
            if code:
                continue
            while True:
                candidate = f'{FIRE_PREFIX}6{next_no:05d}'
                next_no += 1
                if candidate not in existing:
                    existing.add(candidate)
                    break
            planned.append({
                'asset_code': candidate,
                'name': '消防水炮',
                'model': _text(row[header['型号']]) if '型号' in header else '轻量化水炮',
                'manufacturer': _text(row[header['厂家']]) if '厂家' in header else '大连汇高',
                'serial_number': '',
                'received_date': _date(row[header['到货日期']]) if '到货日期' in header else None,
                'status': self._map_status(_text(row[header['状态']]) if '状态' in header else ''),
                'remarks': self._compose(
                    _text(row[header['用途']]) if '用途' in header else '',
                    _text(row[header['备注']]) if '备注' in header else '',
                    _text(row[header['领用人']]) if '领用人' in header else '',
                ),
                'row_number': row_no,
                'reason': f'源表第 {row_no} 行无编码，按 {FIRE_PREFIX} 规则续号补录',
            })
        return planned

    # ---------------- 辅助 ----------------

    def _rows(self, workbook, sheet_name):
        if sheet_name not in workbook.sheetnames:
            return []
        raw = list(workbook[sheet_name].iter_rows(values_only=True))
        if not raw:
            return []
        header = {}
        for index, value in enumerate(raw[0]):
            label = _text(value)
            if label and label not in header:
                header[label] = index
        result = []
        for offset, row in enumerate(raw[1:], start=2):
            if not any(_text(cell) for cell in row):
                continue
            result.append((offset, row, header))
        return result

    @staticmethod
    def _map_status(source):
        from apps.inventory.management.commands.import_legacy_inventory import STATUS_MAP

        return STATUS_MAP.get(SHEET_RND, {}).get(source, Asset.Status.IN_STOCK)

    @staticmethod
    def _compose(*parts):
        seen = []
        for part in parts:
            text = _text(part)
            if text and text not in seen:
                seen.append(text)
        return '；'.join(seen)

    def _create_asset(self, item, actor):
        warehouse = Warehouse.objects.filter(code='RND').first()
        if warehouse is None:
            raise CommandError('找不到研发仓库（RND），请先运行导入命令。')
        domain = InventoryDomain.objects.filter(code='RND').first()
        if domain is None:
            raise CommandError('找不到归属域 RND。')

        category, _ = Category.objects.get_or_create(
            name=LEGACY_CATEGORY, defaults={'code': 'LEGACY'}
        )
        name = item['name']
        item_type = ItemType.objects.filter(category=category, name=name[:120]).first()
        if item_type is None:
            item_type = ItemType.objects.create(
                category=category,
                name=name[:120],
                code=f'LEGACY-{hashlib.sha1(name.encode()).hexdigest()[:12].upper()}',
                is_serialized=True,
                unit='个',
            )

        asset = Asset(
            asset_code=item['asset_code'],
            code_source=Asset.CodeSource.WAREHOUSE_ASSIGNED,
            domain=domain,
            name=name,
            item_type=item_type,
            manufacturer=item['manufacturer'],
            model=item['model'],
            serial_number=item['serial_number'],
            sn_status=Asset.SnStatus.NO_SN,
            received_date=item['received_date'],
            warehouse=warehouse,
            status=item['status'],
            remarks=item['remarks'],
        )
        apply_asset_identity(asset, actor=actor, reason=item['reason'])
        record_operation(
            actor,
            'asset.supplement_import',
            asset,
            summary=item['reason'],
            after={
                'asset_code': asset.asset_code,
                'name': asset.name,
                'status': asset.status,
                'source_row': item['row_number'],
            },
        )
        return asset