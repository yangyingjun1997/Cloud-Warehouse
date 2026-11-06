from django.db.models.signals import post_save
from django.dispatch import receiver

from .code_services import sync_asset_identifiers
from .models import Asset


@receiver(post_save, sender=Asset, dispatch_uid='inventory.sync_asset_identifiers')
def sync_saved_asset_identifiers(sender, instance, raw=False, **kwargs):
    """为所有写入入口维护统一识别码，raw fixture 导入除外。"""
    if not raw and instance.system_asset_no and not getattr(instance, '_skip_identifier_sync', False):
        sync_asset_identifiers(instance)
