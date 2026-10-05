"""架构不变量：七个模块的边界，TUI 的依赖方向，以及「只用终端 16 色」。

TUI 的规矩写成**允许表**，不是黑名单：``src/dida/tui/**`` 只许 import ``dida.sync.engine``
（它唯一的读写入出口）与 ``dida.tui.*`` 自己这一支，``dida`` 命名空间里别的任何东西都算越界。

允许表比黑名单安全的地方正是这一条：以后新加的模块、或者一条绕开黑名单的写法，默认是**违规**，
而不是默认放行。黑名单只认 ``node.module`` 那串字，``from dida import storage`` 收到的是
``dida``、``from ..storage import store`` 收到的是 ``storage``，两条都从它底下走过去。

只审 ``dida`` 命名空间里的 import：stdlib 与 Textual / Rich 不归这条规矩管（TUI 当然要用它们）。
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
"""颜色字面量：``#rrggbb`` / ``#rrggbbaa`` / ``#rgb``（不是照抄实现——这是终端那条规矩的形状）。"""

SEVEN_MODULES = [
    "dida.config",  # 配置与凭据
    "dida.api.client",  # 滴答 API 客户端
    "dida.storage.store",  # 本地存储
    "dida.sync.engine",  # 同步引擎
    "dida.logical_day",  # 逻辑日
    "dida.tui.app",  # TUI
]

ALLOWED_IN_TUI = ("dida.sync.engine", "dida.tui")
"""TUI 的允许表：引擎的公开面，以及它自己这一支（``dida.tui.*``）。

``dida.logical_day`` 不在表里，是故意的：分组、逾期判定、逻辑日换算都是引擎的事，TUI 拿到
的是已经判断好的视图模型（AGENTS.md 与 spec 的接口契约）。
"""


def _module_of(path: Path) -> str:
    """这个文件在包里的名字：``src/dida/tui/pages/index.py`` → ``dida.tui.pages.index``。"""
    parts = list(path.relative_to(ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_of(path: Path) -> str:
    """这个文件所在的包：``src/dida/tui/keys.py`` → ``dida.tui``（``__init__.py`` 就是它自己）。"""
    module = _module_of(path)
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def _absolute_module(node: ast.ImportFrom, *, package: str) -> str:
    """``ImportFrom`` 的绝对模块名；相对导入按文件所在的包往上走 ``node.level`` 层。

    ``level == 1`` 是当前包、``level == 2`` 是上一层：写在 ``dida/tui/keys.py`` 里的
    ``from ..storage import store`` 落到的是 ``dida.storage``，而不是 ``storage``。
    """
    if node.level == 0:
        return node.module or ""
    parts = package.split(".") if package else []
    kept = len(parts) - (node.level - 1)
    base = parts[:kept] if kept > 0 else []
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


def _is_ours(name: str) -> bool:
    """这是不是 ``dida`` 命名空间里的名字（stdlib 与第三方照原样放过）。"""
    return name == "dida" or name.startswith("dida.")


def _referenced_modules(source: str, *, package: str) -> set[str]:
    """这份源码 import 到的 ``dida`` 命名空间里的模块名，一律换算成绝对名。

    ``from X import a, b`` 里被拉进来的可能不是 ``X`` 而是它的子模块——``from dida import
    storage`` 拿到的其实是 ``dida.storage``——所以每个名字都跟着 ``X`` 一起算一个候选。
    """
    referenced: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            referenced.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_module(node, package=package)
            if base:
                referenced.add(base)
            referenced.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return {name for name in referenced if _is_ours(name)}


def _is_allowed(name: str) -> bool:
    """允许表里的名字，或它的容器。

    ``from dida.sync import engine`` 与 ``from dida import tui`` 拉的正是表里那一支，所以要放行
    它们路过的容器（``dida`` / ``dida.sync``）；真正被挡住的是 ``dida.storage`` / ``dida.api``
    这些表外的兄弟支。
    """
    for allowed in ALLOWED_IN_TUI:
        if name == allowed or name.startswith(f"{allowed}."):
            return True
        if allowed.startswith(f"{name}."):
            return True
    return False


def _violations(source: str, *, package: str) -> tuple[str, ...]:
    """这份源码里越界的 ``dida`` import，按名字排好（空元组 = 干净）。"""
    return tuple(
        sorted(name for name in _referenced_modules(source, package=package) if not _is_allowed(name))
    )


def _scan_tui() -> tuple[set[str], list[str]]:
    """扫一遍 ``src/dida/tui``：见过的模块名，以及每处越界的 ``文件: 模块``。"""
    referenced: set[str] = set()
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py")):
        found = _referenced_modules(path.read_text(encoding="utf-8"), package=_package_of(path))
        referenced |= found
        offenders += [f"{path.name}: {name}" for name in sorted(found) if not _is_allowed(name)]
    return referenced, offenders


@pytest.mark.parametrize("module", SEVEN_MODULES)
def test_module_is_importable(module):
    assert importlib.import_module(module) is not None


def test_tui_only_reaches_the_sync_engine():
    referenced, offenders = _scan_tui()

    assert "dida.sync.engine" in referenced, "扫描器没找到 TUI 对引擎的依赖，扫描逻辑失效了"
    assert offenders == [], "TUI 只许 import dida.sync.engine 与 dida.tui.*，越界的有：\n" + "\n".join(
        offenders
    )


def test_the_guard_catches_an_absolute_import_that_reaches_past_the_engine():
    """``from dida import storage``：``node.module`` 只有 ``dida`` 三个字，黑名单看不见它。"""
    assert _violations("from dida import storage\n", package="dida.tui") == ("dida.storage",)


def test_the_guard_catches_a_relative_import_that_escapes_the_package():
    """``from ..storage import store``：``node.module`` 只有 ``storage``，同样溜得过去。"""
    assert _violations("from ..storage import store\n", package="dida.tui") == (
        "dida.storage",
        "dida.storage.store",
    )


def test_the_guard_lets_the_engine_and_the_tui_itself_through():
    source = (
        "from dida.sync.engine import TaskGroup\n"
        "from dida.tui.pages.index import IndexPage\n"
        "from . import keys\n"
        "from dida.sync import engine\n"
    )

    assert _violations(source, package="dida.tui") == ()


@pytest.mark.parametrize(
    "source",
    [
        "import dida.storage\n",
        "from dida.storage.store import Store\n",
        "from dida import logical_day\n",
        "from ..api.client import DidaApiClient\n",
        "from dida.sync import view\n",
    ],
)
def test_the_guard_catches_every_way_into_a_module_outside_the_table(source):
    """表外的一切都算违规——包括同一支下面那些没被点名的模块（``dida.sync.view``）。"""
    assert _violations(source, package="dida.tui")


def test_the_tui_never_hardcodes_a_hex_colour():
    """只用终端 16 色：源码里不许出现 ``#rrggbb`` 之类的颜色字面量。

    颜色字面量写死之后，浅色主题、``NO_COLOR``、以及不是 24 位的终端上那一行就糊了。
    这条规矩扫的是**源码文本**，与渲染出来的屏幕无关，所以它和上面那些边界测试住在一起
    （原本钉在 ``test_app_view.py`` 里，#32 搬出来的——那是纯模块边界的房客）。
    """
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
        if HEX_COLOUR.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
