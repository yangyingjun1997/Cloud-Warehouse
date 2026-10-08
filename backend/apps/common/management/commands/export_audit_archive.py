"""导出审计留痕归档。

把 OperationAuditLog 与 AssetLifecycleEvent 导出为带 SHA256 校验的
JSONL 压缩包，用于按监管/内控要求离线归档。只读操作，不影响在线数据。

用法：
    python manage.py export_audit_archive                     # 全部导出
    python manage.py export_audit_archive --before 2026-01-01  # 只导出该日期之前的记录
    python manage.py export_audit_archive --output /srv/backups/audit/
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.common.models import OperationAuditLog
from apps.inventory.models import AssetLifecycleEvent


class Command(BaseCommand):
    help = '导出审计留痕为带 SHA256 校验的 JSONL 压缩包（只读，不影响在线数据）'

    def add_arguments(self, parser):
        parser.add_argument('--before', help='只导出该日期（YYYY-MM-DD）之前的记录，默认全部')
        parser.add_argument(
            '--output',
            default='backups/audit',
            help='归档输出目录，默认 backups/audit',
        )

    def handle(self, *args, **options):
        before = parse_date(options['before']) if options.get('before') else None
        output_dir = Path(options['output'])
        output_dir.mkdir(parents=True, exist_ok=True)

        stamp = timezone.now().strftime('%Y%m%d_%H%M%S')
        archive_path = output_dir / f'audit-archive-{stamp}.tar.gz'

        datasets = {
            'operation_audit_log.jsonl': OperationAuditLog.objects.all(),
            'asset_lifecycle_event.jsonl': AssetLifecycleEvent.objects.all(),
        }
        if before:
            datasets = {
                name: qs.filter(created_at__date__lt=before)
                for name, qs in datasets.items()
            }

        counts = {}
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            members = []
            for name, qs in datasets.items():
                file_path = tmp_dir / name
                count = 0
                with file_path.open('w', encoding='utf-8') as fh:
                    for obj in qs.iterator():
                        row = {
                            'id': str(obj.pk),
                            'created_at': obj.created_at,
                            'data': {
                                field.name: getattr(obj, field.attname)
                                for field in obj._meta.fields
                                if field.name not in {'id', 'created_at'}
                            },
                        }
                        fh.write(json.dumps(row, cls=DjangoJSONEncoder, ensure_ascii=False) + '\n')
                        count += 1
                counts[name] = count
                members.append(file_path)

            sha_path = tmp_dir / 'SHA256SUMS'
            with sha_path.open('w', encoding='utf-8') as fh:
                for file_path in members:
                    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    fh.write(f'{digest}  {file_path.name}\n')
            members.append(sha_path)

            with tarfile.open(archive_path, 'w:gz') as tar:
                for file_path in members:
                    tar.add(file_path, arcname=file_path.name)

        self.stdout.write(self.style.SUCCESS(f'审计归档完成：{archive_path}'))
        for name, count in counts.items():
            self.stdout.write(f'  {name}: {count} 条')
