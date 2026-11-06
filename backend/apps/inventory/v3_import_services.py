from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from apps.common.audit import record_operation
from apps.common.utils import build_code

from .models import (
    Asset, ImportBatch, ImportIssue, ImportMutation, ItemType, Location, StockItem, Supplier,
    Warehouse,
)
from .import_recovery_services import record_import_mutation, snapshot_instance
from .v2_import_services import (
    HEADERS as ASSET_HEADERS,
    AssetV2ImportError,
    apply_asset_v2_import,
    build_asset_v2_template,
    parse_asset_v2_workbook,
    validate_asset_v2_records,
)


TEMPLATE_VERSION = 3
ASSET_SHEET_NAME = '单件资产'
STOCK_SHEET_NAME = '耗材配件'
STOCK_HEADERS = [
    '外部行号', '导入动作*', '耗材配件编码', '物品名称*', '物品类型代码*',
    '仓库代码*', '库位代码', '库存数量*', '单位*', '安全库存',
    '供应商代码', '供应商名称', '单价（元）',
]


class InventoryV3ImportError(ValueError):
    pass


def _text(value) -> str:
    return '' if value is None else str(value).strip()


def _non_negative_number(value, *, integer=False, default=None):
    if value in (None, ''):
        return default
    try:
        number = Decimal(str(value).replace(',', '').strip())
    except InvalidOperation as exc:
        raise InventoryV3ImportError(f'“{value}”不是有效数字。') from exc
    if number < 0 or (integer and number != number.to_integral_value()):
        label = '非负整数' if integer else '非负数'
        raise InventoryV3ImportError(f'“{value}”必须是{label}。')
    return int(number) if integer else number


def _stock_issue(record, severity, code, message):
    external = record.get('external_row_no') or ''
    if external:
        message = f'[外部行号 {external}] {message}'
    return {
        'sheet_name': STOCK_SHEET_NAME,
        'row_number': record['row_number'],
        'severity': severity,
        'code': code,
        'message': message[:255],
        'source_data': record.get('source') or {},
    }


def is_inventory_v3_workbook(content: bytes) -> bool:
    try:
        workbook = load_workbook(BytesIO(content), read_only=True)
    except Exception:
        return False
    try:
        return {ASSET_SHEET_NAME, STOCK_SHEET_NAME}.issubset(workbook.sheetnames)
    finally:
        workbook.close()


