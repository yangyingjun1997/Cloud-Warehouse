"""钉钉渠道路由回归：默认单群行为不变，ops 渠道可独立配置或回落。"""

from django.test import SimpleTestCase, override_settings

from apps.notifications.dingtalk import (
    CHANNEL_DEFAULT,
    CHANNEL_OPS,
    _channel_settings,
)

DEFAULT_HOOK = 'https://oapi.dingtalk.com/robot/send?access_token=default-token'
OPS_HOOK = 'https://oapi.dingtalk.com/robot/send?access_token=ops-token'


@override_settings(DINGTALK_WEBHOOK=DEFAULT_HOOK, DINGTALK_SECRET='default-secret')
class DefaultChannelTests(SimpleTestCase):
    def test_default_channel_uses_webhook(self):
        webhook, secret = _channel_settings(CHANNEL_DEFAULT)
        self.assertEqual(webhook, DEFAULT_HOOK)
        self.assertEqual(secret, 'default-secret')

    def test_ops_falls_back_when_not_configured(self):
        """未配置独立运维群时必须回落默认群，否则运维告警会静默丢失。"""
        webhook, secret = _channel_settings(CHANNEL_OPS)
        self.assertEqual(webhook, DEFAULT_HOOK)
        self.assertEqual(secret, 'default-secret')

    def test_unknown_channel_falls_back(self):
        webhook, _ = _channel_settings('不存在的渠道')
        self.assertEqual(webhook, DEFAULT_HOOK)

    def test_empty_channel_uses_default(self):
        webhook, _ = _channel_settings('')
        self.assertEqual(webhook, DEFAULT_HOOK)


@override_settings(
    DINGTALK_WEBHOOK=DEFAULT_HOOK,
    DINGTALK_SECRET='default-secret',
    DINGTALK_WEBHOOK_OPS=OPS_HOOK,
    DINGTALK_SECRET_OPS='ops-secret',
)
class OpsChannelTests(SimpleTestCase):
    def test_ops_uses_its_own_webhook_and_secret(self):
        webhook, secret = _channel_settings(CHANNEL_OPS)
        self.assertEqual(webhook, OPS_HOOK)
        self.assertEqual(secret, 'ops-secret')

    def test_default_unaffected_by_ops_config(self):
        webhook, secret = _channel_settings(CHANNEL_DEFAULT)
        self.assertEqual(webhook, DEFAULT_HOOK)
        self.assertEqual(secret, 'default-secret')


@override_settings(
    DINGTALK_WEBHOOK=DEFAULT_HOOK,
    DINGTALK_SECRET='default-secret',
    DINGTALK_WEBHOOK_OPS=OPS_HOOK,
)
class OpsChannelSecretFallbackTests(SimpleTestCase):
    def test_ops_without_secret_falls_back_to_default_secret(self):
        """独立群没配 secret 时沿用默认 secret，避免消息因缺少签名被拒。"""
        _, secret = _channel_settings(CHANNEL_OPS)
        self.assertEqual(secret, 'default-secret')


class OutboxChannelFieldTests(SimpleTestCase):
    def test_outbox_has_channel_field_with_default(self):
        from apps.notifications.models import DingTalkOutbox

        field = DingTalkOutbox._meta.get_field('channel')
        self.assertEqual(field.default, 'default')
