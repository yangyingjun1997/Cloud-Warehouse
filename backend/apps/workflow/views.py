from datetime import date

from django.db import OperationalError
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.permissions import IsWarehouseOperator
from apps.common.database import retry_database_operation
from .models import ApprovalLog, ApprovalTask, InventoryTransaction, WorkflowRequest, WorkflowRequestLine
from .serializers import (
    ApprovalLogSerializer,
    ApprovalTaskSerializer,
    InventoryTransactionSerializer,
    WorkflowRequestLineSerializer,
    WorkflowRequestSerializer,
)
from .services import approve_request, hide_request_for_applicant, is_operator, process_request, reject_request, submit_request, withdraw_request, WorkflowError


def visible_request_queryset(queryset, user):
    """Limit workflow data to an applicant's own records and actionable warehouse tasks."""
    if is_operator(user):
        return queryset.filter(
            Q(applicant=user, is_hidden_by_applicant=False)
            | Q(status=WorkflowRequest.Status.WAITING_WAREHOUSE)
            | Q(approval_tasks__assigned_to__isnull=True, status=WorkflowRequest.Status.PENDING)
            | Q(approval_tasks__assigned_to=user)
        ).distinct()
    return queryset.filter(applicant=user, is_hidden_by_applicant=False)


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _filter_requests(queryset, params):
    query = params.get('q', '').strip()
    if query:
        queryset = queryset.filter(
            Q(request_no__icontains=query)
            | Q(inventory_no__icontains=query)
            | Q(reason__icontains=query)
            | Q(recipient_name__icontains=query)
            | Q(project_code__icontains=query)
            | Q(work_order_no__icontains=query)
            | Q(lines__asset__search_index__icontains=query)
            | Q(lines__stock_item__name__icontains=query)
            | Q(lines__stock_item__code__icontains=query)
        )
    for field in ('request_type', 'status'):
        if params.get(field):
            queryset = queryset.filter(**{field: params[field]})
    if params.get('warehouse'):
        queryset = queryset.filter(
            Q(target_warehouse_id=params['warehouse'])
            | Q(lines__asset__warehouse_id=params['warehouse'])
            | Q(lines__stock_item__warehouse_id=params['warehouse'])
        )
    for param, lookup in (
        ('application_start', 'application_date__gte'),
        ('application_end', 'application_date__lte'),
        ('return_start', 'expected_return_date__gte'),
        ('return_end', 'expected_return_date__lte'),
    ):
        parsed = _parse_date(params.get(param, ''))
        if parsed:
            queryset = queryset.filter(**{lookup: parsed})
    return queryset.distinct()


