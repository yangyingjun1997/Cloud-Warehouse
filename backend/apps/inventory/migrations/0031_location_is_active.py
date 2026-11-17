from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0030_protect_audit_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='location',
            name='is_active',
            field=models.BooleanField(default=True, verbose_name='启用'),
        ),
    ]
