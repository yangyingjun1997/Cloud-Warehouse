from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_accountrequest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='employee_no',
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
    ]