class WorkflowRequestViewSet(viewsets.ModelViewSet):
    queryset = WorkflowRequest.objects.select_related('applicant', 'department', 'designated_approver', 'target_warehouse', 'target_location').prefetch_related(
        'lines__asset__warehouse',
        'lines__asset__location',
        'lines__stock_item__warehouse',
        'lines__stock_item__location',
        'approval_tasks__assigned_to',
        'approval_tasks__approver',
        'approval_logs__actor',
    ).all()
    serializer_class = WorkflowRequestSerializer

    def get_permissions(self):
        if self.action in {'approve', 'reject', 'process'}:
            return [IsWarehouseOperator()]
        return [IsAuthenticated()]

    def get_queryset(self):
        return _filter_requests(visible_request_queryset(super().get_queryset(), self.request.user), self.request.query_params)

    def perform_create(self, serializer):
        department = getattr(getattr(self.request.user, 'profile', None), 'department', None)
        serializer.save(applicant=self.request.user, department=department)

    def perform_update(self, serializer):
        if self.get_object().status != WorkflowRequest.Status.DRAFT:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('仅草稿状态的申请可以修改。')
        serializer.save()

    def _service_response(self, service, request, *, comment=''):
        try:
            obj = retry_database_operation(lambda: service(self.get_object(), request.user, comment))
        except WorkflowError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except OperationalError:
            return Response({'detail': '数据库正在处理其他操作，请稍后重试。'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        return self._service_response(submit_request, request)

    @action(detail=True, methods=['post'])
    def withdraw(self, request, pk=None):
        return self._service_response(withdraw_request, request, comment=request.data.get('comment', ''))

    @action(detail=True, methods=['post'], url_path='hide')
    def hide(self, request, pk=None):
        return self._service_response(hide_request_for_applicant, request)

    @action(detail=True, methods=['post'], permission_classes=[IsWarehouseOperator])
    def approve(self, request, pk=None):
        return self._service_response(approve_request, request, comment=request.data.get('comment', ''))

    @action(detail=True, methods=['post'], permission_classes=[IsWarehouseOperator])
    def reject(self, request, pk=None):
        return self._service_response(reject_request, request, comment=request.data.get('comment', ''))

    @action(detail=True, methods=['post'], permission_classes=[IsWarehouseOperator])
    def process(self, request, pk=None):
        try:
            obj = retry_database_operation(lambda: process_request(
                self.get_object(), request.user,
                comment=request.data.get('comment', ''),
                transaction_date=request.data.get('transaction_date'),
            ))
        except WorkflowError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except OperationalError:
            return Response({'detail': '数据库正在处理其他操作，请稍后重试。'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(self.get_serializer(obj).data)


class WorkflowRequestLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = WorkflowRequestLine.objects.select_related('request', 'asset', 'stock_item').all()
    serializer_class = WorkflowRequestLineSerializer

    def get_permissions(self):
        return [IsWarehouseOperator()]

    def get_queryset(self):
        visible_requests = visible_request_queryset(WorkflowRequest.objects.all(), self.request.user)
        return self.queryset.filter(request__in=visible_requests).distinct().order_by('-created_at')


class ApprovalTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ApprovalTask.objects.select_related('request', 'approver').all()
    serializer_class = ApprovalTaskSerializer

    def get_permissions(self):
        return [IsWarehouseOperator()]

    def get_queryset(self):
        queryset = self.queryset.filter(
            Q(request__applicant=self.request.user)
            | Q(assigned_to__isnull=True, status=ApprovalTask.Status.PENDING)
            | Q(assigned_to=self.request.user)
            | Q(request__status=WorkflowRequest.Status.WAITING_WAREHOUSE)
        ).distinct()
        params = self.request.query_params
        query = params.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(request__request_no__icontains=query)
                | Q(request__inventory_no__icontains=query)
                | Q(request__reason__icontains=query)
                | Q(request__lines__asset__search_index__icontains=query)
                | Q(request__lines__stock_item__name__icontains=query)
                | Q(request__lines__stock_item__code__icontains=query)
            )
        for param, lookup in (
            ('application_start', 'request__application_date__gte'),
            ('application_end', 'request__application_date__lte'),
            ('return_start', 'request__expected_return_date__gte'),
            ('return_end', 'request__expected_return_date__lte'),
        ):
            parsed = _parse_date(params.get(param, ''))
            if parsed:
                queryset = queryset.filter(**{lookup: parsed})
        return queryset.distinct()


class ApprovalLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ApprovalLog.objects.select_related('request', 'actor').all()
    serializer_class = ApprovalLogSerializer

    def get_permissions(self):
        return [IsWarehouseOperator()]

    def get_queryset(self):
        visible_requests = visible_request_queryset(WorkflowRequest.objects.all(), self.request.user)
        return self.queryset.filter(request__in=visible_requests).distinct().order_by('-created_at')


class InventoryTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = InventoryTransaction.objects.select_related(
        'request', 'asset', 'stock_item', 'actor', 'source_warehouse', 'target_warehouse'
    ).all()
    serializer_class = InventoryTransactionSerializer
    permission_classes = [IsWarehouseOperator]

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        query = params.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(request__inventory_no__icontains=query)
                | Q(request__request_no__icontains=query)
                | Q(asset__search_index__icontains=query)
                | Q(stock_item__name__icontains=query)
                | Q(stock_item__code__icontains=query)
                | Q(holder_name__icontains=query)
                | Q(holder_company__icontains=query)
                | Q(project_code__icontains=query)
                | Q(work_order_no__icontains=query)
                | Q(comment__icontains=query)
            )
        if params.get('action'):
            queryset = queryset.filter(action=params['action'])
        if params.get('warehouse'):
            queryset = queryset.filter(
                Q(source_warehouse_id=params['warehouse']) | Q(target_warehouse_id=params['warehouse'])
            )
        for param, lookup in (
            ('transaction_start', 'business_date__gte'),
            ('transaction_end', 'business_date__lte'),
        ):
            parsed = _parse_date(params.get(param, ''))
            if parsed:
                queryset = queryset.filter(**{lookup: parsed})
        return queryset.distinct().order_by('-business_date', '-created_at')
