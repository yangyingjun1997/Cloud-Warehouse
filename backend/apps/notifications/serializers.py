from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    recipient_name = serializers.CharField(source='recipient.username', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'recipient',
            'recipient_name',
            'title',
            'content',
            'level',
            'is_read',
            'related_model',
            'related_object_id',
            'created_at',
            'updated_at',
        ]

