import re
from pathlib import Path

from django.test import SimpleTestCase


class NginxPrivateMediaConfigurationTests(SimpleTestCase):
    def test_asset_archive_paths_are_not_served_as_public_media(self):
        repository_root = Path(__file__).resolve().parents[3]
        config_path = repository_root / 'deploy' / 'ubuntu22' / 'nginx-after-sales-warehouse.conf'
        config = config_path.read_text(encoding='utf-8')

        private_location = re.search(
            r'location\s+~\s+\^/media/assets/\[\^/\]\+/archive/\s*\{(?P<body>.*?)\}',
            config,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(private_location, 'Nginx 必须单独拦截资产归档附件目录。')
        self.assertIn('return 404;', private_location.group('body'))


class DeploymentFailClosedTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repository_root = Path(__file__).resolve().parents[3]
        cls.install_script = (
            cls.repository_root / 'deploy' / 'ubuntu22' / 'install.sh'
        ).read_text(encoding='utf-8')

    def test_database_snapshot_failure_stops_deployment(self):
        self.assertIn('部署前数据库快照失败，已停止部署', self.install_script)
        self.assertNotIn('部署前数据库快照失败，继续部署', self.install_script)
        failure_block = re.search(
            r'部署前数据库快照失败，已停止部署.*?rm -rf "\$target"(?P<body>.*?)fi',
            self.install_script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(failure_block)
        self.assertIn('return 1', failure_block.group('body'))

    def test_release_is_built_before_atomic_current_switch(self):
        script = self.install_script
        snapshot_at = script.index('snapshot_existing_database\n')
        candidate_check_at = script.index('manage.py" check', snapshot_at)
        migrate_at = script.index('migrate --noinput', candidate_check_at)
        switch_at = script.index('mv -Tf "$APP_DIR/current.next" "$CURRENT_LINK"', migrate_at)
        restart_at = script.index('systemctl restart after-sales-warehouse', switch_at)
        health_at = script.index('http://127.0.0.1/health/', restart_at)

        self.assertLess(snapshot_at, candidate_check_at)
        self.assertLess(candidate_check_at, migrate_at)
        self.assertLess(migrate_at, switch_at)
        self.assertLess(switch_at, restart_at)
        self.assertLess(restart_at, health_at)
        self.assertNotIn('"$SOURCE_BACKEND/" "$APP_DIR/backend/"', script)

    def test_failure_handler_restores_previous_release_pointer(self):
        failure_handler = re.search(
            r'deployment_failed\(\) \{(?P<body>.*?)\n\}',
            self.install_script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(failure_handler)
        body = failure_handler.group('body')
        self.assertIn('ln -sfn "$PREVIOUS_RELEASE"', body)
        self.assertIn('mv -Tf "$APP_DIR/current.rollback" "$CURRENT_LINK"', body)
        self.assertIn('systemctl restart after-sales-warehouse', body)

    def test_runtime_units_only_use_atomic_current_release(self):
        deploy_dir = self.repository_root / 'deploy' / 'ubuntu22'
        service_paths = list(deploy_dir.glob('after-sales-warehouse*.service'))
        app_services = [
            path for path in service_paths
            if 'backup' not in path.name and 'health' not in path.name
        ]
        self.assertTrue(app_services)
        for service_path in app_services:
            with self.subTest(service=service_path.name):
                source = service_path.read_text(encoding='utf-8')
                if 'WorkingDirectory=' in source:
                    self.assertIn('/opt/after-sales-warehouse/current/backend', source)
                    self.assertIn('/srv/after-sales-warehouse/app.env', source)

    def test_rollback_switches_pointer_without_deleting_live_code(self):
        rollback = (
            self.repository_root / 'deploy' / 'ubuntu22' / 'rollback.sh'
        ).read_text(encoding='utf-8')

        self.assertIn('mv -Tf "$APP_DIR/current.next" "$CURRENT_LINK"', rollback)
        self.assertIn('restore_current()', rollback)
        self.assertNotIn('rm -rf "$BACKEND_DIR"', rollback)

    def test_rollback_requires_schema_compatibility_before_switch(self):
        deploy_dir = self.repository_root / 'deploy' / 'ubuntu22'
        rollback = (deploy_dir / 'rollback.sh').read_text(encoding='utf-8')
        install = (deploy_dir / 'install.sh').read_text(encoding='utf-8')

        self.assertIn('check_release_schema_compatibility "$TARGET_DIR"', rollback)
        self.assertIn('showmigrations --plan', rollback)
        self.assertIn('database_migration_check', rollback)
        self.assertIn('--allow-unknown-schema', rollback)
        self.assertLess(
            rollback.index('check_release_schema_compatibility "$TARGET_DIR"'),
            rollback.index('mv -Tf "$APP_DIR/current.next" "$CURRENT_LINK"'),
        )
        self.assertIn('database_migration_check=passed', install)
        self.assertIn('database_migration_state_sha256=', install)
        self.assertIn('showmigrations --plan', install)

    def test_local_gate_runs_shell_and_security_scans_fail_closed(self):
        deploy_dir = self.repository_root / 'deploy' / 'ubuntu22'
        check_all = (deploy_dir / 'check-all.sh').read_text(encoding='utf-8')
        security_check = (deploy_dir / 'security-check.sh').read_text(encoding='utf-8')

        self.assertIn('bash -n', check_all)
        self.assertIn('-m bandit', check_all)
        self.assertIn('-m pip_audit', check_all)
        self.assertIn('trap cleanup EXIT', check_all)
        self.assertIn('--allow-missing-security-tools', check_all)
        self.assertIn("fail '未安装 pip-audit", security_check)
        self.assertIn("fail 'pip-audit 发现依赖漏洞", security_check)
        self.assertIn('ALLOW_MISSING_SECURITY_TOOLS', security_check)

    def test_sqlite_import_uses_private_cleaned_up_files(self):
        install = self.install_script

        self.assertNotIn('/tmp/after-sales-warehouse-initial.json', install)
        self.assertIn('mktemp -d "$DATA_DIR/sqlite-import.', install)
        self.assertIn('chmod 700 "$SQLITE_IMPORT_DIR"', install)
        self.assertIn('install -o "$APP_USER" -g "$APP_GROUP" -m 600', install)
        self.assertIn('umask 077', install)
        self.assertIn('rm -rf "$SQLITE_IMPORT_DIR"', install)


class RestoreDrillFormatTests(SimpleTestCase):
    """恢复演练脚本必须跟得上 backup.sh 的实际产物格式。

    历史缺陷：backup.sh 早已改为 media-snapshots 硬链接增量，不再生成
    media.tar.gz，但 restore-drill.sh 仍要求该文件，导致演练在恢复数据库
    之前就必然失败——灾难恢复能力实际为零验证。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        deploy_dir = Path(__file__).resolve().parents[3] / 'deploy' / 'ubuntu22'
        cls.drill = (deploy_dir / 'restore-drill.sh').read_text(encoding='utf-8')
        cls.backup = (deploy_dir / 'backup.sh').read_text(encoding='utf-8')

    @staticmethod
    def _code_lines(script):
        """只保留非注释、非空行；历史缺陷通常写在注释里，不能被误判为代码依赖。"""
        return [
            line for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        ]

    def test_backup_no_longer_produces_media_tarball(self):
        """前提校验：如果 backup.sh 又开始生成 media.tar.gz，本测试需要同步更新。"""
        code = '\n'.join(self._code_lines(self.backup))
        self.assertNotIn('tar -czf', code)
        self.assertIn('media-snapshots', code)

    def test_drill_does_not_require_media_tarball(self):
        code = '\n'.join(self._code_lines(self.drill))
        self.assertNotIn('media.tar.gz', code, '恢复演练不应再依赖已废弃的 media.tar.gz')

    def test_drill_reads_media_snapshots(self):
        self.assertIn('media-snapshots', self.drill)

    def test_drill_checks_database_checksum_selectively(self):
        """SHA256SUMS 里媒体路径被 backup.sh 改写过，不能整份 --check。"""
        self.assertIn('database.dump', self.drill)
        self.assertIn('sha256sum', self.drill)
        self.assertNotIn('sha256sum --check SHA256SUMS', self.drill)

    def test_drill_cleans_up_partial_test_database(self):
        """恢复失败时必须清理半成品临时库，否则下次演练会误判。"""
        self.assertIn('trap cleanup EXIT', self.drill)
        self.assertIn('dropdb --if-exists "$TEST_DB"', self.drill)

    def test_drill_refuses_production_database(self):
        self.assertIn('warehouse_restore_drill', self.drill)
        self.assertIn('不能是生产数据库', self.drill)


class DeployScriptsShippedTests(SimpleTestCase):
    """release 目录必须包含 deploy/，否则部署后无法用本版本脚本复核。

    历史缺陷：install.sh 只 rsync backend/，release 里没有 deploy，
    导致 security-check.sh / check-all.sh / rollback.sh 只在 /tmp 临时目录存在，
    /tmp 一清理就丢失，无法在服务器上按「这台机器正在跑的那个版本」做门禁复核。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repository_root = Path(__file__).resolve().parents[3]
        cls.install_script = (
            cls.repository_root / 'deploy' / 'ubuntu22' / 'install.sh'
        ).read_text(encoding='utf-8')

    def test_install_syncs_deploy_directory(self):
        self.assertIn('$REPO_ROOT/deploy', self.install_script,
                      'install.sh 未把 deploy/ 同步进 release')
        self.assertIn('$CANDIDATE_DIR/deploy', self.install_script,
                      'install.sh 未创建 release 内的 deploy 目录')

    def test_deploy_sync_happens_before_service_restart(self):
        """deploy/ 必须在正常上线路径上就位。

        注意：脚本里第一处 `systemctl restart after-sales-warehouse` 位于
        deployment_failed() 错误处理函数（回滚路径），不是正常上线顺序，
        因此这里比对「最后一处」restart 之前必须已同步 deploy/。
        """
        sync_at = self.install_script.index('$CANDIDATE_DIR/deploy')
        restart_at = self.install_script.rindex('systemctl restart after-sales-warehouse')
        self.assertLess(sync_at, restart_at)

    def test_deploy_sync_outside_failure_handler(self):
        """同步动作应在失败处理函数定义之后、正常流程中，而非只在回滚里。"""
        failure_handler_at = self.install_script.index('deployment_failed() {')
        sync_at = self.install_script.index('$CANDIDATE_DIR/deploy')
        self.assertGreater(sync_at, failure_handler_at,
                           'deploy/ 同步应位于正常部署流程，而非失败处理函数内')

    def test_scripts_referenced_by_release_exist_in_source(self):
        """release 内会被调用的脚本必须在仓库中存在。"""
        deploy_dir = self.repository_root / 'deploy' / 'ubuntu22'
        for name in ('install.sh', 'rollback.sh', 'check-all.sh', 'security-check.sh'):
            with self.subTest(script=name):
                self.assertTrue((deploy_dir / name).exists(), f'{name} 缺失')