def build_inventory_v3_template() -> BytesIO:
    workbook = load_workbook(build_asset_v2_template())
    asset_sheet = workbook['资产导入V2']
    asset_sheet.title = ASSET_SHEET_NAME

    stock_sheet = workbook.create_sheet(STOCK_SHEET_NAME, 1)
    stock_sheet.append(STOCK_HEADERS)
    stock_examples = [
        ['', '新增', '', '示例：六类成品网线', 'NET-PART', 'RND', 'B-01', 30, '根', 8, '', '', 18.5],
        ['', '新增', 'CONNECTOR-GX16-4', '示例：航空插头组件', 'CONNECTOR', 'MARKET', 'C-03', 50, '套', 10, '', '', 12.8],
        ['', '更新', 'CABLE-CAT6-003M', '示例：更新已有耗材', 'NET-PART', 'RND', 'B-01', 24, '根', 8, '', '', 18.5],
    ]
    for row in stock_examples:
        stock_sheet.append(row)
    stock_sheet.freeze_panes = 'D2'
    last_column = stock_sheet.cell(1, len(STOCK_HEADERS)).column_letter
    stock_sheet.auto_filter.ref = f'A1:{last_column}{stock_sheet.max_row}'
    stock_sheet.row_dimensions[1].height = 28
    for cell in stock_sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='176E68')
    for index, header in enumerate(STOCK_HEADERS, 1):
        stock_sheet.column_dimensions[stock_sheet.cell(1, index).column_letter].width = min(
            max(len(header) * 2 + 4, 14), 28,
        )

    instructions = workbook['填写说明']
    instructions.insert_rows(2, amount=5)
    instructions['A2'] = 'V3 工作簿'
    instructions['B2'] = '单件资产与耗材配件分别填写；禁止根据 SN、数量、状态或备注互相推断。'
    instructions['A3'] = '单件资产表'
    instructions['B3'] = '每件实物一行，沿用 V2 身份、SC/DZSB、AST、SN 和状态规则。'
    instructions['A4'] = '耗材配件表'
    instructions['B4'] = '按物品类型、仓库和库位管理数量；新增可留空编码由系统生成，更新必须填写编码。'
    instructions['A5'] = '导入动作'
    instructions['B5'] = '每行必须明确填写“新增”或“更新”；系统不会静默猜测。'
    instructions['A6'] = '再次导入'
    instructions['B6'] = '从系统导出的 V3 文件可修改后重新导入；AST 和耗材配件编码用于精确定位。'

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _parse_stock_sheet(content: bytes) -> tuple[list[dict], list[dict]]:
    try:
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise InventoryV3ImportError('Excel 文件无法读取，请确认文件没有损坏且格式为 .xlsx。') from exc
    try:
        if STOCK_SHEET_NAME not in workbook.sheetnames:
            raise InventoryV3ImportError(f'未找到“{STOCK_SHEET_NAME}”工作表，请使用 V3 模板。')
        rows = workbook[STOCK_SHEET_NAME].iter_rows(values_only=True)
        try:
            source_headers = [_text(value).rstrip('*') for value in next(rows)]
        except StopIteration as exc:
            raise InventoryV3ImportError('耗材配件工作表为空。') from exc
        expected = [header.rstrip('*') for header in STOCK_HEADERS]
        required = ['导入动作', '物品名称', '物品类型代码', '仓库代码', '库存数量', '单位']
        missing = [header for header in required if header not in source_headers]
        if missing:
            raise InventoryV3ImportError(f'耗材配件表缺少列：{"、".join(missing)}。')
        index = {header: source_headers.index(header) for header in expected if header in source_headers}
        records = []
        issues = []
        for row_number, values in enumerate(rows, 2):
            if not any(value not in (None, '') for value in values):
                continue

            def get(key):
                position = index.get(key)
                return values[position] if position is not None and position < len(values) else None

            source = {header: _text(get(header)) for header in expected}
            record = {
                'sheet_name': STOCK_SHEET_NAME,
                'row_number': row_number,
                'source': source,
                'external_row_no': _text(get('外部行号')),
                'operation': _text(get('导入动作')),
                'stock_code': _text(get('耗材配件编码')).upper(),
                'name': _text(get('物品名称')),
                'item_type_code': _text(get('物品类型代码')).upper(),
                'warehouse_code': _text(get('仓库代码')).upper(),
                'location_code': _text(get('库位代码')).upper(),
                'unit': _text(get('单位')),
                'supplier_code': _text(get('供应商代码')).upper(),
                'supplier_name': _text(get('供应商名称')),
            }
            try:
                record['quantity'] = _non_negative_number(get('库存数量'), integer=True)
                record['safety_stock'] = _non_negative_number(get('安全库存'), integer=True, default=0)
                record['unit_cost'] = _non_negative_number(get('单价（元）'), default=None)
            except InventoryV3ImportError as exc:
                issues.append(_stock_issue(record, 'error', 'invalid_number', str(exc)))
                continue
            records.append(record)
        return records, issues
    finally:
        workbook.close()


def parse_inventory_v3_workbook(content: bytes):
    if not is_inventory_v3_workbook(content):
        raise InventoryV3ImportError('不是有效的 V3 工作簿，必须同时包含“单件资产”和“耗材配件”工作表。')
    asset_records = parse_asset_v2_workbook(content, sheet_name=ASSET_SHEET_NAME)
    stock_records, issues = _parse_stock_sheet(content)
    summary = {
        'template_version': TEMPLATE_VERSION,
        'valid_row_count': len(asset_records) + len(stock_records),
        'asset_count': len(asset_records),
        'stock_item_count': len(stock_records),
    }
    return {'assets': asset_records, 'stocks': stock_records}, issues, summary


def _resolve_supplier(record, issues):
    code = record['supplier_code']
    name = record['supplier_name']
    by_code = Supplier.objects.filter(code__iexact=code).first() if code else None
    by_name = Supplier.objects.filter(name=name).first() if name else None
    if by_code and by_name and by_code.pk != by_name.pk:
        issues.append(_stock_issue(record, 'error', 'supplier_mismatch', '供应商代码与名称指向不同资料。'))
        return None
    supplier = by_code or by_name
    if (code or name) and not supplier:
        issues.append(_stock_issue(record, 'error', 'supplier_unknown', '系统中不存在对应供应商，请先维护基础资料。'))
    return supplier


