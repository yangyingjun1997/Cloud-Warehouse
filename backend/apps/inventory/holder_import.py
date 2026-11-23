"""资产持有人批量补录（母版回填）。

背景（2026-11-23 实测）：线上 636 件资产中 261 件状态为「已借出/已领用/
内部借用」，但 current_holder 与 external_holder_name 全为空——Excel 导入时
只填了状态、没填持有人。结果系统不知道这些资产在谁手上，催办与追责全部失效。

现有 V3 导入只支持耗材（StockItem）的创建与更新，资产没有更新通道，
只能逐件在页面上改。261 件手工改不现实。

本模块提供「按系统编号批量补录持有人」的能力，约束如下：
- 只按 system_asset_no（AST 永久编号）匹配，不用 SN/名称/位置猜测身份，
  遵守 AGENTS.md「不允许仅根据 SN、IP、备注、数量或状态推断单件资产」。
- 只补录持有人相关字段（current_holder / external_holder_name /
  holder_department_text），不碰状态、仓库、库位、金额。
- 每行独立事务，失败不影响其他行，全部结果汇总返回。
- 逐行写审计，可追溯谁在什么时候改了哪件资产的持有人。
"""

import csv
import io

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.models import Department
from apps.common.audit import record_operation
from apps.inventory.models import Asset

User = get_user_model()

# 表头别名：兼容用户母版里常见的列名
COLUMN_ALIASES = {
    'system_asset_no': ('系统编号', '系统资产编号', '资产编号(AST)', 'system_asset_no', 'AST'),
    'holder_username': ('持有人账号', '登录账号', '账号', 'holder_username', 'username'),
    'holder_name': ('持有人姓名', '持有人', '使用人', '姓名', 'holder_name'),
    'holder_department': ('部门', '所属部门', '持有人部门', 'holder_department'),
    'external_holder': ('外部持有人', '外部人员', '外部工程师', 'external_holder'),
}

REQUIRED_COLUMN = 'system_asset_no'


class HolderImportError(Exception):
    """资产持有人导入失败。"""


def _normalize_header(raw):
    text = (raw or '').strip().lstrip('\ufeff')
    for field, aliases in COLUMN_ALIASES.items():
        if text in aliases:
            return field
    return ''


def parse_holder_csv(content):
    """把 CSV 文本解析成 [{system_asset_no, holder_username, ...}, ...]。

    返回 (rows, errors)。errors 是 [(行号, 原因), ...]。
    """
    if isinstance(content, bytes):
        # 兼容 Excel 导出的 GBK 与 UTF-8 BOM
        for encoding in ('utf-8-sig', 'gbk', 'utf-8'):
            try:
                content = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HolderImportError('文件编码无法识别，请另存为 UTF-8 或 GBK 的 CSV。')

    reader = csv.reader(io.StringIO(content))
    try:
        header_row = next(reader)
    except StopIteration:
        raise HolderImportError('文件为空。')

    mapping = {}
    for index, cell in enumerate(header_row):
        field = _normalize_header(cell)
        if field:
            mapping[index] = field

    if REQUIRED_COLUMN not in mapping.values():
        raise HolderImportError(
            '缺少「系统编号」列。请确认表头包含：系统编号、持有人姓名（或持有人账号）。'
        )

    rows = []
    errors = []
    for line_no, cells in enumerate(reader, start=2):
        if not any(str(cell).strip() for cell in cells):
            continue
        record = {field: (cells[index].strip() if index < len(cells) else '')
                  for index, field in mapping.items()}
        code = record.get('system_asset_no', '').strip()
        if not code:
            errors.append((line_no, '系统编号为空'))
            continue
        has_holder = any(record.get(key, '').strip() for key in
                         ('holder_username', 'holder_name', 'external_holder'))
        if not has_holder:
            errors.append((line_no, '未填写任何持有人信息（账号/姓名/外部持有人）'))
            continue
        record['_line'] = line_no
        rows.append(record)

    return rows, errors


