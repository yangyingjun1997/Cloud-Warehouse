from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.audit import record_operation

from .models import (
    Asset,
    AssetCodeSequence,
    AssetDomainChange,
    AssetIdentifier,
    InventoryDomain,
)


CODE_PATTERN = re.compile(r'^[A-Z0-9]+(?:-[A-Z0-9]+)*$')
SYSTEM_CODE_PATTERN = re.compile(r'^AST-\d{6,}$')
QR_V2_PATTERN = re.compile(r'^WMS2:(?P<object_type>[A-Z]):(?P<code>[A-Z0-9-]+)$')
SN_PLACEHOLDERS = {'无', '未知', '-', '--', '暂无', '无SN', 'N/A', 'NA', 'NONE', 'NULL'}


class AssetCodeError(ValueError):
    pass


def normalize_identifier(value: str | None) -> str:
    return (value or '').strip().upper()


def validate_code_segment(value: str, label: str) -> str:
    value = normalize_identifier(value)
    if not value or not re.fullmatch(r'[A-Z0-9]+', value):
        raise AssetCodeError(f'{label}只能包含大写字母和数字。')
    return value


def build_asset_qr_payload(system_code: str) -> str:
    code = normalize_identifier(system_code)
    if not SYSTEM_CODE_PATTERN.fullmatch(code):
        raise AssetCodeError('系统永久编号格式无效，无法生成二维码。')
    return f'WMS2:A:{code}'


def parse_asset_qr_payload(value: str) -> dict:
    raw = (value or '').strip()
    match = QR_V2_PATTERN.fullmatch(raw.upper())
    if not match:
        if raw.upper().startswith('WMS'):
            raise AssetCodeError('该标签版本或二维码格式暂不支持。')
        return {'version': 1, 'object_type': 'A', 'identifier': raw}
    if match.group('object_type') != 'A':
        raise AssetCodeError('该二维码不是单件资产标签。')
    return {'version': 2, 'object_type': 'A', 'identifier': match.group('code')}


@transaction.atomic
def allocate_asset_code(*, scheme: str, prefix: str, width: int, maximum: int | None = None) -> str:
    prefix = normalize_identifier(prefix)
    sequence = AssetCodeSequence.objects.select_for_update().filter(
        scheme=scheme, prefix=prefix,
    ).first()
    if sequence is None:
        try:
            with transaction.atomic():
                sequence = AssetCodeSequence.objects.create(
                    scheme=scheme, prefix=prefix, width=width, last_number=0,
                )
        except IntegrityError:
            sequence = AssetCodeSequence.objects.select_for_update().get(
                scheme=scheme, prefix=prefix,
            )
    if sequence.is_exhausted:
        raise AssetCodeError(f'编码前缀 {prefix} 的流水号已用尽，请管理员扩展编码规则。')
    next_number = sequence.last_number + 1
    limit = maximum
    if limit is not None and next_number > limit:
        sequence.is_exhausted = True
        sequence.save(update_fields=['is_exhausted', 'updated_at'])
        raise AssetCodeError(f'编码前缀 {prefix} 已达到 {limit}，未生成格式不一致的新编号。')
    sequence.last_number = next_number
    sequence.save(update_fields=['last_number', 'updated_at'])
    return f'{prefix}-{next_number:0{sequence.width}d}'


def allocate_system_asset_code() -> str:
    return allocate_asset_code(
        scheme=AssetCodeSequence.Scheme.SYSTEM,
        prefix='AST',
        width=8,
        maximum=None,
    )


def _identifier_type_for(asset: Asset) -> str | None:
    code = normalize_identifier(asset.asset_code)
    if code.startswith('SC-'):
        return AssetIdentifier.IdentifierType.MARKET
    # ZSB 是 DZSB 的历史笔误前缀，QTZC 是消防水炮专用前缀；行政侧已无法修正，
    # 统一按“行政/ERP 发放的编码”归类，避免它们既不属于仓库也不再被识别。
    if code.startswith('DZSB-') or code.startswith('ZSB-') or code.startswith('QTZC-'):
        return AssetIdentifier.IdentifierType.ADMIN
    return None


