"""架构不变量：模块的边界，TUI 的依赖方向，以及「只用终端 16 色」。

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
from rich.color import ANSI_COLOR_NAMES
from rich.style import Style
from textual._color_constants import COLOR_NAME_TO_RGB

ROOT = Path(__file__).resolve().parents[1]

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
"""颜色字面量：``#rrggbb`` / ``#rrggbbaa`` / ``#rgb``（不是照抄实现——这是终端那条规矩的形状）。"""

COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?)\s*\(")
"""``rgb(…)`` / ``hsl(…)``：写死的颜色，只是换了个写法。"""

THEME_VARIABLE = re.compile(r"\$[A-Za-z][\w-]*")
"""``$surface`` 这类 Textual 主题变量：它们在 ``textual-dark`` 下是真彩色值（``#1E1E1E``）。"""

STYLE_ATTRIBUTES = frozenset(
    {
        "bold",
        "b",
        "dim",
        "italic",
        "i",
        "underline",
        "u",
        "uu",
        "strike",
        "reverse",
        "blink",
        "overline",
        "o",
        "not",
        "none",
        "default",
        "on",
    }
)
"""Rich 样式串里的**不是颜色**的那些词：字重、装饰、``default``（终端自己的前景/背景）。"""

COLOUR_WORDS = frozenset(
    {name.lower() for name in ANSI_COLOR_NAMES}
    | {
        name.lower()
        for name in COLOR_NAME_TO_RGB
        if not name.startswith("ansi_")  # ansi_* 是唯一合法的拼法
    }
)
"""Rich 与 Textual 两边认得的**具名色**（``red`` / ``cyan`` / ``aquamarine1`` …）。

两边的表都要：Rich 的样式串里 ``cyan`` 是 ANSI 6（合法但只许在 theme.py 拼），Textual 的
CSS 里 ``cyan`` 是 ``#00FFFF``（真彩色）。同一个词两个意思——所以名字只许在一个地方出现。
"""

MODULE_WHITELIST = [
    "dida.config",  # 配置与凭据
    "dida.api.client",  # 滴答 API 客户端
    "dida.storage.store",  # 本地存储
    "dida.sync.engine",  # 同步引擎
    "dida.logical_day",  # 逻辑日
    "dida.tui.app",  # TUI
]
"""深模块的**白名单**：每一个都得能 import（``test_module_is_importable``）。

名字里不写数目：v1 的日期解析器（``dida.date_parser``）已由 #34 删除，而它原来叫
``SEVEN_MODULES``——从此这个名字就在说一句假话（名字说七、内容是六）。这张表本来就是一份
**清单**，不是一次计数；加一个模块就往这里加一行，名字不用动。"""

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


@pytest.mark.parametrize("module", MODULE_WHITELIST)
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


def colour_offences(source: str, *, allow_names: bool) -> tuple[str, ...]:
    """这份源码里出现的每一处颜色字面量（``文件:行: 说明`` 的形状）。

    ``allow_names`` 是给 :mod:`dida.tui.theme` 留的：它是**唯一**允许拼颜色名的地方
    （ANSI 名就是它的活），但连它也不许写 hex / ``rgb()`` / ``$`` 变量——那些一定会把
    真彩色放回来。
    """
    offenders: list[str] = []
    for lineno, text in _code_strings(source):
        for label, pattern in (
            ("hex", HEX_COLOUR),
            ("rgb()/hsl()", COLOUR_FUNCTION),
            ("$ 主题变量", THEME_VARIABLE),
        ):
            if pattern.search(text):
                offenders.append(f"{lineno}: {label} in {text!r}")
    if not allow_names:
        for lineno, text in _style_strings(source):
            named = _named_colour(text)
            if named:
                offenders.append(f"{lineno}: 样式串里的具名色 {named!r} in {text!r}")
        for lineno, declaration in _css_declarations(source):
            named = _named_colour(declaration)
            if named:
                offenders.append(f"{lineno}: CSS 里的具名色 {named!r} in {declaration!r}")
    return tuple(offenders)


def _named_colour(text: str) -> str | None:
    """这段样式串里出现的具名色（不是颜色的那些词不算）。"""
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text):
        lowered = word.lower()
        if lowered in STYLE_ATTRIBUTES:
            continue
        if lowered in COLOUR_WORDS:
            return word
    return None


