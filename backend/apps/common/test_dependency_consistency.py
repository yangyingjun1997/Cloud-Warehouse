"""依赖版本一致性回归。

背景（2026-11-19 实测）：本机 venv 是 Django 5.2.9 / DRF 3.16.1 / Pillow 12.3.0，
生产是 5.2.17 / 3.18.0 / 12.3.0，锁文件是 5.2.19 / 3.18.3 / 13.0.0。
三套环境三套版本，OSV 实测：本机 121 条已知漏洞、生产 65 条、锁文件 0 条。

根因：install.sh 的离线更新分支直接 `cp -a` 旧 venv，从不校验是否仍满足锁文件，
导致 venv 永远不重建（生产 venv 一直停在 Python 3.10，而 Pillow 13 要求 >= 3.11）。

本测试锁定两件事：
1. 当前运行环境的依赖必须与锁文件一致（防止再次漂移）。
2. install.sh 必须包含 venv 兼容性闸门（防止离线更新跳过重建）。
"""

import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parents[3]
LOCK_FILE = BASE_DIR / 'backend' / 'requirements-lock.txt'
INSTALL_SH = BASE_DIR / 'deploy' / 'ubuntu22' / 'install.sh'


def parse_lock():
    locked = {}
    for line in LOCK_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if '==' in line and not line.startswith('#'):
            name, version = line.split('==', 1)
            locked[name.strip().lower()] = version.strip()
    return locked


class DependencyConsistencyTests(SimpleTestCase):
    def test_installed_versions_match_lock_file(self):
        """运行环境装的包必须与锁文件逐项一致。

        不一致意味着：本地测试验证的组合 != 生产实际组合，
        升级引入的回归无法被测试发现。
        """
        locked = parse_lock()
        self.assertGreater(len(locked), 10, '锁文件解析异常')

        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--format=freeze'],
            capture_output=True, text=True, timeout=120,
        )
        actual = {}
        for line in result.stdout.splitlines():
            if '==' in line:
                name, version = line.split('==', 1)
                actual[name.strip().lower()] = version.strip()

        mismatch = [
            f'{name}: 锁文件={want}, 实际={actual.get(name)}'
            for name, want in locked.items()
            if actual.get(name) != want
        ]
        self.assertEqual(
            mismatch, [],
            '以下依赖与锁文件不一致，请按锁文件重建环境：\n  ' + '\n  '.join(mismatch),
        )

    def test_python_supports_pillow_13(self):
        """Pillow 13 要求 Python >= 3.11，低于此版本的 venv 无法装上锁文件版本。"""
        locked = parse_lock()
        pillow_version = locked.get('pillow', '')
        if not pillow_version.startswith('13.'):
            self.skipTest(f'Pillow 版本为 {pillow_version}，跳过 3.11 要求检查')
        self.assertGreaterEqual(
            sys.version_info[:2], (3, 11),
            f'当前 Python {sys.version_info.major}.{sys.version_info.minor} '
            '低于 Pillow 13 要求的 3.11',
        )


class InstallScriptVenvGateTests(SimpleTestCase):
    """install.sh 离线分支必须有 venv 兼容性闸门。"""

    def setUp(self):
        self.text = INSTALL_SH.read_text(encoding='utf-8')

    def test_offline_branch_checks_python_version(self):
        self.assertIn(
            'sys.version_info >= (3, 11)', self.text,
            'install.sh 未校验 venv 的 Python 版本，离线更新会复用旧 venv',
        )

    def test_offline_branch_checks_lock_consistency(self):
        self.assertIn(
            'requirements-lock.txt', self.text,
            'install.sh 未校验锁文件一致性',
        )
        self.assertIn(
            '依赖与锁文件不一致', self.text,
            'install.sh 缺少依赖不一致时的重建分支',
        )

    def test_rebuild_path_exists(self):
        """不满足闸门时必须能重建，而不是静默沿用旧 venv。"""
        self.assertRegex(
            self.text, r'REUSE_VENV=0',
            'install.sh 缺少 REUSE_VENV 标记',
        )
        self.assertIn(
            'python3.12 -m venv "$CANDIDATE_DIR/venv"', self.text,
            'install.sh 缺少 python3.12 重建 venv 的分支',
        )

    def test_rebuild_failure_is_loud(self):
        """重建时安装依赖失败必须明确报错退出，不能静默降级。"""
        self.assertIn('重建 venv 时安装依赖失败', self.text)
        self.assertRegex(
            self.text, r'重建 venv 时安装依赖失败[\s\S]{0,200}exit 1',
            '依赖安装失败后没有 exit 1',
        )
