from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from apps.inventory.models import Asset, ItemType, StockItem

from .low_stock import evaluate_item_type, evaluate_stock_item
from .models import LowStockAlert


def _after_commit(callback):
    transaction.on_commit(callback)


@receiver(pre_save, sender=Asset)
def remember_previous_asset_type(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    if not instance.pk:
        instance._previous_item_type_id = None
        return
    instance._previous_item_type_id = sender.objects.filter(pk=instance.pk).values_list('item_type_id', flat=True).first()


@receiver(post_save, sender=Asset)
def evaluate_asset_type_after_save(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    item_type_ids = {instance.item_type_id, getattr(instance, '_previous_item_type_id', None)} - {None}
    for item_type_id in item_type_ids:
        _after_commit(lambda item_type_id=item_type_id: evaluate_item_type(item_type_id))


@receiver(post_delete, sender=Asset)
def evaluate_asset_type_after_delete(sender, instance, **kwargs):
    if instance.item_type_id:
        _after_commit(lambda: evaluate_item_type(instance.item_type_id))


@receiver(post_save, sender=ItemType)
def evaluate_type_threshold_after_save(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    _after_commit(lambda: evaluate_item_type(instance.id))


@receiver(post_delete, sender=ItemType)
def resolve_deleted_type_alert(sender, instance, **kwargs):
    _after_commit(lambda: LowStockAlert.objects.filter(
        source_type=LowStockAlert.SourceType.ITEM_TYPE,
        source_id=instance.id,
    ).delete())


@receiver(post_save, sender=StockItem)
def evaluate_stock_after_save(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    _after_commit(lambda: evaluate_stock_item(instance.id))


@receiver(post_delete, sender=StockItem)
def resolve_deleted_stock_alert(sender, instance, **kwargs):
    _after_commit(lambda: LowStockAlert.objects.filter(
        source_type=LowStockAlert.SourceType.STOCK_ITEM,
        source_id=instance.id,
    ).delete())