def validate_inventory_v3_records(records):
    issues = validate_asset_v2_records(records['assets'])
    seen_keys = set()
    for record in records['stocks']:
        if record['operation'] not in {'新增', '更新'}:
            issues.append(_stock_issue(record, 'error', 'operation_invalid', '导入动作必须填写“新增”或“更新”。'))
        if not record['name']:
            issues.append(_stock_issue(record, 'error', 'name_required', '物品名称不能为空。'))
        if not record['unit']:
            issues.append(_stock_issue(record, 'error', 'unit_required', '单位不能为空。'))
        item_type = ItemType.objects.filter(code__iexact=record['item_type_code']).first()
        if not item_type:
            issues.append(_stock_issue(record, 'error', 'item_type_missing', '物品类型代码不存在。'))
        elif item_type.is_serialized:
            issues.append(_stock_issue(record, 'error', 'item_type_serialized', '该物品类型按单件资产管理，不能导入耗材配件表。'))
        warehouse = Warehouse.objects.filter(code__iexact=record['warehouse_code'], is_active=True).first()
        if not warehouse:
            issues.append(_stock_issue(record, 'error', 'warehouse_missing', '仓库代码不存在或未启用。'))
        location = None
        if warehouse and record['location_code']:
            location = Location.objects.filter(warehouse=warehouse, code__iexact=record['location_code']).first()
            if not location:
                issues.append(_stock_issue(record, 'error', 'location_missing', '库位代码不属于所选仓库。'))
        _resolve_supplier(record, issues)

        stock_item = StockItem.objects.filter(code__iexact=record['stock_code']).first() if record['stock_code'] else None
        if record['operation'] == '更新':
            if not record['stock_code']:
                issues.append(_stock_issue(record, 'error', 'update_code_required', '更新耗材配件必须填写耗材配件编码。'))
            elif not stock_item:
                issues.append(_stock_issue(record, 'error', 'update_target_missing', '未找到要更新的耗材配件。'))
        elif stock_item:
            issues.append(_stock_issue(record, 'error', 'stock_code_duplicate', '耗材配件编码已存在，已有记录请使用“更新”。'))
        elif item_type and warehouse:
            same_place = StockItem.objects.filter(item_type=item_type, warehouse=warehouse, location=location)
            if same_place.exists():
                issues.append(_stock_issue(record, 'error', 'stock_location_duplicate', '相同物品类型、仓库和库位已有记录，请填写其编码并使用“更新”。'))

        key = record['stock_code'] or f'{record["item_type_code"]}:{record["warehouse_code"]}:{record["location_code"]}'
        if key in seen_keys:
            issues.append(_stock_issue(record, 'error', 'duplicate_row_key', '文件中存在重复的耗材配件定位信息。'))
        seen_keys.add(key)
    return issues


def _apply_v3_stock_records(*, actor, batch, records):
    imported_ids = set()
    represented_warehouses = set()
    created_count = updated_count = zeroed_count = 0
    for record in records:
        warehouse = Warehouse.objects.select_for_update().get(code__iexact=record['warehouse_code'])
        location = Location.objects.filter(warehouse=warehouse, code__iexact=record['location_code']).first() if record['location_code'] else None
        item_type = ItemType.objects.get(code__iexact=record['item_type_code'])
        supplier = _resolve_supplier(record, [])
        stock_item = StockItem.objects.select_for_update().filter(code__iexact=record['stock_code']).first() if record['stock_code'] else None
        is_create = stock_item is None
        if is_create:
            stock_item = StockItem(code=record['stock_code'] or build_code('STOCK')[:50])
        before = {} if is_create else snapshot_instance(stock_item)
        stock_item.name = record['name']
        stock_item.item_type = item_type
        stock_item.warehouse = warehouse
        stock_item.location = location
        stock_item.quantity = record['quantity']
        stock_item.unit = record['unit']
        stock_item.safety_stock = record['safety_stock']
        stock_item.supplier_ref = supplier
        stock_item.supplier = supplier.name if supplier else ''
        stock_item.unit_cost = record['unit_cost']
        stock_item.source_import_batch = batch
        stock_item.save()
        record_import_mutation(
            batch=batch,
            instance=stock_item,
            operation=(
                ImportMutation.Operation.CREATE
                if is_create else ImportMutation.Operation.UPDATE
            ),
            before_snapshot=before,
        )
        imported_ids.add(stock_item.id)
        represented_warehouses.add(warehouse.id)
        created_count += int(is_create)
        updated_count += int(not is_create)
        record_operation(
            actor,
            'inventory_v3_stock_created' if is_create else 'inventory_v3_stock_updated',
            stock_item,
            summary=f'V3 {batch.get_mode_display()}：{stock_item.code}',
            before=before,
            after={
                'name': stock_item.name,
                'quantity': stock_item.quantity,
                'warehouse_id': str(stock_item.warehouse_id),
                'location_id': str(stock_item.location_id) if stock_item.location_id else None,
            },
        )

    if batch.mode == ImportBatch.Mode.BASELINE and represented_warehouses:
        missing = StockItem.objects.select_for_update().filter(
            warehouse_id__in=represented_warehouses, quantity__gt=0,
        ).exclude(id__in=imported_ids)
        for stock_item in missing:
            before_snapshot = snapshot_instance(stock_item)
            before_quantity = stock_item.quantity
            stock_item.quantity = 0
            stock_item.save(update_fields=['quantity', 'updated_at'])
            record_import_mutation(
                batch=batch, instance=stock_item,
                operation=ImportMutation.Operation.BASELINE,
                before_snapshot=before_snapshot,
            )
            record_operation(
                actor, 'inventory_v3_baseline_stock_zeroed', stock_item,
                summary=f'{stock_item.code} 未出现在 V3 全量基准中',
                before={'quantity': before_quantity}, after={'quantity': 0, 'import_batch_id': str(batch.id)},
            )
            zeroed_count += 1
    batch.imported_stock_item_count = len(imported_ids)
    batch.summary = {
        **batch.summary,
        'stock_created_count': created_count,
        'stock_updated_count': updated_count,
        'baseline_stock_zeroed_count': zeroed_count,
    }
    batch.save(update_fields=['imported_stock_item_count', 'summary', 'updated_at'])
    return created_count, updated_count


