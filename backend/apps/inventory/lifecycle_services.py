from __future__ import annotations

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.common.audit import record_operation
from apps.common.permissions import is_system_administrator
from apps.common.upload_validation import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    IMAGE_SUFFIXES,
    sanitize_image_upload,
    validate_attachment_upload,
)

from .models import (
    Asset,
    AssetAttachment,
    AssetHandover,
    AssetLifecycleEvent,
    MaintenanceRecord,
)


class AssetLifecycleError(Exception):
    pass


TERMINAL_ASSET_STATUSES = {
    Asset.Status.SOLD,
    Asset.Status.RETURNED_TO_VENDOR,
    Asset.Status.SCRAPPED,
    Asset.Status.CONVERTED_TO_STOCK,
}


def record_lifecycle_event(
    asset,
    event_type,
    title,
    *,
    actor=None,
    description='',
    related=None,
    event_at=None,
):
    return AssetLifecycleEvent.objects.create(
        asset=asset,
        event_type=event_type,
        title=title,
        description=description,
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        event_at=event_at or timezone.now(),
        related_model=related._meta.label_lower if related else '',
        related_object_id=str(related.pk) if related else '',
    )


def _restore_asset_after_maintenance(asset, previous_status):
    if previous_status in {
        Asset.Status.IN_STOCK,
        Asset.Status.BORROWED,
        Asset.Status.ISSUED,
    }:
        return previous_status
    if asset.current_holder_id or asset.external_holder_name:
        return Asset.Status.BORROWED
    return Asset.Status.IN_STOCK


@transaction.atomic
def create_maintenance_record(*, asset, actor, activate_asset=True, **values):
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if asset.status in TERMINAL_ASSET_STATUSES:
        raise AssetLifecycleError('该资产已处于最终状态，不能新建维修单。')
    if asset.maintenance_records.filter(
        status__in=[
            MaintenanceRecord.Status.OPEN,
            MaintenanceRecord.Status.DIAGNOSING,
            MaintenanceRecord.Status.SENT_OUT,
            MaintenanceRecord.Status.WAITING_PARTS,
            MaintenanceRecord.Status.REPAIRING,
        ],
    ).exists():
        raise AssetLifecycleError('该资产已有未完成的维修单。')
    previous_status = asset.status
    record = MaintenanceRecord.objects.create(
        asset=asset,
        previous_asset_status=previous_status,
        created_by=actor,
        **values,
    )
    if activate_asset and asset.status != Asset.Status.REPAIRING:
        asset.status = Asset.Status.REPAIRING
        asset.save(update_fields=['status', 'updated_at'])
    record_operation(
        actor,
        'asset_maintenance_created',
        record,
        summary=f'新建维修单 {record.maintenance_no}',
        after={
            'asset_id': asset.id,
            'repair_type': record.repair_type,
            'status': record.status,
            'previous_asset_status': previous_status,
        },
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.MAINTENANCE,
        f'新建维修单 {record.maintenance_no}',
        actor=actor,
        description=record.fault_description,
        related=record,
    )
    return record


@transaction.atomic
def ensure_maintenance_from_workflow(*, asset, request_obj, actor, previous_status):
    record, created = MaintenanceRecord.objects.get_or_create(
        asset=asset,
        source_request=request_obj,
        defaults={
            'repair_type': MaintenanceRecord.RepairType.INTERNAL,
            'status': MaintenanceRecord.Status.OPEN,
            'fault_description': request_obj.reason or '由维修申请转入，待补充故障描述。',
            'previous_asset_status': previous_status,
            'responsible_user': actor,
            'created_by': actor,
        },
    )
    if created:
        record_operation(
            actor,
            'asset_maintenance_created_from_workflow',
            record,
            summary=f'申请 {request_obj.request_no} 转为维修单 {record.maintenance_no}',
            after={'asset_id': asset.id, 'source_request_id': request_obj.id},
        )
        record_lifecycle_event(
            asset,
            AssetLifecycleEvent.EventType.MAINTENANCE,
            f'维修申请转入 {record.maintenance_no}',
            actor=actor,
            description=f'关联申请单 {request_obj.request_no}。{record.fault_description}',
            related=record,
        )
    return record


