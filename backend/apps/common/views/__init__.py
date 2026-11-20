"""common 视图包。

为什么改成包（2026-11-19 第十一轮审查 P2-03）：
    legacy.py 原有 5084 行、89 个顶层函数，是事实上的「上帝模块」。
    完整拆分需要迁移 63 个视图与大量共享辅助函数，回归风险高；
    而当前是单/小团队开发，Git 冲突与 IDE 卡顿的实际影响有限。

    因此采用渐进式方案：先把 views.py 原地改名为 views/legacy.py 包住，
    新功能按领域写进本包下的独立模块，legacy 只在必要时逐步搬运。
    这样零回归风险（现有代码一行未改），新代码天然分文件。

目录约定：
    legacy.py      历史视图（资产、申请、审批、报表、导入、账号等）
    __init__.py    转发层，让 `from apps.common import views` 与 `views.xxx`
                   的既有引用继续可用

新增视图时请新建模块，例如：
    views/reports.py    -> 报表相关
    views/assets.py     -> 资产相关
然后在下面显式导入，保持 `views.<name>` 可访问。
"""

import importlib as _importlib

_legacy = _importlib.import_module('.legacy', __name__)

# 把 legacy 的公开名字（不含模块与下划线开头）转发到包命名空间，
# 使 urls.py 的 `views.xxx` 与测试里的 `views._helper` 都继续可用。
# 显式区分「模块」与「可调用/变量」：模块对象不转发，避免污染包命名空间。
import types as _types


def _export(name, value):
    globals()[name] = value


for _name in dir(_legacy):
    if _name.startswith('__'):
        continue
    _value = getattr(_legacy, _name)
    # 私有辅助函数（_asset_return_target 等）要转发，测试与 urls 在用。
    if isinstance(_value, _types.ModuleType):
        continue
    _export(_name, _value)

del _name, _value, _export

__all__ = [
    name for name in globals()
    if not name.startswith('_') and not isinstance(globals()[name], _types.ModuleType)
]
