"""Preview and import the approved legacy inventory workbook.

The default mode intentionally creates only a review batch and issue list.  Use
--apply only after the preview has been checked in the management interface.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from apps.inventory.models import (
    Asset,
    AssetCodeSequence,
    AssetNetworkEndpoint,
    Category,
    ImportBatch,
    ImportIssue,
    InventoryDomain,
    ItemType,
    StockItem,
    Warehouse,
)
from apps.inventory.code_services import apply_asset_identity


WAREHOUSE_CONFIG = {
    '市场库存': 'MARKET',
    '研发库存': 'RND',
}

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
    },
    '研发库存': {
        '未出库': Asset.Status.IN_STOCK,
        '已出库': Asset.Status.ISSUED,
        '返厂': Asset.Status.REPAIRING,
        '归还': Asset.Status.IN_STOCK,
        '已归还': Asset.Status.IN_STOCK,
        '已退货': Asset.Status.RETURNED_TO_VENDOR,
        '报废': Asset.Status.SCRAPPED,
    },
}

PLACEHOLDER_IDENTIFIERS = {'', '/', '-', '无', '未知', 'n/a', 'na', '本批没有序列号'}
# ZSB 是行政系统早期把 DZSB 写错的遗留前缀，行政侧已无法改回；QTZC 是消防水炮
# 的专用编码。两者都是源表给的正式编码，必须原样保留，不能自行改写成 DZSB。
ASSET_CODE_PATTERN = re.compile(r'^(?:SC|DZSB|ZSB|QTZC)-[A-Z0-9]+(?:-[A-Z0-9]+)*$')
SC_SEQUENCE_PATTERN = re.compile(r'^(?P<prefix>SC-[A-Z0-9]+(?:-[A-Z0-9]+)*)-(?P<number>\d+)$')


class Command(BaseCommand):
    help = '预检或导入旧版市场/研发库存 Excel（默认仅生成复核批次）'

    def add_arguments(self, parser):
        parser.add_argument('workbook', type=Path, help='Excel 文件路径')
        parser.add_argument('--apply', action='store_true', help='确认将可导入资产写入正式库')
        parser.add_argument('--user', help='执行导入的系统账号（用于追溯）')
        parser.add_argument('--require-empty', action='store_true', help='要求资产和耗材配件表均为空')
        parser.add_argument('--expected-assets', type=int, default=0)
        parser.add_argument('--expected-stock-items', type=int, default=0)

    def handle(self, *args, **options):
        path = options['workbook'].expanduser().resolve()
        if not path.is_file():
            raise CommandError(f'找不到 Excel 文件: {path}')
        if path.suffix.lower() not in {'.xlsx', '.xlsm'}:
            raise CommandError('仅支持 .xlsx 或 .xlsm 文件。')

        user = self._get_user(options.get('user'))
        asset_records, stock_records, issues, summary = self._parse_workbook(path)
        errors = [issue for issue in issues if issue['severity'] == ImportIssue.Severity.ERROR]
        if errors:
            for issue in errors[:20]:
                self.stderr.write(f'[错误] {issue["sheet_name"]} 第 {issue["row_number"]} 行：{issue["message"]}')
            raise CommandError(f'源表预检存在 {len(errors)} 个阻断错误，未写入数据库。')
        if options['expected_assets'] and len(asset_records) != options['expected_assets']:
            raise CommandError(f'资产数不符合预期：解析 {len(asset_records)}，预期 {options["expected_assets"]}。')
        if options['expected_stock_items'] and len(stock_records) != options['expected_stock_items']:
            raise CommandError(f'耗材配件数不符合预期：解析 {len(stock_records)}，预期 {options["expected_stock_items"]}。')
        if options['require_empty'] and (Asset.objects.exists() or StockItem.objects.exists()):
            raise CommandError('资产或耗材配件表不是空表，已拒绝全量基准导入。')
        batch = ImportBatch.objects.create(
            source_filename=path.name,
            source_sha256=self._sha256(path),
            status=ImportBatch.Status.PREVIEWED,
            summary=summary,
            created_by=user,
        )
        self._save_issues(batch, issues)

        if options['apply']:
            imported_assets, imported_stock_items, imported_endpoints, apply_issues = self._apply_records(
                batch,
                asset_records,
                stock_records,
            )
            self._save_issues(batch, apply_issues)
            batch.status = ImportBatch.Status.IMPORTED
            batch.imported_asset_count = imported_assets
            batch.imported_stock_item_count = imported_stock_items
            batch.imported_network_endpoint_count = imported_endpoints
            batch.summary = {**summary, 'apply_issue_count': len(apply_issues)}
            batch.save(update_fields=[
                'status',
                'imported_asset_count',
                'imported_stock_item_count',
                'imported_network_endpoint_count',
                'summary',
                'updated_at',
            ])
            self.stdout.write(self.style.SUCCESS(
                f'导入完成：资产 {imported_assets} 条，库存物料 {imported_stock_items} 条，'
                f'网络接口 {imported_endpoints} 条，请在后台查看复核问题。'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'预检完成：单件资产 {len(asset_records)} 条，库存物料 {len(stock_records)} 条，'
                f'需复核 {len(issues)} 条。'
                f' 批次 ID: {batch.id}。确认后使用 --apply 重新导入。'
            ))

    def _get_user(self, username):
        if not username:
            return None
        try:
            return get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist as exc:
            raise CommandError(f'账号不存在: {username}') from exc

    def _parse_workbook(self, path):
        workbook = load_workbook(path, read_only=True, data_only=True)
        asset_records, stock_records, issues = [], [], []
        for sheet_name in WAREHOUSE_CONFIG:
            if sheet_name not in workbook.sheetnames:
                issues.append(self._issue(sheet_name, 0, 'error', 'missing_sheet', f'缺少工作表“{sheet_name}”。', {}))
                continue
            worksheet = workbook[sheet_name]
            rows = worksheet.iter_rows(values_only=True)
            try:
                headers = self._headers(next(rows))
            except StopIteration:
                issues.append(self._issue(sheet_name, 0, 'error', 'empty_sheet', '工作表为空。', {}))
                continue

            product_name = ''
            groups = {}
            source_codes = set()
            for row_number, row in enumerate(rows, start=2):
                source = self._source_row(headers, row)
                if not any(source.values()):
                    continue
                row_product_name = self._text(source.get('产品名称'))
                if row_product_name:
                    product_name = row_product_name
                    groups.setdefault(product_name, {
                        'summary_row': row_number,
                        'summary_source': source,
                        'asset_count': 0,
                        'uncoded_detail_rows': [],
                    })
                if not product_name:
                    issues.append(self._issue(sheet_name, row_number, 'error', 'missing_product', '缺少产品名称，无法归类。', source))
                    continue
                group = groups.setdefault(product_name, {
                    'summary_row': row_number,
                    'summary_source': source,
                    'asset_count': 0,
                    'uncoded_detail_rows': [],
                })
                codes = self._asset_codes(source)
                if not codes:
                    if self._looks_like_detail(sheet_name, source):
                        group['uncoded_detail_rows'].append(row_number)
                    malformed = self._malformed_code_value(source)
                    if malformed:
                        issues.append(self._issue(
                            sheet_name, row_number, 'warning', 'malformed_asset_code',
                            f'发现疑似资产编码“{malformed}”，但不符合 SC-/DZSB- 正式格式，未自动导入。', source,
                        ))
                    continue

                status_source = self._text(source.get('状态'))
                mapped_status = STATUS_MAP[sheet_name].get(status_source, Asset.Status.IN_STOCK)
                if status_source and status_source not in STATUS_MAP[sheet_name]:
                    issues.append(self._issue(sheet_name, row_number, 'warning', 'unknown_status', f'未识别状态“{status_source}”，暂按“在库”导入。', source))
                serial = self._text(source.get('序列号'))
                if any(serial.upper() == code for code in codes):
                    serial = ''
                ip_value = serial if self._is_ip(serial) else ''
                usable_serial = '' if self._is_placeholder(serial) or ip_value or self._is_serial_range(serial) else serial
                if ip_value:
                    issues.append(self._issue(sheet_name, row_number, 'info', 'ip_in_serial_column', 'IP 已转存为网络接口记录，生产厂家 SN 保留为空。', source))
                for position, code in enumerate(codes, 1):
                    if code in source_codes:
                        issues.append(self._issue(sheet_name, row_number, 'error', 'duplicate_internal_code', f'正式资产编码 {code} 在源表中重复。', source))
                        continue
                    source_codes.add(code)
                    group['asset_count'] += 1
                    asset_records.append({
                        'sheet_name': sheet_name,
                        'row_number': row_number,
                        'source': source,
                        'name': product_name,
                        'serial_number': usable_serial if len(codes) == 1 else '',
                        'asset_code': code,
                        'ip_address': ip_value if len(codes) == 1 else '',
                        'status': mapped_status,
                        'split_position': position,
                    })

            for name, group in groups.items():
                if group['asset_count'] == 0:
                    stock_records.append({
                        'sheet_name': sheet_name,
                        'row_number': group['summary_row'],
                        'source': group['summary_source'],
                        'name': name,
                        'quantity': self._summary_quantity(sheet_name, group['summary_source']),
                    })
                elif group['uncoded_detail_rows']:
                    rows_text = '、'.join(str(value) for value in group['uncoded_detail_rows'][:12])
                    issues.append(self._issue(
                        sheet_name, group['summary_row'], 'warning', 'uncoded_rows_in_asset_group',
                        f'“{name}”同时存在正式编码资产和无编码明细；无编码行 {rows_text} 未自动创建资产或数量库存。',
                        group['summary_source'],
                    ))

        safe_asset_records = asset_records
        standalone_stock_records = stock_records

        summary = {
            'candidate_asset_count': len(safe_asset_records),
            'candidate_stock_item_count': len(standalone_stock_records),
            'recognized_sc_count': sum(record['asset_code'].startswith('SC-') for record in safe_asset_records),
            'recognized_dzsb_count': sum(record['asset_code'].startswith('DZSB-') for record in safe_asset_records),
            'issue_count': len(issues),
            'status_counts': dict(Counter(record['status'] for record in safe_asset_records)),
            'sheets': list(WAREHOUSE_CONFIG),
        }
        return safe_asset_records, standalone_stock_records, issues, summary

    def _apply_records(self, batch, asset_records, stock_records):
        issues = []
        imported_assets = imported_stock_items = imported_endpoints = 0
        with transaction.atomic():
            warehouses = {
                name: Warehouse.objects.get_or_create(name=name, defaults={'code': code})[0]
                for name, code in WAREHOUSE_CONFIG.items()
            }
            category, _ = Category.objects.get_or_create(name='历史导入', defaults={'code': 'LEGACY'})
            domains = {
                code: InventoryDomain.objects.get(code=code)
                for code in WAREHOUSE_CONFIG.values()
            }
            self._sync_sc_sequences(asset_records)
            for record in stock_records:
                stock_code = self._stock_item_code(record['sheet_name'], record['name'])
                if StockItem.objects.filter(code=stock_code).exists():
                    issues.append(self._issue(record['sheet_name'], record['row_number'], 'warning', 'existing_stock_item', '系统内已存在同仓库的同名库存物料，未覆盖原数量。', record['source']))
                    continue
                StockItem.objects.create(
                    name=record['name'][:120],
                    code=stock_code,
                    unit='个',
                    quantity=record['quantity'],
                    item_type=self._item_type(category, record['name'], is_serialized=False),
                    warehouse=warehouses[record['sheet_name']],
                    source_import_batch=batch,
                )
                imported_stock_items += 1
            for record in asset_records:
                source = record['source']
                serial = record['serial_number']
                requested_code = record['asset_code']
                if requested_code and Asset.objects.filter(asset_code=requested_code).exists():
                    issues.append(self._issue(record['sheet_name'], record['row_number'], 'error', 'existing_asset_code', '系统内已存在相同资产编号，已跳过。', source))
                    continue

                item_type = self._item_type(category, record['name'])
                asset = Asset(
                    asset_code=requested_code,
                    code_source=Asset.CodeSource.WAREHOUSE_ASSIGNED,
                    domain=domains[WAREHOUSE_CONFIG[record['sheet_name']]],
                    name=record['name'],
                    item_type=item_type,
                    manufacturer=self._text(source.get('厂家')),
                    model=self._text(source.get('型号')),
                    serial_number=serial,
                    sn_status=Asset.SnStatus.PROVIDED if serial else Asset.SnStatus.NO_SN,
                    received_date=self._date(source.get('到货日期')),
                    purchase_order_no=self._reference(source),
                    warehouse=warehouses[record['sheet_name']],
                    status=record['status'],
                    source_import_batch=batch,
                    remarks=self._remarks(record['sheet_name'], source),
                )
                apply_asset_identity(
                    asset, actor=batch.created_by,
                    reason=f'从 {batch.source_filename} 第 {record["row_number"]} 行导入',
                )
                imported_assets += 1
                if record['ip_address']:
                    AssetNetworkEndpoint.objects.create(asset=asset, ip_address=record['ip_address'], is_primary=True)
                    imported_endpoints += 1
        return imported_assets, imported_stock_items, imported_endpoints, issues

    @staticmethod
    def _sync_sc_sequences(asset_records):
        grouped = {}
        for record in asset_records:
            match = SC_SEQUENCE_PATTERN.fullmatch(record['asset_code'])
            if not match:
                continue
            prefix = match.group('prefix')
            number_text = match.group('number')
            values = grouped.setdefault(prefix, {'numbers': [], 'widths': Counter()})
            values['numbers'].append(int(number_text))
            values['widths'][len(number_text)] += 1
        for prefix, values in grouped.items():
            width = values['widths'].most_common(1)[0][0]
            sequence, _ = AssetCodeSequence.objects.get_or_create(
                scheme=AssetCodeSequence.Scheme.MARKET,
                prefix=prefix,
                defaults={'width': width, 'last_number': max(values['numbers'])},
            )
            changed = []
            if sequence.last_number < max(values['numbers']):
                sequence.last_number = max(values['numbers'])
                changed.append('last_number')
            if sequence.last_number == 0 and sequence.width != width:
                sequence.width = width
                changed.append('width')
            if changed:
                sequence.save(update_fields=changed + ['updated_at'])

    def _item_type(self, category, name, *, is_serialized=True):
        code = f'LEGACY-{hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:12].upper()}'
        return ItemType.objects.get_or_create(
            category=category,
            name=name[:120],
            defaults={'code': code, 'is_serialized': is_serialized, 'unit': '个'},
        )[0]

    @staticmethod
    def _stock_item_code(sheet_name, name):
        value = f'{sheet_name}:{name}'.encode('utf-8')
        return f'STOCK-{hashlib.sha1(value, usedforsecurity=False).hexdigest()[:12].upper()}'

    def _asset_codes(self, source):
        result = []
        for field in ('资产编号', '八维通SN', '序列号'):
            raw = self._text(source.get(field)).upper()
            for value in re.split(r'[\r\n]+', raw):
                value = value.strip()
                if ASSET_CODE_PATTERN.fullmatch(value) and value not in result:
                    result.append(value)
        return result

    def _malformed_code_value(self, source):
        for field in ('资产编号', '八维通SN'):
            value = self._text(source.get(field)).upper()
            if value and value not in {'无', '-', '/'} and (
                value.startswith(('SC', 'DZSB', 'ZSB', 'QTZC'))
            ):
                return value
        return ''

    def _looks_like_detail(self, sheet_name, source):
        field = '采购个数' if sheet_name == '市场库存' else '采购数量'
        value = self._text(source.get(field))
        return not self._text(source.get('产品名称')) and bool(re.fullmatch(r'\d+(?:\.0+)?', value))

    @staticmethod
    def _is_summary_row(sheet_name, source):
        quantity_field = '库存剩余' if sheet_name == '市场库存' else '库存数量'
        return source.get('产品名称') is not None and source.get(quantity_field) is not None and not source.get('状态')

    def _summary_quantity(self, sheet_name, source):
        quantity_field = '库存剩余' if sheet_name == '市场库存' else '库存数量'
        value = source.get(quantity_field)
        if isinstance(value, int):
            return max(value, 0)
        matched = re.search(r'\d+', self._text(value))
        return int(matched.group()) if matched else 0

    @staticmethod
    def _headers(row):
        return {str(value).strip(): index for index, value in enumerate(row) if value is not None and str(value).strip()}

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

    def _is_placeholder(self, value):
        return self._text(value).lower() in PLACEHOLDER_IDENTIFIERS

    def _is_ip(self, value):
        try:
            ipaddress.ip_address(self._text(value))
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_serial_range(value):
        return bool(re.fullmatch(r'\d+\s*-\s*\d+', value))

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

    def _reference(self, source):
        return self._text(source.get('OA采购订单审批号') or source.get('ERP同步情况'))[:120]

    def _remarks(self, sheet_name, source):
        fields = ['客户名称', '销卖出库日期', '物流单号', '出货日期', '出货类型', '领用人', '归还日期', '用途', '备注']
        values = [f'{field}: {self._text(source.get(field))}' for field in fields if self._text(source.get(field))]
        return f'历史导入/{sheet_name}; ' + '; '.join(values)

    @staticmethod
    def _json_value(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    @staticmethod
    def _issue(sheet_name, row_number, severity, code, message, source_data):
        return {
            'sheet_name': sheet_name,
            'row_number': row_number,
            'severity': severity,
            'code': code,
            'message': message,
            'source_data': source_data,
        }

    @staticmethod
    def _save_issues(batch, issues):
        ImportIssue.objects.bulk_create([ImportIssue(batch=batch, **issue) for issue in issues])

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
