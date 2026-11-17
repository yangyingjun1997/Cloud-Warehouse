from datetime import datetime
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
    """分配永久且与业务日期无关的 8 位系统资产编号。"""
    from apps.inventory.code_services import allocate_system_asset_code

    return allocate_system_asset_code()


# 去掉了容易混淆的 0/O、1/l/I，方便管理员口头或纸质转交临时密码。
_TEMP_PASSWORD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'


def generate_temporary_password(length: int = 12) -> str:
    """生成管理员转交用的临时密码。

    使用 `secrets` 而不是 `random`，避免可预测的密码被猜到。
    长度默认 12，满足系统"至少 8 位"的初始密码校验。
    """
    import secrets

    length = max(int(length), 8)
    return ''.join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))
