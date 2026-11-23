"""离线队列敏感字段不落盘（R10-P2-04）。

背景：离线队列把整张表单原样存进 IndexedDB，包括接收人姓名、手机号、
外部持有人、申请原因等个人信息。IndexedDB 明文可读——任何拿到这台设备的人
（共用电脑、维修、浏览器同步）都能翻出这些内容，条目还保留 7 天。
用户填这些字段时并没有预期它们被长期留在浏览器里。

修复：敏感字段不入队，只记录字段名；含敏感字段的记录不自动同步
（缺字段提交必然失败，还可能产生半截单据）。

这是前端 JS 逻辑，用文本断言锁住关键约定；真实浏览器行为需现场验收。
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

OFFLINE_JS = Path(settings.BASE_DIR) / 'apps' / 'common' / 'static' / 'warehouse' / 'offline.js'


class OfflineSensitiveFieldTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.text = OFFLINE_JS.read_text(encoding='utf-8')

    def test_offline_js_exists(self):
        self.assertTrue(OFFLINE_JS.exists())

    def test_sensitive_pattern_list_present(self):
        """必须有敏感字段识别名单。"""
        self.assertIn('SENSITIVE_FIELD_PATTERNS', self.text)
        self.assertIn('isSensitiveField', self.text)
        self.assertIn('splitSensitiveFields', self.text)

    def test_masks_recipient_fields(self):
        for pattern in (r'recipient_(name|phone|mobile|contact|company)',
                        r'external_holder',
                        r'(phone|mobile|contact_phone|contact_name)',
                        r'(reason|note|remark|comment)'):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.text, f'敏感字段规则缺失：{pattern}')

    def test_fields_are_split_before_storing(self):
        """保存时必须先分流，不能直接落盘全部字段。"""
        self.assertIn('splitSensitiveFields(allFields)', self.text)
        # 确保不再把原始 allFields 直接写进 item
        self.assertNotRegex(
            self.text,
            r'fields:\s*allFields',
            '不得把未脱敏的完整字段写入队列项',
        )

    def test_omitted_fields_recorded(self):
        """要记录哪些字段被省略，便于提示用户重填。"""
        self.assertIn('omittedFields', self.text)

    def test_auto_sync_disabled_when_sensitive_omitted(self):
        """含敏感字段的记录不得自动同步。"""
        self.assertRegex(
            self.text,
            r"autoSync:\s*form\.dataset\.offlineAutoSync === 'true' && omittedFields\.length === 0",
            '含被省略字段时必须关闭自动同步',
        )

    def test_auto_sync_filter_respected_in_sync_queue(self):
        """同步入口必须按 autoSync 过滤。"""
        self.assertIn("item.status === 'pending' && item.autoSync", self.text)

    def test_user_notified_about_omission(self):
        """必须告知用户敏感内容未被保存。"""
        self.assertIn('避免在本机留存接收人姓名、电话等信息', self.text)

    def test_still_uses_indexeddb_not_localstorage_for_queue(self):
        """队列应仍在 IndexedDB；localStorage 只存设备/用户标识。"""
        self.assertIn("const STORE = 'queue'", self.text)
        self.assertIn('indexedDB', self.text)
