from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models

from apps.common.models import UUIDModel, TimeStampedModel


class Department(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = '部门'
        verbose_name_plural = '部门'

    def __str__(self) -> str:
        return self.name


class UserProfile(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)
    employee_no = models.CharField(max_length=50, unique=True)
    phone = models.CharField(max_length=32, blank=True, default='')
    title = models.CharField(max_length=100, blank=True, default='')
    wx_union_id = models.CharField(max_length=128, blank=True, default='')
    is_wx_bound = models.BooleanField(default=False)
    dingtalk_user_id = models.CharField(max_length=128, blank=True, default='')
    is_dingtalk_bound = models.BooleanField(default=False)
    dingtalk_bound_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['employee_no']
        verbose_name = '用户档案'
        verbose_name_plural = '用户档案'

    def __str__(self) -> str:
        return f'{self.employee_no} {self.user.username}'