@transaction.atomic
def update_maintenance_record(record, actor, **changes):
    record = MaintenanceRecord.objects.select_for_update().select_related('asset').get(pk=record.pk)
    if record.status in {MaintenanceRecord.Status.COMPLETED, MaintenanceRecord.Status.CANCELED}:
        raise AssetLifecycleError('已完成或已取消的维修单不能继续修改。')
    requested_status = changes.pop('status', record.status)
    if requested_status in {MaintenanceRecord.Status.COMPLETED, MaintenanceRecord.Status.CANCELED}:
        raise AssetLifecycleError('完成或取消维修请使用对应操作按钮。')
    allowed_fields = {
        'repair_type', 'fault_description', 'diagnosis', 'resolution', 'service_provider_ref', 'service_provider',
        'rma_no', 'outbound_tracking_no', 'return_tracking_no', 'sent_date',
        'expected_return_date', 'returned_date', 'estimated_cost', 'actual_cost',
        'is_under_warranty', 'responsible_user',
    }
    before = {'status': record.status}
    after = {'status': requested_status}
    record.status = requested_status
    for field, value in changes.items():
        if field not in allowed_fields:
            continue
        before[field] = getattr(record, field)
        setattr(record, field, value)
        after[field] = value
    record.save()
    record_operation(
        actor,
        'asset_maintenance_updated',
        record,
        summary=f'更新维修单 {record.maintenance_no}：{record.get_status_display()}',
        before=before,
        after=after,
    )
    record_lifecycle_event(
        record.asset,
        AssetLifecycleEvent.EventType.MAINTENANCE,
        f'维修进度：{record.get_status_display()}',
        actor=actor,
        description=record.diagnosis or record.resolution,
        related=record,
    )
    return record


@transaction.atomic
def finish_maintenance_record(record, actor, *, resolution='', actual_cost=None, returned_date=None, cancel=False):
    record = MaintenanceRecord.objects.select_for_update().select_related('asset').get(pk=record.pk)
    asset = Asset.objects.select_for_update().get(pk=record.asset_id)
    if record.status in {MaintenanceRecord.Status.COMPLETED, MaintenanceRecord.Status.CANCELED}:
        raise AssetLifecycleError('该维修单已经结束。')
    before_status = record.status
    if resolution:
        record.resolution = resolution
    if actual_cost is not None:
        record.actual_cost = actual_cost
    if returned_date:
        record.returned_date = returned_date
    record.status = MaintenanceRecord.Status.CANCELED if cancel else MaintenanceRecord.Status.COMPLETED
    record.completed_by = actor
    record.completed_at = timezone.now()
    record.save()
    asset_before = asset.status
    if asset.status == Asset.Status.REPAIRING:
        asset.status = _restore_asset_after_maintenance(asset, record.previous_asset_status)
        asset.save(update_fields=['status', 'updated_at'])
    action_text = '取消' if cancel else '完成'
    record_operation(
        actor,
        'asset_maintenance_canceled' if cancel else 'asset_maintenance_completed',
        record,
        summary=f'{action_text}维修单 {record.maintenance_no}',
        before={'status': before_status, 'asset_status': asset_before},
        after={'status': record.status, 'asset_status': asset.status, 'actual_cost': record.actual_cost},
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.MAINTENANCE,
        f'{action_text}维修单 {record.maintenance_no}',
        actor=actor,
        description=record.resolution,
        related=record,
    )
    return record


@transaction.atomic
def create_handover(*, asset, actor, **values):
    handover = AssetHandover.objects.create(asset=asset, created_by=actor, **values)
    record_operation(
        actor,
        'asset_handover_created',
        handover,
        summary=f'新建交接记录 {handover.handover_no}',
        after={'asset_id': asset.id, 'to_holder_name': handover.to_holder_name},
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.HANDOVER,
        f'发起交接 {handover.handover_no}',
        actor=actor,
        description=f'{handover.from_holder_name or "未填写"} -> {handover.to_holder_name}',
        related=handover,
    )
    return handover


@transaction.atomic
def finish_handover(handover, actor, *, cancel=False):
    handover = AssetHandover.objects.select_for_update().select_related('asset').get(pk=handover.pk)
    if handover.status != AssetHandover.Status.PENDING:
        raise AssetLifecycleError('该交接记录已经处理。')
    if (
        not cancel
        and handover.to_user_id
        and handover.to_user_id != actor.id
        and not is_system_administrator(actor)
    ):
        raise AssetLifecycleError('该交接记录已指定其他接收人确认。')
    handover.status = AssetHandover.Status.CANCELED if cancel else AssetHandover.Status.CONFIRMED
    handover.confirmed_by = actor
    handover.confirmed_at = timezone.now()
    handover.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'updated_at'])
    action_text = '取消' if cancel else '确认'
    record_operation(
        actor,
        'asset_handover_canceled' if cancel else 'asset_handover_confirmed',
        handover,
        summary=f'{action_text}交接 {handover.handover_no}',
        after={'status': handover.status},
    )
    record_lifecycle_event(
        handover.asset,
        AssetLifecycleEvent.EventType.HANDOVER,
        f'{action_text}交接 {handover.handover_no}',
        actor=actor,
        description=f'接收人：{handover.to_holder_name}',
        related=handover,
    )
    return handover


