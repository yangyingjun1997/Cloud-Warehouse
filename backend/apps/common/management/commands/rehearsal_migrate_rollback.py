"""迁移回滚演练：在隔离数据库上验证每个迁移能否安全回退。

背景（R10-P2-13）：`rollback.sh` 只切换代码符号链接，不碰数据库。
如果新版本带了不可回退的迁移（删表、删列、改类型），代码回切后
Django 会因数据库结构与代码期望不符而崩溃——那时才发现已经晚了。

本脚本在**独立测试库**上演练：
1. 全新库跑全部迁移到最新
2. 逐个迁移向后回退一步，再向前重新应用
3. 记录哪些迁移不可回退（或回退后重新应用失败）

安全约束（不可违反）：
- 绝不连接生产库。数据库名必须以 `_rehearsal` 结尾，否则直接拒绝。
- 绝不读写 /srv/after-sales-warehouse/app.env。
- 用独立的 PostgreSQL 库，演练结束即删除。

用法（在服务器上）：
    python manage.py rehearsal_migrate_rollback --database-rehearsal-name warehouse_rehearsal
"""

import os
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

REQUIRED_SUFFIX = '_rehearsal'


class Command(BaseCommand):
    help = '在隔离库上演练迁移回滚，找出不可回退的迁移（绝不触碰生产库）。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--database-rehearsal-name', required=True,
            help=f'演练库名，必须以 {REQUIRED_SUFFIX} 结尾。',
        )
        parser.add_argument(
            '--max-steps', type=int, default=0,
            help='最多演练多少个迁移（0=全部）。',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只列出迁移链，不回退。',
        )

    def handle(self, *args, **options):
        db_name = options['database_rehearsal_name']
        self._guard(db_name)

        if options['dry_run']:
            return self._list_plan()

        from django.conf import settings

        original = settings.DATABASES['default']
        rehearsal = {**original, 'NAME': db_name}
        # 先记下生产库名，后面替换 DATABASES 后仍要用它查生产库
        self._production_db_name = original['NAME']

        # 重要：演练前后的「生产库未被触碰」证据必须由本命令自己打印。
        # 早期版本靠外部脚本在命令之后查，但那时 Django 连接刚被替换过，
        # 可能把演练库的迁移数（全量灌入后的 106）误报成生产库的数字。
        self._report_production_snapshot('演练前（生产库）')

        # 清理旧库（可能是上次演练残留）；首次运行时库不存在，忽略即可
        try:
            self._drop_database(db_name)
        except RuntimeError as exc:
            self.stderr.write(self.style.WARNING(f'清理旧演练库失败（可忽略）：{exc}'))
        self._create_database(db_name)

        settings.DATABASES['default'] = rehearsal
        connections.databases['default'] = rehearsal
        exit_code = 1
        try:
            exit_code = self._rehearse(options['max_steps'])
        finally:
            # 关闭连接后才能删库
            connections.close_all()
            settings.DATABASES['default'] = original
            connections.databases['default'] = original
            try:
                self._drop_database(db_name)
                self.stdout.write(self.style.SUCCESS(f'演练库 {db_name} 已清理'))
            except RuntimeError as exc:
                self.stderr.write(self.style.ERROR(f'清理演练库失败：{exc}'))
            self._report_production_snapshot('演练后（生产库）')
        # BaseCommand.handle 应返回字符串；用属性记录退出码，
        # 并在结论里明确写出，避免 'int' object has no attribute 'endswith'
        self.exit_code = exit_code
        if exit_code != 0:
            self.stdout.write(self.style.WARNING(
                '演练发现不可回退的迁移，详见上方结论。'
            ))

    def _report_production_snapshot(self, label):
        """打印生产库的关键计数，作为「未被动过」的证据。

        用生产库名直接查，不经过 Django 的 default 连接
        （演练期间该连接已指向演练库）。
        """
        import subprocess

        prod_name = getattr(self, '_production_db_name', '') or 'warehouse'

        self.stdout.write(f'--- {label} ---')
        for name, sql in (
            ('tables', "select count(*) from pg_tables where schemaname='public'"),
            ('migrations', 'select count(*) from django_migrations'),
        ):
            try:
                result = subprocess.run(
                    ['sudo', '-u', 'postgres', 'psql', '-d', prod_name, '-tAc', sql],
                    capture_output=True, text=True, timeout=60,
                )
                value = result.stdout.strip() or '?'
            except Exception:
                value = '?'
            self.stdout.write(f'  {name}: {value}')

    # ── 安全护栏 ────────────────────────────────────────────────
    def _guard(self, db_name):
        from django.conf import settings

        production_name = settings.DATABASES['default']['NAME']
        if not db_name.endswith(REQUIRED_SUFFIX):
            raise CommandError(
                f'演练库名必须以 {REQUIRED_SUFFIX} 结尾，防止误删生产库。'
            )
        if db_name == production_name:
            raise CommandError('演练库名不能与生产库同名。')

    # ── 库管理 ──────────────────────────────────────────────────
    #
    # 应用账号（warehouse）刻意没有 CREATEDB 权限——这是正确的生产配置，
    # 不应为了演练而放开。建库/删库改用 `sudo -u postgres psql`：
    # 本机 pg_hba 对 local 连接用 peer 认证，postgres 系统用户免密即可管理。
    # 这样既不需要把超级用户密码写进环境变量或代码，也不需要放宽应用权限。
    def _run_psql_admin(self, sql):
        """以 postgres 超级用户执行管理 SQL。

        要求当前进程以 root 运行（演练是运维动作，不是业务命令）。
        warehouse 应用账号既没有 CREATEDB 权限，也没有免密 sudo——
        这两点都是刻意的安全配置，不应为演练放宽。
        """
        import subprocess

        if os.geteuid() != 0:
            raise RuntimeError(
                '演练需要创建/删除数据库，请以 root 运行：'
                'sudo /opt/after-sales-warehouse/venv/bin/python manage.py '
                'rehearsal_migrate_rollback --database-rehearsal-name <name>_rehearsal'
            )

        result = subprocess.run(
            ['sudo', '-u', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-tAc', sql],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f'psql 执行失败（{sql[:60]}）：{result.stderr.strip()[:300]}'
            )
        return result.stdout.strip()

    def _drop_database(self, db_name):
        try:
            self._run_psql_admin(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
            )
            self._run_psql_admin(f'DROP DATABASE IF EXISTS "{db_name}"')
        except RuntimeError as exc:
            # 库不存在时 DROP IF EXISTS 不会报错；报错通常是权限或连接占用，
            # 交给调用方决定是否致命。
            raise RuntimeError(str(exc)) from exc

    def _create_database(self, db_name):
        from django.conf import settings

        app_user = settings.DATABASES['default']['USER']
        self._run_psql_admin(f'CREATE DATABASE "{db_name}" OWNER "{app_user}"')
        self.stdout.write(self.style.SUCCESS(f'已创建演练库 {db_name}（属主 {app_user}）'))

    # ── 演练主体 ────────────────────────────────────────────────
    def _list_plan(self):
        loader = MigrationLoader(connections['default'])
        plan = loader.graph.leaf_nodes()
        self.stdout.write('迁移链末端：')
        for app_label, name in plan:
            self.stdout.write(f'  {app_label}.{name}')
        return

    def _rehearse(self, max_steps):
        from django.core.management import call_command

        call_command('migrate', verbosity=0, interactive=False)

        # 关键：migrate 全部应用后，migration_plan(leaf_nodes) 返回空列表
        # （因为没有「待应用」的迁移）。要拿到「已应用」的迁移，必须从
        # loader.applied_migrations 取，再按依赖顺序排列。
        executor = MigrationExecutor(connections['default'])
        loader = executor.loader
        applied = set(loader.applied_migrations.keys())
        # graph.forwards_plan 给出某节点的正向依赖顺序；用全部叶节点合并去重
        ordered = []
        seen = set()
        for leaf in loader.graph.leaf_nodes():
            for node in loader.graph.forwards_plan(leaf):
                if node in applied and node not in seen:
                    seen.add(node)
                    ordered.append(loader.graph.nodes[node])

        if max_steps:
            ordered = ordered[-max_steps:]

        self.stdout.write(f'共 {len(ordered)} 个已应用迁移待演练回退。')
        self.stdout.write(
            '说明：真实回切通常是「撤销最近几个迁移」，而不是把全部迁移逐条回退。'
        )
        self.stdout.write(
            '注意：本演练在单个进程内连续回退，可能触发 PostgreSQL 的 '
            '"pending trigger events"——那是同事务连锁回退造成的，'
            '真实回切时每条 migrate 是独立事务，不会出现。\n'
        )
        if not ordered:
            self.stdout.write(self.style.WARNING(
                '没有取到已应用迁移，演练无法进行（请检查数据库连接与迁移表）。'
            ))
            return 1

        reversible = 0
        irreversible = []
        for index, migration in enumerate(ordered, start=1):
            label = f'{migration.app_label}.{migration.name}'
            node = (migration.app_label, migration.name)
            # 从 migration graph 取该节点的直接父节点。
            # Migration 对象本身没有 parents 属性，依赖关系在 loader.graph 里。
            executor = MigrationExecutor(connections['default'])
            graph = executor.loader.graph
            parents = list(graph.node_map[node].parents) if node in graph.node_map else []
            if not parents:
                # 首个迁移没有父节点，回退到该 app 的空状态
                back_targets = [(migration.app_label, None)]
            else:
                back_targets = parents

            try:
                # 1) 回退到父节点（即撤销这一条迁移）
                executor = MigrationExecutor(connections['default'])
                executor.migrate(back_targets)
                # 2) 重新向前应用这一条，确认回退后仍能重建
                executor = MigrationExecutor(connections['default'])
                executor.migrate([(migration.app_label, migration.name)])
                reversible += 1
                self.stdout.write(f'  [{index}/{len(ordered)}] {label} 可回退')
            except Exception as exc:
                reason = str(exc).replace('\n', ' ')[:160]
                irreversible.append((label, reason))
                self.stdout.write(self.style.WARNING(
                    f'  [{index}/{len(ordered)}] {label} 不可回退：{reason}'
                ))
                # 恢复到最新，避免后续全部连锁失败
                try:
                    executor = MigrationExecutor(connections['default'])
                    executor.migrate(executor.loader.graph.leaf_nodes())
                except Exception:
                    self.stderr.write(self.style.ERROR(
                        '无法恢复到最新状态，中止演练（不影响生产库）。'
                    ))
                    break

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'可回退：{reversible} 个'))
        if irreversible:
            self.stdout.write(self.style.ERROR(f'不可回退：{len(irreversible)} 个'))
            for label, reason in irreversible:
                self.stdout.write(f'  - {label}：{reason}')
            self.stdout.write('')
            self.stdout.write(
                '结论：这些迁移部署后无法靠迁移回退撤销。'
                '回切时请用「恢复发布前数据库快照」，不要依赖 rollback.sh 的迁移回退。'
            )
            return 1
        self.stdout.write('结论：全部迁移可安全回退。')
        return 0
