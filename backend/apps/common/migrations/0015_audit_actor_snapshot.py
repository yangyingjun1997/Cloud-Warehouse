import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """给审计日志增加操作人快照字段。

    不改 `actor` 外键的 PROTECT 语义：数据库上 `warehouse_immutable_audit`
    触发器禁止对审计表做任何 UPDATE/DELETE，若改成 SET_NULL，删除用户时
    Django 发出的 `UPDATE ... SET actor_id=NULL` 会被触发器拒绝，删号直接报错。

    因此策略是：
    - 外键保持 PROTECT：有审计记录的账号不能硬删，这是**正确的**合规行为；
    - 新增账号/姓名字段做快照，用于 UI 展示与未来需要时松绑；
    - 人员离职统一用「停用」，而不是删除。
    """

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('common', '0014_protect_audit_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='operationauditlog',
            name='actor_display_name',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='操作人姓名快照'),
        ),
        migrations.AddField(
            model_name='operationauditlog',
            name='actor_username',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='操作人账号快照'),
        ),
    ]
