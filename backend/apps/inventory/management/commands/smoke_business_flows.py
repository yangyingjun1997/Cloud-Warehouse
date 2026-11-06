from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.inventory.availability import is_asset_available
from apps.inventory.lifecycle_services import create_maintenance_record, finish_maintenance_record
from apps.inventory.models import Asset, AssetAssemblyLink, Location, MaintenanceRecord
from apps.workflow.models import InventoryReservation, InventoryTransaction, WorkflowRequest
from apps.workflow.services import (
    approve_request,
    process_request,
    quick_process_request,
    reject_request,
    submit_request,
)


class Command(BaseCommand):
    help = '在当前数据库提交关键业务链路冒烟数据；仅用于可随后重置的测试环境。'

    def add_arguments(self, parser):
        parser.add_argument('--actor', default='admin')
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
            # 防止服务器已配置的钉钉在冒烟期间收到测试消息。
            with patch('apps.workflow.services.send_users_or_group', return_value=True):
                quick_asset, approval_asset, rejection_asset = assets
                self._quick_borrow_and_return(quick_asset, actor)
                self._approve_process_and_return(approval_asset, actor)
                self._reject_request(rejection_asset, actor)
                self._maintenance_round_trip(rejection_asset, actor)

            self.stdout.write(self.style.SUCCESS(
                '业务冒烟通过：快速借出/归还、申请提交/审批/出库/归还、审批驳回、维修完工回库。'
            ))
            self.stdout.write(
                f'当前事务内申请 {WorkflowRequest.objects.count()} 张、出入库流水 '
                f'{InventoryTransaction.objects.count()} 条、维修单 {MaintenanceRecord.objects.count()} 张。'
            )
            if options['rollback']:
                transaction.set_rollback(True)
                self.stdout.write(self.style.SUCCESS('回滚模式已启用，本次冒烟产生的数据不会保留。'))

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
    def _approve_process_and_return(self, asset, actor):
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
        approve_request(request_obj, actor, '服务器冒烟审批通过')
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
    def _reject_request(self, asset, actor):
        request_obj = self._request(WorkflowRequest.RequestType.BORROW, actor)
        request_obj.lines.create(asset=asset, quantity=1)
        submit_request(request_obj, actor)
        reject_request(request_obj, actor, '服务器冒烟：资料不完整')
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
