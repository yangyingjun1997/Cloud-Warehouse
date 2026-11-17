from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency('auth'),
        ('accounts', '0006_employee_no_optional'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='accountrequest',
            name='unique_pending_account_request_per_employee',
        ),
        migrations.AlterField(
            model_name='accountrequest',
            name='employee_no',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='员工工号'),
        ),
        migrations.AddConstraint(
            model_name='accountrequest',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'pending')),
                fields=('real_name', 'phone'),
                name='unique_pending_account_request_per_person',
            ),
        ),
    ]
