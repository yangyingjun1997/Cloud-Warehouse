from django.contrib import admin

from apps.common.admin import ChineseModelAdmin
from .models import Department, UserProfile


@admin.register(Department)
class DepartmentAdmin(ChineseModelAdmin):
    field_labels = {'name': '部门名称', 'code': '部门编码', 'is_active': '是否启用'}
    list_display = ('name', 'code', 'is_active', 'created_at')
    search_fields = ('name', 'code')
    list_filter = ('is_active',)


@admin.register(UserProfile)
class UserProfileAdmin(ChineseModelAdmin):
    field_labels = {
        'user': '系统用户', 'department': '所属部门', 'employee_no': '员工工号',
        'phone': '联系电话', 'title': '职位', 'wx_union_id': '微信 UnionID',
        'is_wx_bound': '是否已绑定微信', 'dingtalk_user_id': '钉钉用户 ID',
        'is_dingtalk_bound': '是否已绑定钉钉', 'dingtalk_bound_at': '钉钉绑定时间',
    }
    list_display = ('employee_no', 'user', 'department', 'phone', 'is_dingtalk_bound')
    search_fields = ('employee_no', 'user__username', 'phone', 'dingtalk_user_id')
    list_filter = ('department', 'is_dingtalk_bound')
