import os
from pathlib import Path

from django.conf import settings


def release_info() -> dict:
    """读取部署清单，供管理员确认当前实际运行的版本。"""

    info = {
        'release_version': os.getenv('APP_VERSION', ''),
        'source_revision': os.getenv('GIT_REVISION', ''),
        'source_revision_short': '',
        'deployed_at': '',
        'working_tree_dirty': '',
        'source_content_sha256': '',
        'source': '环境变量',
    }
    candidates = [
        Path(settings.BASE_DIR).parent / 'current-release',
        Path(settings.BASE_DIR) / 'current-release',
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except OSError:
            continue
        for line in content.splitlines():
            key, _, value = line.partition('=')
            key = key.strip()
            if key in info:
                info[key] = value.strip()
        info['source'] = str(path)
        break
    if not info['source_revision_short'] and info['source_revision']:
        info['source_revision_short'] = info['source_revision'][:10]
    if not info['release_version']:
        info['release_version'] = '未标记版本（本地开发或手工源码）'
    return info