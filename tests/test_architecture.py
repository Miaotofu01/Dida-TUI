"""架构不变量：七个模块的边界，以及 TUI 的依赖方向。"""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SEVEN_MODULES = [
    "dida.config",  # 配置与凭据
    "dida.api.client",  # 滴答 API 客户端
    "dida.storage.store",  # 本地存储
    "dida.sync.engine",  # 同步引擎
    "dida.logical_day",  # 逻辑日
    "dida.date_parser",  # 日期解析器
    "dida.tui.app",  # TUI
]

# TUI 只许通过同步引擎读写；存储与网络句柄不许出现在这一层
FORBIDDEN_IN_TUI = ("dida.api.client", "dida.api.transport", "dida.storage")


@pytest.mark.parametrize("module", SEVEN_MODULES)
def test_module_is_importable(module):
    assert importlib.import_module(module) is not None


def test_tui_only_reaches_the_sync_engine():
    imported: set[str] = set()
    for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert "dida.sync.engine" in imported, "扫描器没找到 TUI 对引擎的依赖，扫描逻辑失效了"
    assert not [name for name in imported if name.startswith(FORBIDDEN_IN_TUI)]
