"""把标签打印客户端打包成单个 exe。

在 Windows 上执行：

    pip install pillow pywin32 qrcode pyinstaller
    python build_exe.py

产物：dist/BwtLabelClient.exe

目标机器只需要这个 exe，不需要装 Python。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'BwtLabelClient'
ENTRY = os.path.join(HERE, 'label_client.py')
LOGO = os.path.join(HERE, 'assets', 'label-logo-mono.png')


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print('缺少 PyInstaller，请先执行：pip install pyinstaller', file=sys.stderr)
        return 1

    for module_name in ('PIL', 'win32print', 'qrcode'):
        try:
            __import__(module_name)
        except ImportError:
            print(f'缺少依赖 {module_name}，请先执行：pip install -r requirements.txt',
                  file=sys.stderr)
            return 1

    for stale in ('build', 'dist'):
        path = os.path.join(HERE, stale)
        if os.path.isdir(path):
            shutil.rmtree(path)

    command = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--onefile',
        '--windowed',
        '--name', NAME,
        '--collect-submodules', 'PIL',
        '--hidden-import', 'win32print',
        '--hidden-import', 'win32ui',
        '--hidden-import', 'PIL.ImageWin',
        '--hidden-import', 'qrcode',
    ]
    if os.path.exists(LOGO):
        command.extend(['--add-data', f'{LOGO}{os.pathsep}.'])
    command.append(ENTRY)
    print('执行：', ' '.join(command))
    result = subprocess.run(command, cwd=HERE)
    if result.returncode != 0:
        print('打包失败。', file=sys.stderr)
        return result.returncode

    exe = os.path.join(HERE, 'dist', f'{NAME}.exe')
    if os.path.exists(exe):
        size_mb = os.path.getsize(exe) / 1024 / 1024
        print(f'\n完成：{exe}  ({size_mb:.1f} MB)')
        print('把这个 exe 拷到打印机那台电脑，双击即可。')
        return 0
    print('未找到产物 exe。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
