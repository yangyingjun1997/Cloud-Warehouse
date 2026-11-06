"""资产标签打印客户端 API。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.common.audit import record_operation
from apps.common.permissions import IsAssetLabelPrinter

from .label_services import build_asset_labels, labels_to_print_payload
from .models import Asset, AssetLabelPrintJob


MAX_LABELS_PER_JOB = 500
DEFAULT_LEASE_SECONDS = 300


def _token_hash(token):
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def _lease_seconds():
    return max(int(getattr(settings, 'LABEL_PRINT_LEASE_SECONDS', DEFAULT_LEASE_SECONDS)), 60)


def _requeue_expired_jobs(now):
    return AssetLabelPrintJob.objects.filter(
        status=AssetLabelPrintJob.Status.CLAIMED,
        claim_expires_at__lt=now,
    ).update(
        status=AssetLabelPrintJob.Status.PENDING,
        claimed_by='',
        claimed_by_user=None,
        claim_token_hash='',
        claim_expires_at=None,
        last_heartbeat_at=None,
        error_message='上次打印客户端领取超时，已自动重新排队。',
    )


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def create_label_job(request):
    raw_ids = request.data.get('asset_ids') or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return Response({'detail': '请至少选择一件资产。'}, status=400)
    if len(raw_ids) > MAX_LABELS_PER_JOB:
        return Response({'detail': f'单个作业最多 {MAX_LABELS_PER_JOB} 张标签，请分批提交。'}, status=400)
    try:
        asset_ids = [UUID(str(value)) for value in raw_ids]
    except (TypeError, ValueError, AttributeError):
        return Response({'detail': '资产编号格式不正确。'}, status=400)
    if len(set(asset_ids)) != len(asset_ids):
        return Response({'detail': '资产列表中存在重复项，请重新选择。'}, status=400)

    assets_by_id = {
        asset.id: asset for asset in Asset.objects.select_related('item_type').filter(id__in=asset_ids)
    }
    missing = [str(asset_id) for asset_id in asset_ids if asset_id not in assets_by_id]
    disabled = [
        str(asset_id) for asset_id in asset_ids
        if asset_id in assets_by_id and assets_by_id[asset_id].status == Asset.Status.DISABLED
    ]
    if missing or disabled:
        return Response({
            'detail': '部分资产不存在或已停用，未创建打印作业。',
            'missing_asset_ids': missing,
            'disabled_asset_ids': disabled,
        }, status=400)
    assets = [assets_by_id[asset_id] for asset_id in asset_ids]
    labels = build_asset_labels(assets)
    job = AssetLabelPrintJob.objects.create(
        payload=labels_to_print_payload(labels),
        asset_count=len(labels),
        printer_name=str(request.data.get('printer_name') or '')[:128],
        template=str(request.data.get('template') or 'asset-label-60x40')[:64],
        requested_by=request.user,
    )
    record_operation(
        request.user, 'asset_label_job_created', job,
        summary=f'创建标签打印作业，共 {len(labels)} 张',
        after={'asset_count': len(labels), 'template': job.template},
    )
    return Response({'job_id': str(job.id), 'asset_count': job.asset_count, 'status': job.status}, status=201)


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def claim_label_job(request):
    client_name = str(request.data.get('client_name') or '').strip()[:128]
    if not client_name:
        return Response({'detail': '打印客户端名称不能为空。'}, status=400)
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=_lease_seconds())
    with transaction.atomic():
        _requeue_expired_jobs(now)
        job = (
            AssetLabelPrintJob.objects.select_for_update(skip_locked=True)
            .filter(status=AssetLabelPrintJob.Status.PENDING)
            .order_by('created_at')
            .first()
        )
        if job is None:
            return Response({'job': None})
        job.mark_claimed(client_name, request.user, _token_hash(token), expires_at)
    record_operation(
        request.user, 'asset_label_job_claimed', job,
        summary=f'打印客户端 {client_name} 领取作业',
    )
    return Response({'job': {
        'id': str(job.id),
        'template': job.template,
        'printer_name': job.printer_name,
        'asset_count': job.asset_count,
        'payload': job.payload,
        'claim_token': token,
        'claim_expires_at': expires_at.isoformat(),
    }})


def _locked_claimed_job(request, job_id):
    try:
        job = AssetLabelPrintJob.objects.select_for_update().get(id=job_id)
    except (AssetLabelPrintJob.DoesNotExist, ValueError):
        return None, Response({'detail': '作业不存在。'}, status=404)
    if job.status != AssetLabelPrintJob.Status.CLAIMED:
        return None, Response({'detail': '只有已领取且未结束的作业可以上报。'}, status=409)
    if job.claimed_by_user_id != request.user.id:
        return None, Response({'detail': '该作业由其他打印账号领取。'}, status=403)
    token = str(request.data.get('claim_token') or '')
    if not token or not secrets.compare_digest(job.claim_token_hash, _token_hash(token)):
        return None, Response({'detail': '领取令牌无效。'}, status=403)
    if not job.claim_expires_at or job.claim_expires_at <= timezone.now():
        return None, Response({'detail': '领取租约已过期，请重新领取作业。'}, status=409)
    return job, None


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def heartbeat_label_job(request, job_id):
    with transaction.atomic():
        job, error = _locked_claimed_job(request, job_id)
        if error:
            return error
        now = timezone.now()
        job.last_heartbeat_at = now
        job.claim_expires_at = now + timedelta(seconds=_lease_seconds())
        job.save(update_fields=['last_heartbeat_at', 'claim_expires_at', 'updated_at'])
    return Response({'job_id': str(job.id), 'claim_expires_at': job.claim_expires_at.isoformat()})


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def finish_label_job(request, job_id):
    succeeded = request.data.get('success') is True
    message = str(request.data.get('message') or '')
    with transaction.atomic():
        job, error = _locked_claimed_job(request, job_id)
        if error:
            return error
        if succeeded:
            job.mark_printed()
            event, summary = 'asset_label_job_printed', f'标签打印完成，共 {job.asset_count} 张'
        else:
            job.mark_failed(message or '打印客户端未提供失败原因。')
            event, summary = 'asset_label_job_failed', f'标签打印失败：{job.error_message[:80]}'
    record_operation(request.user, event, job, summary=summary)
    return Response({'job_id': str(job.id), 'status': job.status})


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def retry_label_job(request, job_id):
    with transaction.atomic():
        job = AssetLabelPrintJob.objects.select_for_update().filter(id=job_id).first()
        if not job:
            return Response({'detail': '作业不存在。'}, status=404)
        if job.status != AssetLabelPrintJob.Status.FAILED:
            return Response({'detail': '只有打印失败的作业可以重新排队。'}, status=409)
        job.status = AssetLabelPrintJob.Status.PENDING
        job.claimed_by = ''
        job.claimed_by_user = None
        job.claim_token_hash = ''
        job.claim_expires_at = None
        job.last_heartbeat_at = None
        job.finished_at = None
        job.save(update_fields=[
            'status', 'claimed_by', 'claimed_by_user', 'claim_token_hash',
            'claim_expires_at', 'last_heartbeat_at', 'finished_at', 'updated_at',
        ])
    record_operation(request.user, 'asset_label_job_retried', job, summary='打印失败作业重新排队')
    return Response({'job_id': str(job.id), 'status': job.status})


@api_view(['POST'])
@permission_classes([IsAssetLabelPrinter])
def cancel_label_job(request, job_id):
    with transaction.atomic():
        job = AssetLabelPrintJob.objects.select_for_update().filter(id=job_id).first()
        if not job:
            return Response({'detail': '作业不存在。'}, status=404)
        if job.status not in {AssetLabelPrintJob.Status.PENDING, AssetLabelPrintJob.Status.FAILED}:
            return Response({'detail': '只有待打印或失败的作业可以取消。'}, status=409)
        job.status = AssetLabelPrintJob.Status.CANCELLED
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'finished_at', 'updated_at'])
    record_operation(request.user, 'asset_label_job_cancelled', job, summary='取消标签打印作业')
    return Response({'job_id': str(job.id), 'status': job.status})


@api_view(['GET'])
@permission_classes([IsAssetLabelPrinter])
def label_job_status(request):
    _requeue_expired_jobs(timezone.now())
    pending = AssetLabelPrintJob.objects.filter(status=AssetLabelPrintJob.Status.PENDING).count()
    claimed = AssetLabelPrintJob.objects.filter(status=AssetLabelPrintJob.Status.CLAIMED).count()
    recent = list(AssetLabelPrintJob.objects.order_by('-created_at').values(
        'id', 'status', 'asset_count', 'claimed_by', 'attempt_count',
        'created_at', 'finished_at', 'error_message',
    )[:10])
    for row in recent:
        row['id'] = str(row['id'])
        row['created_at'] = row['created_at'].isoformat() if row['created_at'] else None
        row['finished_at'] = row['finished_at'].isoformat() if row['finished_at'] else None
    return Response({'pending': pending, 'claimed': claimed, 'recent': recent})
