"""P2-15 回归：上传文案与实际限制必须一致，且同类图片在各入口限制相同。

背景：asset_lifecycle.html 曾写「单个文件最大 20 MB」，而实际
MAX_IMAGE_SIZE=10MB、MAX_ATTACHMENT_SIZE=100MB；
lifecycle_services 又单独对图片传 max_size=20MB，与资产照片入口的 10MB 不一致。
"""

import re

from django.conf import settings
from django.test import SimpleTestCase

from apps.common.upload_validation import MAX_ATTACHMENT_SIZE, MAX_IMAGE_SIZE


class UploadLimitConsistencyTests(SimpleTestCase):
    def test_limits_are_as_documented(self):
        self.assertEqual(MAX_IMAGE_SIZE, 10 * 1024 * 1024)
        self.assertEqual(MAX_ATTACHMENT_SIZE, 100 * 1024 * 1024)

    def test_lifecycle_template_matches_limits(self):
        from pathlib import Path

        base = Path(settings.BASE_DIR) / 'templates'
        text = (base / 'asset_lifecycle.html').read_text(encoding='utf-8')
        # 必须同时体现图片与附件两个限制
        self.assertIn('10 MB', text, '文案未体现图片 10 MB 限制')
        self.assertIn('100 MB', text, '文案未体现附件 100 MB 限制')
        self.assertNotIn('单个文件最大 20 MB', text, '旧的 20 MB 文案应已移除')

    def test_lifecycle_service_uses_shared_image_limit(self):
        """图片上传必须走统一 MAX_IMAGE_SIZE，不再局部放宽到 20MB。"""
        from pathlib import Path

        import apps.inventory.lifecycle_services as module

        source = Path(module.__file__).read_text(encoding='utf-8')
        self.assertNotIn('max_size=20 * 1024 * 1024', source,
                         '不应再对图片单独指定 20MB')
        self.assertIn('sanitize_image_upload(uploaded_file)', source)

    def test_no_template_claims_unsupported_size(self):
        """扫描全部模板，确认没有「N MB」文案与实际限制冲突。"""
        from pathlib import Path

        base = Path(settings.BASE_DIR) / 'templates'
        image_mb = MAX_IMAGE_SIZE // (1024 * 1024)
        attachment_mb = MAX_ATTACHMENT_SIZE // (1024 * 1024)
        allowed = {image_mb, attachment_mb, 2, 10}  # 2=站点图标, 10=Excel 导入

        problems = []
        for tpl in base.glob('*.html'):
            text = tpl.read_text(encoding='utf-8', errors='replace')
            for match in re.finditer(r'(\d+)\s*MB', text):
                value = int(match.group(1))
                if value not in allowed:
                    problems.append(f'{tpl.name}: {match.group(0)}')
        self.assertEqual(problems, [], f'文案数值与限制不符: {problems}')
