"""Reconcile legacy summary rows with imported serialized assets."""

from __future__ import annotations

import hashlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inventory.models import Asset, Category, ImportBatch, ImportIssue, ItemType, StockItem


class Command(BaseCommand):
    help = '对账历史导入的数量库存汇总行，移除与单件资产重复的数量。'

    def add_arguments(self, parser):
        parser.add_argument('--batch', help='导入批次 UUID，默认取最新已导入批次。')
        parser.add_argument('--apply', action='store_true', help='确认执行对账修正。')

    def handle(self, *args, **options):
        batches = ImportBatch.objects.filter(status=ImportBatch.Status.IMPORTED)
        if options['batch']:
            batches = batches.filter(pk=options['batch'])
        batch = batches.first()
        if not batch:
            raise CommandError('未找到已导入的目标批次。')

        stock_items = StockItem.objects.filter(source_import_batch=batch).select_related('warehouse')
        duplicates = [
            stock for stock in stock_items
            if Asset.objects.filter(warehouse=stock.warehouse, item_type__name=stock.name).exists()
        ]
        standalone = [stock for stock in stock_items if stock not in duplicates]
        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                f'预览：将移除 {len(duplicates)} 条重复汇总行，保留 {len(standalone)} 条耗材配件。使用 --apply 执行。'
            ))
            return

        with transaction.atomic():
            category, _ = Category.objects.get_or_create(name='历史导入', defaults={'code': 'LEGACY'})
            for stock in standalone:
                item_type, _ = ItemType.objects.get_or_create(
                    category=category,
                    name=stock.name,
                    defaults={
                        'code': f'LEGACY-{hashlib.sha1(stock.name.encode("utf-8")).hexdigest()[:12].upper()}',
                        'is_serialized': False,
                        'unit': stock.unit,
                    },
                )
                stock.item_type = item_type
                stock.save(update_fields=['item_type', 'updated_at'])

            for stock in duplicates:
                ImportIssue.objects.get_or_create(
                    batch=batch,
                    sheet_name=stock.warehouse.name if stock.warehouse else '未分配仓库',
                    row_number=0,
                    code='summary_stock_reconciled',
                    defaults={
                        'severity': ImportIssue.Severity.INFO,
                        'message': '汇总库存与同仓库单件资产重复，已移除重复数量。',
                        'source_data': {
                            'stock_item_id': str(stock.id),
                            'name': stock.name,
                            'quantity': stock.quantity,
                        },
                        'resolved': True,
                        'resolution_note': '已以单件资产台账为准。',
                    },
                )
            duplicate_count = len(duplicates)
            StockItem.objects.filter(id__in=[stock.id for stock in duplicates]).delete()
            batch.summary = {
                **batch.summary,
                'stock_reconciliation': {
                    'removed_duplicate_summary_count': duplicate_count,
                    'retained_standalone_stock_item_count': len(standalone),
                },
            }
            batch.save(update_fields=['summary', 'updated_at'])

        self.stdout.write(self.style.SUCCESS(
            f'对账完成：移除 {len(duplicates)} 条重复汇总行，保留 {len(standalone)} 条耗材配件。'
        ))