# ── 资产编码前缀：页面维护与批量导入必须共用同一套规则 ──────────────────
#
# 历史坑：V2 导入曾在本地写死 `(?:SC|DZSB)-` 正则，把 `ZSB-`、`QTZC-` 判为非法，
# 而页面维护和编码服务接受这四种前缀。结果同一张源表在页面能存、走导入被拒，
# 用户完全看不出原因。此处抽出唯一事实来源，V2 导入改为调用它。
ADMIN_ASSET_CODE_PREFIXES = ('DZSB-', 'ZSB-', 'QTZC-')
ASSET_CODE_PREFIXES = ('SC-', *ADMIN_ASSET_CODE_PREFIXES)

# SC / DZSB / ZSB / QTZC 后接「字母数字段，允许用连字符分段」
_ASSET_CODE_PATTERN = re.compile(r'(?:SC|DZSB|ZSB|QTZC)-[A-Z0-9]+(?:-[A-Z0-9]+)*')


def is_valid_warehouse_assigned_code(code: str) -> bool:
    """源表既有编码（SC/DZSB/ZSB/QTZC 任一前缀）格式是否合法。"""
    return bool(_ASSET_CODE_PATTERN.fullmatch(normalize_identifier(code)))


def is_valid_admin_assigned_code(code: str) -> bool:
    """行政/ERP 发放的编码只允许 DZSB/ZSB/QTZC 前缀——SC 是仓库自编号，不算行政码。"""
    normalized = normalize_identifier(code)
    return bool(
        normalized
        and normalized.startswith(ADMIN_ASSET_CODE_PREFIXES)
        and _ASSET_CODE_PATTERN.fullmatch(normalized)
    )


def warehouse_assigned_code_error() -> str:
    return '源表既有资产编码必须以 SC-、DZSB-、ZSB- 或 QTZC- 开头。'


def admin_assigned_code_error() -> str:
    return '行政/ERP 资产编码必须以 DZSB-、ZSB- 或 QTZC- 开头。'


@transaction.atomic
def sync_asset_identifiers(asset: Asset, *, actor=None, reason: str = '') -> None:
    AssetIdentifier.objects.get_or_create(
        asset=asset,
        identifier_type=AssetIdentifier.IdentifierType.SYSTEM,
        defaults={
            'value': asset.system_asset_no,
            'normalized_value': normalize_identifier(asset.system_asset_no),
            'issuer': '售后仓库系统',
            'created_by': actor if getattr(actor, 'is_authenticated', False) else None,
        },
    )

    current_primary = AssetIdentifier.objects.filter(
        asset=asset, is_primary=True, is_active=True,
    ).first()
    desired_type = _identifier_type_for(asset)
    desired_value = normalize_identifier(asset.asset_code)
    if current_primary and (
        not desired_value
        or current_primary.normalized_value != desired_value
        or current_primary.identifier_type != desired_type
    ):
        current_primary.is_primary = False
        current_primary.is_active = False
        current_primary.valid_until = timezone.now()
        if reason:
            current_primary.change_reason = reason
        current_primary.save(update_fields=[
            'is_primary', 'is_active', 'valid_until', 'change_reason', 'updated_at',
        ])
    if desired_type and desired_value:
        identifier, _ = AssetIdentifier.objects.get_or_create(
            identifier_type=desired_type,
            normalized_value=desired_value,
            defaults={
                'asset': asset,
                'value': asset.asset_code,
                'is_primary': True,
                'is_active': True,
                'issuer': '仓库自编' if desired_type == AssetIdentifier.IdentifierType.MARKET else '公司行政/ERP',
                'change_reason': reason,
                'created_by': actor if getattr(actor, 'is_authenticated', False) else None,
            },
        )
        if identifier.asset_id != asset.id:
            raise AssetCodeError(f'主资产编码 {desired_value} 已被其他资产使用。')
        if not identifier.is_primary or not identifier.is_active:
            identifier.is_primary = True
            identifier.is_active = True
            identifier.valid_until = None
            identifier.save(update_fields=['is_primary', 'is_active', 'valid_until', 'updated_at'])

    _sync_optional_identifier(
        asset, AssetIdentifier.IdentifierType.MANUFACTURER_SN,
        asset.serial_number, actor=actor, reason=reason,
    )
    _sync_optional_identifier(
        asset, AssetIdentifier.IdentifierType.MANUFACTURER_BARCODE,
        asset.manufacturer_barcode, actor=actor, reason=reason,
    )


