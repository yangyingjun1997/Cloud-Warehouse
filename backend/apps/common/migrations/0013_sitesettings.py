from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0012_alter_apiaccesstoken_expires_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('site_name', models.CharField(default='售后仓库', max_length=60, verbose_name='系统名称')),
                ('subtitle', models.CharField(default='资产与出入库管理', max_length=80, verbose_name='副标题')),
                ('logo', models.ImageField(blank=True, null=True, upload_to='branding/', verbose_name='站点 Logo')),
                ('label_logo', models.ImageField(blank=True, null=True, upload_to='branding/', verbose_name='标签彩色 Logo')),
                ('label_logo_mono', models.ImageField(blank=True, null=True, upload_to='branding/', verbose_name='标签单色 Logo')),
                ('updated_by', models.CharField(blank=True, default='', max_length=64, verbose_name='最后修改人')),
            ],
            options={
                'verbose_name': '站点设置',
                'verbose_name_plural': '站点设置',
            },
        ),
    ]