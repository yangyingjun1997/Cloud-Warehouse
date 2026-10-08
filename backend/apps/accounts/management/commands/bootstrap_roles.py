from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create warehouse roles and assign initial system accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--admin-user', default='admin')
        parser.add_argument('--warehouse-user', action='append', default=[])

    def handle(self, *args, **options):
        warehouse_staff, _ = Group.objects.get_or_create(name='warehouse_staff')
        warehouse_admin, _ = Group.objects.get_or_create(name='warehouse_admin')
        user_model = get_user_model()
        admin = user_model.objects.filter(username=options['admin_user']).first()
        if admin:
            admin.groups.add(warehouse_admin)
            self.stdout.write(self.style.SUCCESS(f'仓库管理员：{admin.username}'))
        else:
            self.stdout.write(self.style.WARNING('未找到默认管理员账号，未分配仓库管理员角色。'))
        for username in options['warehouse_user'] or ['warehouse_demo']:
            user = user_model.objects.filter(username=username).first()
            if user:
                user.groups.add(warehouse_staff)
                user.is_staff = True
                user.save(update_fields=['is_staff'])
                self.stdout.write(self.style.SUCCESS(f'仓库人员：{user.username}'))
