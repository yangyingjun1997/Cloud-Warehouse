"""成品设备 BOM Excel 批量导入。

模板格式（第一个工作表，首行为表头，列顺序固定）：

| 成品名称* | 成品编码 | 型号 | 组件资产编码* | 装入备注 |

同一个"成品名称"的连续多行视为同一台成品的多个组件；组件资产编码必须对应
在库状态的资产。导入整体在一个事务中执行，任一行失败则全部回滚并返回
行级错误清单。
"""
from __future__ import annotations

from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook

from .assembly_services import AssemblyError, add_components, create_unit
from .models import Asset


class BomImportError(Exception):
    pass


HEADERS = ['成品名称', '成品编码', '型号', '组件资产编码', '装入备注']


def build_bom_template() -> BytesIO:
    """生成 BOM 导入模板（含一行示例）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = '成品BOM'
    ws.append(HEADERS)
    ws.append(['G1 巡检整机', 'G1-001', 'G1-Pro', 'JT-2024-0001', '主控板'])
    ws.append(['G1 巡检整机', 'G1-001', 'G1-Pro', 'JT-2024-0002', '激光雷达'])
    ws.append(['G1 巡检整机', 'G1-001', 'G1-Pro', 'JT-2024-0003', ''])
    ws.append(['G2 搬运整机', 'G2-001', 'G2-Std', 'JT-2024-0010', ''])
    for col, width in zip('ABCDE', (18, 14, 14, 18, 20)):
        ws.column_dimensions[col].width = width
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _cell_text(value) -> str:
    return str(value).strip() if value is not None else ''


def _parse_rows(content: bytes):
    """解析工作簿，返回行列表；格式问题直接抛 BomImportError。"""
    try:
        wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise BomImportError(f'Excel 文件无法读取：{exc}') from exc
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise BomImportError('Excel 是空的。')
    header = [_cell_text(cell) for cell in rows[0]]
    if header[: len(HEADERS)] != HEADERS:
        raise BomImportError(f'表头必须是：{"、".join(HEADERS)}。请下载最新模板填写。')
    data_rows = []
    for index, row in enumerate(rows[1:], start=2):
        cells = [_cell_text(cell) for cell in row[: len(HEADERS)]] + [''] * (len(HEADERS) - len(row))
        if not any(cells):
            continue
        data_rows.append((index, dict(zip(HEADERS, cells))))
    if not data_rows:
        raise BomImportError('Excel 中没有可导入的数据行。')
    return data_rows


@transaction.atomic
def import_unit_bom(*, actor, content: bytes):
    """解析并导入 BOM，整体事务。

    返回 (units, errors)：成功时 errors 为空；任一行失败抛 BomImportError
    并携带全部行级错误（此时整体已回滚，不产生半成品数据）。
    """
    rows = _parse_rows(content)
    errors = []
    units_by_key = {}
    order = []

    for line_no, row in rows:
        name = row['成品名称']
        asset_code = row['组件资产编码']
        if not name:
            errors.append(f'第 {line_no} 行：成品名称不能为空。')
            continue
        if not asset_code:
            errors.append(f'第 {line_no} 行：组件资产编码不能为空。')
            continue
        asset = Asset.objects.filter(asset_code=asset_code).first()
        if not asset:
            errors.append(f'第 {line_no} 行：资产编码 {asset_code} 不存在。')
            continue
        if asset.status != Asset.Status.IN_STOCK:
            errors.append(f'第 {line_no} 行：资产 {asset_code} 当前为{asset.get_status_display()}，只有在库资产可装入。')
            continue

        key = (name, row['成品编码'])
        if key not in units_by_key:
            units_by_key[key] = {
                'name': name,
                'unit_code': row['成品编码'],
                'model': row['型号'],
                'asset_ids': [],
                'notes': {},
            }
            order.append(key)
        units_by_key[key]['asset_ids'].append(asset.id)
        if row['装入备注']:
            units_by_key[key]['notes'][asset.id] = row['装入备注']

    if errors:
        raise BomImportError('\n'.join(errors))

    created_units = []
    for key in order:
        spec = units_by_key[key]
        unit = create_unit(
            actor=actor,
            name=spec['name'],
            unit_code=spec['unit_code'],
            model=spec['model'],
            remarks='由 BOM Excel 批量导入。',
        )
        add_components(
            unit=unit,
            asset_ids=spec['asset_ids'],
            actor=actor,
            note='BOM 批量导入。',
        )
        created_units.append(unit)
    return created_units
