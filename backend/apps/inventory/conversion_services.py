from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError

from apps.common.audit import record_operation
from apps.common.utils import build_code, build_daily_code
from .availability import is_asset_available

from .models import Asset, InventoryConversion, StockItem


class InventoryConversionError(ValueError):
    pass


def _serial_numbers(value: str, quantity: int) -> list[str]:
    values = [line.strip() for line in (value or '').replace(',', '\n').splitlines() if line.strip()]
    if len(values) > quantity:
        raise InventoryConversionError('填写的厂家序列号数量不能超过转换数量。')
    if len(values) != len(set(values)):
        raise InventoryConversionError('厂家序列号存在重复，请检查后重试。')
    if values and Asset.objects.filter(serial_number__in=values).exists():
        raise InventoryConversionError('至少一个厂家序列号已存在于系统中。')
    return values + [''] * (quantity - len(values))


@transaction.atomic
def convert_stock_to_assets(*, actor, stock_item_id, quantity: int, serial_numbers='', note=''):
    if quantity <= 0:
        raise InventoryConversionError('转换数量必须大于 0。')
    stock_item = StockItem.objects.select_for_update().select_related(
        'item_type', 'warehouse', 'location'
    ).filter(pk=stock_item_id).first()
    if not stock_item:
        raise InventoryConversionError('未找到要转换的耗材配件。')
    if not stock_item.item_type_id:
        raise InventoryConversionError('请先为该耗材配件设置物品类型。')
    if stock_item.quantity < quantity:
        raise InventoryConversionError(f'当前库存只有 {stock_item.quantity} {stock_item.unit}，无法转换。')

    serials = _serial_numbers(serial_numbers, quantity)
    before_quantity = stock_item.quantity
    stock_item.quantity -= quantity
    stock_item.save(update_fields=['quantity', 'updated_at'])

    assets = []
    for serial_number in serials:
        assets.append(Asset.objects.create(
            name=stock_item.name,
            item_type=stock_item.item_type,
            serial_number=serial_number,
            supplier=stock_item.supplier,
            purchase_amount=stock_item.unit_cost,
            warehouse=stock_item.warehouse,
            location=stock_item.location,
            status=Asset.Status.IN_STOCK,
            remarks=f'由耗材配件 {stock_item.code} 转为单件管理。',
        ))

    conversion = InventoryConversion.objects.create(
        conversion_no=build_daily_code('CONV'),
        direction=InventoryConversion.Direction.STOCK_TO_ASSET,
        item_type=stock_item.item_type,
        stock_item=stock_item,
        quantity=quantity,
        note=(note or '').strip()[:255],
        created_by=actor,
    )
    conversion.assets.add(*assets)
    record_operation(
        actor,
        'stock_converted_to_assets',
        conversion,
        summary=f'{stock_item.code} 转为 {quantity} 件单件资产',
        before={'stock_item_id': stock_item.id, 'quantity': before_quantity},
        after={
            'stock_item_id': stock_item.id,
            'quantity': stock_item.quantity,
            'asset_ids': [asset.id for asset in assets],
        },
    )
    return conversion, assets


@transaction.atomic
def convert_assets_to_stock(*, actor, asset_ids, target_stock_item_id=None, note=''):
    normalized_ids = list(dict.fromkeys(str(value) for value in asset_ids if value))
    if not normalized_ids:
        raise InventoryConversionError('请至少选择一件在库资产。')
    try:
        assets = list(Asset.objects.select_for_update().select_related(
            'item_type', 'warehouse', 'location'
        ).filter(pk__in=normalized_ids).order_by('asset_code'))
    except (ValidationError, ValueError):
        raise InventoryConversionError('资产编号格式无效，请刷新页面后重试。')
    if len(assets) != len(normalized_ids):
        raise InventoryConversionError('部分资产不存在，请刷新页面后重试。')
    if any(not is_asset_available(asset) for asset in assets):
        raise InventoryConversionError('只有“在库”状态的资产可以转为耗材配件。')

    first = assets[0]
    scope = (first.item_type_id, first.warehouse_id, first.location_id)
    if any((asset.item_type_id, asset.warehouse_id, asset.location_id) != scope for asset in assets):
        raise InventoryConversionError('所选资产必须属于同一物品类型、仓库和库位。')

    stock_item = None
    if target_stock_item_id:
        stock_item = StockItem.objects.select_for_update().filter(pk=target_stock_item_id).first()
        if not stock_item:
            raise InventoryConversionError('目标耗材配件记录不存在。')
        target_scope = (stock_item.item_type_id, stock_item.warehouse_id, stock_item.location_id)
        if target_scope != scope:
            raise InventoryConversionError('目标耗材配件的物品类型、仓库和库位与所选资产不一致。')
    else:
        matches = StockItem.objects.select_for_update().filter(
            item_type_id=first.item_type_id,
            warehouse_id=first.warehouse_id,
            location_id=first.location_id,
        ).order_by('created_at')
        if matches.count() > 1:
            raise InventoryConversionError('同一仓位存在多条匹配的耗材配件，请手动选择合并目标。')
        stock_item = matches.first()
        if stock_item is None:
            stock_item = StockItem.objects.create(
                name=first.name,
                code=build_code('STOCK'),
                unit=first.item_type.unit,
                quantity=0,
                safety_stock=first.item_type.safety_stock,
                supplier=first.supplier,
                unit_cost=first.purchase_amount,
                item_type=first.item_type,
                warehouse=first.warehouse,
                location=first.location,
            )

    before_quantity = stock_item.quantity
    before_unit_cost = stock_item.unit_cost
    asset_costs = [asset.purchase_amount for asset in assets]
    if all(value is not None for value in asset_costs) and (before_quantity == 0 or before_unit_cost is not None):
        existing_cost = Decimal(before_quantity) * (before_unit_cost or Decimal('0'))
        converted_cost = sum(asset_costs, Decimal('0'))
        stock_item.unit_cost = (existing_cost + converted_cost) / Decimal(before_quantity + len(assets))
    stock_item.quantity += len(assets)
    stock_item.save(update_fields=['quantity', 'unit_cost', 'updated_at'])
    for asset in assets:
        asset.status = Asset.Status.CONVERTED_TO_STOCK
        asset.save(update_fields=['status', 'updated_at'])

    conversion = InventoryConversion.objects.create(
        conversion_no=build_daily_code('CONV'),
        direction=InventoryConversion.Direction.ASSET_TO_STOCK,
        item_type=first.item_type,
        stock_item=stock_item,
        quantity=len(assets),
        note=(note or '').strip()[:255],
        created_by=actor,
    )
    conversion.assets.add(*assets)
    record_operation(
        actor,
        'assets_converted_to_stock',
        conversion,
        summary=f'{len(assets)} 件单件资产转入 {stock_item.code}',
        before={
            'stock_item_id': stock_item.id,
            'quantity': before_quantity,
            'unit_cost': before_unit_cost,
            'asset_ids': [asset.id for asset in assets],
        },
        after={'stock_item_id': stock_item.id, 'quantity': stock_item.quantity, 'unit_cost': stock_item.unit_cost},
    )
    return conversion, stock_item
