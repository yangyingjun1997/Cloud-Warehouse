from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.permissions import IsSystemAdministrator

from .models import Department, UserProfile
from .serializers import DepartmentSerializer, UserProfileSerializer

User = get_user_model()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    profile = getattr(request.user, 'profile', None)
    is_warehouse_operator = (
        request.user.is_staff
        or request.user.is_superuser
        or request.user.groups.filter(name__in=['warehouse_staff', 'warehouse_admin']).exists()
    )
    return Response({
        'id': str(request.user.id),
        'username': request.user.username,
        'full_name': request.user.get_full_name(),
        'is_staff': request.user.is_staff,
        'is_superuser': request.user.is_superuser,
        'is_warehouse_operator': is_warehouse_operator,
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
