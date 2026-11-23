from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.inventory.availability import is_asset_available
from apps.inventory.check_services import (
    create_check_task,
    review_check_task,
    scan_check_value,
    start_check_task,
    submit_check_task,
)
from apps.inventory.import_recovery_services import rollback_import_batch
from apps.inventory.lifecycle_services import create_maintenance_record, finish_maintenance_record
from apps.inventory.models import (
    Asset,
    AssetAssemblyLink,
    Category,
    ImportBatch,
    InventoryCheckAdjustment,
    InventoryCheckTask,
    ItemType,
    Location,
    MaintenanceRecord,
    StockItem,
    Warehouse,
)
from apps.inventory.v3_import_services import apply_inventory_v3_import
from apps.workflow.models import (
    InventoryReservation,
    InventoryTransaction,
    OfflineOperation,
    PurchaseOrder,
    PurchaseReceipt,
    PurchaseRequest,
    WorkflowRequest,
)
from apps.workflow.offline_services import apply_offline_operation, receive_offline_operation
from apps.workflow.services import (
    approve_request,
    approve_purchase_request,
    create_purchase_order,
    place_purchase_order,
    process_request,
    quick_process_request,
    receive_purchase_order,
    reject_request,
    submit_request,
)


class Command(BaseCommand):
    help = '在当前数据库提交关键业务链路冒烟数据；仅用于可随后重置的测试环境。'

    def add_arguments(self, parser):
        parser.add_argument('--actor', default='admin')
        parser.add_argument(
            '--approver', default='',
            help='可选审批账号。未指定时自动选择另一名活跃超级管理员。',
        )
        parser.add_argument('--confirm', required=True)
        parser.add_argument(
            '--rollback', action='store_true',
            help='完整执行并校验业务写流程后回滚全部测试数据，适合已有数据的服务器。',
        )

    def handle(self, *args, **options):
        if options['confirm'] != '确认写入冒烟数据':
            raise CommandError('必须传入 --confirm 确认写入冒烟数据')
        actor = get_user_model().objects.filter(username=options['actor'], is_superuser=True).first()
        if actor is None:
            raise CommandError('未找到指定超级管理员。')
        approver = actor
        if options['approver']:
            approver = get_user_model().objects.filter(
                username=options['approver'], is_superuser=True, is_active=True,
            ).first()
            if approver is None:
                raise CommandError('未找到指定审批超级管理员。')
        else:
            approver = get_user_model().objects.filter(
                is_superuser=True, is_active=True,
            ).exclude(pk=actor.pk).order_by('pk').first() or actor
        active_assembly_links = AssetAssemblyLink.objects.filter(removed_at__isnull=True)
        assets = list(
            Asset.objects.filter(
                status=Asset.Status.IN_STOCK,
                warehouse__isnull=False,
            ).exclude(
                pk__in=active_assembly_links.values('child_asset_id'),
            ).exclude(
                pk__in=active_assembly_links.values('parent_asset_id'),
            ).select_related('warehouse', 'location').order_by('system_asset_no')
        )
        assets = [asset for asset in assets if is_asset_available(asset)][:3]
        if len(assets) < 3:
            raise CommandError('至少需要 3 件已登记仓库的可用在库资产。')

        with transaction.atomic():
            quick_asset, approval_asset, rejection_asset = assets
            self._quick_borrow_and_return(quick_asset, actor)
            self._approve_process_and_return(approval_asset, actor, approver)
            self._reject_request(rejection_asset, actor, approver)
            self._maintenance_round_trip(rejection_asset, actor)
            self._extended_rollback_smoke(actor, approver)

            self.stdout.write(self.style.SUCCESS(
                '业务冒烟通过：快速借出/归还、申请审批出库、审批驳回、维修回库、'
                '采购到货、盘点修正、导入撤销、离线幂等与冲突。'
            ))
            self.stdout.write(
                f'当前事务内申请 {WorkflowRequest.objects.count()} 张、出入库流水 '
                f'{InventoryTransaction.objects.count()} 条、维修单 {MaintenanceRecord.objects.count()} 张。'
            )
            if options['rollback']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.SUCCESS('回滚模式已启用，本次冒烟产生的数据不会保留。'))

    def _extended_rollback_smoke(self, actor, approver):
        """扩展链路使用独立保存点，成功后也不在任何环境留下 QA 数据。"""
        with transaction.atomic():
            suffix = uuid4().hex[:8].upper()
            warehouse = Warehouse.objects.create(
                name=f'冒烟隔离仓 {suffix}', code=f'SMOKE-WH-{suffix}',
            )
            location = Location.objects.create(
                warehouse=warehouse, name='冒烟隔离库位', code=f'SMOKE-{suffix}',
            )
            category = Category.objects.create(
                name=f'冒烟隔离分类 {suffix}', code=f'SMOKE-CAT-{suffix}',
            )
            stock_type = ItemType.objects.create(
                name='冒烟耗材类型', code=f'SMOKE-STOCK-{suffix}',
                category=category, is_serialized=False, unit='件',
            )
            asset_type = ItemType.objects.create(
                name='冒烟资产类型', code=f'SMOKE-ASSET-{suffix}',
                category=category, is_serialized=True, unit='台',
            )
            stock = StockItem.objects.create(
                name='冒烟耗材', code=f'SMOKE-PART-{suffix}', item_type=stock_type,
                warehouse=warehouse, location=location, quantity=5, unit='件',
            )

            self._purchase_receipt(stock, actor, approver)
            self._inventory_check_adjustment(stock, actor)
            self._import_and_rollback(
                actor=actor, warehouse=warehouse, location=location,
                item_type=stock_type, suffix=suffix,
            )
            self._offline_idempotency_and_conflict(
                actor=actor, warehouse=warehouse, location=location,
                item_type=asset_type,
            )
            transaction.set_rollback(True)

    def _purchase_receipt(self, stock, actor, approver):
        before_quantity = stock.quantity
        purchase_request = PurchaseRequest.objects.create(
            applicant=actor,
            reason='服务器冒烟：采购到货',
            status=PurchaseRequest.Status.PENDING_APPROVAL,
        )
        purchase_request.lines.create(
            stock_item=stock,
            name_snapshot=stock.name,
            code_snapshot=stock.code,
            management_mode='耗材配件',
            quantity=2,
            unit=stock.unit,
            reference_unit_cost='10.00',
            supplier_snapshot='冒烟供应商',
        )
        approve_purchase_request(purchase_request, approver, '服务器冒烟审批采购')
        order = create_purchase_order(purchase_request, actor)
        place_purchase_order(order, actor, timezone.localdate() + timedelta(days=1), '服务器冒烟下达')
        receipt = receive_purchase_order(
            order, actor, {str(order.lines.get().id): 2}, stock.warehouse, stock.location,
        )
        stock.refresh_from_db()
        order.refresh_from_db()
        self._assert(stock.quantity == before_quantity + 2, '采购到货后耗材库存数量错误。')
        self._assert(order.status == PurchaseOrder.Status.RECEIVED, '采购到货后订单未完成。')
        self._assert(
            PurchaseReceipt.objects.filter(pk=receipt.pk, inventory_request__isnull=False).exists(),
            '采购到货未生成关联入库单。',
        )

    def _inventory_check_adjustment(self, stock, actor):
        expected_quantity = stock.quantity - 1
        task = create_check_task(
            actor=actor,
            name='服务器冒烟盘点',
            warehouse_id=stock.warehouse_id,
            location_id=stock.location_id,
        )
        start_check_task(task, actor)
        scan_check_value(task, actor, stock.code, quantity=expected_quantity)
        submit_check_task(task, actor)
        task, adjustments = review_check_task(task, actor, '服务器冒烟确认盘点差异')
        stock.refresh_from_db()
        self._assert(task.status == InventoryCheckTask.Status.COMPLETED, '盘点任务未完成。')
        self._assert(stock.quantity == expected_quantity, '盘点差异未正确修正耗材数量。')
        self._assert(len(adjustments) == 1, '盘点修正数量不正确。')
        self._assert(
            InventoryCheckAdjustment.objects.filter(task=task, stock_item=stock).exists(),
            '盘点修正未生成差异记录。',
        )

    def _import_and_rollback(self, *, actor, warehouse, location, item_type, suffix):
        stock_code = f'SMOKE-IMPORT-{suffix}'
        batch = ImportBatch.objects.create(
            source_filename=f'smoke-{suffix}.xlsx',
            mode=ImportBatch.Mode.INCREMENTAL,
            status=ImportBatch.Status.PREVIEWED,
            summary={'smoke': True},
            created_by=actor,
        )
        records = {
            'assets': [],
            'stocks': [{
                'row_number': 2,
                'operation': '新增',
                'stock_code': stock_code,
                'name': '冒烟导入耗材',
                'item_type_code': item_type.code,
                'warehouse_code': warehouse.code,
                'location_code': location.code,
                'quantity': 3,
                'unit': '件',
                'safety_stock': 1,
                'supplier_code': '',
                'supplier_name': '',
                'unit_cost': None,
            }],
        }
        apply_inventory_v3_import(actor=actor, batch=batch, records=records)
        self._assert(StockItem.objects.filter(code=stock_code).exists(), 'V3 冒烟导入未创建耗材。')
        self._assert(batch.mutations.count() == 1, 'V3 冒烟导入未生成变更账本。')
        rollback_import_batch(
            actor=actor,
            batch=batch,
            confirmation=str(batch.id),
            reason='服务器冒烟：验证整批撤销',
        )
        batch.refresh_from_db()
        self._assert(batch.status == ImportBatch.Status.ROLLED_BACK, '导入批次未进入已撤销状态。')
        self._assert(not StockItem.objects.filter(code=stock_code).exists(), '整批撤销后仍残留导入耗材。')

    def _offline_idempotency_and_conflict(self, *, actor, warehouse, location, item_type):
        asset = Asset.objects.create(
            name='冒烟离线资产', item_type=item_type,
            warehouse=warehouse, location=location,
        )
        payload = {
            'request_type': ['borrow'],
            'asset_ids': [str(asset.id)],
            'recipient_name': ['冒烟接收人'],
            'recipient_phone': ['13800000000'],
            'usage_location': ['冒烟测试区'],
            'reason': ['服务器冒烟：离线借用'],
            'contact_source': ['现场'],
            'transaction_date': [timezone.localdate().isoformat()],
            'expected_return_date': [(timezone.localdate() + timedelta(days=1)).isoformat()],
        }
        client_operation_id = uuid4()
        client_created_at = timezone.now().isoformat()
        operation, created = receive_offline_operation(
            operator=actor,
            client_operation_id=str(client_operation_id),
            client_created_at=client_created_at,
            payload=payload,
        )
        repeated, repeated_created = receive_offline_operation(
            operator=actor,
            client_operation_id=str(client_operation_id),
            client_created_at=client_created_at,
            payload=payload,
        )
        self._assert(created and not repeated_created and operation.id == repeated.id,
                     '离线重复同步未保持幂等。')
        applied = apply_offline_operation(operation, actor)
        apply_offline_operation(operation, actor)
        asset.refresh_from_db()
        self._assert(applied.status == OfflineOperation.Status.APPLIED, '离线记录未执行成功。')
        self._assert(asset.status == Asset.Status.BORROWED, '离线借用未更新资产状态。')
        self._assert(
            InventoryTransaction.objects.filter(request=applied.resulting_request).count() == 1,
            '离线记录重复执行产生了重复库存流水。',
        )

        return_request = self._request(
            WorkflowRequest.RequestType.RETURN,
            actor,
            quick=True,
            target_warehouse=warehouse,
            target_location=location,
        )
        return_request.lines.create(asset=asset, quantity=1)
        quick_process_request(return_request, actor, comment='服务器冒烟：离线借用后归还')

        conflict_asset = Asset.objects.create(
            name='冒烟离线冲突资产', item_type=item_type,
            warehouse=warehouse, location=location,
        )
        conflict_payload = {**payload, 'asset_ids': [str(conflict_asset.id)]}
        conflict_operation, _ = receive_offline_operation(
            operator=actor,
            client_operation_id=str(uuid4()),
            client_created_at=timezone.now().isoformat(),
            payload=conflict_payload,
        )
        conflict_asset.status = Asset.Status.SOLD
        conflict_asset.save(update_fields=['status', 'updated_at'])
        before_transactions = InventoryTransaction.objects.count()
        conflict_operation = apply_offline_operation(conflict_operation, actor)
        self._assert(conflict_operation.status == OfflineOperation.Status.CONFLICT,
                     '资产状态变化后离线记录未进入冲突状态。')
        self._assert(InventoryTransaction.objects.count() == before_transactions,
                     '冲突离线记录不应产生库存流水。')

    @transaction.atomic
    def _quick_borrow_and_return(self, asset, actor):
        request_obj = self._request(
            WorkflowRequest.RequestType.BORROW, actor, quick=True,
        )
        request_obj.lines.create(asset=asset, quantity=1)
        quick_process_request(request_obj, actor, comment='服务器冒烟：快速借出')
        asset.refresh_from_db()
        self._assert(asset.status == Asset.Status.BORROWED, '快速借出后状态错误。')

        return_obj = self._request(
            WorkflowRequest.RequestType.RETURN,
            actor,
            quick=True,
            target_warehouse=asset.warehouse,
            target_location=asset.location,
        )
        return_obj.lines.create(asset=asset, quantity=1)
        quick_process_request(return_obj, actor, comment='服务器冒烟：快速归还')
        asset.refresh_from_db()
        self._assert(asset.status == Asset.Status.IN_STOCK, '快速归还后状态错误。')

    @transaction.atomic
    def _approve_process_and_return(self, asset, actor, approver):
        request_obj = self._request(WorkflowRequest.RequestType.BORROW, actor)
        request_obj.lines.create(asset=asset, quantity=1)
        submit_request(request_obj, actor)
        self._assert(
            InventoryReservation.objects.filter(
                request_line__request=request_obj,
                status=InventoryReservation.Status.ACTIVE,
            ).exists(),
            '申请提交后未建立库存预占。',
        )
        approve_request(request_obj, approver, '服务器冒烟审批通过')
        process_request(request_obj, actor, '服务器冒烟出库')
        request_obj.refresh_from_db()
        asset.refresh_from_db()
        self._assert(request_obj.status == WorkflowRequest.Status.DONE, '审批出库单未完成。')
        self._assert(asset.status == Asset.Status.BORROWED, '审批出库后资产状态错误。')

        return_obj = self._request(
            WorkflowRequest.RequestType.RETURN,
            actor,
            quick=True,
            target_warehouse=asset.warehouse,
            target_location=asset.location,
        )
        return_obj.lines.create(asset=asset, quantity=1)
        quick_process_request(return_obj, actor, comment='服务器冒烟：审批出库后归还')
        asset.refresh_from_db()
        self._assert(asset.status == Asset.Status.IN_STOCK, '审批出库后的归还失败。')

    @transaction.atomic
    def _reject_request(self, asset, actor, approver):
        request_obj = self._request(WorkflowRequest.RequestType.BORROW, actor)
        request_obj.lines.create(asset=asset, quantity=1)
        submit_request(request_obj, actor)
        reject_request(request_obj, approver, '服务器冒烟：资料不完整')
        request_obj.refresh_from_db()
        asset.refresh_from_db()
        self._assert(request_obj.status == WorkflowRequest.Status.REJECTED, '审批驳回状态错误。')
        self._assert(asset.status == Asset.Status.IN_STOCK, '审批驳回不应改变资产状态。')
        self._assert(
            not InventoryReservation.objects.filter(
                request_line__request=request_obj,
                status=InventoryReservation.Status.ACTIVE,
            ).exists(),
            '审批驳回后库存预占未释放。',
        )

    @transaction.atomic
    def _maintenance_round_trip(self, asset, actor):
        warehouse, location = asset.warehouse, asset.location
        if location is None:
            location, _ = Location.objects.get_or_create(
                warehouse=warehouse,
                code='SMOKE-CHECK',
                defaults={'name': '部署冒烟临时库位'},
            )
        record = create_maintenance_record(
            asset=asset,
            actor=actor,
            fault_description='服务器冒烟：模拟故障',
            repair_type=MaintenanceRecord.RepairType.INTERNAL,
        )
        asset.refresh_from_db()
        self._assert(asset.status == Asset.Status.REPAIRING, '新建维修单后资产未进入维修中。')
        finish_maintenance_record(
            record,
            actor,
            resolution='服务器冒烟：维修完成',
            destination='warehouse',
            target_warehouse=warehouse,
            target_location=location,
        )
        asset.refresh_from_db()
        self._assert(asset.status == Asset.Status.IN_STOCK, '维修完工后资产未回到在库。')
        self._assert(asset.warehouse_id == warehouse.id and asset.location_id == location.id,
                     '维修完工后的仓库或库位错误。')

    @staticmethod
    def _request(request_type, actor, *, quick=False, target_warehouse=None, target_location=None):
        return WorkflowRequest.objects.create(
            request_type=request_type,
            applicant=actor,
            reason='服务器端到端冒烟',
            recipient_name='冒烟测试接收人',
            recipient_phone='13800000000',
            usage_location='服务器测试区',
            is_quick_process=quick,
            contact_source='部署验收' if quick else '',
            target_warehouse=target_warehouse,
            target_location=target_location,
        )

    @staticmethod
    def _assert(condition, message):
        if not condition:
            raise CommandError(message)
