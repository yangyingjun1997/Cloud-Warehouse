from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.audit import record_operation
from apps.inventory.models import Asset, AssetAssemblyLink, ImportBatch, ImportIssue
from apps.inventory.v2_import_services import (
    apply_asset_v2_import,
    parse_asset_v2_workbook,
    validate_asset_v2_records,
)


class Command(BaseCommand):
    help = '事务导入资产编码 V2 全量基准工作簿，可按外部行号建立装配关系。'

    def add_arguments(self, parser):
        parser.add_argument('workbook')
        parser.add_argument('--actor', default='admin')
        parser.add_argument('--source-name', default='')
        parser.add_argument('--assembly-map', default='')
        parser.add_argument('--accept-warnings', action='store_true')
        parser.add_argument('--require-empty', action='store_true')
        parser.add_argument('--expected-count', type=int, default=0)

    def handle(self, *args, **options):
        workbook_path = Path(options['workbook']).expanduser().resolve()
        if not workbook_path.is_file():
            raise CommandError(f'导入工作簿不存在：{workbook_path}')
        actor = get_user_model().objects.filter(username=options['actor']).first()
        if actor is None:
            raise CommandError(f'执行账号不存在：{options["actor"]}')
        if options['require_empty'] and Asset.objects.exists():
            raise CommandError('资产表不是空表，已拒绝全量基准导入。')

        content = workbook_path.read_bytes()
        records = parse_asset_v2_workbook(content)
        issues = validate_asset_v2_records(records)
        errors = [issue for issue in issues if issue['severity'] == ImportIssue.Severity.ERROR]
        warnings = [issue for issue in issues if issue['severity'] == ImportIssue.Severity.WARNING]
        if errors:
            for issue in errors[:20]:
                self.stderr.write(f'[错误] 第 {issue["row_number"]} 行 {issue["message"]}')
            raise CommandError(f'预检存在 {len(errors)} 个阻断错误，未导入。')
        if warnings and not options['accept_warnings']:
            for issue in warnings[:20]:
                self.stderr.write(f'[警告] 第 {issue["row_number"]} 行 {issue["message"]}')
            raise CommandError(f'预检存在 {len(warnings)} 个警告；复核后使用 --accept-warnings。')
        if options['expected_count'] and len(records) != options['expected_count']:
            raise CommandError(
                f'记录数不符合预期：解析 {len(records)}，预期 {options["expected_count"]}。'
            )

        source_name = options['source_name'].strip() or workbook_path.name
        warning_reason = (
            f'已人工确认本批 {len(warnings)} 条预检警告，按源表保留并在导入后继续核对。'
            if warnings else ''
        )
        with transaction.atomic():
            batch = ImportBatch.objects.create(
                source_filename=source_name,
                source_sha256=hashlib.sha256(content).hexdigest(),
                mode=ImportBatch.Mode.BASELINE,
                status=ImportBatch.Status.PREVIEWED,
                created_by=actor,
                summary={'template_version': 2, 'row_count': len(records)},
            )
            ImportIssue.objects.bulk_create([
                ImportIssue(batch=batch, **issue) for issue in issues
            ])
            apply_asset_v2_import(
                actor=actor,
                batch=batch,
                records=records,
                warning_reason=warning_reason,
            )
            assembly_count = self._apply_assembly_map(
                options['assembly_map'], batch, actor,
            )
            batch.refresh_from_db()
            batch.summary = {**batch.summary, 'assembly_link_count': assembly_count}
            batch.save(update_fields=['summary', 'updated_at'])
            if assembly_count:
                record_operation(
                    actor,
                    'asset_v2_assembly_imported',
                    batch,
                    summary=f'V2 基准导入建立 {assembly_count} 条装配关系',
                    after={'assembly_link_count': assembly_count},
                )

        self.stdout.write(self.style.SUCCESS(
            f'导入完成：资产 {batch.imported_asset_count} 件，警告 {len(warnings)} 条，'
            f'装配关系 {assembly_count} 条。'
        ))

    def _apply_assembly_map(self, map_value, batch, actor):
        if not map_value:
            return 0
        map_path = Path(map_value).expanduser().resolve()
        if not map_path.is_file():
            raise CommandError(f'装配关系映射不存在：{map_path}')
        payload = json.loads(map_path.read_text(encoding='utf-8'))
        parents_by_kit = {kit: row for row, kit in payload.get('parents', {}).items()}
        row_to_asset_no = batch.summary.get('external_row_asset_map') or {}
        created = 0
        for child_row, kit in payload.get('children', {}).items():
            parent_row = parents_by_kit.get(kit)
            parent_no = row_to_asset_no.get(parent_row)
            child_no = row_to_asset_no.get(child_row)
            if not parent_no or not child_no:
                raise CommandError(f'装配映射无法解析：{kit} / {parent_row} / {child_row}')
            parent = Asset.objects.get(system_asset_no=parent_no)
            child = Asset.objects.get(system_asset_no=child_no)
            _, was_created = AssetAssemblyLink.objects.get_or_create(
                child_asset=child,
                removed_at__isnull=True,
                defaults={
                    'parent_asset': parent,
                    'operator': actor,
                    'note': f'11.2 基准台账套装 {kit}',
                },
            )
            created += int(was_created)
        return created
