from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.permissions import is_warehouse_operator

from apps.common.permissions import IsSystemAdministrator

from .models import Department, UserProfile
from .serializers import DepartmentSerializer, UserProfileSerializer

User = get_user_model()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    profile = getattr(request.user, 'profile', None)
    # 与接口实际权限保持一致：过去这里只认 warehouse_staff/warehouse_admin，
    # 导致只持有细分权限（如 warehouse_outbound）的账号 /me 报 False，
    # 但调用资产、审批、盘点接口却返回 200，前端按 /me 判断会误判。
    is_warehouse_operator_flag = is_warehouse_operator(request.user)
    return Response({
        'id': str(request.user.id),
        'username': request.user.username,
        'full_name': request.user.get_full_name(),
        'is_staff': request.user.is_staff,
        'is_superuser': request.user.is_superuser,
        'is_warehouse_operator': is_warehouse_operator_flag,
        'employee_no': profile.employee_no if profile else '',
        'department': str(profile.department_id) if profile and profile.department_id else None,
        'department_name': profile.department.name if profile and profile.department else '',
        'phone': profile.phone if profile else '',
    })


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsSystemAdministrator]


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.select_related('user', 'department').all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsSystemAdministrator]
