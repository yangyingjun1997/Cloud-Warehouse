"""cloudflared selfheal 告警策略回归（2026-11-23）。

背景：用户反馈钉钉群持续收到「DNS 故障」告警。排查结论是公司内网
DNS（10.10.5.3/10.10.5.4）间歇超时，cloudflared 反复解析
region1.v2.argotunnel.com 失败，3 天内触发 9 次。隧道本身会自动恢复
（稳定时 4 条边缘连接），告警属噪音。

踩过的坑：第一次只在服务器上手工改了 /usr/local/sbin/ 下的脚本，
下次部署时被 install.sh 从 deploy/ubuntu22/cloudflared-selfheal.sh
重新安装覆盖，告警又回来了。因此必须有测试锁住仓库里的源文件。
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO_ROOT = Path(settings.BASE_DIR).parent
SELFHEAL = REPO_ROOT / 'deploy' / 'ubuntu22' / 'cloudflared-selfheal.sh'


class CloudflaredSelfhealAlertPolicyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = SELFHEAL.read_text(encoding='utf-8')

    def test_script_exists(self):
        self.assertTrue(SELFHEAL.exists(), 'selfheal 源脚本缺失')

    def test_dns_persistent_alert_disabled(self):
        """DNS 持续故障不再推钉钉。"""
        self.assertNotIn(
            "notify '售后仓库 DNS 持续故障'", self.text,
            'DNS 持续故障告警应已停用（内网 DNS 抖动属常态，属噪音）',
        )

    def test_tunnel_persistent_alert_disabled(self):
        """隧道持续无法连接不再推钉钉。"""
        self.assertNotIn(
            "notify '售后仓库隧道持续无法连接'", self.text,
            '隧道持续无法连接告警应已停用',
        )

    def test_recovery_notifications_kept(self):
        """两条「已恢复」消息应保留：是好消息，且各自限流。"""
        self.assertIn("notify '售后仓库 DNS 故障已自动修复'", self.text)
        self.assertIn("notify '售后仓库隧道已自动恢复'", self.text)

    def test_self_healing_actions_preserved(self):
        """停用的只是推送，自愈动作必须全部保留。"""
        for action in (
            'systemctl start "$SERVICE"',
            'systemctl restart systemd-resolved',
            'resolvectl flush-caches',
            'systemctl restart "$SERVICE"',
        ):
            with self.subTest(action=action):
                self.assertIn(action, self.text, f'自愈动作被误删：{action}')

    def test_metrics_based_health_check_preserved(self):
        """健康判定必须仍基于 metrics 端点，不能退回日志窗口判定。"""
        self.assertIn('cloudflared_tunnel_ha_connections', self.text)
        self.assertIn('METRICS_URL', self.text)

    def test_dns_check_preserved(self):
        self.assertIn('region1.v2.argotunnel.com', self.text)

    def test_disabled_notice_logged(self):
        """停用推送后应留下日志，便于日后排查。"""
        self.assertIn('已停用钉钉告警', self.text)
