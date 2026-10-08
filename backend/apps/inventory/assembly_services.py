from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.audit import record_operation

from .lifecycle_services import record_lifecycle_event
from .models import Asset, AssetLifecycleEvent, ComponentLink, CompositeUnit


class AssemblyError(Exception):
    pass


# 可作为组件装入成品的资产状态：只有在库的空闲资产可被装入。
ASSEMBLABLE_STATUSES = {Asset.Status.IN_STOCK}
# 允许组件变更（加件/换装/拆散）的成品状态：出库后一律冻结配置。
UNIT_EDITABLE_STATUSES = {CompositeUnit.Status.DRAFT, CompositeUnit.Status.ASSEMBLED}

TERMINAL_ASSET_STATUSES = {
    Asset.Status.SOLD,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
    Asset.Status.CONVERTED_TO_STOCK,
}


def _label(asset) -> str:
    return asset.asset_code or f'未编号·{asset.name}'


def _lock_assets(asset_ids):
    asset_ids = list(dict.fromkeys(asset_ids))
    assets = list(Asset.objects.select_for_update().filter(pk__in=asset_ids))
    if len(assets) != len(asset_ids):
        raise AssemblyError('存在无效或已删除的子资产，请刷新后重试。')
    return assets


def _check_assemblable(asset):
    if asset.status in TERMINAL_ASSET_STATUSES:
        raise AssemblyError(f'资产 {_label(asset)} 已处于最终状态，不能用于组装。')
    if asset.status == Asset.Status.ASSEMBLED:
        link = ComponentLink.objects.filter(asset=asset, disassembled_at__isnull=True).select_related('composite_unit').first()
        raise AssemblyError(f'资产 {_label(asset)} 已组装在成品设备 {link.composite_unit if link else ""} 中。')
    if asset.status not in ASSEMBLABLE_STATUSES:
        raise AssemblyError(f'资产 {_label(asset)} 当前状态为{asset.get_status_display()}，只有在库资产可用于组装。')


def _check_unit_editable(unit):
    if unit.status == CompositeUnit.Status.DISASSEMBLED:
        raise AssemblyError('该成品设备已拆散，不能再变更组件。')
    if unit.status not in UNIT_EDITABLE_STATUSES:
        raise AssemblyError(f'该成品设备当前为{unit.get_status_display()}状态，组件配置已冻结，请先归还入库。')


@transaction.atomic
def create_unit(*, actor, name, unit_code='', model='', customer=None, remarks=''):
    """创建成品空壳（待组装），之后可分批装入组件。"""
    if not name.strip():
        raise AssemblyError('成品名称不能为空。')
    unit = CompositeUnit.objects.create(
        unit_code=unit_code.strip() or None,
        name=name.strip(),
        model=model.strip(),
        customer=customer,
        remarks=remarks.strip(),
        status=CompositeUnit.Status.DRAFT,
    )
    record_operation(
        actor,
        'composite_unit_created',
        unit,
        summary=f'创建成品设备空壳 {unit}',
        after={'unit_code': unit.unit_code, 'name': unit.name},
    )
    return unit


@transaction.atomic
def add_components(*, unit, asset_ids, actor, note=''):
    """向成品装入组件（可多次调用）。组件立即锁定为"已组装"。"""
    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    _check_unit_editable(unit)
    assets = _lock_assets(asset_ids)
    if not assets:
        raise AssemblyError('请至少选择一件子资产。')
    for asset in assets:
        _check_assemblable(asset)

    now = timezone.now()
    links = []
    for asset in assets:
        asset.status = Asset.Status.ASSEMBLED
        asset.save(update_fields=['status', 'updated_at'])
        links.append(ComponentLink.objects.create(
            composite_unit=unit,
            asset=asset,
            assembled_at=now,
            note=note,
        ))
        record_lifecycle_event(
            asset,
            AssetLifecycleEvent.EventType.NOTE,
            f'装入成品设备 {unit}',
            actor=actor,
            description=note,
            related=unit,
        )
    if unit.status == CompositeUnit.Status.DRAFT:
        unit.status = CompositeUnit.Status.ASSEMBLED
        unit.save(update_fields=['status', 'updated_at'])
    record_operation(
        actor,
        'composite_components_added',
        unit,
        summary=f'成品设备 {unit} 装入 {len(links)} 件组件',
        after={'asset_ids': [link.asset_id for link in links]},
    )
    return links


