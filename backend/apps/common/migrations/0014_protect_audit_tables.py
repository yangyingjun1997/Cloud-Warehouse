import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


FUNCTION_NAME = 'warehouse_reject_audit_mutation'
COMMON_AUDIT_TABLES = (
    'common_operationauditlog',
    'common_securityauditevent',
)


def install_common_audit_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'''
            CREATE OR REPLACE FUNCTION {FUNCTION_NAME}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'audit records are append-only: %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME
                    USING ERRCODE = '55000';
            END;
            $$;
        ''')
        for table_name in COMMON_AUDIT_TABLES:
            cursor.execute(f'''
                DROP TRIGGER IF EXISTS warehouse_immutable_audit ON {table_name};
                CREATE TRIGGER warehouse_immutable_audit
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}();
            ''')


def remove_common_audit_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table_name in COMMON_AUDIT_TABLES:
            cursor.execute(
                f'DROP TRIGGER IF EXISTS warehouse_immutable_audit ON {table_name};'
            )
        cursor.execute(f'DROP FUNCTION IF EXISTS {FUNCTION_NAME}();')


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0013_sitesettings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='operationauditlog',
            name='actor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='operation_audit_logs',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            install_common_audit_triggers,
            remove_common_audit_triggers,
        ),
    ]
