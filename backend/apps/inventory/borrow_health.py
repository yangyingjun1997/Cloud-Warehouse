"""借用超期催办的增强：识别「借出但无持有人」的孤儿数据。

背景（2026-11-23 实测）：线上 48 件资产状态为「已借出」，
但 current_holder 与 external_holder_name 全为空——都是 Excel 导入时
只填状态、没填持有人的历史数据。现有催办逻辑要求
`request__applicant == asset.current_holder`，因此永远匹配不到，提醒恒为 0。

这不是催办逻辑的 bug，而是数据缺失。但现状对管理者是隐形的：
系统有 48 件「不知道在谁手上」的资产，却没有任何地方提示。

本模块提供：
1. orphan_borrowed_assets()：借出但无任何持有人信息的资产
2. summarize_borrow_health()：借出总数 / 可催办数 / 孤儿数
3. 供催办命令与页面提示使用
"""

from django.db.models import Q

from apps.inventory.models import Asset

# 视为「已借出在外」的状态（与 my_loans 的口径一致）
OUT_HOLDER_STATUSES = (
    Asset.Status.BORROWED,
    Asset.Status.ISSUED,
    Asset.Status.INTERNAL_BORROW,
)


def orphan_borrowed_assets():
    """状态为已借出/已领用/内部借用，但完全没有持有人信息的资产。

    这类资产无法催办、无法追责，需要人工补录持有人后才能纳入催办。
    """
    return (
        Asset.objects.filter(status__in=OUT_HOLDER_STATUSES)
        .filter(Q(current_holder__isnull=True))
        .filter(Q(external_holder_name='') | Q(external_holder_name__isnull=True))
        .select_related('item_type', 'warehouse', 'location')
        .order_by('system_asset_no')
    )


def summarize_borrow_health():
    """借用健康度汇总，供工作台与催办报告使用。"""
    total = Asset.objects.filter(status__in=OUT_HOLDER_STATUSES).count()
    orphan = orphan_borrowed_assets().count()
    tracked = total - orphan
    return {
        'total': total,
        'tracked': tracked,
        'orphan': orphan,
        # 孤儿占比，便于判断严重程度
        'orphan_ratio': round(orphan / total, 3) if total else 0.0,
    }
