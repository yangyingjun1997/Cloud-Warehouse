from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = 'apps.inventory'
    verbose_name = '仓库与资产'

    def ready(self):
        from . import signals  # noqa: F401
