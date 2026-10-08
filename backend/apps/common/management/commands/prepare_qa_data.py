"""准备《产品开发计划与回归验收手册》要求的 QA 测试账号与最小测试数据。

幂等：全部使用 get_or_create / update_or_create，可重复执行不产生重复数据。
账号密码仅用于本地 Win11 验收，禁止用于生产。

用法：
    python manage.py prepare_qa_data            # 建账号 + 最小业务数据
    python manage.py prepare_qa_data --accounts # 只建账号
"""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.inventory.models import (
    Asset,
    Category,
    CompositeUnit,
    ComponentLink,
    Customer,
    ItemType,
    Location,
    Project,
    ServiceProvider,
    StockItem,
    Supplier,
    Warehouse,
    WorkOrder,
)

User = get_user_model()

# QA 账号统一密码：仅从环境变量读取，禁止硬编码。仅本地 Win11 验收用，禁止用于生产。
# PowerShell 示例：$env:QA_TEST_PASSWORD = "你设定的QA密码"
QA_PASSWORD = os.environ.get('QA_TEST_PASSWORD', '').strip()

# 账号 -> (是否超管, 权限组列表)
ACCOUNTS = {
    'qa_admin': (True, []),
    'qa_entry': (False, ['warehouse_entry']),
    'qa_operator': (False, ['warehouse_outbound', 'warehouse_staff']),
    'qa_approver': (False, ['warehouse_approval']),
    'qa_report': (False, ['warehouse_reports']),
    'qa_employee_a': (False, []),
    'qa_employee_b': (False, []),
}


class Command(BaseCommand):
    help = '准备 QA 验收测试账号与最小业务数据（幂等，仅本地使用）'

    def add_arguments(self, parser):
        parser.add_argument('--accounts', action='store_true', help='只创建测试账号')

    def handle(self, *args, **options):
        if not QA_PASSWORD:
            self.stderr.write(self.style.ERROR(
                '未设置 QA 账号密码。请先设置环境变量 QA_TEST_PASSWORD 再执行。\n'
                'PowerShell 示例：$env:QA_TEST_PASSWORD = "你设定的QA密码"'
            ))
            return
        self._create_accounts()
        if not options['accounts']:
            self._create_business_data()
        self.stdout.write(self.style.SUCCESS('QA 数据准备完成（密码来自环境变量 QA_TEST_PASSWORD）'))

    def _create_accounts(self):
        for username, (is_superuser, groups) in ACCOUNTS.items():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'is_superuser': is_superuser, 'is_staff': is_superuser or bool(groups)},
            )
            if created:
                user.set_password(QA_PASSWORD)
                user.save()
            for group_name in groups:
                group, _ = Group.objects.get_or_create(name=group_name)
                user.groups.add(group)
            self.stdout.write(f'  账号 {username}: {"新建" if created else "已存在"}')

    def _create_business_data(self):
        # 仓库与库位
        market_wh, _ = Warehouse.objects.get_or_create(name='市场库', defaults={'code': 'QA-MARKET'})
        rd_wh, _ = Warehouse.objects.get_or_create(name='研发库', defaults={'code': 'QA-RD'})
        for wh in (market_wh, rd_wh):
            for code in ('A-01', 'A-02'):
                Location.objects.get_or_create(warehouse=wh, code=code, defaults={'name': f'{code} 货架'})

        # 物品类型：一个资产类型 + 一个耗材类型
        category, _ = Category.objects.get_or_create(name='QA 测试分类', defaults={'code': 'QA-CAT'})
        asset_type, _ = ItemType.objects.get_or_create(
            category=category, name='QA 资产类型',
            defaults={'code': 'QA-ASSET', 'is_serialized': True},
        )
        stock_type, _ = ItemType.objects.get_or_create(
            category=category, name='QA 耗材类型',
            defaults={'code': 'QA-STOCK', 'is_serialized': False, 'safety_stock': 5},
        )

        # 基础资料
        customer, _ = Customer.objects.get_or_create(code='QA-CUST', defaults={'name': 'QA 客户'})
        Supplier.objects.get_or_create(code='QA-SUP', defaults={'name': 'QA 供应商'})
        ServiceProvider.objects.get_or_create(code='QA-SVC', defaults={'name': 'QA 维修服务商'})
        project, _ = Project.objects.get_or_create(
            code='QA-PROJ', defaults={'name': 'QA 项目', 'customer': customer},
        )
        WorkOrder.objects.get_or_create(
            code='QA-WO', defaults={'name': 'QA 工单', 'project': project, 'customer': customer},
        )

        # 各状态资产：在库 3、借出 1、维修中 1、已售出 1
        location = Location.objects.filter(warehouse=market_wh).first()
        status_plan = [
            (Asset.Status.IN_STOCK, 3),
            (Asset.Status.BORROWED, 1),
            (Asset.Status.REPAIRING, 1),
            (Asset.Status.SOLD, 1),
        ]
        seq = 0
        for status, count in status_plan:
            for _ in range(count):
                seq += 1
                Asset.objects.get_or_create(
                    asset_code=f'QA-ASSET-{seq:03d}',
                    defaults={
                        'name': f'QA 测试资产 {seq}',
                        'item_type': asset_type,
                        'warehouse': market_wh,
                        'location': location,
                        'status': status,
                        'serial_number': f'QA-SN-{seq:03d}',
                    },
                )
        # 一条重复 SN（供检索/导入冲突测试）
        Asset.objects.get_or_create(
            asset_code='QA-ASSET-DUP',
            defaults={
                'name': 'QA 重复SN资产', 'item_type': asset_type,
                'warehouse': market_wh, 'location': location,
                'serial_number': 'QA-SN-001',
            },
        )

        # 耗材：库存 20，安全库存 5
        StockItem.objects.get_or_create(
            code='QA-STOCK-001',
            defaults={
                'name': 'QA 测试耗材', 'item_type': stock_type, 'warehouse': market_wh,
                'location': location, 'quantity': 20, 'safety_stock': 5, 'unit_cost': 1,
            },
        )

        # 一个带组件的成品设备
        unit, _ = CompositeUnit.objects.get_or_create(
            unit_code='QA-UNIT-001',
            defaults={'name': 'QA 成品设备', 'model': 'QA-MODEL', 'status': CompositeUnit.Status.ASSEMBLED},
        )
        component = Asset.objects.filter(status=Asset.Status.IN_STOCK, asset_code__startswith='QA-ASSET').first()
        if component and not ComponentLink.objects.filter(composite_unit=unit, asset=component, disassembled_at__isnull=True).exists():
            ComponentLink.objects.create(composite_unit=unit, asset=component)
        self.stdout.write('  业务数据已就绪')