@transaction.atomic
def apply_inventory_v3_import(*, actor, batch, records, warning_reason=''):
    if batch.status != ImportBatch.Status.PREVIEWED:
        raise InventoryV3ImportError('该导入批次已处理，不能重复执行。')
    if batch.issues.filter(severity=ImportIssue.Severity.ERROR).exists():
        raise InventoryV3ImportError('预检存在阻断问题，未写入正式库存。')
    if batch.issues.filter(severity=ImportIssue.Severity.WARNING).exists() and not warning_reason.strip():
        raise InventoryV3ImportError('预检存在警告，正式导入前必须填写警告确认原因。')

    asset_existing = []
    existing_asset_ids = set()
    represented_warehouse_ids = set()
    for record in records['assets']:
        existing = None
        if record['operation'] == '更新':
            if record['system_asset_no']:
                existing = Asset.objects.select_for_update().filter(
                    system_asset_no__iexact=record['system_asset_no'],
                ).first()
            elif record['asset_code']:
                existing = Asset.objects.select_for_update().filter(
                    asset_code__iexact=record['asset_code'],
                ).first()
        asset_existing.append((existing, snapshot_instance(existing) if existing else {}))
        if existing:
            existing_asset_ids.add(existing.id)
        warehouse = Warehouse.objects.filter(code__iexact=record['warehouse_code']).first()
        if warehouse:
            represented_warehouse_ids.add(warehouse.id)
    baseline_asset_before = {
        asset.id: snapshot_instance(asset)
        for asset in Asset.objects.select_for_update().filter(
            warehouse_id__in=represented_warehouse_ids,
        ).exclude(status=Asset.Status.DISABLED)
    }

    try:
        apply_asset_v2_import(
            actor=actor, batch=batch, records=records['assets'],
            warning_reason=warning_reason, finalize_batch=False,
        )
    except AssetV2ImportError as exc:
        raise InventoryV3ImportError(str(exc)) from exc
    new_assets = iter(
        Asset.objects.select_for_update().filter(source_import_batch=batch)
        .exclude(id__in=existing_asset_ids).order_by('created_at', 'system_asset_no')
    )
    imported_asset_ids = set()
    for (existing, before_snapshot), record in zip(asset_existing, records['assets']):
        if existing:
            existing.refresh_from_db()
        asset = existing or next(new_assets, None)
        if asset is None:
            raise InventoryV3ImportError(f'第 {record["row_number"]} 行导入后无法定位资产，事务已回滚。')
        imported_asset_ids.add(asset.id)
        record_import_mutation(
            batch=batch,
            instance=asset,
            operation=(
                ImportMutation.Operation.UPDATE
                if existing else ImportMutation.Operation.CREATE
            ),
            before_snapshot=before_snapshot,
        )
    for asset_id, before_snapshot in baseline_asset_before.items():
        if asset_id in imported_asset_ids:
            continue
        asset = Asset.objects.select_for_update().filter(pk=asset_id).first()
        if asset and snapshot_instance(asset) != before_snapshot:
            record_import_mutation(
                batch=batch, instance=asset,
                operation=ImportMutation.Operation.BASELINE,
                before_snapshot=before_snapshot,
            )
    asset_created = batch.summary.get('created_count', 0)
    asset_updated = batch.summary.get('updated_count', 0)
    stock_created, stock_updated = _apply_v3_stock_records(
        actor=actor, batch=batch, records=records['stocks'],
    )
    batch.status = ImportBatch.Status.IMPORTED
    batch.summary = {
        **batch.summary,
        'template_version': TEMPLATE_VERSION,
        'asset_created_count': asset_created,
        'asset_updated_count': asset_updated,
        'created_count': asset_created + stock_created,
        'updated_count': asset_updated + stock_updated,
        'warning_confirmation': warning_reason.strip(),
    }
    batch.save(update_fields=['status', 'summary', 'updated_at'])
    record_operation(
        actor, 'inventory_v3_import_applied', batch,
        summary=f'V3 {batch.get_mode_display()}完成：资产 {batch.imported_asset_count} 件，耗材配件 {batch.imported_stock_item_count} 条',
        after=batch.summary,
    )
    return batch


