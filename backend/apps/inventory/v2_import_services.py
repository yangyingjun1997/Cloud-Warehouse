from __future__ import annotations

import logging
import re

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import BadZipFile

from django.db import transaction
from django.db.models import Q
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException

from apps.common.audit import record_operation

logger = logging.getLogger(__name__)

from .code_services import (
    SYSTEM_CODE_PATTERN, AssetCodeError, apply_asset_identity, normalize_identifier,
    validate_sn,
    admin_assigned_code_error, is_valid_admin_assigned_code,
    is_valid_warehouse_assigned_code, warehouse_assigned_code_error,
)
from .models import (
    Asset, AssetAssemblyLink, AssetIdentifier, AssetTypeCode, ImportBatch, InventoryDomain,
    ItemType, Location, MaintenanceRecord, Manufacturer, ProductModelCode, Project, Supplier,
    Warehouse, WorkOrder,
)


SHEET_NAME = '资产导入V2'
HEADERS = [
    '外部行号', '导入动作*', '系统永久编号', '资产归属*', '编码方式*', '主资产编码',
    '设备类型代码', '型号代码', '资产名称*', '物品类型代码*',
    '生产厂家代码', '生产厂家名称', '型号名称', 'SN状态*', '生产厂家SN',
    '生产厂家条码', '供应商代码', '供应商名称', '仓库代码*', '库位代码',
    '项目代码', '工单编号', '资产状态', '到货日期', '采购订单号', '采购金额', '质保到期日',
    '别名标签', '当前持有人', '持有人单位', '联系电话', '备注', '父资产永久编号',
]

# 父资产列用于“同一套设备的多个部件”：填主件的系统永久编号（AST-xxxxxxxx），
# 导入后自动建立装配关系。留空表示独立资产。
OPTIONAL_HEADERS = {
    '外部行号', '资产状态', '导入动作', '父资产永久编号',
    '当前持有人', '持有人单位', '联系电话',
}

# 导入允许显式指定的资产状态。刻意不包含“已停用”：停用只能通过全量基准
# 或人工入口触发，避免一次误填把在役资产批量停用。
IMPORTABLE_ASSET_STATUSES = [
    Asset.Status.IN_STOCK,
    Asset.Status.BORROWED,
    Asset.Status.ISSUED,
    Asset.Status.PENDING_OUT,
    Asset.Status.PENDING_RETURN,
    Asset.Status.PENDING_INSPECTION,
    Asset.Status.MAINTENANCE,
    Asset.Status.REPAIRING,
    Asset.Status.SOLD,
    Asset.Status.INTERNAL_BORROW,
    Asset.Status.SHIPPED_OUT,
    Asset.Status.ASSEMBLED,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
    Asset.Status.DAMAGED,
    Asset.Status.LOST,
    Asset.Status.CONVERTED_TO_STOCK,
]

_STATUS_ALIASES = {label: value for value, label in Asset.Status.choices}
_STATUS_ALIASES.update({value: value for value, label in Asset.Status.choices})
_DEFAULT_IMPORT_STATUS = Asset.Status.IN_STOCK


def _baseline_asset_blockers(asset):
    """Return active relationships that make baseline omission unsafe."""
    blockers = []
    if asset.status not in {Asset.Status.IN_STOCK, Asset.Status.DISABLED}:
        blockers.append(f'当前状态为{asset.get_status_display()}')
    if asset.reservations.filter(status='active').exists():
        blockers.append('存在库存预占')
    if MaintenanceRecord.objects.filter(asset=asset).exclude(
        status__in=[MaintenanceRecord.Status.COMPLETED, MaintenanceRecord.Status.CANCELED],
    ).exists():
        blockers.append('存在未完成维修记录')
    return blockers


def validate_asset_baseline_safety(*, warehouse_ids, imported_ids):
    """Raise before any write when a baseline would disable an active asset."""
    missing = Asset.objects.select_for_update().filter(
        warehouse_id__in=warehouse_ids,
    ).exclude(id__in=imported_ids).exclude(status=Asset.Status.DISABLED)
    blocked = []
    for asset in missing:
        blockers = _baseline_asset_blockers(asset)
        if blockers:
            blocked.append(f'{asset.system_asset_no or asset.asset_code or asset.name}（{"、".join(blockers)}）')
    if blocked:
        preview = '；'.join(blocked[:5])
        suffix = f'；另有 {len(blocked) - 5} 件' if len(blocked) > 5 else ''
        raise AssetV2ImportError(
            f'全量基准导入将停用仍有业务关系的资产，已阻断写入：{preview}{suffix}。'
            '请补入源表或先完成对应业务闭环。'
        )


def parse_asset_status(raw: str) -> str:
    """把中文标签或英文枚举值解析为 Asset.Status 值，未知返回空字符串。"""
    key = _text(raw)
    if not key:
        return ''
    return _STATUS_ALIASES.get(key, '')


# 新增列保持向后兼容：已发出的旧版 V2 模板没有外部行号、资产状态，且导入动作
# 当时叫“操作”，解析时按别名兼容，缺列仍可导入。父资产列同理，缺列不影响导入。


def _asset_status_value(raw: str, row_number) -> str:
    """解析并校验资产状态；空值按“在库”处理。"""
    key = _text(raw)
    if not key:
        return _DEFAULT_IMPORT_STATUS
    status = parse_asset_status(key)
    if not status:
        allowed = '、'.join(label for value, label in Asset.Status.choices if value in IMPORTABLE_ASSET_STATUSES)
        raise AssetV2ImportError(f'第 {row_number} 行“资产状态”无法识别“{key}”，可填：{allowed}。')
    if status == Asset.Status.DISABLED:
        raise AssetV2ImportError(f'第 {row_number} 行“资产状态”不允许填“已停用”，停用请走全量基准或人工入口。')
    if status not in IMPORTABLE_ASSET_STATUSES:
        raise AssetV2ImportError(f'第 {row_number} 行“资产状态”不支持导入。')
    return status


