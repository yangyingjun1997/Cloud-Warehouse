"""Create a minimal fixture in a dedicated, empty SQLite E2E database."""
from __future__ import annotations

import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / 'backend'
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_backend.settings')

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

from apps.inventory.models import Asset, Category, ItemType, Location, Warehouse  # noqa: E402


def main() -> int:
    password = os.environ.get('QA_TEST_PASSWORD', '').strip()
    if not password:
        raise RuntimeError('QA_TEST_PASSWORD is required for the disposable E2E account.')
    database = settings.DATABASES['default']
    database_path = Path(database['NAME']).resolve()
    if database['ENGINE'] != 'django.db.backends.sqlite3' or 'e2e' not in database_path.name.lower():
        raise RuntimeError('Refusing to seed: DB must be a SQLite file whose name contains "e2e".')

    User = get_user_model()
    if User.objects.exclude(username='qa_admin').exists() or Asset.objects.exclude(asset_code='SC-E2E-0001').exists():
        raise RuntimeError('Refusing to seed a non-empty or non-E2E database.')

    user, _ = User.objects.get_or_create(
        username='qa_admin',
        defaults={'is_staff': True, 'is_superuser': True, 'is_active': True},
    )
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.set_password(password)
    user.save()

    warehouse, _ = Warehouse.objects.get_or_create(name='E2E 测试仓', defaults={'code': 'E2E-WH'})
    location, _ = Location.objects.get_or_create(
        warehouse=warehouse,
        code='E2E-01',
        defaults={'name': 'E2E 测试库位'},
    )
    category, _ = Category.objects.get_or_create(name='E2E 测试分类', defaults={'code': 'E2E-CAT'})
    item_type, _ = ItemType.objects.get_or_create(
        name='E2E 测试设备',
        defaults={'code': 'E2E-TYPE', 'category': category, 'is_serialized': True},
    )
    Asset.objects.get_or_create(
        asset_code='SC-E2E-0001',
        defaults={
            'name': 'E2E 扫码资产',
            'item_type': item_type,
            'warehouse': warehouse,
            'location': location,
            'serial_number': 'E2E-SN-0001',
            'status': Asset.Status.IN_STOCK,
        },
    )
    print(f'E2E fixture ready: {database_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
