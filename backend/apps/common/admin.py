from django.contrib import admin

from .audit import form_change_data, record_operation
from .models import (
    ApiAccessToken,
    ApiThrottleState,
    LoginThrottleState,
    OperationAuditLog,
    SecurityAuditEvent,
)


class ChineseModelAdmin(admin.ModelAdmin):
    """Keep admin forms and list headers consistent with the Chinese UI."""

    field_labels = {}

    def __init__(self, model, admin_site):
        super().__init__(model, admin_site)
        for field_name, label in self.field_labels.items():
            try:
                model._meta.get_field(field_name).verbose_name = label
            except (LookupError, AttributeError):
                pass

    def save_model(self, request, obj, form, change):
        before, after = form_change_data(form)
        super().save_model(request, obj, form, change)
        if not isinstance(obj, OperationAuditLog):
            record_operation(
                request.user,
                f'{obj._meta.model_name}_admin_{"updated" if change else "created"}',
                obj,
                summary=f'通过管理后台{"修改" if change else "新建"}{obj._meta.verbose_name} {obj}',
                before=before,
                after=after,
            )

    def delete_model(self, request, obj):
        if not isinstance(obj, OperationAuditLog):
            record_operation(
                request.user,
                f'{obj._meta.model_name}_admin_deleted',
                obj,
                summary=f'通过管理后台删除{obj._meta.verbose_name} {obj}',
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset.iterator():
            self.delete_model(request, obj)


admin.site.site_header = '售后仓库管理系统'
admin.site.site_title = '售后仓库管理系统'
admin.site.index_title = '仓库运营管理'


@admin.register(OperationAuditLog)
class OperationAuditLogAdmin(ChineseModelAdmin):
    field_labels = {
        'actor': '操作人', 'action': '操作类型', 'target_model': '对象类型',
        'target_id': '对象编号', 'summary': '操作说明', 'before_data': '修改前数据',
        'after_data': '修改后数据',
    }
    list_display = ('created_at', 'actor', 'action', 'target_model', 'summary')
    search_fields = ('action', 'target_model', 'target_id', 'summary', 'actor__username')
    list_filter = ('action', 'target_model')
    readonly_fields = ('actor', 'action', 'target_model', 'target_id', 'summary', 'before_data', 'after_data', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {'GET', 'HEAD'}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoginThrottleState)
class LoginThrottleStateAdmin(ChineseModelAdmin):
    field_labels = {
        'key': '限流摘要',
        'failure_count': '失败次数',
        'blocked_until': '锁定截止时间',
    }
    list_display = ('updated_at', 'failure_count', 'blocked_until', 'key')
    list_filter = ('blocked_until',)
    search_fields = ('key',)
    readonly_fields = ('id', 'key', 'failure_count', 'blocked_until', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {'GET', 'HEAD'}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiThrottleState)
class ApiThrottleStateAdmin(ChineseModelAdmin):
    field_labels = {
        'key': '限流摘要',
        'request_count': '窗口请求次数',
        'window_started_at': '窗口开始时间',
    }
    list_display = ('updated_at', 'request_count', 'window_started_at', 'key')
    search_fields = ('key',)
    readonly_fields = ('id', 'key', 'request_count', 'window_started_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {'GET', 'HEAD'}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiAccessToken)
class ApiAccessTokenAdmin(ChineseModelAdmin):
    field_labels = {
        'user': '账号',
        'token_prefix': '令牌前缀',
        'expires_at': '过期时间',
        'revoked_at': '撤销时间',
        'replaced_by': '替换令牌',
    }
    list_display = ('created_at', 'user', 'token_prefix', 'expires_at', 'revoked_at')
    list_filter = ('revoked_at',)
    search_fields = ('user__username', 'token_prefix')
    readonly_fields = ('id', 'user', 'token_hash', 'token_prefix', 'expires_at', 'revoked_at', 'replaced_by', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {'GET', 'HEAD'}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SecurityAuditEvent)
class SecurityAuditEventAdmin(ChineseModelAdmin):
    field_labels = {
        'event_type': '事件类型',
        'subject_hash': '账号摘要',
        'ip_hash': '地址摘要',
        'metadata': '事件信息',
    }
    list_display = ('created_at', 'event_type', 'subject_hash', 'ip_hash')
    list_filter = ('event_type',)
    search_fields = ('event_type', 'subject_hash', 'ip_hash')
    readonly_fields = ('id', 'event_type', 'subject_hash', 'ip_hash', 'metadata', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {'GET', 'HEAD'}

    def has_delete_permission(self, request, obj=None):
        return False
