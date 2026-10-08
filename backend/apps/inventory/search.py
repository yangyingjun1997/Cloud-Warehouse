from django.db.models import Q


def asset_search_query(value: str) -> Q:
    """Search all supported user-facing asset identifiers and text aliases."""

    value = value.strip()
    return (
        Q(system_asset_no__iexact=value)
        | Q(asset_code__iexact=value)
        | Q(qr_value__iexact=value)
        | Q(serial_number__iexact=value)
        | Q(manufacturer_barcode__iexact=value)
        | Q(system_asset_no__icontains=value)
        | Q(asset_code__icontains=value)
        | Q(name__icontains=value)
        | Q(item_type__name__icontains=value)
        | Q(manufacturer__icontains=value)
        | Q(model__icontains=value)
        | Q(search_aliases__icontains=value)
        | Q(remarks__icontains=value)
        | Q(search_index__icontains=value.lower())
    )
