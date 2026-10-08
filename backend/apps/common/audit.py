from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db.models import Model

from .models import OperationAuditLog


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime, Decimal, UUID)):
        return str(value)
    if isinstance(value, Model):
        return {'id': str(value.pk), 'label': str(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


def form_change_data(form) -> tuple[dict, dict]:
    before, after = {}, {}
    for field_name in form.changed_data:
        before[field_name] = _json_value(form.initial.get(field_name))
        after[field_name] = _json_value(form.cleaned_data.get(field_name))
    return before, after


def record_operation(actor, action: str, target, *, summary: str = '', before=None, after=None):
    return OperationAuditLog.objects.create(
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action=action,
        target_model=target._meta.label_lower,
        target_id=target.pk,
        summary=summary,
        before_data=_json_value(before or {}),
        after_data=_json_value(after or {}),
    )
