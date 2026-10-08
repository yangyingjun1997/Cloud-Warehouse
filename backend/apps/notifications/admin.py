from django.contrib import admin

from apps.common.admin import ChineseModelAdmin
from .models import LowStockAlert, Notification


@admin.register(Notification)
class NotificationAdmin(ChineseModelAdmin):
    field_labels = {'recipient': '接收人', 'title': '通知标题', 'content': '通知内容', 'level': '通知级别', 'is_read': '是否已读', 'related_model': '关联模型', 'related_object_id': '关联对象'}
    list_display = ('title', 'recipient', 'level', 'is_read', 'created_at')
    list_filter = ('level', 'is_read')
    search_fields = ('title', 'content', 'recipient__username')


@admin.register(LowStockAlert)
class LowStockAlertAdmin(ChineseModelAdmin):
    field_labels = {
        'source_type': '预警对象类型', 'source_id': '预警对象编号', 'source_name': '预警对象',
        'current_quantity': '当前可用数量', 'safety_stock': '安全库存', 'is_active': '正在预警',
        'active_since': '开始预警时间', 'last_notified_at': '最近通知时间', 'resolved_at': '恢复时间',
    }
    list_display = ('source_name', 'source_type', 'current_quantity', 'safety_stock', 'is_active', 'active_since')
    list_filter = ('source_type', 'is_active')
    search_fields = ('source_name',)
    readonly_fields = tuple(field.name for field in LowStockAlert._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
