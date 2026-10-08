from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Department, UserProfile

User = get_user_model()


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'code', 'is_active', 'created_at', 'updated_at']


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.CharField(source='user.get_full_name', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'user',
            'username',
            'full_name',
            'department',
            'department_name',
            'employee_no',
            'phone',
            'title',
            'wx_union_id',
            'is_wx_bound',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {'user': {'write_only': True}}