def _sync_optional_identifier(asset, identifier_type, value, *, actor=None, reason=''):
    normalized = normalize_identifier(value)
    current = AssetIdentifier.objects.filter(
        asset=asset, identifier_type=identifier_type, is_active=True,
    ).first()
    if current and current.normalized_value != normalized:
        current.is_active = False
        current.valid_until = timezone.now()
        current.change_reason = reason or current.change_reason
        current.save(update_fields=['is_active', 'valid_until', 'change_reason', 'updated_at'])
    if normalized and (not current or current.normalized_value != normalized):
        AssetIdentifier.objects.create(
            asset=asset,
            identifier_type=identifier_type,
            value=value.strip(),
            normalized_value=normalized,
            issuer=asset.manufacturer or '',
            change_reason=reason,
            created_by=actor if getattr(actor, 'is_authenticated', False) else None,
        )


def validate_sn(asset: Asset, *, exclude_asset_id=None) -> list[dict]:
    value = normalize_identifier(asset.serial_number)
    if value in SN_PLACEHOLDERS:
        raise ValidationError({'serial_number': '请不要用“无、未知、-、暂无”等文字代替 SN，请选择正确的 SN 状态。'})
    if asset.sn_status in {Asset.SnStatus.PROVIDED, Asset.SnStatus.MANUAL} and not value:
        raise ValidationError({'serial_number': '当前 SN 状态要求填写生产厂家 SN。'})
    if asset.sn_status in {Asset.SnStatus.NO_SN, Asset.SnStatus.PENDING} and value:
        raise ValidationError({'serial_number': '当前 SN 状态要求 SN 留空。'})
    if not value:
        return []
    matches = Asset.objects.filter(serial_number__iexact=value)
    if exclude_asset_id:
        matches = matches.exclude(pk=exclude_asset_id)
    if not matches.exists():
        return []
    same_context = matches.filter(
        manufacturer_ref_id=asset.manufacturer_ref_id,
        product_model_code_id=asset.product_model_code_id,
    ).exists()
    return [{
        'code': 'duplicate_sn_high' if same_context else 'duplicate_sn',
        'severity': 'high' if same_context else 'warning',
        'message': (
            '同生产厂家、同型号下已存在相同 SN，请核对实物标签。'
            if same_context else '系统中已有其他资产使用相同 SN，请确认后继续。'
        ),
    }]


