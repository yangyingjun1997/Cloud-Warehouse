"""输出可能属于测试/压测的数据清单；本命令永远只读。"""

import re

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from apps.common.models import OperationAuditLog
from apps.inventory.models import Asset, ItemType, Location, StockItem, Warehouse
from apps.notifications.models import Notification
from apps.workflow.models import ApprovalTask, InventoryTransaction, WorkflowRequest


MARKER = re.compile(r'(^|[-_\s])(qa|test|loadtest)([-_\s]|$)|测试|压测', re.IGNORECASE)


def marked(value):
    return bool(MARKER.search(str(value or '')))


class Command(BaseCommand):
    help = '只读列出带有明确测试标识的数据及其业务关联，不执行删除或修改。'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json', help='以 JSON 输出，便于保存审阅。')

    def handle(self, *args, **options):
        User = get_user_model()
        users = list(User.objects.filter(username__regex=r'(?i)(qa|test|loadtest|测试|压测)'))
        warehouses = list(Warehouse.objects.values('id', 'code', 'name'))
        warehouse_by_id = {item['id']: item for item in warehouses}
        locations = list(Location.objects.values('id', 'warehouse_id', 'code', 'name'))
        item_types = list(ItemType.objects.values('id', 'code', 'name'))
        stock_items = list(StockItem.objects.values('id', 'code', 'name', 'quantity', 'warehouse_id'))
        # 仅读取报告需要的列，兼容迁移尚未应用的本机旧 SQLite；
        # 不调用 Asset 实例序列化，避免无关新字段阻断诊断报告。
        assets = list(Asset.objects.values(
            'id', 'system_asset_no', 'asset_code', 'name', 'status',
            'serial_number', 'warehouse_id', 'item_type_id',
        ))

        candidates = {
            'users': [
                {'id': str(item.pk), 'username': item.get_username(), 'is_active': item.is_active}
                for item in users
            ],
            'warehouses': [
                {'id': str(item['id']), 'code': item['code'], 'name': item['name']}
                for item in warehouses if marked(item['code']) or marked(item['name'])
            ],
            'locations': [
                {
                    'id': str(item['id']),
                    'warehouse': warehouse_by_id.get(item['warehouse_id'], {}).get('name', ''),
                    'code': item['code'], 'name': item['name'],
                }
                for item in locations
                if marked(item['code']) or marked(item['name'])
                or marked(warehouse_by_id.get(item['warehouse_id'], {}).get('code', ''))
            ],
            'item_types': [
                {'id': str(item['id']), 'code': item['code'], 'name': item['name']}
                for item in item_types if marked(item['code']) or marked(item['name'])
            ],
            'stock_items': [
                {'id': str(item['id']), 'code': item['code'], 'name': item['name'], 'quantity': item['quantity']}
                for item in stock_items if marked(item['code']) or marked(item['name'])
            ],
            'assets': [
                {
                    'id': str(item['id']), 'system_asset_no': item['system_asset_no'],
                    'asset_code': item['asset_code'] or '', 'name': item['name'],
                    'status': item['status'],
                }
                for item in assets
                if any(marked(value) for value in (item['system_asset_no'], item['asset_code'], item['name'], item['serial_number']))
            ],
        }

        user_ids = [item.pk for item in users]
        asset_ids = [item['id'] for item in assets if any(marked(value) for value in (item['system_asset_no'], item['asset_code'], item['name'], item['serial_number']))]
        stock_ids = [item['id'] for item in stock_items if marked(item['code']) or marked(item['name'])]
        warehouse_ids = [item['id'] for item in warehouses if marked(item['code']) or marked(item['name'])]
        candidates['related_counts'] = {
            'workflow_requests': WorkflowRequest.objects.filter(applicant_id__in=user_ids).count() if user_ids else 0,
            'approval_tasks': ApprovalTask.objects.filter(approver_id__in=user_ids).count() if user_ids else 0,
            'inventory_transactions': InventoryTransaction.objects.filter(asset_id__in=asset_ids).count() if asset_ids else 0,
            'stock_transactions': InventoryTransaction.objects.filter(stock_item_id__in=stock_ids).count() if stock_ids else 0,
            'notifications': Notification.objects.filter(recipient_id__in=user_ids).count() if user_ids else 0,
            'audit_logs_by_user': OperationAuditLog.objects.filter(actor_id__in=user_ids).count() if user_ids else 0,
            'assets_in_marked_warehouses': Asset.objects.filter(warehouse_id__in=warehouse_ids).count() if warehouse_ids else 0,
            'stock_in_marked_warehouses': StockItem.objects.filter(warehouse_id__in=warehouse_ids).count() if warehouse_ids else 0,
        }

        if options['as_json']:
            import json
            self.stdout.write(json.dumps(candidates, ensure_ascii=False, indent=2, default=str))
            return

        self.stdout.write(self.style.WARNING('只读报告：未执行删除、停用、脱敏或数据库写入。'))
        for key in ('users', 'warehouses', 'locations', 'item_types', 'stock_items', 'assets'):
            rows = candidates[key]
            self.stdout.write(f'[{key}] {len(rows)}')
            for row in rows[:100]:
                self.stdout.write('  ' + ' | '.join(f'{field}={value}' for field, value in row.items()))
            if len(rows) > 100:
                self.stdout.write(f'  ... 其余 {len(rows) - 100} 条请使用 --json 保存。')
        self.stdout.write('[related_counts]')
        for key, value in candidates['related_counts'].items():
            self.stdout.write(f'  {key}={value}')
