from datetime import date, datetime
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone


def build_code(prefix: str) -> str:
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    tail = uuid4().hex[:6].upper()
    return f'{prefix}-{stamp}-{tail}'


@transaction.atomic
def build_daily_code(prefix: str, sequence_date=None) -> str:
    """Allocate a readable, per-prefix daily number inside a transaction."""
    from .models import DailySequence

    sequence_date = sequence_date or timezone.localdate()
    sequence = DailySequence.objects.select_for_update().filter(
        prefix=prefix,
        sequence_date=sequence_date,
    ).first()
    if sequence is None:
        try:
            with transaction.atomic():
                sequence = DailySequence.objects.create(
                    prefix=prefix,
                    sequence_date=sequence_date,
                    last_number=1,
                )
        except IntegrityError:
            sequence = DailySequence.objects.select_for_update().get(
                prefix=prefix,
                sequence_date=sequence_date,
            )
            sequence.last_number += 1
            sequence.save(update_fields=['last_number', 'updated_at'])
    else:
        sequence.last_number += 1
        sequence.save(update_fields=['last_number', 'updated_at'])
    return f'{prefix}-{sequence_date:%Y%m%d}-{sequence.last_number:06d}'


@transaction.atomic
def build_asset_no() -> str:
    """Allocate the immutable warehouse asset number (AST-000001...).

    The sequence is stored in the existing locked DailySequence table. A fixed
    sequence date keeps the number independent from the business date while
    preserving the table's unique/concurrency guarantees.
    """
    from .models import DailySequence

    sequence_date = date(1900, 1, 1)
    sequence = DailySequence.objects.select_for_update().filter(
        prefix='AST', sequence_date=sequence_date,
    ).first()
    if sequence is None:
        try:
            with transaction.atomic():
                sequence = DailySequence.objects.create(
                    prefix='AST', sequence_date=sequence_date, last_number=1,
                )
        except IntegrityError:
            sequence = DailySequence.objects.select_for_update().get(
                prefix='AST', sequence_date=sequence_date,
            )
            sequence.last_number += 1
            sequence.save(update_fields=['last_number', 'updated_at'])
    else:
        sequence.last_number += 1
        sequence.save(update_fields=['last_number', 'updated_at'])
    return f'AST-{sequence.last_number:06d}'