@transaction.atomic
def remove_component(*, unit, asset_id, actor, note=''):
    """从成品拆出单个组件（仅限未出库的成品）。组件回到在库。"""
    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    _check_unit_editable(unit)
    link = ComponentLink.objects.select_for_update().filter(
        composite_unit=unit, asset_id=asset_id, disassembled_at__isnull=True,
    ).select_related('asset').first()
    if link is None:
        raise AssemblyError('该资产不在此成品设备的当前组件中。')

    asset = Asset.objects.select_for_update().get(pk=link.asset_id)
    link.disassembled_at = timezone.now()
    link.note = f'{link.note}｜拆出：{note}'.strip('｜') if note else link.note
    link.save(update_fields=['disassembled_at', 'note', 'updated_at'])
    asset.status = Asset.Status.IN_STOCK
    asset.save(update_fields=['status', 'updated_at'])
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.NOTE,
        f'从成品设备 {unit} 拆出',
        actor=actor,
        description=note,
        related=unit,
    )
    record_operation(
        actor,
        'composite_component_removed',
        unit,
        summary=f'成品设备 {unit} 拆出组件 {_label(asset)}',
        after={'asset_id': asset.id, 'note': note},
    )
    return asset


@transaction.atomic
def swap_component(*, unit, old_asset_id, new_asset_id, actor, reason=''):
    """换装：未出库成品中原子替换一个组件（旧件回在库，新件装入）。"""
    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    _check_unit_editable(unit)
    if old_asset_id == new_asset_id:
        raise AssemblyError('换装的新旧组件不能是同一件资产。')

    old_link = ComponentLink.objects.select_for_update().filter(
        composite_unit=unit, asset_id=old_asset_id, disassembled_at__isnull=True,
    ).first()
    if old_link is None:
        raise AssemblyError('旧组件不在此成品设备的当前组件中。')
    new_asset = Asset.objects.select_for_update().get(pk=new_asset_id)
    _check_assemblable(new_asset)

    old_asset = Asset.objects.select_for_update().get(pk=old_asset_id)
    now = timezone.now()
    old_link.disassembled_at = now
    old_link.note = f'{old_link.note}｜换装拆出：{reason}'.strip('｜') if reason else old_link.note
    old_link.save(update_fields=['disassembled_at', 'note', 'updated_at'])
    old_asset.status = Asset.Status.IN_STOCK
    old_asset.save(update_fields=['status', 'updated_at'])

    new_asset.status = Asset.Status.ASSEMBLED
    new_asset.save(update_fields=['status', 'updated_at'])
    new_link = ComponentLink.objects.create(
        composite_unit=unit,
        asset=new_asset,
        assembled_at=now,
        note=f'换装装入：{reason}' if reason else '',
    )
    for asset, title in (
        (old_asset, f'换装拆出（成品 {unit}）'),
        (new_asset, f'换装装入（成品 {unit}）'),
    ):
        record_lifecycle_event(
            asset,
            AssetLifecycleEvent.EventType.NOTE,
            title,
            actor=actor,
            description=reason,
            related=unit,
        )
    record_operation(
        actor,
        'composite_component_swapped',
        unit,
        summary=f'成品设备 {unit} 换装：{_label(old_asset)} → {_label(new_asset)}',
        before={'old_asset_id': old_asset.id},
        after={'new_asset_id': new_asset.id, 'reason': reason},
    )
    return old_asset, new_link


