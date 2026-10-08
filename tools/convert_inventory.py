# -*- coding: utf-8 -*-
"""
将《9.20市场+研发库存明细.xlsx》拆成系统可导入的两份标准格式：
  - import_output/市场库_标准格式.xlsx
  - import_output/研发库_标准格式.xlsx

规则（与使用方确认）：
1. 客户清单清洗：客户列只保留真实客户/单位；展会、测试、维修、返厂、领用等说明性文字移入备注。
2. 物品类型别名合并：A2 pro/A2-pro、A2W、AS2、B2W/B2-W、蓝牙音响/蓝牙音箱 统一为规范名。
3. 领用人不进系统账号，仅以文本保留在备注。
4. 资产编码不由系统生成；源表有 DZSB 编码则带出，没有就留空，由行政出货后赋予。
5. 状态映射到系统状态机；能识别的映射，不能识别的（内部借用库/已出库）新增状态；原文进备注。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / '9.20市场+研发库存明细.xlsx'
OUT_DIR = ROOT / 'import_output'
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 物品类型别名 -> 规范名
# ---------------------------------------------------------------------------
TYPE_ALIASES = {
    '宇树A2 pro': '宇树A2-pro',
    'A2 pro': '宇树A2-pro',
    '宇树A2W': '宇树A2-pro',   # A2W 与 A2 pro 同款 WiFi 版，统一
    'A2W': '宇树A2-pro',
    '宇树AS2': '宇树A2S',      # 统一大小写
    '宇树As2': '宇树A2S',
    '宇树B2W': '宇树B2-W',
    '蓝牙音响': '蓝牙音箱',
}

# 规范名 -> (分类, 是否整机/serialized)
# 整机逐件管理；小配件也逐件但单位不同。这里全部按单件资产，类型编码留空由系统生成。
CANONICAL_CATEGORY = {
    '宇树': '机器人本体',
    '智元': '机器人本体',
    '龙远': '机器人本体',
    '优宝特': '机器人本体',
}

# ---------------------------------------------------------------------------
# 客户列清洗：这些关键词出现 -> 不是客户，移到备注
# ---------------------------------------------------------------------------
NON_CLIENT_PATTERNS = [
    '维修', '返厂', '借用', '测试', '领用', '领出', '展会', '专用',
    '上装', '事业部', '研发', '修复', '换新', '生锈', '不出图', '拿走',
    '展厅', '反馈', '已修好', '已换新', '中博会', '数贸会', '已归还',
    '备用', '部署', '演示', '样品', '检测', '拍摄',
]
NON_CLIENT_RE = re.compile('|'.join(re.escape(p) for p in NON_CLIENT_PATTERNS))


def is_real_client(text: str) -> bool:
    """客户列是否像真实客户/单位（而不是用途/状态说明）。"""
    if not text or not text.strip():
        return False
    t = text.strip()
    # 含说明性关键词 -> 非客户
    if NON_CLIENT_RE.search(t):
        return False
    # 太短且不像公司/单位名 -> 存疑，但保留（如“西藏项目”）
    return True


# ---------------------------------------------------------------------------
# 状态映射（系统状态值, 是否新增/映射, 说明）
# ---------------------------------------------------------------------------
STATUS_MAP = {
    # 市场库
    '出库出售': ('sold', '已售出'),
    '在库空闲': ('in_stock', '在库'),
    '出库借用': ('borrowed', '已借出'),
    '内部借用库': ('internal_borrow', '内部借用库'),
    '返厂维修': ('repairing', '维修中'),
    '已归还': ('in_stock', '在库'),           # 已归还 = 回到库存
    '转研发仓库': (None, None),               # 跳过本库，由研发库承接
    # 研发库
    '已出库': ('shipped_out', '已出库'),
    '未出库': ('in_stock', '在库'),
    '返厂': ('repairing', '维修中'),
}


def norm_date(value):
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    return str(value).strip()


def join_remarks(parts):
    return '；'.join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 解析两个 sheet
# ---------------------------------------------------------------------------
def load_sheet(ws, kind):
    """kind: 'market' | 'research'，返回明细行 dict 列表。"""
    rows = []
    current_product = None
    current_stats = None
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        a = row[0]
        has_a = a is not None and str(a).strip() != ''
        if kind == 'market':
            status = row[9]
        else:
            status = row[8]
        # 汇总行：A列有值 且 状态列为空
        if has_a and (status is None or str(status).strip() == ''):
            current_product = str(a).strip()
            current_stats = row
            continue
        # 明细行
        rec = {'_row': idx, '_product': current_product, '_stats': current_stats}
        if kind == 'market':
            rec.update({
                'seq': row[1], 'sn': row[4], 'asset_code': row[5],
                'manufacturer': row[6], 'model': row[7],
                'arrive_date': row[8], 'status': row[9], 'client': row[10],
                'sell_date': row[11], 'ship_no': row[12], 'remark': row[13],
                'oa': row[14],
            })
        else:
            # 研发库“图瑞”批次（379-383 行）整行从 D 列起右移一列：
            #   D=资产编号(DZSB)、E=厂家、F=空、G=到货日期、H=状态、I=出货日期……
            # 判定：资产编号列(E)是厂家文本(非DZSB) 且 序列号列(D)是 DZSB 编码。
            sn_s = str(row[3]).strip() if row[3] else ''
            code_s = str(row[4]).strip() if row[4] else ''
            shifted = bool(sn_s.upper().startswith('DZSB') and code_s and not code_s.upper().startswith('DZSB'))
            if shifted:
                # 右移恢复：code=D(DZSB), mfr=E, model=F, arrive=G, status=H, out=I, type=J, user=K, remark=L, return=M, erp=N, purpose=O
                rec.update({
                    'seq': row[1], 'sn': None, 'asset_code': row[3],
                    'manufacturer': row[4], 'model': row[5],
                    'arrive_date': row[6], 'status': row[7], 'out_date': row[8],
                    'out_type': row[9], 'user': row[10], 'remark': row[11],
                    'return_date': row[12], 'erp': row[13], 'purpose': row[14],
                })
            else:
                rec.update({
                    'seq': row[1], 'sn': row[3], 'asset_code': row[4],
                    'manufacturer': row[5], 'model': row[6],
                    'arrive_date': row[7], 'status': row[8], 'out_date': row[9],
                    'out_type': row[10], 'user': row[11], 'remark': row[12],
                    'return_date': row[13], 'erp': row[14], 'purpose': row[15],
                })
        rows.append(rec)
    return rows


def _s(value):
    return str(value).strip() if value is not None else ''


def canonical_type(product, model):
    """根据产品名/型号得到规范物品类型名。"""
    base = (_s(product) + ' ' + _s(model)).strip()
    for alias, canon in TYPE_ALIASES.items():
        if alias in base:
            return canon
    # 未命中别名则取产品名
    return _s(product) or _s(model) or '未命名'


def guess_category(product, model):
    base = f'{product or ""} {model or ""}'
    for key, cat in CANONICAL_CATEGORY.items():
        if key in base:
            return cat
    return '配件/外设'


# 序列号列里混入的非厂家 SN：纯占位直接丢弃，IP/内部代号有追溯价值移到备注。
SN_PLACEHOLDER = {'无', '/', '本批没有序列号', '没有', '暂无', '-', ''}
IP_RE = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
INTERNAL_TAG_RE = re.compile(r'^(研发|测试|专用)\d*$|^\d+-\d+$')


def split_sn(sn):
    """返回 (厂家SN, 需移到备注的内部标记)。占位文本两边都空。"""
    if sn is None:
        return '', ''
    s = str(sn).strip()
    if s in SN_PLACEHOLDER:
        return '', ''
    if IP_RE.match(s):
        return '', f'设备IP：{s}'
    if INTERNAL_TAG_RE.match(s):
        return '', f'内部标记：{s}'
    return s, ''


def clean_code(code):
    """资产编码：只认 DZSB-...，其余原样保留但去空格；没有就空。"""
    if code is None:
        return ''
    s = str(code).strip()
    return s if s and s != '/' else ''


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    wb = openpyxl.load_workbook(SOURCE, data_only=True)
    market = load_sheet(wb['市场库存'], 'market')
    research = load_sheet(wb['研发库存'], 'research')

    issues = []          # (sheet, row, level, message, identifier)
    client_counter = Counter()
    type_counter = Counter()

    market_out, research_out = [], []

    # ---------- 市场库 ----------
    for r in market:
        row_no = r['_row']
        product = r['_product']
        sn, sn_note = split_sn(r['sn'])
        code = clean_code(r['asset_code'])
        status_raw = (str(r['status']).strip() if r['status'] else '')
        ident = sn or code or f'行{row_no}'

        # 转研发仓库 -> 跳过本库
        if status_raw == '转研发仓库':
            issues.append(('市场库存', row_no, '提示', '已转研发库，本库不导入', ident))
            continue

        # 状态映射
        if status_raw in STATUS_MAP:
            status_val, status_label = STATUS_MAP[status_raw]
        elif status_raw == '':
            status_val, status_label = 'in_stock', '在库'
            issues.append(('市场库存', row_no, '警告', '状态为空，默认在库', ident))
        else:
            status_val, status_label = 'in_stock', '在库'
            issues.append(('市场库存', row_no, '警告', f'无法识别状态“{status_raw}”，默认在库', ident))

        # 客户清洗
        client = str(r['client']).strip() if r['client'] else ''
        remark_parts = []
        if sn_note:
            remark_parts.append(sn_note)
        if client:
            if is_real_client(client):
                client_counter[client] += 1
                remark_parts.append(f'客户：{client}')
            else:
                remark_parts.append(f'原客户栏：{client}')
        if r['oa']:
            remark_parts.append(f"OA：{str(r['oa']).strip()}")
        if r['ship_no']:
            remark_parts.append(f"物流单号：{str(r['ship_no']).strip()}")
        if r['sell_date']:
            remark_parts.append(f"售卖出库：{norm_date(r['sell_date'])}")
        if r['arrive_date']:
            remark_parts.append(f"到货：{norm_date(r['arrive_date'])}")
        if r['remark']:
            remark_parts.append(str(r['remark']).strip())
        if status_raw and status_raw != status_label and status_raw != '在库空闲':
            remark_parts.append(f'原状态：{status_raw}')
        remarks = join_remarks(remark_parts)

        item_type = canonical_type(product, r['model'])
        type_counter[item_type] += 1
        market_out.append([
            '单件资产', item_type, item_type, '', '市场库', '',
            code, sn, '', '', '', 1, '台', 0,
            (str(r['manufacturer']).strip() if r['manufacturer'] else ''),
            (str(r['model']).strip() if r['model'] else ''),
            '', None, status_label, remarks,
        ])

    # ---------- 研发库 ----------
    for r in research:
        row_no = r['_row']
        product = r['_product']
        sn, sn_note = split_sn(r['sn'])
        code = clean_code(r['asset_code'])
        status_raw = (str(r['status']).strip() if r['status'] else '')
        ident = sn or code or f'行{row_no}'

        # 状态列是日期等异常
        if hasattr(r['status'], 'strftime'):
            issues.append(('研发库存', row_no, '警告', f'状态列是日期“{norm_date(r["status"])}”，已忽略', ident))
            status_raw = ''

        if status_raw in STATUS_MAP:
            status_val, status_label = STATUS_MAP[status_raw]
        elif status_raw == '':
            status_val, status_label = 'in_stock', '在库'
            issues.append(('研发库存', row_no, '警告', '状态为空，默认在库', ident))
        else:
            status_val, status_label = 'in_stock', '在库'
            issues.append(('研发库存', row_no, '警告', f'无法识别状态“{status_raw}”，默认在库', ident))

        remark_parts = []
        if sn_note:
            remark_parts.append(sn_note)
        if r['user']:
            remark_parts.append(f"领用人：{str(r['user']).strip()}")
        if r['out_type']:
            remark_parts.append(f"出货类型：{str(r['out_type']).strip()}")
        if r['purpose']:
            remark_parts.append(f"用途：{str(r['purpose']).strip()}")
        if r['out_date']:
            remark_parts.append(f"出货：{norm_date(r['out_date'])}")
        if r['return_date']:
            remark_parts.append(f"归还：{norm_date(r['return_date'])}")
        if r['erp']:
            remark_parts.append(f"ERP：{str(r['erp']).strip()}")
        if r['arrive_date']:
            remark_parts.append(f"到货：{norm_date(r['arrive_date'])}")
        if r['remark']:
            remark_parts.append(str(r['remark']).strip())
        if status_raw and status_raw != status_label:
            remark_parts.append(f'原状态：{status_raw}')
        remarks = join_remarks(remark_parts)

        item_type = canonical_type(product, r['model'])
        type_counter[item_type] += 1
        research_out.append([
            '单件资产', item_type, item_type, '', '研发库', '',
            code, sn, '', '', '', 1, '台', 0,
            (str(r['manufacturer']).strip() if r['manufacturer'] else ''),
            (str(r['model']).strip() if r['model'] else ''),
            '', None, status_label, remarks,
        ])

    # ---------- 写文件 ----------
    headers = [
        '管理方式*', '物品名称*', '物品类型*', '物品类型编码', '仓库*', '库位',
        '系统编号', '厂家出厂SN序列号', '厂家条码/二维码', '系统二维码内容',
        '耗材配件编码', '库存数量', '单位', '最低库存提醒值', '厂家', '型号',
        '供应商', '单价（元）', '状态', '备注',
    ]

    def write_sheet(path, rows):
        w = Workbook()
        s = w.active
        s.title = '库存导入'
        s.append(headers)
        for row in rows:
            s.append(row)
        for cell in s[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='176E68')
        s.freeze_panes = 'A2'
        w.save(path)

    write_sheet(OUT_DIR / '市场库_标准格式.xlsx', market_out)
    write_sheet(OUT_DIR / '研发库_标准格式.xlsx', research_out)

    # ---------- 报告 ----------
    def dump_list(path, counter, title):
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'=== {title} ===\n\n')
            for name, cnt in counter.most_common():
                f.write(f'{name}: {cnt}\n')

    dump_list(ROOT / '客户清单.txt', client_counter, '清洗后的客户清单')
    dump_list(ROOT / '物品类型清单.txt', type_counter, '合并后的物品类型清单')

    errors = [i for i in issues if i[2] == '错误']
    warns = [i for i in issues if i[2] == '警告']
    infos = [i for i in issues if i[2] == '提示']
    with open(ROOT / '导入问题清单.txt', 'w', encoding='utf-8') as f:
        f.write('=== 导入问题清单 ===\n\n')
        f.write(f'错误（阻断导入）: {len(errors)} 条\n')
        for sheet, row, level, msg, ident in errors:
            f.write(f'  [{sheet}] 行{row}: {msg} | {ident}\n')
        f.write(f'\n警告（默认处理，需人工复核）: {len(warns)} 条\n')
        for sheet, row, level, msg, ident in warns:
            f.write(f'  [{sheet}] 行{row}: {msg} | {ident}\n')
        f.write(f'\n提示: {len(infos)} 条\n')
        for sheet, row, level, msg, ident in infos:
            f.write(f'  [{sheet}] 行{row}: {msg} | {ident}\n')

    print(f'市场库 {len(market_out)} 行，研发库 {len(research_out)} 行')
    print(f'客户 {len(client_counter)} 种，物品类型 {len(type_counter)} 种')
    print(f'问题：错误 {len(errors)} / 警告 {len(warns)} / 提示 {len(infos)}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
