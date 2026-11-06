"""资产标签渲染服务。

标签版式集中在这里，模板和打印客户端共用同一份数据，避免两边各画一套。

版式（用户指定）：

    LOGO                二维码
                        二维码
    厂家SN：XXXXX
    资产编码：XXXXXX

其中“资产编码”优先显示正式资产编码（`SC-...` 仓库自编码或 `DZSB-...` 行政码），
尚未赋码时显示“暂未赋码”，保证标签总能打出可用信息。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
from typing import Iterable, Sequence

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from .models import Asset


@dataclass(frozen=True)
class AssetLabel:
    """一张标签的完整渲染数据。"""

    asset_id: str
    qr_payload: str
    qr_image_base64: str
    logo_text: str
    title: str
    manufacture_sn: str
    primary_code: str
    permanent_no: str
    pending_code: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            'asset_id': self.asset_id,
            'qr_payload': self.qr_payload,
            'qr_image': self.qr_image_base64,
            'logo_text': self.logo_text,
            'title': self.title,
            'manufacture_sn': self.manufacture_sn,
            'primary_code': self.primary_code,
            'permanent_no': self.permanent_no,
            'pending_code': self.pending_code,
            'warnings': list(self.warnings),
        }


def build_qr_png_base64(payload: str, box_size: int = 8, border: int = 2) -> str:
    """生成二维码 PNG 的 base64 文本，直接内嵌到 HTML 里。"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def resolve_label_codes(asset: Asset) -> tuple[str, bool, tuple[str, ...]]:
    """返回（打印用编码，是否待赋码，告警）。

    标签必须能打出可人工识别的编码。资产可以使用 `SC-` 仓库自编码
    或 `DZSB-` 行政/ERP 编码；两者都没有时退回系统永久编号。
    """
    warnings: list[str] = []
    if asset.asset_code:
        return asset.asset_code, False, ()

    if asset.system_asset_no:
        warnings.append('该资产尚未分配主资产编码，标签上以系统永久编号代替。')
        return f'暂未赋码 {asset.system_asset_no}', True, tuple(warnings)

    warnings.append('该资产既无主资产编码也无系统永久编号，无法生成可用标签。')
    return '无可用编码', True, tuple(warnings)


def build_asset_label(asset: Asset, *, logo_text: str = '八维通') -> AssetLabel:
    """把一件资产渲染成标签数据。"""
    payload = asset.qr_value or asset.system_asset_no or str(asset.id)
    primary_code, pending, warnings = resolve_label_codes(asset)
    return AssetLabel(
        asset_id=str(asset.id),
        qr_payload=payload,
        qr_image_base64=build_qr_png_base64(payload),
        logo_text=logo_text,
        title=asset.name or '未命名资产',
        manufacture_sn=asset.serial_number or '',
        primary_code=primary_code,
        permanent_no=asset.system_asset_no or '',
        pending_code=pending,
        warnings=warnings,
    )


def build_asset_labels(assets: Iterable[Asset], *, logo_text: str = '八维通') -> list[AssetLabel]:
    return [build_asset_label(asset, logo_text=logo_text) for asset in assets]


def labels_to_print_payload(labels: Sequence[AssetLabel], *, template: str = 'asset-label-60x40') -> dict:
    """组装给 Windows 打印客户端的任务体。

    客户端只需要二维码图片和几行文本，不需要理解业务字段，
    这样标签改版时只需改服务端。
    """
    return {
        'template': template,
        'count': len(labels),
        'labels': [label.as_dict() for label in labels],
    }
