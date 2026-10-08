import uuid

# Generated manually for the warehouse project.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Warehouse',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('code', models.CharField(max_length=50, unique=True)),
                ('location_desc', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('code', models.CharField(max_length=50, unique=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='Location',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('code', models.CharField(max_length=50)),
                ('description', models.CharField(blank=True, default='', max_length=255)),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='locations', to='inventory.warehouse')),
            ],
            options={'ordering': ['warehouse__name', 'code'], 'unique_together': {('warehouse', 'code')}},
        ),
        migrations.CreateModel(
            name='ItemType',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('code', models.CharField(max_length=50, unique=True)),
                ('is_serialized', models.BooleanField(default=True)),
                ('unit', models.CharField(default='pcs', max_length=20)),
                ('safety_stock', models.PositiveIntegerField(default=0)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='item_types', to='inventory.category')),
            ],
            options={'ordering': ['category__name', 'name'], 'unique_together': {('category', 'name')}},
        ),
        migrations.CreateModel(
            name='Asset',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('asset_code', models.CharField(default='', max_length=80, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('serial_number', models.CharField(blank=True, default='', max_length=120)),
                ('qr_value', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('in_stock', '在库'), ('borrowed', '已借出'), ('issued', '已领用'), ('pending_out', '待出库'), ('pending_return', '待归还验收'), ('maintenance', '待维修'), ('repairing', '维修中'), ('damaged', '已报损'), ('lost', '已丢失'), ('disabled', '已停用')], default='in_stock', max_length=32)),
                ('photo_url', models.CharField(blank=True, default='', max_length=255)),
                ('remarks', models.TextField(blank=True, default='')),
                ('current_holder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.department')),
                ('item_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='inventory.itemtype')),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.location')),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.warehouse')),
            ],
            options={'ordering': ['asset_code']},
        ),
        migrations.CreateModel(
            name='StockItem',
            fields=[
                ('id', models.UUIDField(default=__import__('uuid').uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('code', models.CharField(max_length=50, unique=True)),
                ('unit', models.CharField(default='pcs', max_length=20)),
                ('quantity', models.PositiveIntegerField(default=0)),
                ('safety_stock', models.PositiveIntegerField(default=0)),
                ('location', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.location')),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='inventory.warehouse')),
            ],
            options={'ordering': ['name']},
        ),
    ]