def export_inventory_v3_workbook(asset_queryset=None, stock_queryset=None, *, include_financials=True) -> BytesIO:
    workbook = load_workbook(build_inventory_v3_template())
    asset_sheet = workbook[ASSET_SHEET_NAME]
    stock_sheet = workbook[STOCK_SHEET_NAME]
    if asset_sheet.max_row > 1:
        asset_sheet.delete_rows(2, asset_sheet.max_row - 1)
    if stock_sheet.max_row > 1:
        stock_sheet.delete_rows(2, stock_sheet.max_row - 1)

    assets = (asset_queryset or Asset.objects.all()).select_related(
        'domain', 'asset_type_code', 'product_model_code', 'item_type',
        'manufacturer_ref', 'supplier_ref', 'warehouse', 'location', 'project', 'work_order',
    ).order_by('system_asset_no')
    for asset in assets:
        values = {
            '外部行号': '', '导入动作': '更新', '系统永久编号': asset.system_asset_no,
            '资产归属': asset.domain.code if asset.domain_id else '', '编码方式': asset.code_source,
            '主资产编码': asset.asset_code or '', '设备类型代码': asset.asset_type_code.code if asset.asset_type_code_id else '',
            '型号代码': asset.product_model_code.code if asset.product_model_code_id else '', '资产名称': asset.name,
            '物品类型代码': asset.item_type.code if asset.item_type_id else '',
            '生产厂家代码': asset.manufacturer_ref.code if asset.manufacturer_ref_id else '',
            '生产厂家名称': asset.manufacturer, '型号名称': asset.model, 'SN状态': asset.sn_status,
            '生产厂家SN': asset.serial_number, '生产厂家条码': asset.manufacturer_barcode,
            '供应商代码': asset.supplier_ref.code if asset.supplier_ref_id else '', '供应商名称': asset.supplier,
            '仓库代码': asset.warehouse.code if asset.warehouse_id else '', '库位代码': asset.location.code if asset.location_id else '',
            '项目代码': asset.project.code if asset.project_id else '', '工单编号': asset.work_order.code if asset.work_order_id else '',
            '资产状态': asset.get_status_display(), '到货日期': asset.received_date,
            '采购订单号': asset.purchase_order_no, '采购金额': asset.purchase_amount if include_financials else '',
            '质保到期日': asset.warranty_until, '别名标签': asset.search_aliases,
            '当前持有人': asset.external_holder_name, '持有人单位': asset.external_holder_company,
            '联系电话': asset.external_holder_phone, '备注': asset.remarks, '父资产永久编号': '',
        }
        asset_sheet.append([values.get(header.rstrip('*'), '') for header in ASSET_HEADERS])

    stocks = (stock_queryset or StockItem.objects.all()).select_related(
        'item_type', 'supplier_ref', 'warehouse', 'location',
    ).order_by('code')
    for stock in stocks:
        values = {
            '外部行号': '', '导入动作': '更新', '耗材配件编码': stock.code,
            '物品名称': stock.name, '物品类型代码': stock.item_type.code if stock.item_type_id else '',
            '仓库代码': stock.warehouse.code if stock.warehouse_id else '',
            '库位代码': stock.location.code if stock.location_id else '',
            '库存数量': stock.quantity, '单位': stock.unit, '安全库存': stock.safety_stock,
            '供应商代码': stock.supplier_ref.code if stock.supplier_ref_id else '',
            '供应商名称': stock.supplier_ref.name if stock.supplier_ref_id else stock.supplier,
            '单价（元）': stock.unit_cost,
        }
        stock_sheet.append([values.get(header.rstrip('*'), '') for header in STOCK_HEADERS])

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
