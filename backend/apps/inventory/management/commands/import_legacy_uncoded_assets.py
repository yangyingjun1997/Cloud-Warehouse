"""把旧台账里「无正式编码」的明细行补录成「未赋码资产」。

背景
----
`import_legacy_inventory` 只认 SC-/DZSB-/ZSB-/QTZC- 四种正式前缀。
当某个产品组里同时存在「有编码资产」和「无编码明细行」时，
无编码行会被 `continue` 跳过 —— 既不会建成资产，也不会并入耗材数量，
这部分数据在首次导入时就丢了。

本命令只做补录，不改动已有资产：
    1. 找出所有「无正式编码」的明细行；
    2. 每行建一台资产，`code_source=PENDING_ADMIN`（即「暂未赋码」）；
    3. 资产编码保持为空，由行政后续赋码；
    4. 序列号、到货日期、状态、型号、厂家等原样保留；
    5. 源表编码列如果有非编码文字（如「宇树买」「2-3」），写进备注，避免丢信息。

用法
----
    python manage.py import_legacy_uncoded_assets <工作簿> --list       # 只列出
    python manage.py import_legacy_uncoded_assets <工作簿> --apply --user admin
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from apps.inventory.code_services import apply_asset_identity
from apps.inventory.models import (
    Asset,
    Category,
    ImportBatch,
    ImportIssue,
    InventoryDomain,
    ItemType,
    Warehouse,
)

WAREHOUSE_CONFIG = {'市场库存': 'MARKET', '研发库存': 'RND'}

STATUS_MAP = {
    '市场库存': {
        '在库空闲': Asset.Status.IN_STOCK,
        '已归还': Asset.Status.IN_STOCK,
        '出库借用': Asset.Status.BORROWED,
        '内部借用库': Asset.Status.INTERNAL_BORROW,
        '出库出售': Asset.Status.SOLD,
        '返厂维修': Asset.Status.REPAIRING,
        '销售预定': Asset.Status.PENDING_OUT,
        '退货': Asset.Status.RETURNED_TO_VENDOR,
        '报废': Asset.Status.SCRAPPED,
        '待质检': Asset.Status.PENDING_INSPECTION,
        '转研发仓库': Asset.Status.IN_STOCK,
    },
    '研发库存': {
        '未出库': Asset.Status.IN_STOCK,
        '已出库': Asset.Status.ISSUED,
        '返厂': Asset.Status.REPAIRING,
        '归还': Asset.Status.IN_STOCK,
        '已归还': Asset.Status.IN_STOCK,
        '已退货': Asset.Status.RETURNED_TO_VENDOR,
        '报废': Asset.Status.SCRAPPED,
        '转研发仓库': Asset.Status.IN_STOCK,
    },
}

FORMAL_CODE = re.compile(r'^(?:SC|DZSB|ZSB|QTZC)-[A-Z0-9]+(?:-[A-Z0-9]+)*$')

# 这些值出现在编码列时并不是编码，只是占位或备注。
NON_CODE_LITERALS = {'', '/', '-', '无', '未知', 'n/a', 'na', '本批没有序列号'}

# 判断一行是否是真的明细行（有实物信息），避免把零散备注行也算进来。
DETAIL_HINTS = (
    '状态', '到货日期', '序列号', '领用人', '客户名称', '出货日期',
    '售卖出库日期', '备注', '用途', '型号', '厂家', '八维通SN', '资产编号',
)

# 已经单独落库的字段，不再重复写进备注。
MAPPED_FIELDS = {'产品名称', '采购个数', '采购数量', '库存剩余', '库存数量', '资产编号', '八维通SN'}

STATUS_ALIAS = {'转研发仓库': '转研发仓库'}


class Command(BaseCommand):
    help = '将旧台账中无正式编码的明细行补录为「未赋码资产」'

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path, help='Excel 文件路径')
        parser.add_argument('--list', action='store_true', help='只列出待补录行，不写库')
        parser.add_argument('--apply', action='store_true', help='确认写库')
        parser.add_argument('--user', help='执行补录的系统账号（用于追溯）')

    def handle(self, *args, **options):
        path = options['workbook'].expanduser().resolve()
        if not path.is_file():
            raise CommandError(f'找不到 Excel 文件: {path}')

        records = self._collect(path)

        self.stdout.write(f'待补录明细行: {len(records)}')
        per_sheet = Counter(record['sheet_name'] for record in records)
        for sheet_name, count in per_sheet.items():
            self.stdout.write(f'  {sheet_name}: {count} 行')

        if options['list'] or not options['apply']:
            self.stdout.write('')
            self.stdout.write(f'{"工作表":<10}{"行号":>6}  {"产品":<26}{"状态":<12}备注')
            self.stdout.write('-' * 96)
            for record in records:
                note = record['source'].get('用途') or record['source'].get('备注') or ''
                self.stdout.write(
                    f'{record["sheet_name"]:<10}{record["row_number"]:>6}  '
                    f'{record["name"][:24]:<26}{record["status_label"] or "(空)":<12}{str(note)[:30]}'
                )
            self.stdout.write('')
            self.stdout.write('以上为预览，未写库。确认无误后加 --apply 执行。')
            return

        actor = self._actor(options['user'])
        imported, issues = self._apply_records(records, path, actor)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'补录完成：新增 {imported} 台未赋码资产'))
        if issues:
            self.stdout.write(f'需复核 {len(issues)} 条：')
            for issue in issues[:20]:
                self.stdout.write(f'  {issue["sheet_name"]} 行{issue["row_number"]}: {issue["message"]}')

    # ------------------------------------------------------------------ 采集

    def _collect(self, path):
        workbook = load_workbook(path, read_only=True, data_only=True)
        records = []
        for sheet_name in WAREHOUSE_CONFIG:
            if sheet_name not in workbook.sheetnames:
                raise CommandError(f'缺少工作表“{sheet_name}”。')
            records.extend(self._collect_sheet(workbook[sheet_name], sheet_name))
        return records

    def _collect_sheet(self, worksheet, sheet_name):
        """两遍扫描：先判断哪些产品组「有正式编码资产」，再只挑这些组里的无编码行。

        关键点：如果一个产品组从头到尾都没有任何正式编码，原导入器会把它整组
        建成「数量库存」(StockItem)，这些行已经有了代表，不能重复补录。
        只有「组里既有编码资产、又有无编码明细」时，无编码行才是真被丢掉的。
        """
        code_field = '资产编号' if sheet_name == '市场库存' else '八维通SN'
        stock_field = '库存剩余' if sheet_name == '市场库存' else '库存数量'

        rows = list(worksheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = self._headers(rows[0])

        product = ''
        groups = {}
        for row_number, row in enumerate(rows[1:], start=2):
            source = self._source_row(headers, row)
            if not any(self._text(value) for value in source.values()):
                continue

            product_name = self._text(source.get('产品名称'))
            if product_name:
                product = product_name

            # 组标题行：有产品名、有库存数量、没有状态 —— 不是实物明细。
            if product_name and self._text(source.get(stock_field)) and not self._text(source.get('状态')):
                continue
            if not product:
                continue

            raw_code = self._text(source.get(code_field)).upper()
            parts = [part.strip() for part in re.split(r'[\r\n]+', raw_code) if part.strip()]
            has_formal = any(FORMAL_CODE.fullmatch(part) for part in parts)

            group = groups.setdefault(product, {'has_code': False, 'uncoded': []})
            if has_formal:
                group['has_code'] = True
                continue
            if not any(self._text(source.get(field)) for field in DETAIL_HINTS):
                continue
            group['uncoded'].append({
                'sheet_name': sheet_name,
                'row_number': row_number,
                'name': product,
                'source': source,
                'raw_code_text': raw_code if raw_code not in NON_CODE_LITERALS else '',
                'status_label': self._text(source.get('状态')),
                'serial_number': self._usable_serial(self._text(source.get('序列号'))),
            })

        records = []
        for name, group in groups.items():
            if not group['has_code']:
                # 整组无编码 → 已由原导入器建成数量库存，跳过。
                continue
            for record in group['uncoded']:
                record['status'] = STATUS_MAP[sheet_name].get(
                    record['status_label'], Asset.Status.IN_STOCK,
                )
                records.append(record)
        return records

    # ------------------------------------------------------------------ 写库

    def _apply_records(self, records, path, actor):
        issues = []
        imported = 0
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                source_filename=path.name,
                mode=ImportBatch.Mode.INCREMENTAL,
                source_sha256=self._sha256(path),
                status=ImportBatch.Status.PREVIEWED,
                summary={'purpose': '补录无正式编码的明细行为未赋码资产', 'candidate_count': len(records)},
                created_by=actor,
            )
            warehouses = {
                name: Warehouse.objects.get_or_create(name=name, defaults={'code': code})[0]
                for name, code in WAREHOUSE_CONFIG.items()
            }
            category, _ = Category.objects.get_or_create(name='历史导入', defaults={'code': 'LEGACY'})
            domains = {code: InventoryDomain.objects.get(code=code) for code in WAREHOUSE_CONFIG.values()}

            for record in records:
                item_type = self._item_type(category, record['name'])
                asset = Asset(
                    asset_code=None,
                    code_source=Asset.CodeSource.PENDING_ADMIN,
                    domain=domains[WAREHOUSE_CONFIG[record['sheet_name']]],
                    name=record['name'],
                    item_type=item_type,
                    manufacturer=self._text(record['source'].get('厂家')),
                    model=self._text(record['source'].get('型号')),
                    serial_number=record['serial_number'],
                    sn_status=Asset.SnStatus.PROVIDED if record['serial_number'] else Asset.SnStatus.NO_SN,
                    received_date=self._date(record['source'].get('到货日期')),
                    purchase_order_no=self._text(
                        record['source'].get('OA采购订单审批号') or record['source'].get('ERP同步情况')
                    )[:120],
                    warehouse=warehouses[record['sheet_name']],
                    status=record['status'],
                    source_import_batch=batch,
                    remarks=self._remarks(record),
                )
                apply_asset_identity(
                    asset, actor=actor,
                    reason=f'补录无编码明细：{path.name} 第 {record["row_number"]} 行',
                )
                imported += 1

                if record['status_label'] and record['status_label'] not in STATUS_MAP[record['sheet_name']]:
                    issues.append({
                        'sheet_name': record['sheet_name'],
                        'row_number': record['row_number'],
                        'message': f'未识别状态“{record["status_label"]}”，按在库导入。',
                    })

            batch.status = ImportBatch.Status.IMPORTED
            batch.imported_asset_count = imported
            batch.summary = {**batch.summary, 'imported_asset_count': imported}
            batch.save(update_fields=['status', 'imported_asset_count', 'summary', 'updated_at'])

            ImportIssue.objects.bulk_create([
                ImportIssue(
                    batch=batch,
                    sheet_name=issue['sheet_name'],
                    row_number=issue['row_number'],
                    severity=ImportIssue.Severity.WARNING,
                    code='unknown_status',
                    message=issue['message'],
                    source_data={},
                )
                for issue in issues
            ])
        return imported, issues

    # ------------------------------------------------------------------ 工具

    def _item_type(self, category, name):
        code = f'LEGACY-{hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:12].upper()}'
        return ItemType.objects.get_or_create(
            category=category,
            name=name[:120],
            defaults={'code': code, 'is_serialized': True, 'unit': '个'},
        )[0]

    def _remarks(self, record):
        source = record['source']
        parts = [f'补录/{record["sheet_name"]} 第{record["row_number"]}行']
        if record['raw_code_text']:
            parts.append(f'源编码列原值: {record["raw_code_text"]}')
        for field, value in source.items():
            if field in MAPPED_FIELDS:
                continue
            text = self._text(value)
            if text:
                parts.append(f'{field}: {text}')
        return '; '.join(parts)

    def _usable_serial(self, serial):
        if not serial or serial.strip().lower() in NON_CODE_LITERALS:
            return ''
        # 纯 IP 不进序列号，源表里那是网络地址。
        try:
            import ipaddress

            ipaddress.ip_address(serial)
            return ''
        except ValueError:
            pass
        if re.fullmatch(r'\d+\s*-\s*\d+', serial):
            return ''
        return serial[:120]

    def _actor(self, username):
        User = get_user_model()
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist as exc:
                raise CommandError(f'找不到账号 {username}') from exc
        actor = User.objects.filter(is_superuser=True).order_by('id').first()
        if not actor:
            raise CommandError('系统里没有超级管理员，无法记录操作人。')
        return actor

    @staticmethod
    def _headers(row):
        return {
            str(value).strip(): index
            for index, value in enumerate(row)
            if value is not None and str(value).strip()
        }

    def _source_row(self, headers, row):
        return {
            header: self._json_value(row[index]) if index < len(row) else ''
            for header, index in headers.items()
        }

    @staticmethod
    def _text(value):
        if value is None:
            return ''
        return str(value).strip()

    @staticmethod
    def _json_value(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    @staticmethod
    def _date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
