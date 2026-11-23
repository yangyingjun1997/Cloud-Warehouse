"""依赖文件一致性校验。

问题（2026-11-23）：
三个依赖文件并存，但没有任何机制保证它们一致：
- requirements.txt        版本范围（供人读，未固定）
- requirements-lock.txt   精确版本（跨平台）
- requirements-linux.txt  lock + gunicorn + packaging（服务器实际安装）

风险：改了 requirements.txt 却忘记重生 lock，本机能跑但服务器装到不同版本；
或 lock 漏了某个包，部署时静默装上未固定版本。

本脚本校验：
1. requirements.txt 里的每个直接依赖，都在 lock 里出现
2. lock 里的版本，与当前 venv 实际安装版本一致（本机校验时）
3. requirements-linux.txt 必须 -r lock 并补齐 gunicorn

用法：
    python -m scripts.check_requirements_consistency        # 只做静态校验
    python -m scripts.check_requirements_consistency --venv # 额外比对已装版本
"""

import argparse
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REQ = BACKEND / 'requirements.txt'
LOCK = BACKEND / 'requirements-lock.txt'
LINUX = BACKEND / 'requirements-linux.txt'

# 环境标记里排除的包不算服务器必需（如 Windows 专用）
PLATFORM_MARKERS = ('platform_system', 'sys_platform')


def normalize(name):
    """PEP 503 名称规范化：大小写与 -/_/. 等价。"""
    return re.sub(r'[-_.]+', '-', name.strip().lower())


def parse_requirements(path):
    """解析 requirements 文件，返回 [(name, specifier, has_marker), ...]。"""
    entries = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        has_marker = ';' in line
        if has_marker:
            line = line.split(';', 1)[0].strip()
        match = re.match(r'^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(.*)$', line)
        if not match:
            continue
        entries.append((normalize(match.group(1)), match.group(3).strip(), has_marker))
    return entries


def parse_lock(path):
    """解析 lock 文件，返回 {normalized_name: version}。"""
    locked = {}
    for name, specifier, _ in parse_requirements(path):
        if specifier.startswith('=='):
            locked[name] = specifier[2:].strip()
    return locked


def check_direct_deps_covered():
    """requirements.txt 的每个直接依赖，必须出现在 lock 或 linux 文件中。

    分工约定：跨平台依赖进 lock；仅服务器需要的（gunicorn）进 linux 文件。
    两者任一覆盖即可，但必须被固定版本，不能谁都不管。
    """
    problems = []
    direct = parse_requirements(REQ)
    locked = parse_lock(LOCK)
    linux_pinned = {
        name: specifier
        for name, specifier, _ in parse_requirements(LINUX)
        if specifier.startswith('==')
    }
    for name, specifier, has_marker in direct:
        if name in locked or name in linux_pinned:
            continue
        problems.append(
            f'{name} 在 requirements.txt 中声明，但既不在 requirements-lock.txt '
            f'也不在 requirements-linux.txt 的固定版本里'
        )
    return problems


def check_linux_includes_lock():
    """requirements-linux.txt 必须 -r lock，并显式列出服务器专用包。"""
    problems = []
    text = LINUX.read_text(encoding='utf-8')
    lines = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    if not any(line in ('-r requirements-lock.txt', '-r requirements-lock.txt ') for line in lines):
        problems.append('requirements-linux.txt 未包含 "-r requirements-lock.txt"')
    locked = parse_lock(LOCK)
    linux_extra = {name for name, _, _ in parse_requirements(LINUX)}
    # 服务器专用包（不在 lock 里）必须显式固定版本
    for name, specifier, _ in parse_requirements(LINUX):
        if name not in locked and not specifier.startswith('=='):
            problems.append(
                f'requirements-linux.txt 的 {name} 未固定版本（应写 ==x.y.z）'
            )
    if 'gunicorn' not in linux_extra:
        problems.append('requirements-linux.txt 缺少 gunicorn（服务器必需）')
    return problems


def check_installed_matches_lock():
    """lock 里的版本与当前 venv 实际安装版本一致。"""
    problems = []
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return ['当前 Python 不支持 importlib.metadata']

    locked = parse_lock(LOCK)
    for name, expected in sorted(locked.items()):
        try:
            actual = version(name)
        except PackageNotFoundError:
            problems.append(f'{name} 已在 lock 中固定为 {expected}，但当前环境未安装')
            continue
        if actual != expected:
            problems.append(
                f'{name} 版本不一致：lock={expected} 实际={actual}'
            )
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description='校验依赖文件一致性')
    parser.add_argument(
        '--venv', action='store_true',
        help='额外比对当前 Python 环境已安装的版本',
    )
    options = parser.parse_args(argv)

    problems = []
    problems += check_direct_deps_covered()
    problems += check_linux_includes_lock()
    if options.venv:
        problems += check_installed_matches_lock()

    if problems:
        print('依赖文件不一致：', file=sys.stderr)
        for item in problems:
            print(f'  - {item}', file=sys.stderr)
        return 1

    direct_count = len(parse_requirements(REQ))
    lock_count = len(parse_lock(LOCK))
    print(
        f'依赖文件一致：requirements.txt 的 {direct_count} 个直接依赖'
        f'均已在 lock 的 {lock_count} 个固定版本中覆盖。'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