@transaction.atomic
def delete_unit(*, unit, actor, reason='', force=False):
    """删除成品设备。仅允许删除"待组装"（空壳）或"已拆散"的成品。

    安全约束：有组件关联（含历史）或有关联申请单的成品禁止删除，
    防止误删在途/已出库资产以及审批履历断链。删除前写审计留档。

    force=True 仅供系统管理员清理测试数据：跳过履历/申请单检查，
    并把仍在装的组件强制释放回在库（避免资产卡在"已组装/已借出"状态）。
    """
    from apps.common.permissions import is_system_administrator

    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    if force and not is_system_administrator(actor):
        raise AssemblyError('只有系统管理员可以强制删除成品设备。')
    if not force and unit.status not in {CompositeUnit.Status.DRAFT, CompositeUnit.Status.DISASSEMBLED}:
        raise AssemblyError(f'该成品设备当前为{unit.get_status_display()}状态，只有待组装或已拆散的成品才能删除。')
    active_links = list(
        unit.component_links.filter(disassembled_at__isnull=True).select_related('asset')
    )
    if active_links and not force:
        raise AssemblyError('该成品设备还有组件，请先拆散或拆出后再删除。')
    if not force and unit.component_links.exists():
        raise AssemblyError('该成品设备存在历史组件履历，不能删除。')

    from apps.workflow.models import WorkflowRequest
    linked_request_nos = list(
        WorkflowRequest.objects.filter(composite_unit=unit)
        .order_by('-created_at').values_list('request_no', flat=True)[:20]
    )
    if linked_request_nos and not force:
        raise AssemblyError('该成品设备存在关联申请单，不能删除（避免审批履历断链）。')

    released_assets = []
    if force and active_links:
        now = timezone.now()
        for link in active_links:
            asset = Asset.objects.select_for_update().get(pk=link.asset_id)
            link.disassembled_at = now
            link.note = (link.note + ' ' if link.note else '') + '强制删除成品时释放。'
            link.save(update_fields=['disassembled_at', 'note', 'updated_at'])
            before_status = asset.status
            asset.status = Asset.Status.IN_STOCK
            asset.current_holder = None
            asset.department = None
            asset.external_holder_name = asset.external_holder_company = asset.external_holder_phone = ''
            asset.save(update_fields=['status', 'current_holder', 'department', 'external_holder_name', 'external_holder_company', 'external_holder_phone', 'updated_at'])
            record_lifecycle_event(
                asset,
                AssetLifecycleEvent.EventType.NOTE,
                f'强制删除成品（{unit}）时释放',
                actor=actor,
                description=reason,
                related=unit,
            )
            released_assets.append({'asset_code': asset.asset_code, 'name': asset.name, 'status_before': before_status})

    label = str(unit)
    history_link_count = unit.component_links.count()
    record_operation(
        actor,
        'composite_unit_deleted',
        unit,
        summary=f'{"强制删除" if force else "删除"}成品设备 {label}（{unit.get_status_display()}）',
        before={
            'unit_code': unit.unit_code,
            'name': unit.name,
            'model': unit.model,
            'status': unit.status,
            'reason': reason,
            'force': force,
            'history_link_count': history_link_count,
            'released_assets': released_assets,
            'linked_request_nos': linked_request_nos,
        },
    )
    unit.delete()
    return label


@transaction.atomic
def disassemble_unit(*, unit, actor, reason=''):
    """拆散成品：全部组件回在库，履历标失效，成品编码作废留档。"""
    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    _check_unit_editable(unit)
    active_links = list(
        ComponentLink.objects.select_for_update()
        .filter(composite_unit=unit, disassembled_at__isnull=True)
        .select_related('asset')
    )
    now = timezone.now()
    for link in active_links:
        asset = Asset.objects.select_for_update().get(pk=link.asset_id)
        link.disassembled_at = now
        link.save(update_fields=['disassembled_at', 'updated_at'])
        asset.status = Asset.Status.IN_STOCK
        asset.save(update_fields=['status', 'updated_at'])
        record_lifecycle_event(
            asset,
            AssetLifecycleEvent.EventType.NOTE,
            f'从成品设备 {unit} 拆散',
            actor=actor,
            description=reason,
            related=unit,
        )
    unit.status = CompositeUnit.Status.DISASSEMBLED
    unit.save(update_fields=['status', 'updated_at'])
    record_operation(
        actor,
        'composite_disassembled',
        unit,
        summary=f'成品设备 {unit} 已拆散，编码作废',
        after={'reason': reason, 'released_asset_count': len(active_links)},
    )
    return unit