@transaction.atomic
def archive_asset_attachment(*, asset, actor, **values):
    maintenance = values.get('maintenance')
    handover = values.get('handover')
    if maintenance and maintenance.asset_id != asset.id:
        raise AssetLifecycleError('维修单与当前资产不匹配。')
    if handover and handover.asset_id != asset.id:
        raise AssetLifecycleError('交接记录与当前资产不匹配。')
    uploaded_file = values.get('file')
    try:
        validate_attachment_upload(uploaded_file, ALLOWED_ATTACHMENT_SUFFIXES)
        if Path(uploaded_file.name or '').suffix.lower() in IMAGE_SUFFIXES:
            values['file'] = sanitize_image_upload(uploaded_file, max_size=20 * 1024 * 1024)
    except ValidationError as exc:
        raise AssetLifecycleError('附件校验失败：' + ' '.join(exc.messages)) from exc
    attachment = AssetAttachment.objects.create(asset=asset, uploaded_by=actor, **values)
    record_operation(
        actor,
        'asset_attachment_archived',
        attachment,
        summary=f'归档资产附件 {attachment.display_name}',
        after={'asset_id': asset.id, 'category': attachment.category, 'file': attachment.file.name},
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.ATTACHMENT,
        f'归档附件：{attachment.display_name}',
        actor=actor,
        description=attachment.note,
        related=attachment,
    )
    return attachment


@transaction.atomic
def create_scrap_approval_request(*, asset, actor, reason):
    from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
    from apps.workflow.services import submit_request

    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if asset.status in TERMINAL_ASSET_STATUSES:
        raise AssetLifecycleError('该资产已处于最终状态，不能再次发起报废审批。')
    existing = WorkflowRequest.objects.filter(
        lines__asset=asset,
        request_type=WorkflowRequest.RequestType.SCRAP,
        status__in=[WorkflowRequest.Status.PENDING, WorkflowRequest.Status.WAITING_WAREHOUSE],
    ).first()
    if existing:
        raise AssetLifecycleError(f'该资产已有待处理的报废申请 {existing.request_no}。')
    request_obj = WorkflowRequest.objects.create(
        request_type=WorkflowRequest.RequestType.SCRAP,
        applicant=actor,
        department=getattr(getattr(actor, 'profile', None), 'department', None),
        reason=reason,
        status=WorkflowRequest.Status.DRAFT,
    )
    WorkflowRequestLine.objects.create(request=request_obj, asset=asset, quantity=1)
    submit_request(request_obj, actor)
    record_operation(
        actor,
        'asset_scrap_approval_requested',
        request_obj,
        summary=f'为资产 {asset.asset_code} 发起报废审批 {request_obj.request_no}',
        after={'asset_id': asset.id, 'reason': reason},
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.SCRAP_REQUEST,
        f'发起报废审批 {request_obj.request_no}',
        actor=actor,
        description=reason,
        related=request_obj,
    )
    return request_obj


@transaction.atomic
def create_damage_approval_request(*, asset, actor, reason):
    """为资产发起报损审批（例如归还验收差异中确认无法收回的组件）。"""
    from apps.workflow.models import WorkflowRequest, WorkflowRequestLine
    from apps.workflow.services import submit_request

    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if asset.status in TERMINAL_ASSET_STATUSES:
        raise AssetLifecycleError('该资产已处于最终状态，不能再次发起报损审批。')
    existing = WorkflowRequest.objects.filter(
        lines__asset=asset,
        request_type=WorkflowRequest.RequestType.DAMAGE,
        status__in=[WorkflowRequest.Status.PENDING, WorkflowRequest.Status.WAITING_WAREHOUSE],
    ).first()
    if existing:
        raise AssetLifecycleError(f'该资产已有待处理的报损申请 {existing.request_no}。')
    request_obj = WorkflowRequest.objects.create(
        request_type=WorkflowRequest.RequestType.DAMAGE,
        applicant=actor,
        department=getattr(getattr(actor, 'profile', None), 'department', None),
        reason=reason,
        status=WorkflowRequest.Status.DRAFT,
    )
    WorkflowRequestLine.objects.create(request=request_obj, asset=asset, quantity=1)
    submit_request(request_obj, actor)
    record_operation(
        actor,
        'asset_damage_approval_requested',
        request_obj,
        summary=f'为资产 {asset.asset_code} 发起报损审批 {request_obj.request_no}',
        after={'asset_id': asset.id, 'reason': reason},
    )
    record_lifecycle_event(
        asset,
        AssetLifecycleEvent.EventType.NOTE,
        f'发起报损审批 {request_obj.request_no}',
        actor=actor,
        description=reason,
        related=request_obj,
    )
    return request_obj
