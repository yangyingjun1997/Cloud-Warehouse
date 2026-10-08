"""只读的数据一致性检查服务。

页面、管理命令和定时任务都从这里读取检查结果。检查只生成问题清单，
不会自动修复业务数据；每条问题都包含稳定的问题代码、对象和定位链接，
便于告警去重以及管理员后续处理。
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import quote

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from apps.inventory.models import Asset, StockItem, StockItemHolding
from apps.workflow.models import (
    InventoryReservation,
    PurchaseOrderLine,
    WorkflowRequestLine,
)


ISSUE_SUGGESTIONS = {
    'location_without_warehouse': '进入物品资料，先补充所属仓库，再确认库位。',
    'location_warehouse_mismatch': '选择与所属仓库一致的库位，或通过盘点任务修正实际位置。',
    'asset_status_dimensions_mismatch': '核对申请、出入库、维修或盘点记录，按实际业务流程修正，不要直接改兼容状态。',
    'asset_state_combination_invalid': '核对实物位置、可用性、质量和处置情况，优先通过出入库、维修、盘点或报废流程修正。',
    'asset_in_stock_without_warehouse': '补充资产所属仓库和库位，确认资产实际存放位置。',
    'in_stock_with_holder': '核对资产是否已经归还；已归还则走归还执行流程，未归还则保留正确持有人。',
    'outbound_without_holder': '补录内部持有人或外部接收人，确保借出/领用资产可追溯。',
    'stock_without_warehouse': '补充耗材配件所属仓库和库位，确认当前库存的实际存放位置。',
    'duplicate_serial_number': '核对资产实物标签，保留正确编号并更正重复的生产厂家 SN。',
    'duplicate_manufacturer_barcode': '核对厂家条码标签，确保每个厂家条码只对应一件资产。',
    'duplicate_qr_value': '重新生成或更正系统二维码内容，确保每件资产二维码唯一。',
    'reserved_quantity_exceeds_stock': '核对有效申请和预占数量，关闭无效申请或通过库存调整补正账面数量。',
    'reserved_asset_not_in_stock': '核对关联申请和资产当前状态，释放无效预占或按实际业务完成出库。',
    'expired_active_reservation': '核对申请是否仍有效；无效申请应关闭并释放预占。',
    'holding_quantity_exceeds_stock': '核对借用、归还和库存流水，补录归还或修正异常借用余额。',
    'request_line_target_invalid': '打开申请单，删除无效明细并重新选择一项资产或耗材配件。',
    'purchase_received_exceeds_ordered': '核对采购订单和到货登记，确认是否重复登记或采购数量填写错误。',
    'receipt_total_mismatch': '核对到货明细与采购订单累计到货数量，修正重复或遗漏的到货记录。',
}


def consistency_issue(severity, code, object_type, target, message, url='', suggestion=''):
    return {
        'severity': severity,
        'code': code,
        'object_type': object_type,
        'target': target,
        'message': message,
        'url': url,
        'suggestion': suggestion or ISSUE_SUGGESTIONS.get(code, '请核对相关业务单据和库存流水后处理。'),
    }


def build_consistency_issues():
    """返回当前数据库中可发现的结构与业务状态矛盾。"""
    issues = []

    def add(severity, code, object_type, target, message, url=''):
        issues.append(consistency_issue(severity, code, object_type, target, message, url))

    active_reservations = InventoryReservation.objects.filter(
        status=InventoryReservation.Status.ACTIVE,
    )
    active_reserved_asset_ids = set(
        active_reservations.filter(asset__isnull=False).values_list('asset_id', flat=True)
    )
    dimension_labels = {
        'location_state': ('实物位置', dict(Asset.LocationState.choices)),
        'availability_state': ('可用性', dict(Asset.AvailabilityState.choices)),
        'quality_state': ('质量', dict(Asset.QualityState.choices)),
        'disposition_state': ('处置', dict(Asset.DispositionState.choices)),
    }
    valid_dimension_combinations = {
        tuple(dimensions[field] for field in dimension_labels)
        for dimensions in Asset.LEGACY_STATUS_DIMENSIONS.values()
    }
    # 申请提交后，实物仍在仓库，但可用性暂时变为已预占。
    valid_dimension_combinations.add((
        Asset.LocationState.IN_WAREHOUSE,
        Asset.AvailabilityState.RESERVED,
        Asset.QualityState.NORMAL,
        Asset.DispositionState.INTERNAL,
    ))
    assets = list(Asset.objects.select_related('warehouse', 'location', 'item_type'))
    stock_items = list(StockItem.objects.select_related('warehouse', 'location', 'item_type'))
    for item in [*assets, *stock_items]:
        target = getattr(item, 'system_asset_no', None) or getattr(item, 'code', '')
        url = (
            reverse('asset_lifecycle', kwargs={'asset_id': item.id})
            if isinstance(item, Asset)
            else reverse('warehouse_stock_item_edit', kwargs={'stock_item_id': item.id})
        )
        if item.location_id and not item.warehouse_id:
            add('error', 'location_without_warehouse', '资产' if isinstance(item, Asset) else '耗材配件',
                target, '已设置库位，但没有设置所属仓库。', url)
        elif item.location_id and item.location.warehouse_id != item.warehouse_id:
            add('error', 'location_warehouse_mismatch', '资产' if isinstance(item, Asset) else '耗材配件',
                target, '所属仓库与库位所属仓库不一致。', url)
        if isinstance(item, Asset):
            expected_dimensions = dict(Asset.LEGACY_STATUS_DIMENSIONS.get(item.status, {}))
            if item.id in active_reserved_asset_ids:
                expected_dimensions['availability_state'] = Asset.AvailabilityState.RESERVED
            mismatches = []
            for field_name, expected_value in expected_dimensions.items():
                actual_value = getattr(item, field_name)
                if actual_value != expected_value:
                    label, choices = dimension_labels[field_name]
                    mismatches.append(
                        f'{label}应为“{choices.get(expected_value, expected_value)}”，'
                        f'当前为“{choices.get(actual_value, actual_value) or "空值"}”'
                    )
            if mismatches:
                add('error', 'asset_status_dimensions_mismatch', '资产', target,
                    '；'.join(mismatches) + '。', url)
            actual_combination = tuple(
                getattr(item, field_name) for field_name in dimension_labels
            )
            if actual_combination not in valid_dimension_combinations:
                labels = [
                    f'{dimension_labels[field_name][0]}“{dimension_labels[field_name][1].get(value, value) or "空值"}”'
                    for field_name, value in zip(dimension_labels, actual_combination)
                ]
                add(
                    'error', 'asset_state_combination_invalid', '资产', target,
                    '四维状态组合不受支持：' + '、'.join(labels) + '。', url,
                )
            if item.status == Asset.Status.IN_STOCK and not item.warehouse_id:
                add('warning', 'asset_in_stock_without_warehouse', '资产', target,
                    '资产状态为在库，但没有所属仓库。', url)
            if item.status == Asset.Status.IN_STOCK and item.current_holder_id:
                add('warning', 'in_stock_with_holder', '资产', target,
                    '资产状态为在库，但仍保留当前持有人。', url)
            if item.status in {Asset.Status.BORROWED, Asset.Status.ISSUED} and not (
                item.current_holder_id or item.external_holder_name
            ):
                add('warning', 'outbound_without_holder', '资产', target,
                    '资产已借出或领用，但没有登记持有人。', url)
        elif item.quantity > 0 and not item.warehouse_id:
            add('warning', 'stock_without_warehouse', '耗材配件', target,
                '当前库存大于 0，但没有所属仓库。', url)

    duplicate_fields = (
        ('serial_number', '生产厂家 SN', 'duplicate_serial_number'),
        ('manufacturer_barcode', '生产厂家条码', 'duplicate_manufacturer_barcode'),
        ('qr_value', '系统二维码内容', 'duplicate_qr_value'),
    )
    for field, label, code in duplicate_fields:
        values = list(
            Asset.objects.exclude(**{field: ''})
            .values(field)
            .annotate(total=Count('id'))
            .filter(total__gt=1)
            .values_list(field, flat=True)
        )
        for value in values:
            matched = list(Asset.objects.filter(**{field: value}).order_by('system_asset_no'))
            target = '、'.join(asset.system_asset_no for asset in matched)
            url = f'{reverse("asset_lookup")}?q={quote(value)}'
            add('error', code, '资产识别码', target,
                f'{label}“{value}”被 {len(matched)} 件资产重复使用。', url)

    reserved_by_stock = defaultdict(int)
    for reservation in active_reservations.filter(stock_item__isnull=False).values(
        'stock_item_id', 'quantity',
    ):
        reserved_by_stock[reservation['stock_item_id']] += reservation['quantity']
    stock_map = {
        item.id: item
        for item in StockItem.objects.filter(id__in=reserved_by_stock).select_related('item_type')
    }
    for stock_id, reserved_quantity in reserved_by_stock.items():
        item = stock_map.get(stock_id)
        if item and reserved_quantity > item.quantity:
            add('error', 'reserved_quantity_exceeds_stock', '耗材配件', item.code,
                f'有效预占 {reserved_quantity} {item.unit}，超过当前库存 {item.quantity} {item.unit}。',
                reverse('warehouse_stock_item_edit', kwargs={'stock_item_id': item.id}))
    for reservation in active_reservations.filter(asset__isnull=False).select_related('asset'):
        if reservation.asset.status not in {Asset.Status.IN_STOCK, Asset.Status.ASSEMBLED}:
            add('error', 'reserved_asset_not_in_stock', '资产', reservation.asset.system_asset_no,
                f'资产当前状态为“{reservation.asset.get_status_display()}”，但仍有有效预占。',
                reverse('asset_lifecycle', kwargs={'asset_id': reservation.asset.id}))
    for reservation in active_reservations.filter(
        expires_at__lt=timezone.now(),
    ).select_related('asset', 'stock_item'):
        item = reservation.asset or reservation.stock_item
        target = item.system_asset_no if reservation.asset else item.code
        add('warning', 'expired_active_reservation',
            '资产' if reservation.asset else '耗材配件', target,
            '预占已超过有效期，但状态仍为预占中。')

    holding_totals = defaultdict(int)
    for row in StockItemHolding.objects.filter(quantity__gt=0).values('stock_item_id', 'quantity'):
        holding_totals[row['stock_item_id']] += row['quantity']
    holding_items = {
        item.id: item for item in StockItem.objects.filter(id__in=holding_totals)
    }
    for stock_id, holding_quantity in holding_totals.items():
        item = holding_items.get(stock_id)
        if item and holding_quantity > item.quantity:
            add('error', 'holding_quantity_exceeds_stock', '耗材配件', item.code,
                f'个人借用余额合计 {holding_quantity} {item.unit}，超过当前库存 {item.quantity} {item.unit}。',
                reverse('warehouse_stock_item_edit', kwargs={'stock_item_id': item.id}))

    invalid_lines = WorkflowRequestLine.objects.filter(
        Q(asset__isnull=True, stock_item__isnull=True)
        | Q(asset__isnull=False, stock_item__isnull=False)
    ).select_related('request')
    for line in invalid_lines:
        add('error', 'request_line_target_invalid', '申请明细', line.request.request_no,
            '申请明细必须且只能关联一项资产或耗材配件。',
            reverse('workflow_detail', kwargs={'request_id': line.request_id}))

    purchase_lines = PurchaseOrderLine.objects.annotate(
        receipt_total=Coalesce(Sum('receipt_lines__received_quantity'), 0),
    ).select_related('order')
    for line in purchase_lines.filter(received_quantity__gt=F('ordered_quantity')):
        add('error', 'purchase_received_exceeds_ordered', '采购订单明细', line.order.order_no,
            f'{line.name_snapshot} 的累计到货数量超过订购数量。',
            reverse('purchase_order_detail', kwargs={'order_id': line.order_id}))
    for line in purchase_lines.filter(receipt_total__gt=F('received_quantity')):
        add('error', 'receipt_total_mismatch', '采购到货明细', line.order.order_no,
            f'{line.name_snapshot} 的到货明细合计超过采购订单记录的已到货数量。',
            reverse('purchase_order_detail', kwargs={'order_id': line.order_id}))

    severity_order = {'error': 0, 'warning': 1}
    issues.sort(key=lambda item: (severity_order[item['severity']], item['code'], item['target']))
    return issues
