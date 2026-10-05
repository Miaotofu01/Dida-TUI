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
from typing import Iterator

import pytest
from rich.style import Style

ROOT = Path(__file__).resolve().parents[1]

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
"""颜色字面量：``#rrggbb`` / ``#rrggbbaa`` / ``#rgb``（不是照抄实现——这是终端那条规矩的形状）。"""

COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?)\s*\(")
"""``rgb(…)`` / ``hsl(…)``：写死的颜色，只是换了个写法。"""

THEME_VARIABLE = re.compile(r"\$[A-Za-z][\w-]*")
"""``$surface`` 这类 Textual 主题变量：它们在 ``textual-dark`` 下是真彩色值（``#1E1E1E``）。"""

NAMED_COLOUR = re.compile(
    r":\s*(?!ansi_)[A-Za-z]+\s*[;}]"
)
"""CSS 声明里的具名色（``color: red;``）：Textual 那边 ``red`` 是 ``#FF0000``。

``ansi_*`` 是唯一合法的拼法，所以先把它排除掉。
"""

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


def test_the_tui_names_colours_in_exactly_one_place():
    """只用终端 16 色：颜色字面量只许出现在 :mod:`dida.tui.theme` 一处（工单 #51）。

    四类都拦：``#rrggbb`` / ``rgb()`` / ``hsl()`` / CSS 具名色 / ``$`` 主题变量。只拦 hex
    的那一版实测挡不住后面三种（``rgb(255,0,0)``、``red``、``$surface`` 全部通过），而任意
    一条都能把「跟随终端主题」静默毁掉：具名色与主题变量都是 Textual 那边的真彩色。
    （纯 ANSI 名 ``ansi_cyan`` 不受影响——那正是主题自己拼出来的写法。）

    **文档字符串不算**：仓库里到处在引用实测色值当证据（ADR-0007、theme 的模块文档），
    那是说明，不是样式。扫的是**代码里的字符串**——样式在运行时只可能从那里来。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for lineno, style_text in _style_strings(source):
            for label, pattern in (
                ("hex", HEX_COLOUR),
                ("rgb()/hsl()", COLOUR_FUNCTION),
                ("$ 主题变量", THEME_VARIABLE),
            ):
                if pattern.search(style_text):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {label} in {style_text!r}")
        for lineno, declaration in _css_declarations(source):
            found = NAMED_COLOUR.search(declaration)
            if found:
                offenders.append(
                    f"{path.relative_to(ROOT)}:{lineno}: CSS 具名色 {found.group()!r}"
                    f" in {declaration!r}"
                )

    assert offenders == [], "颜色只许在 dida/tui/theme.py 里拼：\n" + "\n".join(offenders)


def test_no_colour_goes_into_a_base_style():
    """颜色只许进 **span**，不许进 base style。

    ``Text("x", style="cyan")`` 把样式放进 base style，而 Textual 用**自己的 CSS 颜色解析器**
    读它——那里 ``cyan`` 是 ``#00FFFF``。于是那个「看起来写的是 ANSI 名」的调用点静默发出
    真彩色，第一条颜色决定当场作废而且不报错。渲染级那条断言（``tests/test_theme.py``）
    盯的是屏幕上的字节，这条盯的是源码的形状。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py")):
        if path.name == "theme.py":
            continue  # 唯一允许拼颜色的地方，它自己那条测试在 tests/test_theme.py
        for lineno, style_text in _base_style_strings(path.read_text(encoding="utf-8")):
            if not style_text:
                continue
            try:
                style = Style.parse(style_text)
            except Exception:  # noqa: BLE001 - 解析不了的串不是样式串
                continue
            if style.color is not None or style.bgcolor is not None:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {style_text!r}")

    assert offenders == [], (
        "颜色要走 span（theme.styled / Text().append(style=…)），"
        "base style 会被 Textual 的 CSS 解析器当成真彩色：\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 扫源码：颜色字面量只许在一个地方
# ---------------------------------------------------------------------------


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """所有文档字符串那个 ``Constant`` 的 id（模块 / 类 / 函数的第一条语句）。"""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def _code_strings(source: str) -> Iterator[tuple[int, str]]:
    """源码里**不是文档字符串**的每一个字符串常量（f-string 的片段也算）。"""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.lineno, node.value


def _style_strings(source: str) -> Iterator[tuple[int, str]]:
    """会被 Rich / Textual 当**样式**读的那些代码字符串。

    两类：``style=`` 关键字（``Text(...)`` / ``Static(...)`` / ``append(...)``）与
    ``stylize("…")`` 这种位置参数。
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "style" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    yield keyword.value.lineno, keyword.value.value
        name = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
        if name in ("stylize", "styled"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg.lineno, arg.value


def _base_style_strings(source: str) -> Iterator[tuple[int, str]]:
    """``style=`` 关键字上的那些串——它们会被放进 base style（trap 1 的入口）。"""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "style" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    yield keyword.value.lineno, keyword.value.value


def _css_declarations(source: str) -> Iterator[tuple[int, str]]:
    """代码字符串里那些 CSS 声明（``属性: 值``）。"""
    declaration = re.compile(r"[a-z-]+\s*:\s*[^;{}\n]+")
    for lineno, text in _code_strings(source):
        if ";" not in text and "{" not in text:
            continue  # 不像样式表，别拿它当 CSS 扫
        for match in declaration.finditer(text):
            yield lineno, match.group()