class AssetV2ImportError(ValueError):
    pass


def is_asset_v2_workbook(content: bytes) -> bool:
    """嗅探上传文件是否为 V2 资产模板。

    只吞「不是合法 xlsx」这一类异常：坏 zip、非 xlsx 格式、openpyxl 无法识别。
    早期这里捕获所有 Exception 并返回 False，会把真正的程序缺陷（如读取逻辑写错）
    伪装成「模板不匹配」，排查时看不到任何线索。
    """
    try:
        workbook = load_workbook(BytesIO(content), read_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        logger.debug('V2 模板嗅探失败（按非目标模板处理）: %s', exc)
        return False
    return SHEET_NAME in workbook.sheetnames


def _text(value) -> str:
    return '' if value is None else str(value).strip()


def _date_value(value, label, row_number):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value))
    except ValueError as exc:
        raise AssetV2ImportError(f'第 {row_number} 行“{label}”必须是 YYYY-MM-DD 日期。') from exc


def _decimal_value(value, row_number):
    if value in (None, ''):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise AssetV2ImportError(f'第 {row_number} 行“采购金额”不是有效数字。') from exc
    if amount < 0:
        raise AssetV2ImportError(f'第 {row_number} 行“采购金额”不能小于 0。')
    return amount


def build_asset_v2_template() -> BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_NAME
    sheet.append(HEADERS)
    def sample_row(**values):
        return [values.get(header.rstrip('*'), '') for header in HEADERS]

    # 示例只展示填写方式，不代表正式基础资料；导入前必须替换或删除。
    sample_rows = [
        sample_row(
            外部行号='A-001', 导入动作='新增', 资产归属='MARKET',
            编码方式=Asset.CodeSource.WAREHOUSE_ASSIGNED,
            主资产编码='SC-G1-YS-0031', 资产名称='示例：既有 SC 资产',
            物品类型代码='ROBOT', SN状态=Asset.SnStatus.PROVIDED,
            生产厂家SN='SN-DEMO-SC-001', 仓库代码='MARKET', 资产状态='在库',
            备注='既有 SC 原样保留；正式导入前请替换或删除本示例行',
        ),
        sample_row(
            外部行号='A-002', 导入动作='新增', 资产归属='RND',
            编码方式=Asset.CodeSource.WAREHOUSE_ASSIGNED,
            主资产编码='DZSB-D7-31-0002-600001', 资产名称='示例：既有 DZSB 资产',
            物品类型代码='ROBOT', SN状态=Asset.SnStatus.NO_SN,
            仓库代码='RND', 资产状态='在库',
            备注='既有 DZSB 原样保留；正式导入前请替换或删除本示例行',
        ),
        sample_row(
            外部行号='A-003', 导入动作='新增', 资产归属='MARKET',
            编码方式=Asset.CodeSource.MARKET_GENERATED,
            设备类型代码='JQR', 型号代码='G1EDU',
            资产名称='示例：按源表前缀续号', 物品类型代码='ROBOT',
            SN状态=Asset.SnStatus.PENDING, 仓库代码='MARKET', 资产状态='在库',
            备注='主资产编码留空，系统按该型号维护的 SC-G1-YS 前缀续号',
        ),
        sample_row(
            外部行号='A-004', 导入动作='新增', 资产归属='RND',
            编码方式=Asset.CodeSource.PENDING_ADMIN,
            资产名称='示例：已确认资产但暂未赋码', 物品类型代码='ROBOT',
            SN状态=Asset.SnStatus.NO_SN, 仓库代码='RND', 资产状态='在库',
            备注='仅确认属于单件资产时使用；低值无编码物品应填耗材模板',
        ),
        sample_row(
            外部行号='A-005', 导入动作='更新', 系统永久编号='AST-00000001',
            资产归属='MARKET', 编码方式=Asset.CodeSource.WAREHOUSE_ASSIGNED,
            主资产编码='SC-G1-YS-0001', 资产名称='示例：更新已有资产',
            物品类型代码='ROBOT', SN状态=Asset.SnStatus.PROVIDED,
            生产厂家SN='SN-DEMO-UPDATE-001', 仓库代码='MARKET', 资产状态='维修中',
            备注='更新优先使用 AST 定位；正式导入前请替换或删除本示例行',
        ),
    ]
    for row in sample_rows:
        sheet.append(row)
    sheet.freeze_panes = 'B2'
    last_column = sheet.cell(1, len(HEADERS)).column_letter
    sheet.auto_filter.ref = f'A1:{last_column}{sheet.max_row}'
    sheet.row_dimensions[1].height = 28
    for cell in sheet[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F4E78')
    for index, header in enumerate(HEADERS, 1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = min(max(len(header) * 2 + 4, 14), 28)

    reference_sheets = [
        ('编码方式', ['值', '中文含义', '使用场景'], Asset.CodeSource.choices, lambda row: [
            row[0], row[1], {
                Asset.CodeSource.WAREHOUSE_ASSIGNED: '源表已有 SC 或 DZSB，原样保留',
                Asset.CodeSource.MARKET_GENERATED: '没有正式编码且确认是资产，按已维护的源表 SC 前缀续号',
                Asset.CodeSource.ADMIN_ASSIGNED: '人工新增行政/ERP DZSB 编码',
                Asset.CodeSource.PENDING_ADMIN: '已确认是资产，但暂时没有主资产编码',
            }[row[0]],
        ]),
        ('资产归属', ['代码', '名称'], InventoryDomain.objects.filter(is_active=True).order_by('code'), lambda row: [row.code, row.name]),
        ('资产状态', ['值', '中文名称'], [(value, label) for value, label in Asset.Status.choices if value in IMPORTABLE_ASSET_STATUSES], lambda row: [row[0], row[1]]),
        ('SN状态', ['值', '中文名称'], Asset.SnStatus.choices, lambda row: [row[0], row[1]]),
        ('设备类型', ['代码', '名称', '状态'], AssetTypeCode.objects.order_by('code'), lambda row: [row.code, row.name, '启用' if row.is_active else '停用']),
        ('产品型号', ['设备类型代码', '型号代码', '型号名称', '生产厂家'], ProductModelCode.objects.select_related('asset_type_code', 'manufacturer').order_by('asset_type_code__code', 'code'), lambda row: [row.asset_type_code.code, row.code, row.name, row.manufacturer.name if row.manufacturer_id else '']),
        ('生产厂家', ['代码', '名称', '状态'], Manufacturer.objects.order_by('code'), lambda row: [row.code, row.name, '启用' if row.is_active else '停用']),
        ('供应商', ['代码', '名称', '状态'], Supplier.objects.order_by('code'), lambda row: [row.code, row.name, '启用' if row.is_active else '停用']),
        ('仓库库位', ['仓库代码', '仓库名称', '库位代码', '库位名称'], Location.objects.select_related('warehouse').order_by('warehouse__code', 'code'), lambda row: [row.warehouse.code, row.warehouse.name, row.code, row.name]),
        ('物品类型', ['代码', '名称', '管理方式'], ItemType.objects.order_by('code'), lambda row: [row.code, row.name, '单件资产' if row.is_serialized else '耗材配件']),
        ('项目工单', ['项目代码', '项目名称', '工单编号', '工单名称'], WorkOrder.objects.select_related('project').order_by('project__code', 'code'), lambda row: [row.project.code if row.project_id else '', row.project.name if row.project_id else '', row.code, row.name]),
    ]
    for title, headers, queryset, mapper in reference_sheets:
        ref = workbook.create_sheet(title)
        ref.append(headers)
        for item in queryset:
            ref.append(mapper(item))
        for cell in ref[1]:
            cell.font = Font(bold=True)
        ref.freeze_panes = 'A2'

    instructions = workbook.create_sheet('填写说明')
    instructions.append(['规则', '说明'])
    instruction_rows = [
        ('模板版本', '资产身份编码 V2。操作必须填写“新增”或“更新”。'),
        ('外部行号', '选填，建议填写便于与自有台账核对；错误定位同时给出 Excel 实际行号。'),
        ('系统永久编号', '新增时必须留空，由系统生成 AST-00000001 格式；更新时优先用此编号定位。'),
        ('既有正式编码', '源表已有 SC 或 DZSB 时，编码方式填 warehouse_assigned，主资产编码原样填写；市场库和研发库均适用，系统不会改写。'),
        ('系统续 SC', '仅在确认属于单件资产但没有正式编码时使用 market_generated；必须选择已维护源表 SC 前缀的设备类型和型号，主资产编码留空。'),
        ('暂未赋码', '仅对已确认的单件资产使用 pending_admin；低值、按数量管理或无须单件追踪的物品应使用耗材配件模板。'),
        ('资产归属', 'MARKET / RND 表示业务归属，不决定 SC 或 DZSB 是否有效，也不会触发主资产编码改写。'),
        ('SN', '可以为空和重复，但必须填写 SN 状态；SN 或条码不能单独作为更新依据。'),
        ('生产厂家与供应商', '两者是独立资料，必须使用系统中已有代码/名称，导入不会静默建档。'),
        ('资产状态', '留空按“在库”导入。支持在库、已借出、已领用、待出库、待归还验收、待质检、待维修、维修中、已售出、内部借用库、已出库、已组装、已退供应商、已报废、已报损、已丢失、已转为耗材配件；填写中文标签或英文枚举均可，不接受“已停用”。导入时按该状态自动同步实物位置/可用性/质量/处置四维。'),
        ('项目与工单', '均为选填；同时填写时工单必须属于所选项目。'),
        ('持有人信息', '资产状态为已借出或已领用时建议填写当前持有人；外部人员可直接填写姓名，不要求创建系统账号。'),
        ('警告确认', '存在重复 SN 等警告时，正式导入必须填写确认原因。'),
    ]
    for row in instruction_rows:
        instructions.append(row)
    instructions.column_dimensions['A'].width = 20
    instructions.column_dimensions['B'].width = 110
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def export_asset_v2_workbook(queryset=None) -> BytesIO:
    """导出可直接修改后再以“更新”方式导入的 V2 资产工作簿。"""
    template = build_asset_v2_template()
    workbook = load_workbook(template)
    sheet = workbook[SHEET_NAME]
    sheet.delete_rows(2, sheet.max_row - 1)
    assets = (queryset or Asset.objects.all()).select_related(
        'domain', 'asset_type_code', 'product_model_code', 'item_type',
        'manufacturer_ref', 'supplier_ref', 'warehouse', 'location', 'project', 'work_order',
    ).order_by('system_asset_no')
    for asset in assets:
        sheet.append([
            '', '更新', asset.system_asset_no, asset.domain.code if asset.domain_id else '', asset.code_source,
            asset.asset_code or '', asset.asset_type_code.code if asset.asset_type_code_id else '',
            asset.product_model_code.code if asset.product_model_code_id else '', asset.name, asset.item_type.code,
            asset.manufacturer_ref.code if asset.manufacturer_ref_id else '', asset.manufacturer,
            asset.model, asset.sn_status, asset.serial_number, asset.manufacturer_barcode,
            asset.supplier_ref.code if asset.supplier_ref_id else '', asset.supplier,
            asset.warehouse.code if asset.warehouse_id else '', asset.location.code if asset.location_id else '',
            asset.project.code if asset.project_id else '', asset.work_order.code if asset.work_order_id else '',
            asset.get_status_display(), asset.received_date, asset.purchase_order_no, asset.purchase_amount,
            asset.warranty_until, asset.search_aliases,
            asset.external_holder_name, asset.external_holder_company, asset.external_holder_phone,
            asset.remarks, '',
        ])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


_IP_PATTERN = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')

# 研发台账里用“研发001”这类内部编号占位，一套设备的 2~3 个部件共用同一个号。
# 它不是厂家 SN（同一台机器上的云台和全景相机不可能有相同出厂号），
# 留在 SN 列会让“按 SN 查资产”一次命中好几件，因此同样转为无 SN。
_INTERNAL_SN_PATTERN = re.compile(r'^研发\s*0*\d+$')


def _split_ip_serial(serial: str, remarks: str, row_label: str = '') -> tuple[str, str, str]:
    """把误填进 SN 列的 IP 地址或内部研发编号挪到备注。

    台账里主机类资产的序列号经常被填成内网 IP（如 192.168.10.86）；
    研发台账则用“研发001”这类内部编号给同一套设备的多个部件占位。
    两者都不是厂家 SN：IP 会被多台机器共用，研发编号会被同一台设备的
    多个部件共用，留在 SN 列会让“按 SN 查资产”命中多件。这里统一改为：
    SN 清空、状态置为无 SN、原值附加到备注末尾，并返回一条提示。

    备注里带上外部行号，因为同一个值可能落在多台机器上，没行号就回不到台账。

    返回 (serial, remarks, note)，note 为空表示未发生改写。
    """
    value = (serial or '').strip()
    is_ip = bool(_IP_PATTERN.match(value))
    is_internal = bool(_INTERNAL_SN_PATTERN.match(value))
    if not is_ip and not is_internal:
        return serial, remarks, ''
    where = f'（{row_label}）' if row_label else ''
    kind = '内网 IP' if is_ip else '内部研发编号'
    note = f'原 SN 列为{kind} {value}{where}，已改为无 SN 并附注于此'
    merged = f'{remarks}；{note}' if remarks else note
    return '', merged, note


def parse_asset_v2_workbook(content: bytes, *, sheet_name: str = SHEET_NAME) -> list[dict]:
    try:
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:
        raise AssetV2ImportError('Excel 文件无法读取，请确认文件没有损坏且格式为 .xlsx。') from exc
    if sheet_name not in workbook.sheetnames:
        raise AssetV2ImportError(f'未找到“{sheet_name}”工作表，请使用正确模板。')
    sheet = workbook[sheet_name]
    rows = sheet.iter_rows(values_only=True)
    try:
        source_headers = [_text(value).rstrip('*') for value in next(rows)]
    except StopIteration as exc:
        raise AssetV2ImportError('资产导入工作表为空。') from exc
    expected = [header.rstrip('*') for header in HEADERS]
    required = [header for header in expected if header not in OPTIONAL_HEADERS]
    missing = [header for header in required if header not in source_headers]
    if missing:
        raise AssetV2ImportError(f'模板缺少列：{"、".join(missing)}。')
    index = {header: source_headers.index(header) for header in expected if header in source_headers}
    # 兼容已发出的旧版模板：该列原先叫“操作”。
    if '导入动作' not in index and '操作' in source_headers:
        index['导入动作'] = source_headers.index('操作')
    records = []
    for row_number, values in enumerate(rows, 2):
        if not any(value not in (None, '') for value in values):
            continue

        def get(key, _values=values):
            position = index.get(key)
            if position is None or position >= len(_values):
                return None
            return _values[position]

        sn_value = _text(get('生产厂家SN'))
        remarks_value = _text(get('备注'))
        sn_value, remarks_value, ip_note = _split_ip_serial(
            sn_value, remarks_value, _text(get('外部行号')),
        )
        sn_status_value = _text(get('SN状态'))
        if ip_note:
            sn_status_value = Asset.SnStatus.NO_SN

        records.append({
            'sheet_name': sheet_name, 'row_number': row_number,
            'external_row_no': _text(get('外部行号')),
            'operation': _text(get('导入动作')), 'system_asset_no': normalize_identifier(_text(get('系统永久编号'))),
            'domain_code': normalize_identifier(_text(get('资产归属'))), 'code_source': _text(get('编码方式')),
            'asset_code': normalize_identifier(_text(get('主资产编码'))),
            'asset_type_code': normalize_identifier(_text(get('设备类型代码'))), 'product_model_code': normalize_identifier(_text(get('型号代码'))),
            'name': _text(get('资产名称')), 'item_type_code': normalize_identifier(_text(get('物品类型代码'))),
            'manufacturer_code': normalize_identifier(_text(get('生产厂家代码'))), 'manufacturer_name': _text(get('生产厂家名称')),
            'model': _text(get('型号名称')), 'sn_status': sn_status_value, 'serial_number': sn_value,
            'manufacturer_barcode': _text(get('生产厂家条码')), 'supplier_code': normalize_identifier(_text(get('供应商代码'))),
            'supplier_name': _text(get('供应商名称')), 'warehouse_code': normalize_identifier(_text(get('仓库代码'))),
            'location_code': normalize_identifier(_text(get('库位代码'))), 'project_code': normalize_identifier(_text(get('项目代码'))),
            'work_order_code': normalize_identifier(_text(get('工单编号'))),
            'asset_status': _asset_status_value(get('资产状态'), row_number) if '资产状态' in index else '',
            'asset_status_supplied': '资产状态' in index,
            'received_date': _date_value(get('到货日期'), '到货日期', row_number),
            'purchase_order_no': _text(get('采购订单号')), 'purchase_amount': _decimal_value(get('采购金额'), row_number),
            'warranty_until': _date_value(get('质保到期日'), '质保到期日', row_number),
            'search_aliases': _text(get('别名标签')),
            'external_holder_name': _text(get('当前持有人')),
            'external_holder_company': _text(get('持有人单位')),
            'external_holder_phone': _text(get('联系电话')),
            'remarks': remarks_value,
            'parent_asset_no': normalize_identifier(_text(get('父资产永久编号'))),
            'source': {header: _text(get(header)) for header in expected},
        })
    return records


def _issue(record, severity, code, message):
    external = record.get('external_row_no') or ''
    if external:
        message = f'[外部行号 {external}] {message}'
    return {
        'sheet_name': record.get('sheet_name') or SHEET_NAME, 'row_number': record['row_number'], 'severity': severity,
        'code': code, 'message': message[:255], 'source_data': record.get('source') or {},
    }


def _duplicate_sn_issues(records: list[dict]) -> list[dict]:
    """检查同一批文件内部的重复 SN。

    `validate_sn()` 只比对**已入库**的资产，所以一份 Excel 里两行写了同一个 SN
    时，它一条都发现不了，问题要等到写库后才暴露。这里补上批次内自检。

    严重度统一用 warning：`ImportIssue` 只定义了 info/warning/error，写 high
    会在别处被当成未知值；而 warning 能触发“必须填写确认原因”，正好符合
    “仓管看过后才继续”的意图。上下文差异体现在 code 和文案上。
    """
    from collections import defaultdict

    issues = []
    grouped = defaultdict(list)
    for record in records:
        # 只比较真正填了 SN 的行；无 SN 的资产本来就可以有多件。
        value = normalize_identifier(record.get('serial_number') or '')
        if not value:
            continue
        grouped[value].append(record)

    for value, rows in grouped.items():
        if len(rows) < 2:
            continue
        for record in rows:
            others = [row for row in rows if row is not record]
            same_context = [
                row for row in others
                if (row.get('manufacturer_code') or row.get('manufacturer_name')) ==
                   (record.get('manufacturer_code') or record.get('manufacturer_name'))
                and (row.get('product_model_code') or '') == (record.get('product_model_code') or '')
            ]
            peers = '、'.join(row.get('external_row_no') or f'第 {row["row_number"]} 行' for row in others[:4])
            if same_context:
                message = f'同一批次内 SN“{value}”重复出现 {len(rows)} 次（{peers}），且厂家型号相同，请核对实物标签。'
                issues.append(_issue(record, 'warning', 'duplicate_sn_in_batch_high', message))
            else:
                message = f'同一批次内 SN“{value}”重复出现 {len(rows)} 次（{peers}），请确认是否同一件设备。'
                issues.append(_issue(record, 'warning', 'duplicate_sn_in_batch', message))
    return issues


def _resolve_pair(model, code, name, label, record, issues, required=False):
    by_code = model.objects.filter(code__iexact=code).first() if code else None
    by_name = model.objects.filter(name=name).first() if name else None
    if by_code and by_name and by_code.pk != by_name.pk:
        issues.append(_issue(record, 'error', f'{label}_mismatch', f'{label}代码与名称指向不同资料。'))
        return None
    value = by_code or by_name
    if required and not value:
        issues.append(_issue(record, 'error', f'{label}_missing', f'系统中不存在对应{label}，请先维护基础资料。'))
    elif (code or name) and not value:
        issues.append(_issue(record, 'error', f'{label}_unknown', f'系统中不存在对应{label}，导入不会自动建档。'))
    return value


def validate_asset_v2_records(records: list[dict]) -> list[dict]:
    issues = []
    seen_keys = set()
    for record in records:
        if record['operation'] not in {'新增', '更新'}:
            issues.append(_issue(record, 'error', 'operation_invalid', '操作必须填写“新增”或“更新”。'))
        if record['operation'] == '新增' and record['system_asset_no']:
            issues.append(_issue(record, 'error', 'new_system_code_present', '新增资产时系统永久编号必须留空。'))
        if not record['name']:
            issues.append(_issue(record, 'error', 'name_required', '资产名称不能为空。'))
        domain = InventoryDomain.objects.filter(code=record['domain_code'], is_active=True).first()
        if not domain:
            issues.append(_issue(record, 'error', 'domain_invalid', '资产归属必须填写 MARKET 或 RND。'))
        source = record['code_source']
        if source == Asset.CodeSource.WAREHOUSE_ASSIGNED:
            if not record['asset_code']:
                issues.append(_issue(record, 'error', 'warehouse_code_required', '编码方式为源表既有编码时必须填写资产编码。'))
            elif not is_valid_warehouse_assigned_code(record['asset_code']):
                # 与页面维护共用同一套前缀规则（SC/DZSB/ZSB/QTZC），
                # 避免「页面能存、导入被拒」的不一致。
                issues.append(_issue(record, 'error', 'warehouse_code_prefix', warehouse_assigned_code_error()))
        elif source == Asset.CodeSource.PENDING_ADMIN:
            if record['asset_code']:
                issues.append(_issue(record, 'error', 'pending_code_present', '编码方式为暂未赋码时资产编码必须留空。'))
        elif source == Asset.CodeSource.ADMIN_ASSIGNED:
            if not is_valid_admin_assigned_code(record['asset_code']):
                issues.append(_issue(record, 'error', 'admin_code_required', admin_assigned_code_error()))
        elif source == Asset.CodeSource.MARKET_GENERATED:
            type_code = AssetTypeCode.objects.filter(code=record['asset_type_code'], is_active=True).first()
            model_code = ProductModelCode.objects.filter(
                asset_type_code=type_code, code=record['product_model_code'], is_active=True,
            ).first() if type_code else None
            if not type_code or not model_code:
                issues.append(_issue(record, 'error', 'market_code_missing', '生成 SC 编码所需的设备类型或型号资料不存在。'))
            elif not model_code.sc_prefix:
                issues.append(_issue(record, 'error', 'sc_prefix_missing', '所选型号尚未维护源表 SC 编码前缀。'))
            if record['operation'] == '新增' and record['asset_code']:
                issues.append(_issue(record, 'error', 'generated_code_present', '新增并生成 SC 编码时资产编码应留空，由系统续号。'))
        else:
            issues.append(_issue(record, 'error', 'code_source_invalid', '编码方式不受支持。'))

        _resolve_pair(Manufacturer, record['manufacturer_code'], record['manufacturer_name'], '生产厂家', record, issues)
        _resolve_pair(Supplier, record['supplier_code'], record['supplier_name'], '供应商', record, issues)
        item_type = ItemType.objects.filter(code=record['item_type_code'], is_serialized=True).first()
        if not item_type:
            issues.append(_issue(record, 'error', 'item_type_invalid', '物品类型不存在或不是单件资产类型。'))
        warehouse = Warehouse.objects.filter(code=record['warehouse_code'], is_active=True).first()
        if not warehouse:
            issues.append(_issue(record, 'error', 'warehouse_invalid', '仓库代码不存在或未启用。'))
        elif record['location_code'] and not Location.objects.filter(warehouse=warehouse, code=record['location_code']).exists():
            issues.append(_issue(record, 'error', 'location_invalid', '库位代码不属于所选仓库。'))
        project = Project.objects.filter(code=record['project_code'], is_active=True).first() if record['project_code'] else None
        work_order = WorkOrder.objects.filter(code=record['work_order_code'], is_active=True).first() if record['work_order_code'] else None
        if record['project_code'] and not project:
            issues.append(_issue(record, 'error', 'project_invalid', '项目代码不存在或未启用。'))
        if record['work_order_code'] and not work_order:
            issues.append(_issue(record, 'error', 'work_order_invalid', '工单编号不存在或未启用。'))
        if project and work_order and work_order.project_id and work_order.project_id != project.id:
            issues.append(_issue(record, 'error', 'project_work_order_mismatch', '工单不属于所选项目。'))

        asset = None
        if record['operation'] == '更新':
            if record['system_asset_no']:
                asset = Asset.objects.filter(system_asset_no__iexact=record['system_asset_no']).first()
            elif record['asset_code']:
                asset = Asset.objects.filter(asset_code__iexact=record['asset_code']).first()
            else:
                issues.append(_issue(record, 'error', 'update_key_required', '更新必须填写系统永久编号，或填写当前唯一主资产编码。'))
            if not asset and (record['system_asset_no'] or record['asset_code']):
                issues.append(_issue(record, 'error', 'update_target_missing', '未找到要更新的资产。'))
        elif record['asset_code'] and AssetIdentifier.objects.filter(normalized_value=record['asset_code'], identifier_type__in=['market', 'admin', 'legacy']).exists():
            issues.append(_issue(record, 'error', 'primary_code_duplicate', '主资产编码已经被使用或属于历史编码，不能复用。'))

        # 父资产列：用于表达“同一套设备的多个部件”。只校验编号存在且不是自己，
        # 具体的装配关系在提交阶段建立。
        parent_no = record.get('parent_asset_no') or ''
        if parent_no:
            if not SYSTEM_CODE_PATTERN.match(parent_no):
                issues.append(_issue(record, 'error', 'parent_asset_no_invalid', f'父资产永久编号格式不正确：{parent_no}，应形如 AST-00000001。'))
            else:
                parent = Asset.objects.filter(system_asset_no__iexact=parent_no).first()
                if not parent:
                    issues.append(_issue(record, 'error', 'parent_asset_missing', f'父资产 {parent_no} 不存在，请先导入主件或核对编号。'))
                elif record['system_asset_no'] and parent.system_asset_no.upper() == record['system_asset_no'].upper():
                    issues.append(_issue(record, 'error', 'parent_asset_self', '父资产不能指向自己。'))

        key = record['system_asset_no'] or record['asset_code'] or f'NEW:{record["row_number"]}'
        if key in seen_keys:
            issues.append(_issue(record, 'error', 'duplicate_row_key', '文件中存在重复的资产定位编号。'))
        seen_keys.add(key)
        candidate = asset or Asset()
        candidate.serial_number = record['serial_number']
        candidate.sn_status = record['sn_status']
        candidate.manufacturer_ref = _resolve_pair(Manufacturer, record['manufacturer_code'], record['manufacturer_name'], '生产厂家', record, [], required=False)
        if domain and domain.code == 'MARKET':
            candidate.product_model_code = ProductModelCode.objects.filter(asset_type_code__code=record['asset_type_code'], code=record['product_model_code']).first()
        try:
            for warning in validate_sn(candidate, exclude_asset_id=getattr(asset, 'id', None)):
                issues.append(_issue(record, 'warning', warning['code'], warning['message']))
        except Exception as exc:
            messages = getattr(exc, 'messages', [str(exc)])
            issues.append(_issue(record, 'error', 'sn_invalid', ' '.join(messages)))

    # 批次内部自检放在单行校验之后：`validate_sn` 只能看到已入库资产，
    # 同批文件里的重复 SN 必须在这里补查。
    issues.extend(_duplicate_sn_issues(records))
    return issues


@transaction.atomic
def apply_asset_v2_import(
    *, actor, batch: ImportBatch, records: list[dict], warning_reason: str = '',
    finalize_batch: bool = True,
):
    if batch.status != ImportBatch.Status.PREVIEWED:
        raise AssetV2ImportError('该导入批次已处理，不能重复执行。')
    if batch.issues.filter(severity='error').exists():
        raise AssetV2ImportError('预检存在阻断问题，未写入正式库存。')
    if batch.issues.filter(severity='warning').exists() and not warning_reason.strip():
        raise AssetV2ImportError('预检存在警告，正式导入前必须填写警告确认原因。')
    imported_ids = set()
    represented_warehouses = set()
    assembly_intents = []
    external_row_asset_map = {}
    created_count = updated_count = disabled_count = 0

    # === 预加载外键字典，避免 N+1 查询 ===
    # 收集所有需要用到的 code
    domain_codes = {r['domain_code'] for r in records if r.get('domain_code')}
    warehouse_codes = {r['warehouse_code'] for r in records if r.get('warehouse_code')}
    item_type_codes = {r['item_type_code'] for r in records if r.get('item_type_code')}
    asset_type_codes = {r['asset_type_code'] for r in records if r.get('asset_type_code')}
    project_codes = {r['project_code'] for r in records if r.get('project_code')}
    work_order_codes = {r['work_order_code'] for r in records if r.get('work_order_code')}

    # 批量查询
    domains = {d.code: d for d in InventoryDomain.objects.filter(code__in=domain_codes)}
    warehouses = {w.code: w for w in Warehouse.objects.filter(code__in=warehouse_codes)}
    item_types = {t.code: t for t in ItemType.objects.filter(code__in=item_type_codes)}
    asset_type_codes_map = {t.code: t for t in AssetTypeCode.objects.filter(code__in=asset_type_codes)}
    projects = {p.code: p for p in Project.objects.filter(code__in=project_codes)}
    work_orders = {w.code: w for w in WorkOrder.objects.filter(code__in=work_order_codes)}

    represented_warehouse_ids = {
        warehouses[record['warehouse_code']].id
        for record in records
        if record.get('warehouse_code') in warehouses
    }
    if batch.mode == ImportBatch.Mode.BASELINE and represented_warehouse_ids:
        imported_system_numbers = {record['system_asset_no'] for record in records if record.get('system_asset_no')}
        imported_asset_codes = {record['asset_code'] for record in records if record.get('asset_code')}
        imported_ids = set(
            Asset.objects.filter(
                Q(system_asset_no__in=imported_system_numbers)
                | Q(asset_code__in=imported_asset_codes),
            )
            .values_list('id', flat=True)
        )
        validate_asset_baseline_safety(
            warehouse_ids=represented_warehouse_ids,
            imported_ids=imported_ids,
        )
    represented_warehouses = set(represented_warehouse_ids)

    # 预加载 Location（依赖 warehouse）
    location_keys = {(r['warehouse_code'], r['location_code']) for r in records if r.get('location_code')}
    location_whs = {wh_code for wh_code, _ in location_keys}
    locations = {}
    if location_keys:
        wh_ids = {warehouses[code].id for code in location_whs if code in warehouses}
        for loc in Location.objects.filter(warehouse_id__in=wh_ids).select_related('warehouse'):
            key = (loc.warehouse.code, loc.code)
            if key in location_keys:
                locations[key] = loc

    # 预加载 ProductModelCode（依赖 asset_type_code）
    product_model_keys = {(r['asset_type_code'], r['product_model_code']) for r in records if r.get('product_model_code')}
    product_models = {}
    if product_model_keys := product_model_keys:
        type_ids = {asset_type_codes_map[code].id for code, _ in product_model_keys if code in asset_type_codes_map}
        for pm in ProductModelCode.objects.filter(asset_type_code_id__in=type_ids).select_related('asset_type_code'):
            key = (pm.asset_type_code.code, pm.code)
            if key in product_model_keys:
                product_models[key] = pm

    # === 主循环 ===
    for record in records:
        asset = None
        if record['operation'] == '更新':
            asset = Asset.objects.select_for_update().filter(system_asset_no__iexact=record['system_asset_no']).first() if record['system_asset_no'] else Asset.objects.select_for_update().filter(asset_code__iexact=record['asset_code']).first()
        is_create = asset is None
        if is_create:
            asset = Asset()

        # 从预加载字典获取外键
        domain = domains.get(record['domain_code'])
        if not domain:
            raise AssetV2ImportError(f'第 {record["row_number"]} 行：归属域 {record["domain_code"]} 不存在。')
        warehouse = warehouses.get(record['warehouse_code'])
        if not warehouse:
            raise AssetV2ImportError(f'第 {record["row_number"]} 行：仓库 {record["warehouse_code"]} 不存在。')
        location = locations.get((record['warehouse_code'], record['location_code'])) if record.get('location_code') else None

        asset.domain = domain
        asset.code_source = record['code_source']
        asset.asset_code = record['asset_code'] or None
        asset.asset_type_code = asset_type_codes_map.get(record['asset_type_code'])
        asset.product_model_code = product_models.get((record['asset_type_code'], record['product_model_code'])) if asset.asset_type_code else None
        asset.name = record['name']
        asset.item_type = item_types.get(record['item_type_code'])
        if not asset.item_type:
            raise AssetV2ImportError(f'第 {record["row_number"]} 行：物品类型 {record["item_type_code"]} 不存在。')
        asset.manufacturer_ref = _resolve_pair(Manufacturer, record['manufacturer_code'], record['manufacturer_name'], '生产厂家', record, [])
        asset.manufacturer = record['manufacturer_name']
        asset.model = record['model']
        asset.sn_status = record['sn_status']
        asset.serial_number = record['serial_number']
        asset.manufacturer_barcode = record['manufacturer_barcode']
        asset.supplier_ref = _resolve_pair(Supplier, record['supplier_code'], record['supplier_name'], '供应商', record, [])
        asset.supplier = record['supplier_name']
        asset.warehouse = warehouse
        asset.location = location
        asset.project = projects.get(record['project_code']) if record.get('project_code') else None
        asset.work_order = work_orders.get(record['work_order_code']) if record.get('work_order_code') else None
        asset.received_date = record['received_date']
        asset.purchase_order_no = record['purchase_order_no']
        asset.purchase_amount = record['purchase_amount']
        asset.warranty_until = record['warranty_until']
        asset.search_aliases = record['search_aliases']
        asset.external_holder_name = record.get('external_holder_name', '')
        asset.external_holder_company = record.get('external_holder_company', '')
        asset.external_holder_phone = record.get('external_holder_phone', '')
        asset.remarks = record['remarks']
        # 只有模板确实带“资产状态”列时才改写状态；旧版模板缺列时保留原状态，
        # 避免一次往返导入把报废、维修中的资产悄悄改回在库。
        if record.get('asset_status_supplied'):
            asset.status = record.get('asset_status') or _DEFAULT_IMPORT_STATUS
        asset.source_import_batch = batch
        try:
            apply_asset_identity(asset, actor=actor, reason=f'资产 V2 Excel {record["operation"]}：{warning_reason}'.strip('：'))
        except AssetCodeError as exc:
            raise AssetV2ImportError(f'第 {record["row_number"]} 行：{exc}') from exc
        imported_ids.add(asset.id)
        if record.get('external_row_no'):
            external_row_asset_map[str(record['external_row_no'])] = asset.system_asset_no
        represented_warehouses.add(warehouse.id)
        created_count += int(is_create)
        updated_count += int(not is_create)
        if record.get('parent_asset_no'):
            assembly_intents.append((asset, record['parent_asset_no'], record['row_number']))

    # 装配关系放在所有资产都落库之后再建：文件里主件可能排在后半段，
    # 边导入边连会碰到“父资产还不存在”的情况。
    assembly_count = 0
    for child, parent_no, row_number in assembly_intents:
        parent = Asset.objects.select_for_update().filter(system_asset_no__iexact=parent_no).first()
        if parent is None:
            raise AssetV2ImportError(f'第 {row_number} 行：父资产 {parent_no} 不存在。')
        if parent.pk == child.pk:
            raise AssetV2ImportError(f'第 {row_number} 行：父资产不能指向自己。')
        already = AssetAssemblyLink.objects.filter(child_asset=child, removed_at__isnull=True).exists()
        if already:
            continue
        AssetAssemblyLink.objects.create(
            parent_asset=parent, child_asset=child, operator=actor,
            note=f'资产 V2 Excel 导入建立装配关系（批次 {batch.pk}）',
        )
        assembly_count += 1
    if batch.mode == ImportBatch.Mode.BASELINE and represented_warehouses:
        missing = Asset.objects.select_for_update().filter(warehouse_id__in=represented_warehouses).exclude(id__in=imported_ids).exclude(status=Asset.Status.DISABLED)
        for asset in missing:
            before = asset.status
            asset.status = Asset.Status.DISABLED
            asset.save(update_fields=['status', 'updated_at'])
            record_operation(actor, 'asset_v2_baseline_disabled', asset, summary=f'{asset.system_asset_no} 未出现在 V2 全量基准中', before={'status': before}, after={'status': asset.status})
            disabled_count += 1
    if finalize_batch:
        batch.status = ImportBatch.Status.IMPORTED
    batch.imported_asset_count = len(imported_ids)
    batch.summary = {
        **batch.summary,
        'template_version': 2,
        # V2 导入不写 ImportMutation 账本，因此提交后无法用
        # rollback_import_batch 撤销（它会因「没有可逆变更账本」而拒绝）。
        # 这里显式标记，让导入页与批次详情能提前告知用户，而不是等到
        # 点撤销时才收到一条看不懂的错误。V3 路径有完整快照，不受影响。
        'reversible': False,
        'created_count': created_count,
        'updated_count': updated_count,
        'baseline_disabled_count': disabled_count,
        'assembly_link_count': assembly_count,
        'external_row_asset_map': external_row_asset_map,
        'warning_confirmation': warning_reason.strip(),
    }
    update_fields = ['imported_asset_count', 'summary', 'updated_at']
    if finalize_batch:
        update_fields.append('status')
    batch.save(update_fields=update_fields)
    if finalize_batch:
        record_operation(actor, 'asset_v2_import_applied', batch, summary=f'资产 V2 导入完成：{len(imported_ids)} 件', after=batch.summary)
    return batch
