from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = 'apps.notifications'
    verbose_name = '通知中心'

    def ready(self):
        from . import signals  # noqa: F401
