from django.db.models import Q

from .models import Asset


def in_warehouse_asset_filter(prefix=''):
    """Return assets that remain physical internal inventory.

    This intentionally includes reserved, maintenance and damaged assets: they
    are not requestable, but their cost still belongs to warehouse inventory.
    """
    return Q(
        **{
            f'{prefix}location_state': Asset.LocationState.IN_WAREHOUSE,
            f'{prefix}disposition_state': Asset.DispositionState.INTERNAL,
        }
    )


def available_asset_filter(prefix=''):
    """Return the canonical filter for an asset that can be selected now.

    Active reservations are synchronized into ``availability_state``. Callers
    that handle a race-sensitive operation should still recheck reservations
    inside the workflow transaction.
    """
    return Q(
        **{
            f'{prefix}status': Asset.Status.IN_STOCK,
            f'{prefix}location_state': Asset.LocationState.IN_WAREHOUSE,
            f'{prefix}availability_state': Asset.AvailabilityState.AVAILABLE,
            f'{prefix}quality_state': Asset.QualityState.NORMAL,
            f'{prefix}disposition_state': Asset.DispositionState.INTERNAL,
        }
    )


def is_asset_available(asset, *, allow_reserved=False):
    return (
        asset.status == Asset.Status.IN_STOCK
        and asset.location_state == Asset.LocationState.IN_WAREHOUSE
        and asset.availability_state in {
            Asset.AvailabilityState.AVAILABLE,
            Asset.AvailabilityState.RESERVED if allow_reserved else Asset.AvailabilityState.AVAILABLE,
        }
        and asset.quality_state == Asset.QualityState.NORMAL
        and asset.disposition_state == Asset.DispositionState.INTERNAL
    )