@transaction.atomic
def apply_asset_identity(asset: Asset, *, actor, reason: str = '') -> Asset:
    if not asset.domain_id:
        raise AssetCodeError('必须选择资产归属域。')
    if asset.code_source == Asset.CodeSource.WAREHOUSE_ASSIGNED:
        # 源台账已有的 SC / DZSB / ZSB / QTZC 都是正式资产编码，与所在仓库无关。
        asset.asset_code = normalize_identifier(asset.asset_code)
        if not asset.asset_code:
            raise AssetCodeError('使用源表既有编码时必须填写资产编码。')
        if not is_valid_warehouse_assigned_code(asset.asset_code):
            raise AssetCodeError(
                '源表既有资产编码必须以 SC-、DZSB-、ZSB- 或 QTZC- 开头，'
                '且只能包含大写字母、数字和连字符。'
            )
    elif asset.code_source == Asset.CodeSource.MARKET_GENERATED:
        if not asset.asset_type_code_id or not asset.product_model_code_id:
            raise AssetCodeError('生成 SC 编码前必须选择设备类型和型号。')
        if asset.product_model_code.asset_type_code_id != asset.asset_type_code_id:
            raise AssetCodeError('型号代码不属于所选设备类型。')
        prefix = normalize_identifier(asset.product_model_code.sc_prefix)
        if not re.fullmatch(r'SC-[A-Z0-9]+(?:-[A-Z0-9]+)*', prefix):
            raise AssetCodeError('所选型号尚未维护有效的源表 SC 编码前缀。')
        if not asset.asset_code:
            asset.asset_code = allocate_asset_code(
                scheme=AssetCodeSequence.Scheme.MARKET,
                prefix=prefix,
                width=asset.product_model_code.sc_serial_width,
            )
        elif not asset.asset_code.startswith(f'{prefix}-'):
            raise AssetCodeError(f'当前资产编码不符合所选型号前缀 {prefix}。')
    elif asset.code_source == Asset.CodeSource.ADMIN_ASSIGNED:
        asset.asset_code = normalize_identifier(asset.asset_code)
        if not is_valid_admin_assigned_code(asset.asset_code):
            raise AssetCodeError('行政/ERP 资产编码必须以 DZSB-、ZSB- 或 QTZC- 开头。')
    elif asset.code_source == Asset.CodeSource.PENDING_ADMIN:
        asset.asset_code = None
    else:
        raise AssetCodeError('不支持的资产编码来源。')
    asset._skip_identifier_sync = True
    try:
        asset.save()
    finally:
        asset._skip_identifier_sync = False
    sync_asset_identifiers(asset, actor=actor, reason=reason)
    record_operation(
        actor, 'asset_identity_applied', asset,
        summary=f'设置资产身份 {asset.system_asset_no} / {asset.asset_code or "暂未赋码"}',
        after={
            'domain': asset.domain.code,
            'code_source': asset.code_source,
            'primary_code': asset.asset_code,
            'qr_value': asset.qr_value,
        },
    )
    return asset


@transaction.atomic
def change_asset_domain(
    *, asset: Asset, to_domain: InventoryDomain, actor, reason: str,
    asset_type_code=None, product_model_code=None,
    code_source: str | None = None, asset_code: str | None = None,
) -> AssetDomainChange:
    if not reason.strip():
        raise AssetCodeError('调整资产归属必须填写原因。')
    asset = Asset.objects.select_for_update(of=('self',)).select_related(
        'domain', 'asset_type_code', 'product_model_code',
    ).get(pk=asset.pk)
    if not asset.domain_id or asset.domain_id == to_domain.id:
        raise AssetCodeError('新归属域必须与当前归属域不同。')
    old_domain = asset.domain
    old_identifier = asset.identifiers.filter(is_primary=True, is_active=True).first()
    asset.domain = to_domain
    asset.asset_type_code = asset_type_code or asset.asset_type_code
    asset.product_model_code = product_model_code or asset.product_model_code
    if code_source:
        asset.code_source = code_source
        asset.asset_code = normalize_identifier(asset_code) or None
    elif not asset.asset_code and product_model_code:
        asset.code_source = Asset.CodeSource.MARKET_GENERATED
    apply_asset_identity(asset, actor=actor, reason=reason)
    new_identifier = asset.identifiers.filter(is_primary=True, is_active=True).first()
    change = AssetDomainChange.objects.create(
        asset=asset,
        from_domain=old_domain,
        to_domain=to_domain,
        old_primary_identifier=old_identifier,
        new_primary_identifier=new_identifier,
        reason=reason.strip(),
        requested_by=actor,
        approved_by=actor,
    )
    record_operation(
        actor, 'asset_domain_changed', asset,
        summary=f'资产归属调整：{old_domain.name} → {to_domain.name}',
        before={'domain': old_domain.code, 'primary_code': old_identifier.value if old_identifier else ''},
        after={
            'domain': to_domain.code,
            'primary_code': new_identifier.value if new_identifier else '',
            'asset_type_code': asset.asset_type_code.code if asset.asset_type_code_id else '',
            'product_model_code': asset.product_model_code.code if asset.product_model_code_id else '',
        },
    )
    return change
