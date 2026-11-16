import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FUNCTION_NAME = 'warehouse_reject_audit_mutation'
INVENTORY_AUDIT_TABLES = (
    'inventory_assetdomainchange',
    'inventory_assetlifecycleevent',
    'inventory_assetstatecorrection',
)


def install_inventory_audit_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table_name in INVENTORY_AUDIT_TABLES:
            cursor.execute(f'''
                DROP TRIGGER IF EXISTS warehouse_immutable_audit ON {table_name};
                CREATE TRIGGER warehouse_immutable_audit
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();
            ''')


def remove_inventory_audit_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table_name in INVENTORY_AUDIT_TABLES:
            cursor.execute(
                f'DROP TRIGGER IF EXISTS warehouse_immutable_audit ON {table_name};'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0014_protect_audit_tables'),
        ('inventory', '0029_asset_customer_ref'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='assetlifecycleevent',
            name='actor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='asset_lifecycle_events',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            install_inventory_audit_triggers,
            remove_inventory_audit_triggers,
        ),
    ]