def _resolve_holder(record, errors):
    """确定这一行的持有人：优先账号，其次外部持有人，最后按姓名匹配唯一用户。

    姓名匹配有歧义风险，只在「全库仅一个同名用户」时才采用，
    否则拒绝并要求改用账号，避免把资产挂到错误的人头上。
    """
    username = record.get('holder_username', '').strip()
    if username:
        user = User.objects.filter(username__iexact=username, is_active=True).first()
        if not user:
            errors.append('账号 %s 不存在或已停用' % username)
            return None, ''
        return user, ''

    external = record.get('external_holder', '').strip()
    if external:
        return None, external

    name = record.get('holder_name', '').strip()
    if not name:
        errors.append('未填写持有人')
        return None, ''

    matches = list(User.objects.filter(is_active=True))
    matched = []
    for user in matches:
        full = (user.get_full_name() or '').strip()
        if full == name or user.username == name:
            matched.append(user)
    if len(matched) == 1:
        return matched[0], ''
    if len(matched) > 1:
        errors.append('姓名「%s」对应多个账号，请改用「持有人账号」列' % name)
        return None, ''
    errors.append('姓名「%s」没有匹配到账号，请改用「持有人账号」列或填「外部持有人」' % name)
    return None, ''


def apply_holder_rows(rows, *, actor, dry_run=False):
    """按行补录持有人。每行独立事务，失败不回滚其他行。

    返回 {'updated': n, 'skipped': n, 'failed': [(行号, 原因)], 'preview': [...]}。
    """
    updated = 0
    skipped = 0
    failed = []
    preview = []

    for record in rows:
        line_no = record.get('_line', 0)
        code = record['system_asset_no'].strip()
        asset = Asset.objects.filter(system_asset_no__iexact=code).first()
        if not asset:
            failed.append((line_no, '系统中没有编号为 %s 的资产' % code))
            continue

        row_errors = []
        holder, external = _resolve_holder(record, row_errors)
        if row_errors:
            failed.append((line_no, '；'.join(row_errors)))
            continue

        department_name = record.get('holder_department', '').strip()
        department = None
        if department_name:
            department = Department.objects.filter(name__iexact=department_name).first()
            if not department:
                failed.append((line_no, '部门「%s」不存在' % department_name))
                continue

        changes = {}
        if holder and asset.current_holder_id != holder.id:
            changes['current_holder'] = holder
        if external and (asset.external_holder_name or '') != external:
            changes['external_holder_name'] = external
        if department and asset.department_id != department.id:
            changes['department'] = department

        if not changes:
            skipped += 1
            preview.append({
                'line': line_no, 'code': code, 'action': '无变化',
                'holder': holder.get_username() if holder else external,
            })
            continue

        if dry_run:
            updated += 1
            preview.append({
                'line': line_no, 'code': code, 'action': '将更新',
                'holder': holder.get_username() if holder else external,
                'fields': ', '.join(changes),
            })
            continue

        try:
            with transaction.atomic():
                locked = Asset.objects.select_for_update().get(pk=asset.pk)
                before = {
                    'current_holder': locked.current_holder_id,
                    'external_holder_name': locked.external_holder_name,
                    'department': locked.department_id,
                }
                for field, value in changes.items():
                    setattr(locked, field, value)
                locked.save(update_fields=[*changes, 'updated_at'])
                record_operation(
                    actor,
                    'asset_holder_import',
                    locked,
                    summary=(
                        f'批量补录持有人：{code} '
                        + ' '.join(f'{k}:{before.get(k)}->'
                                   f'{getattr(value, "pk", value)}'
                                   for k, value in changes.items())
                    ),
                )
            updated += 1
            preview.append({
                'line': line_no, 'code': code, 'action': '已更新',
                'holder': holder.get_username() if holder else external,
                'fields': ', '.join(changes),
            })
        except Exception as exc:  # 单行失败不阻断整批
            failed.append((line_no, '写入失败：%s' % exc))

    return {
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'preview': preview,
        'dry_run': dry_run,
    }
