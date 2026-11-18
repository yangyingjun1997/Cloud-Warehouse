"""导入影响预估：在执行前算清「将新增 / 更新 / 停用 / 归零」各多少条。

背景（2026-11-18 与用户确认）：
用户导出 xlsx 做大范围增删改后直接导入时，需要明确知道会发生什么，
而不是等导入完成后再从结果里找数字。特别是「全量基准」模式会把文件里
缺失的记录停用/归零——这是有破坏性的动作，必须在执行前看到具体数量。

设计：
- 只做只读统计，不写库。
- 与真正的 apply 逻辑用同一套匹配规则（按 AST / 资产编码 / 物料编码），
  避免预估数与实际执行数不一致。
- 返回可直接渲染的中文摘要。
"""

from django.db.models import Q

from apps.inventory.models import Asset, ImportBatch, StockItem


def _asset_match_q(record):
    """按 AST 或资产编码定位已有资产，与 apply 阶段保持一致。"""
    conditions = Q(pk__in=[])
    if record.get('system_asset_no'):
        conditions |= Q(system_asset_no__iexact=record['system_asset_no'])
    if record.get('asset_code'):
        conditions |= Q(asset_code__iexact=record['asset_code'])
    return conditions


def estimate_import_impact(*, records, mode, template_version):
    """返回影响预估 dict。records 是解析后的行列表。

    template_version 2 = 单件资产；3 = 资产 + 耗材两个业务表。
    """
    if template_version == 3:
        asset_records = records.get('assets', []) if isinstance(records, dict) else []
        stock_records = records.get('stocks', []) if isinstance(records, dict) else []
    else:
        asset_records = records if isinstance(records, list) else []
        stock_records = []

    # ── 资产：新增 / 更新 ────────────────────────────────────────────
    asset_create = 0
    asset_update = 0
    asset_update_missing = 0
    matched_asset_ids = set()

    for record in asset_records:
        operation = (record.get('operation') or '').strip()
        if operation == '新增':
            asset_create += 1
            continue
        if operation != '更新':
            continue
        match = Asset.objects.filter(_asset_match_q(record)).values_list('id', flat=True).first()
        if match:
            asset_update += 1
            matched_asset_ids.add(match)
        else:
            # 标了「更新」但库里找不到 —— 预检会报错，这里单独计数便于提示
            asset_update_missing += 1

    # ── 耗材：新增 / 更新 ────────────────────────────────────────────
    stock_create = 0
    stock_update = 0
    matched_stock_ids = set()
    for record in stock_records:
        operation = (record.get('operation') or '').strip()
        if operation == '新增':
            stock_create += 1
            continue
        if operation != '更新':
            continue
        code = (record.get('code') or '').strip()
        if not code:
            continue
        match = StockItem.objects.filter(code__iexact=code).values_list('id', flat=True).first()
        if match:
            stock_update += 1
            matched_stock_ids.add(match)

    # ── 全量基准：停用 / 归零 ────────────────────────────────────────
    asset_disable = 0
    stock_zero = 0
    baseline_scoped = False

    if mode == ImportBatch.Mode.BASELINE:
        # 复刻 apply 逻辑：只影响「本批文件里出现过的仓库」
        warehouse_codes = {
            (r.get('warehouse_code') or '').strip()
            for r in [*asset_records, *stock_records]
            if (r.get('warehouse_code') or '').strip()
        }
        if warehouse_codes:
            baseline_scoped = True
            from apps.inventory.models import Warehouse

            warehouse_ids = set(
                Warehouse.objects.filter(code__in=warehouse_codes).values_list('id', flat=True)
            )
            if warehouse_ids:
                asset_disable = (
                    Asset.objects.filter(warehouse_id__in=warehouse_ids)
                    .exclude(id__in=matched_asset_ids)
                    .exclude(status=Asset.Status.DISABLED)
                    .count()
                )
                stock_zero = (
                    StockItem.objects.filter(warehouse_id__in=warehouse_ids, quantity__gt=0)
                    .exclude(id__in=matched_stock_ids)
                    .count()
                )

    total = (
        asset_create + asset_update + stock_create + stock_update
        + asset_disable + stock_zero
    )

    return {
        'template_version': template_version,
        'asset_create': asset_create,
        'asset_update': asset_update,
        'asset_update_missing': asset_update_missing,
        'stock_create': stock_create,
        'stock_update': stock_update,
        'asset_disable': asset_disable,
        'stock_zero': stock_zero,
        'baseline_scoped': baseline_scoped,
        'is_baseline': mode == ImportBatch.Mode.BASELINE,
        'has_destructive': bool(asset_disable or stock_zero),
        'total_affected': total,
    }


def describe_import_impact(impact):
    """把预估结果转成一句中文摘要。"""
    parts = []
    if impact['asset_create']:
        parts.append(f'新增资产 {impact["asset_create"]} 条')
    if impact['asset_update']:
        parts.append(f'更新资产 {impact["asset_update"]} 条')
    if impact['stock_create']:
        parts.append(f'新增耗材 {impact["stock_create"]} 条')
    if impact['stock_update']:
        parts.append(f'更新耗材 {impact["stock_update"]} 条')
    if impact['asset_disable']:
        parts.append(f'停用资产 {impact["asset_disable"]} 条')
    if impact['stock_zero']:
        parts.append(f'耗材归零 {impact["stock_zero"]} 条')
    if impact['asset_update_missing']:
        parts.append(f'{impact["asset_update_missing"]} 条标记为更新但库中不存在')
    return '、'.join(parts) if parts else '没有会写入或停用的变更'
