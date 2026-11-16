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
