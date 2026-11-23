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