@pytest.mark.parametrize(
    "source",
    [
        'Text("x", style="cyan")',
        'text.append("x", style="red bold")',
        'text.stylize("on #00FFFF")',
        'CSS = "Screen { color: red; }"',
        'CSS = "Static { background: $surface; }"',
        'CSS = "Static { background: rgb(255, 0, 0); }"',
        'CSS = "Static { color: hsl(0, 100%, 50%); }"',
        'CSS = "Toast { border-left: outer ansi_green; }" + "#ff0000"',
    ],
)
def test_the_colour_guard_catches_every_way_of_naming_a_colour(source):
    """这条守卫自己也要有人守：它拦得住的东西逐条钉住（不然它只是好看）。

    ``#ff0000`` / ``rgb()`` / ``hsl()`` / ``red`` / ``$surface`` —— 这五种实测**都能**
    溜过只拦 hex 的那一版，而任意一种都能把「跟随终端主题」静默毁掉。
    """
    assert colour_offences(source, allow_names=False)


@pytest.mark.parametrize(
    "source",
    [
        "Text(detail.title, style='bold')",
        "line.stylize('reverse')",
        'CSS = "Toast { border-left: outer ansi_green; }"',
        'CSS = "Screen { background: ansi_default; }"',
        'theme.styled("x", theme.MUTED)',
        'CSS = "Static { text-wrap: nowrap; text-overflow: ellipsis; }"',
    ],
)
def test_the_colour_guard_lets_the_ansi_vocabulary_through(source):
    """字重、装饰、与 ``ansi_*`` 都是合法的：守卫拦的是**写死的颜色**，不是样式本身。"""
    assert colour_offences(source, allow_names=False) == ()


def test_the_colour_guard_allows_names_inside_the_theme_module():
    """``theme.py`` 是唯一允许拼颜色名的地方——但 ``rgb()`` / ``$`` 变量连它也不许。"""
    assert colour_offences('ACCENT = "cyan"', allow_names=True) == ()
    assert colour_offences('ACCENT = "rgb(0,255,255)"', allow_names=True)
    assert colour_offences('CSS_PAGE = "$background"', allow_names=True)


def test_the_tui_names_colours_in_exactly_one_place():
    """只用终端 16 色：颜色字面量只许出现在 :mod:`dida.tui.theme` 一处（工单 #51）。

    四类都拦：``#rrggbb`` / ``rgb()`` / ``hsl()`` / CSS 具名色 / ``$`` 主题变量。只拦 hex
    的那一版实测挡不住后面三种（``rgb(255,0,0)``、``red``、``$surface`` 全部通过），而任意
    一条都能把「跟随终端主题」静默毁掉：具名色与主题变量都是 Textual 那边的真彩色。
    （纯 ANSI 名 ``ansi_cyan`` 不受影响——那正是主题自己拼出来的写法。）

    **文档字符串不算**：仓库里到处在引用实测色值当证据（ADR-0007、theme 的模块文档），
    那是说明，不是样式。扫的是**代码里的字符串**——样式在运行时只可能从那里来。
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{offence}"
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
        for offence in colour_offences(
            path.read_text(encoding="utf-8"), allow_names=path.name == "theme.py"
        )
    ]

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


def _documentation_nodes(tree: ast.AST) -> set[int]:
    """**说明性**的字符串：光秃秃摆在那一行的那些（模块/类/函数的文档、常量下面那句说明）。

    ``ACCENT = "cyan"`` 后面那一段独立成句的字符串不是样式，它是注释——它没有被赋给谁、
    也没有被当参数传出去，运行时不可能是颜色。样式只可能从「有归属的字符串」来。
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                found.add(id(node.value))
    return found


def _code_strings(source: str) -> Iterator[tuple[int, str]]:
    """源码里**真的会被用到**的每一个字符串常量（f-string 的片段也算）——说明性的除外。"""
    tree = ast.parse(source)
    documentation = _documentation_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in documentation:
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

