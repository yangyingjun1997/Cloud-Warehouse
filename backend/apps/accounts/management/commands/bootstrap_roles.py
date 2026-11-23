from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.common.permissions import PERMISSION_GROUPS, ROLE_GROUPS


class Command(BaseCommand):
    help = 'Create warehouse roles and assign initial system accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--admin-user', default='admin')
        parser.add_argument('--warehouse-user', action='append', default=[])

    def handle(self, *args, **options):
        # 细粒度能力组（历史，能力标签）
        for group_name in PERMISSION_GROUPS:
            Group.objects.get_or_create(name=group_name)
        # 角色组（2026-11-23 新增，13 组归并为 4 个角色）
        created_roles = []
        for group_name in ROLE_GROUPS:
            _, was_created = Group.objects.get_or_create(name=group_name)
            if was_created:
                created_roles.append(group_name)

        warehouse_admin, _ = Group.objects.get_or_create(name='warehouse_admin')
        user_model = get_user_model()
        admin = user_model.objects.filter(username=options['admin_user']).first()
        if admin:
            admin.groups.add(warehouse_admin)
            self.stdout.write(self.style.SUCCESS(f'仓库管理员：{admin.username}'))
        else:
            self.stdout.write(self.style.WARNING('未找到默认管理员账号，未分配仓库管理员角色。'))

        # 仓库人员默认给「执行」角色（出入库 + 台账校正），
        # 不再使用已废弃的 warehouse_staff 全权限组。
        operator_role = Group.objects.get(name='warehouse_role_operator')
        for username in options['warehouse_user'] or ['warehouse_demo']:
            user = user_model.objects.filter(username=username).first()
            if user:
                user.groups.add(operator_role)
                user.is_staff = True
                user.save(update_fields=['is_staff'])
                self.stdout.write(self.style.SUCCESS(f'仓库人员：{user.username}（角色·执行）'))

        if created_roles:
            self.stdout.write(self.style.SUCCESS(f'新建角色组：{", ".join(created_roles)}'))
        self.stdout.write(
            self.style.NOTICE(
                '说明：warehouse_staff 为历史全权限组，已不再分配。'
                '新账号请使用 warehouse_role_viewer/editor/operator/approver。'
            )
        )