@transaction.atomic
def create_unit_outbound_request(*, unit, request_type, actor, usage_location='', reason='',
                                 recipient_name='', recipient_company='', recipient_phone='',
                                 expected_return_date=None, customer=None):
    """成品出库审批：整台的组件随申请一并出库。"""
    from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
    from apps.workflow.services import submit_request

    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    if request_type not in {WorkflowRequest.RequestType.BORROW, WorkflowRequest.RequestType.SALE}:
        raise AssemblyError('成品出库仅支持借用或售出。')
    if unit.status != CompositeUnit.Status.ASSEMBLED:
        raise AssemblyError(f'该成品设备当前为{unit.get_status_display()}状态，不能出库。')
    active_links = list(unit.component_links.filter(disassembled_at__isnull=True))
    if not active_links:
        raise AssemblyError('该成品设备还没有组件，不能出库。')
    existing = WorkflowRequest.objects.filter(
        composite_unit=unit,
        request_type__in=[WorkflowRequest.RequestType.BORROW, WorkflowRequest.RequestType.SALE],
        status__in=[WorkflowRequest.Status.PENDING, WorkflowRequest.Status.APPROVED, WorkflowRequest.Status.WAITING_WAREHOUSE],
    ).first()
    if existing:
        raise AssemblyError(f'该成品设备已有在途出库申请 {existing.request_no}。')

    request_obj = WorkflowRequest.objects.create(
        request_type=request_type,
        applicant=actor,
        department=getattr(getattr(actor, 'profile', None), 'department', None),
        reason=reason or f'成品设备出库：{unit}',
        status=WorkflowRequest.Status.DRAFT,
        composite_unit=unit,
        usage_location=usage_location,
        recipient_name=recipient_name,
        recipient_company=recipient_company,
        recipient_phone=recipient_phone,
        expected_return_date=expected_return_date,
        customer_ref=customer or unit.customer,
    )
    for link in active_links:
        WorkflowRequestLine.objects.create(request=request_obj, asset_id=link.asset_id, quantity=1)
    submit_request(request_obj, actor)
    record_operation(
        actor,
        'composite_outbound_requested',
        request_obj,
        summary=f'发起成品出库审批 {request_obj.request_no}：{unit}（{len(active_links)} 件组件）',
        after={'composite_unit_id': unit.id, 'request_type': request_type},
    )
    return request_obj


@transaction.atomic
def create_unit_return_request(*, unit, actor, reason='', target_warehouse=None, target_location=None):
    """成品归还审批：整台的组件随申请一并回库（回到"已组装"）。"""
    from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
    from apps.workflow.services import submit_request

    unit = CompositeUnit.objects.select_for_update().get(pk=unit.pk)
    if unit.status != CompositeUnit.Status.BORROWED:
        raise AssemblyError(f'该成品设备当前为{unit.get_status_display()}状态，只有已借出的成品可归还。')
    # 只纳入仍在借出状态的组件（已收回的不重复归还），支持多次部分归还。
    active_links = [
        link for link in unit.component_links.filter(disassembled_at__isnull=True).select_related('asset')
        if link.asset and link.asset.status == Asset.Status.BORROWED
    ]
    if not active_links:
        raise AssemblyError('该成品设备当前没有仍在借出的组件，无法归还（可能已全部收回）。')
    existing = WorkflowRequest.objects.filter(
        composite_unit=unit,
        request_type=WorkflowRequest.RequestType.RETURN,
        status__in=[WorkflowRequest.Status.PENDING, WorkflowRequest.Status.APPROVED, WorkflowRequest.Status.WAITING_WAREHOUSE],
    ).first()
    if existing:
        raise AssemblyError(f'该成品设备已有在途归还申请 {existing.request_no}。')

    request_obj = WorkflowRequest.objects.create(
        request_type=WorkflowRequest.RequestType.RETURN,
        applicant=actor,
        department=getattr(getattr(actor, 'profile', None), 'department', None),
        reason=reason or f'成品设备归还：{unit}',
        status=WorkflowRequest.Status.DRAFT,
        composite_unit=unit,
        target_warehouse=target_warehouse,
        target_location=target_location,
    )
    for link in active_links:
        WorkflowRequestLine.objects.create(request=request_obj, asset_id=link.asset_id, quantity=1)
    submit_request(request_obj, actor)
    record_operation(
        actor,
        'composite_return_requested',
        request_obj,
        summary=f'发起成品归还审批 {request_obj.request_no}：{unit}（{len(active_links)} 件组件）',
        after={'composite_unit_id': unit.id},
    )
    return request_obj
