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
from typing import Any, Iterator

import pytest
from rich.color import ANSI_COLOR_NAMES
from rich.style import Style
from textual._color_constants import COLOR_NAME_TO_RGB

from dida.sync import engine
from dida.tui import messages

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
    "dida.vocabulary",  # 共用词汇（同步与本地副本的共同下界）
    "dida.storage.store",  # 本地存储
    "dida.storage.queue",  # 队列表的记账机制（#82；本地存储那一支里的账本，只依赖 stdlib）
    "dida.sync.engine",  # 同步引擎
    "dida.sync.capabilities",  # 构造那一处问清「谁会做什么」（#85）
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


# ---------------------------------------------------------------------------
# 单一出处：一条判断只许有一份实现（工单 #58 的 T3 / T5 / T6）
#
# 三条守的是同一件事，只是说法不同：**一张表 / 一个函数 / 一次比较**。它们的形状都是
# 「谁再抄一份就当场红」，而不是「现在这一份是对的」——抄一份正是这三条当初的病因。
# ---------------------------------------------------------------------------

PRIORITY_CODES = frozenset({0, 1, 3, 5})
"""优先级四档的**线上编码**（服务端那一套，见 :data:`dida.sync.view.PRIORITY_CYCLE`）。"""

PRIORITY_WORDS = frozenset({"无", "低", "中", "高"})
"""四档的**用户语言**（spec 用户故事 73 那几个字）。"""

PRIORITY_HOME = "src/dida/sync/view.py"
"""优先级四档中文写法的**唯一一处**（工单 #58 的 T3）。

它只能在 sync 那一侧：``tui/`` 只许 import ``dida.sync.engine``，而 ``sync/`` 永远不
import ``tui/``，所以这张表不可能住在 :mod:`dida.tui.messages` 又被视图表单 import。
"""


def _constant_dicts(source: str) -> Iterator[tuple[int, dict[Any, Any]]]:
    """源码里的字典字面量（键值都是字面量的那些）→ ``(行号, 内容)``。"""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict) or not node.keys:
            continue
        pairs: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(value, ast.Constant)):
                break
            pairs[key.value] = value.value
        else:
            yield node.lineno, pairs


def _priority_tables_in(source: str) -> list[int]:
    """这份源码里那些「``0/1/3/5`` → 无 / 低 / 中 / 高」的字典字面量在第几行。"""
    return [
        lineno
        for lineno, pairs in _constant_dicts(source)
        if set(pairs) == PRIORITY_CODES and set(pairs.values()) == PRIORITY_WORDS
    ]


def _priority_tables() -> dict[str, list[int]]:
    """``src/dida`` 里那些表的所在：模块（相对仓库根）→ 行号。"""
    found: dict[str, list[int]] = {}
    for path in sorted((ROOT / "src" / "dida").rglob("*.py")):
        lineno = _priority_tables_in(path.read_text(encoding="utf-8"))
        if lineno:
            found[str(path.relative_to(ROOT))] = lineno
    return found


def test_the_priority_vocabulary_is_written_down_once():
    """优先级四档的中文写法只有**一处**（工单 #58 的 T3）。

    :data:`dida.tui.messages.PRIORITY_NAMES` 与 ``sync/views.py`` 的 ``_PRIORITY_LABELS``
    曾经各写一份——而 ``app.py`` 那几行的注释还把前者称作「唯一一张表」。这一条扫的是源码里
    的字典字面量：谁再抄一份同样的映射就当场红。修法是 import :data:`PRIORITY_HOME` 那一份
    （界面经过引擎的公开面拿它）。
    """
    assert list(_priority_tables()) == [PRIORITY_HOME], (
        f"优先级四档的中文写法只许在 {PRIORITY_HOME} 里写一次，别处 import 它"
    )


def test_the_priority_table_guard_catches_a_second_copy():
    """上一条守卫自己也要有人守：它认得出抄来的第二份（工单 #58 的 T3）。"""
    assert _priority_tables_in('MARKS = {0: "无", 1: "低", 3: "中", 5: "高"}\n') == [1]
    assert _priority_tables_in('MARKS = {5: "高", 3: "中", 1: "低", 0: "无"}\n') == [1], (
        "换个次序也是同一张表，守卫不许漏"
    )
    assert _priority_tables_in('MARK_GLYPHS = {5: "!", 3: "~"}\n') == [], "别的字典不是这张表"


def test_the_tui_reads_the_priority_vocabulary_through_the_engine():
    """界面拿到的是**引擎那一份表**，不是自己抄的一张（工单 #58 的 T3）。

    这一条比上一条更严一点：上一条拦「又写了一个字面量」，这一条拦「换成一个长得一样的新
    字典」（推导式、``dict(...)``、逐项抄）。断的是**同一个对象**，所以只有真的转出来才过。
    """
    assert messages.PRIORITY_NAMES is engine.PRIORITY_NAMES


def _module_functions(source: str) -> Iterator[tuple[str, int, str]]:
    """模块级函数 → ``(名字, 行号, 函数体的形状)``；文档字符串不算（那是说明，不是行为）。

    只扫**模块级**：类体里那一堆 ``def …: ...`` 是 Protocol / mixin 的声明桩，同名同形是
    故意的（``Engine.move_task`` 与 ``PushMixin.move_task`` 就是一对），拿它们报重复只会
    教人关掉这条守卫。
    """
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        yield node.name, node.lineno, ast.dump(ast.Module(body=body, type_ignores=[]))


def _duplicate_function_bodies(sources: dict[str, str]) -> list[str]:
    """这批源码里「同名的函数体出现在不止一个模块里」的那些，写成 ``名字: 甲 = 乙``。"""
    seen: dict[tuple[str, str], list[str]] = {}
    for name, source in sources.items():
        for function, lineno, body in _module_functions(source):
            seen.setdefault((function, body), []).append(f"{name}:{lineno}")
    return [
        f"{function}: {' = '.join(where)}"
        for (function, _), where in sorted(seen.items())
        if len({location.rsplit(":", 1)[0] for location in where}) > 1
    ]


def test_no_two_modules_define_the_same_function_body():
    """同名的函数体不许在两个模块里各写一份（工单 #58 的 T5）。

    ``_project_in`` 曾在 ``sync/push.py`` 与 ``sync/writes.py`` 里逐字相同（同名、同签名、
    同函数体），而 ``push`` 本来就 import 了 ``writes``——那第二份是纯粹的重复。该往哪个方向
    收，看的是既有的 import 图：叶模块留着它，依赖方 import 它（反过来就是一个环）。
    """
    sources = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src" / "dida").rglob("*.py"))
    }

    assert _duplicate_function_bodies(sources) == [], (
        "同名的函数体在两个模块里各有一份——让其中一个 import 另一个，别再抄一遍：\n"
        + "\n".join(_duplicate_function_bodies(sources))
    )


def test_the_duplicate_body_guard_catches_a_second_copy():
    """上一条守卫自己也要有人守（工单 #58 的 T5）：抄一份当场红，只是文档不同不算抄。"""
    copy = "def read(payload):\n    if not payload:\n        return None\n    return payload\n"
    assert _duplicate_function_bodies({"a.py": copy, "b.py": copy}) == ["read: a.py:1 = b.py:1"]
    assert _duplicate_function_bodies({"a.py": copy, "b.py": '"""说明。"""\n\n' + copy}) == [
        "read: a.py:1 = b.py:3"
    ], "文档字符串不同不影响判定：那是说明，不是行为"
    assert _duplicate_function_bodies({"a.py": copy, "b.py": "def read(payload):\n    return payload\n"}) == [], (
        "同名但行为不同的两个函数不是重复"
    )


def _is_the_local_value(node: ast.AST) -> bool:
    """这一边是不是「**界面自己那份值**」：裸名字 / 下标 / 调用 / 字面量。

    ``values[LIST_FIELD]``（下标）、``int(picked)``（调用）、``value``（裸名字）都是界面手里
    那一份。**属性不是**——属性说的是「另一个对象手里的那一份」，两边都是属性就是两个对象之间
    的比较（挑选项回填那种：``option.value == detail.list_id``，见
    :func:`_equality_against_a_bare_name` 的同一条口径）。
    """
    return isinstance(node, (ast.Name, ast.Subscript, ast.Call, ast.Constant))


def _equality_with_attribute(source: str, attribute: str) -> list[int]:
    """源码里 ``<引擎那一份 .attribute> == <界面自己那份值>`` 这种**相等比较**的行号。

    两件事同时才算：有一边是那个引擎属性（``detail.list_id`` / ``detail.priority`` /
    ``detail.tags``），**而且**另一边是界面自己手里的值（见 :func:`_is_the_local_value`）。
    两边都是属性不算（``option.value == detail.list_id``：那是在问「当前这个值在不在这一份
    选项里」，是回填不是判改动，``pages/detail.py`` 的 ``list_picker`` 正是这么用的；
    ``mine.list_id == other.list_id`` 同理，那是两个对象之间的比较）。
    """
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        engines = [
            item
            for item in operands
            if isinstance(item, ast.Attribute) and item.attr == attribute
        ]
        if not engines:
            continue
        others = [item for item in operands if all(item is not engine for engine in engines)]
        if any(_is_the_local_value(item) for item in others):
            found.append(node.lineno)
    return found


def _equality_against_a_bare_name(source: str, attribute: str) -> list[int]:
    """源码里 ``<某个裸名字> == … .attribute``（或反过来）这种比较的行号。

    与 :func:`_equality_with_attribute` 的差别在**另一边**：这里要求另一边是一个**裸名字**
    （界面自己手里那份值），所以只抓「拿我这边那份值与引擎给的那一份比」这种形状；
    ``option.value == field.value``（两边都是属性，挑选项回填那种正当比较）不算。
    """
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        named = any(isinstance(item, ast.Name) for item in operands)
        valued = any(
            isinstance(item, ast.Attribute) and item.attr == attribute for item in operands
        )
        if named and valued:
            found.append(node.lineno)
    return found


JUDGEMENT_NAMES = frozenset({"is_a_change", "is_a_move", "is_list_edit", "is_view_edit"})
"""「这一次到底改了没有」那几个判据的名字（**本体只有一个**，其余是同一族的适配）。

从 #79 起写的那一次自己回话（回报布尔），所以界面一处都不许再问、也不许再自己比一遍。
"""


def _judgements_in(source: str) -> list[str]:
    """这份源码里用到那几个判据的地方，写成 ``行号: 名字``（空元组 = 干净）。

    两种形状都算：``from … import is_a_move`` 进来的那个名字（``ast.alias``），以及直接用它
    （``ast.Name`` / ``ast.Attribute``，后者兜住 ``import dida.sync.writes as w; w.is_a_move``）。
    """
    found: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in JUDGEMENT_NAMES:
            found.add((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in JUDGEMENT_NAMES:
            found.add((node.lineno, node.attr))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in JUDGEMENT_NAMES:
                    found.add((node.lineno, alias.name))
    return [f"{lineno}: {name}" for lineno, name in sorted(found)]


def test_the_interface_never_judges_whether_something_changed():
    """界面里 **0 处**自己判「改了没有」（工单 #79）。

    判据本体是 :func:`dida.sync.writes.is_a_change`（写词汇那一层，比的是本地那一份原文），
    六条写路径各自回报布尔；界面只按回报值决定推不推、说不说。所以 ``tui/`` 里一处都不许
    出现那几个判据的名字——包括「再 import 进来问一遍」这条退路。

    在 #79 之前这里恰好相反：``#58`` 的守卫要求界面 import ``is_a_move``（那时写入口回不了
    话，只能各问一遍），于是同一个判断在六个地方各写一遍、第七处漏掉（ADR-0008 第二节记的
    那次空写）。这条守卫守的是**那个方向**：判据只有一个主语。
    """
    sources = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
    }

    offenders = [
        f"{name}:{where}" for name, source in sources.items() for where in _judgements_in(source)
    ]

    assert offenders == [], (
        "界面自己判了「改了没有」——这条判断归引擎（dida.sync.writes.is_a_change），"
        "写的那一次回报布尔，界面只按回报值走：\n" + "\n".join(offenders)
    )


def test_the_interface_does_not_inline_the_comparisons_it_used_to_make():
    """界面里**任何一处**都不许把那个比较**手写**回来（工单 #79）：import 与内联两种形状都要拦。

    **覆盖整个 ``tui/``，不是只扫 ``app.py``**（工单 #83）：这几处曾经逐处写在 ``app.py`` 里、
    比的是屏幕上那一份（而不是引擎手里的本地原文）。#83 把挑选型字段那一格搬进了
    ``dida/tui/write_flow.py``——搬走的代码照样是界面，只认文件名的写法从那一刻起就没有覆盖，
    所以这里扫的是 ``rglob("*.py")``：以后再往 ``tui/`` 里添模块，它一并被守着。

    拦的是两组：

    - 挑选型字段那几格（清单 / 优先级 / 标签）的属性比较（``values[LIST_FIELD] == detail.list_id``）；
    - ``pages/detail.py`` 自由文本框退出时「编辑器里那段文字与 ``Field.value`` 相等」那一下
      （形状是**裸名字 vs 属性**：``value == field.value``）。
    """
    sources = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
    }

    inlined = [
        f"{name}:{attribute}:{lineno}"
        for name, source in sources.items()
        for attribute in ("list_id", "priority", "tags")
        for lineno in _equality_with_attribute(source, attribute)
    ]
    assert inlined == [], (
        "界面又自己比了一遍「改了没有」（比的是屏幕上那一份）：这条判断归引擎\n" + repr(inlined)
    )

    against_a_bare_name = [
        f"{name}:{lineno}"
        for name, source in sources.items()
        for lineno in _equality_against_a_bare_name(source, "value")
    ]
    assert against_a_bare_name == [], (
        "详细页又自己比了一遍「编辑器里那段文字与字段当前值相等」——这条判断归引擎"
        "（dida.sync.writes.is_a_change），写的那一次回报布尔\n" + repr(against_a_bare_name)
    )


def test_the_interface_judgement_guard_catches_both_shapes():
    """上两条守卫自己也要有人守（工单 #79）：import 与内联两种写法逐条钉住。"""
    assert _judgements_in("from dida.sync.engine import is_a_move\n") == ["1: is_a_move"]
    assert _judgements_in("if not is_a_move(a, b):\n    return False\n") == ["1: is_a_move"]
    assert _judgements_in("import dida.sync.writes as w\n\nw.is_a_change(a, b)\n") == [
        "3: is_a_change"
    ], "换个写法绕过去也要拦得住"
    assert _judgements_in("from dida.sync.engine import WriteKind, move_targets\n") == [], (
        "不是那几个名字的 import 不算"
    )
    assert _judgements_in("self.engine.move_task(task_id, to_list_id=x)\n") == [], (
        "把 id 当参数交出去不是「自己判」"
    )


def test_the_inlined_comparison_guard_catches_a_second_copy():
    """内联那一条的守卫自己也要有人守：再写一遍那个比较当场红。

    「两边都是属性」那一档是**故意**不算的（工单 #83 扩覆盖时定下来的口径）：它说的是两个
    对象之间的比较，而 ``pages/detail.py`` 的 ``list_picker`` 真的在用它问「当前这个值在不在
    这一份选项里」。#79 的判据形状永远是「**界面自己那份值** vs 引擎那份原文」——那一侧是
    下标 / 调用 / 裸名字。
    """
    inlined = "if field == LIST_FIELD:\n    if values[LIST_FIELD] == detail.list_id:\n        return False\n"

    assert _equality_with_attribute(inlined, "list_id") == [2]
    assert _equality_with_attribute("self.engine.move_task(task_id, to_list_id=x)\n", "list_id") == [], (
        "把 id 当参数传出去不是「自己判」"
    )
    assert _equality_with_attribute("if int(picked) == detail.priority:\n    pass\n", "priority") == [1], (
        "调用那一侧（``int(picked)``）也是界面自己那份值"
    )
    assert _equality_with_attribute(
        "if any(option.value == detail.list_id for option in options):\n    pass\n", "list_id"
    ) == [], "两边都是属性：挑选项回填那种正当比较（list_picker 正是这么用的）"
    assert _equality_with_attribute("if other.list_id == mine.list_id:\n    pass\n", "list_id") == [], (
        "同上：两个对象之间的比较不是「拿我这份值比对引擎那份原文」"
    )

    free_text = "value = self._editor_value()\nif value == field.value:\n    return\n"
    assert _equality_against_a_bare_name(free_text, "value") == [2]
    assert _equality_against_a_bare_name("if option.value == field.value:\n    pass\n", "value") == [], (
        "两边都是属性的正当比较（挑选项回填）不算「自己判」"
    )
    assert _equality_against_a_bare_name(
        'if event.input.id == "due-date" and event.value != self._due_prefill:\n    pass\n', "value"
    ) == [], "「这一格被用户动过没有」不是「改了没有」"


# ---------------------------------------------------------------------------
# 写链只有一个家：接住 DidaError 与「叫一次写」不许长在同一个函数里（工单 #83）
#
# 在 #83 之前，十一个处理函数各自走完那十二行——接住失败 → 挑一句话 → 推一轮 → 重画，
# 其中三处逐字相同。收进 dida/tui/write_flow.py 之后这条守卫盯着它不会被搬回来：处理函数
# 只声明「写了什么」（叫一次写），失败怎么说、落在哪只有 WriteFlow.finish_write 一处。
#
# 别把这条读成「不许接 DidaError」：拉标签（on_detail_page_pick_requested）、同步收尾
# （_pull_completed / _sync）接它是正当的——它们没有写。所以规矩是**两件事同时出现**才算。
# ---------------------------------------------------------------------------

ENGINE_WRITES = frozenset(
    {
        "write",
        "move_task",
        "reschedule",
        "defer",
        "create",
        "delete",
        "complete",
        "uncomplete",
        "create_list",
        "update_list",
        "delete_list",
        "create_view",
        "update_view",
        "delete_view",
    }
)
"""引擎那几条**写**路径（``self.engine.write(...)`` 那一族）。接住 ``DidaError`` 又调它们，
就是在那一处手写了一遍「写的后半段」。"""

DECLARED_WRITE = "perform"
"""``Write.perform``——那个唯一的家把「写了什么」执行掉的那一下（``<那次写>.perform()``）。

``perform`` 也算「叫了一次写」，为的是让 :data:`WRITE_CHAIN_HOME` **真的**撞上这条规矩：
豁免是活的（今天它正是靠 perform 走到引擎的），不是一句摆设。
"""

WRITE_CHAIN_HOME = "WriteFlow.finish_write"
"""那个唯一的家：唯一允许同时「接住 ``DidaError``」与「叫一次写」的函数。"""


def _qualified_names(tree: ast.AST, prefix: str = "") -> dict[int, str]:
    """每个函数/方法的名字：模块级就是函数名，类里写成 ``类.方法``（这条守卫要指名那个家）。"""
    names: dict[int, str] = {}
    for child in ast.iter_child_nodes(tree):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{child.name}"
            names[id(child)] = name
            names.update(_qualified_names(child, f"{name}."))
        elif isinstance(child, ast.ClassDef):
            names.update(_qualified_names(child, f"{prefix}{child.name}."))
    return names


def _own_parts(node: ast.AST) -> Iterator[ast.AST]:
    """这个函数**自己**的那几段（不进嵌套函数——闭包算它自己的那一份）。"""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        item = stack.pop()
        yield item
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(item))


def _catches_dida_error(node: ast.Try) -> bool:
    """这个 ``try`` 接住 ``DidaError`` 吗（``except DidaError`` / ``except (A, DidaError)``）。"""
    for handler in node.handlers:
        caught = handler.type
        if isinstance(caught, ast.Name) and caught.id == "DidaError":
            return True
        if isinstance(caught, ast.Tuple) and any(
            isinstance(item, ast.Name) and item.id == "DidaError" for item in caught.elts
        ):
            return True
    return False


def _calls_a_write(node: ast.AST) -> str | None:
    """这个调用是不是「一次写」：``self.engine.write(...)`` 那一族，或者 ``<那次写>.perform()``。"""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == DECLARED_WRITE:
        return func.attr
    if func.attr not in ENGINE_WRITES:
        return None
    receiver = func.value
    grounded = (isinstance(receiver, ast.Attribute) and receiver.attr == "engine") or (
        isinstance(receiver, ast.Name) and receiver.id == "engine"
    )
    return func.attr if grounded else None


def _write_chain_offenders(source: str) -> list[str]:
    """这份源码里「自己写了一遍一次写的后半段」的函数，写成 ``函数: 接住…写…``（空 = 干净）。

    唯一的例外是 :data:`WRITE_CHAIN_HOME` 本人——它是那个家。
    """
    tree = ast.parse(source)
    names = _qualified_names(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        where = names.get(id(node), node.name)
        if where == WRITE_CHAIN_HOME:
            continue
        parts = list(_own_parts(node))
        catches = [
            item.lineno for item in parts if isinstance(item, ast.Try) and _catches_dida_error(item)
        ]
        writes = [
            f"{item.lineno}: {called}"
            for item in parts
            if (called := _calls_a_write(item)) is not None
        ]
        if catches and writes:
            offenders.append(f"{where}: 接住 DidaError @{catches}，又写 @[{', '.join(writes)}]")
    return offenders


def test_the_write_chain_has_exactly_one_home():
    """一次写的后半段**只有一个家**：``dida/tui/write_flow.py`` 的 ``WriteFlow.finish_write``（#83）。

    十一个处理函数只声明「写了什么」（叫一次引擎写）与「成功那句说什么」；接住失败、挑话、
    推一轮、重画都在那个家里。所以 ``tui/`` 里**任何函数**都不许同时出现「接住 ``DidaError``」
    与「叫一次写」——那种组合就是一份手写的后半段。

    这条守的是**搬家之后不会搬回来**（#83 之前它正是十一个地方各写一遍的样子），不是
    「不许接 ``DidaError``」：那两处正当的（拉标签、同步收尾）都没有写。
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{where}"
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
        for where in _write_chain_offenders(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "这一处在自己写一遍「写的后半段」（接住失败 → 挑话 → 推 → 重画）——它归 "
        f"dida/tui/write_flow.py 的 {WRITE_CHAIN_HOME}，处理函数只声明「写了什么」：\n"
        + "\n".join(offenders)
    )


def test_the_write_chain_guard_catches_a_second_copy():
    """上一条守卫自己也要有人守（仓库惯例）：抄一份当场红，那个家与两种「只有一半」都不算。"""
    second_copy = (
        "class TasksPage:\n"
        "    async def save_the_title(self, task_id, title):\n"
        "        try:\n"
        "            self.engine.write(task_id, changes={'title': title})\n"
        "        except DidaError as exc:\n"
        "            self._write_status(str(exc))\n"
    )
    assert _write_chain_offenders(second_copy) != [], "别处再写一遍后半段要红"

    home = (
        "class WriteFlow:\n"
        "    async def finish_write(self, write):\n"
        "        try:\n"
        "            write.perform()\n"
        "        except DidaError as error:\n"
        "            self._fail(error)\n"
    )
    assert _write_chain_offenders(home) == [], "那个唯一的家是允许的（豁免的对象正是它）"
    assert _write_chain_offenders(home.replace("finish_write", "finish_write_again")) != [], (
        "换个名字把它抄一份就不是那个家了"
    )
    assert _write_chain_offenders(
        "async def load():\n    try:\n        await self.engine.load_tags()\n    except DidaError:\n        return\n"
    ) == [], "接住 DidaError 但没写的不算（拉标签、同步收尾那几处正是这样）"
    assert _write_chain_offenders(
        "async def save():\n    await self.engine.write('t1', changes={})\n"
    ) == [], "只声明「写了什么」、不接错的不算"
    assert _write_chain_offenders(
        "async def pump():\n    try:\n        await self.engine.push_pending()\n    except DidaError:\n        return\n"
    ) == [], "推不是写（推送有多个正当调用点）"
    assert _write_chain_offenders(
        "class Inner:\n"
        "    async def close(self):\n"
        "        try:\n"
        "            self.engine.delete('t1')\n"
        "        except DidaError:\n"
        "            return\n"
        "    def outer(self):\n"
        "        pass\n"
    ) != [], "藏在类里的那一份，别指望它溜过去"


# ---------------------------------------------------------------------------
# 依赖方向：sync 与 storage 都只指向 dida.vocabulary（工单 #78）
#
# 在共用词汇独立成模块之前，sync 为了用本地库那几个类型，撑了 9 处补丁：6 处只在
# ``if TYPE_CHECKING:`` 里 import、3 处把 import 写进函数体。三种形状都得拦——它们都能让
# 「新加一个共用类型得先猜放哪边」这件事回来。零容忍：sync 里一处都不许出现。
# ---------------------------------------------------------------------------

SYNC_PACKAGE = "dida.sync"


def _is_storage(name: str) -> bool:
    """这是不是本地存储那一支（``dida.storage`` 或它的子模块）。"""
    return name == "dida.storage" or name.startswith("dida.storage.")


def _names_type_checking(test: ast.expr) -> bool:
    """这个 ``if`` 的条件里出现了 ``TYPE_CHECKING`` 吗。"""
    return any(isinstance(node, ast.Name) and node.id == "TYPE_CHECKING" for node in ast.walk(test))


def _import_place(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    """这处 import 在哪儿：模块顶层 / 只在类型检查时 / 函数体内（可叠加）。"""
    places: list[str] = []
    parent = parents.get(id(node))
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            places.append("函数体内")
        elif isinstance(parent, ast.If) and _names_type_checking(parent.test):
            places.append("只在类型检查时")
        parent = parents.get(id(parent))
    return " + ".join(reversed(places)) or "模块顶层"


def _storage_imports(source: str, *, package: str) -> tuple[str, ...]:
    """这份源码里每一处 import 本地库的位置，写成 ``行号: 在哪``（空元组 = 干净）。"""
    tree = ast.parse(source)
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        names: set[str] = set()
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        else:
            base = _absolute_module(node, package=package)
            if base:
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names)
        if any(_is_storage(name) for name in names):
            found.append(f"{node.lineno}: {_import_place(node, parents)}")
    return tuple(found)


def test_sync_never_reaches_back_into_the_local_store():
    """``sync`` 只许依赖 :mod:`dida.vocabulary`，一处本地库 import 都不许有（工单 #78）。

    这条守的是**方向**，不是「现在这几处对不对」：顶层、``if TYPE_CHECKING:``、函数体内
    三种形状一视同仁——补丁当初正是靠后两种绕开那个环的。
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{where}"
        for path in sorted((ROOT / "src" / "dida" / "sync").rglob("*.py"))
        for where in _storage_imports(path.read_text(encoding="utf-8"), package=_package_of(path))
    ]

    assert offenders == [], (
        "sync 只许依赖 dida.vocabulary（工单 #78）：本地库那一支一处都不许 import，"
        "函数内 import 与 TYPE_CHECKING 后门同样不行：\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("from dida.storage.store import Store\n", ("1: 模块顶层",)),
        ("import dida.storage\n", ("1: 模块顶层",)),
        ("from ..storage import store\n", ("1: 模块顶层",)),
        (
            "if TYPE_CHECKING:\n    from dida.storage.store import RefreshReport\n",
            ("2: 只在类型检查时",),
        ),
        (
            "def f():\n    from dida.storage.store import COMPLETED_STATUS\n"
            "    return COMPLETED_STATUS\n",
            ("2: 函数体内",),
        ),
    ],
)
def test_the_storage_backdoor_guard_catches_every_shape(source, expected):
    """这条守卫自己也要有人守（工单 #78）：补丁的三种形状逐条钉住（不然它只是好看）。"""
    assert _storage_imports(source, package=SYNC_PACKAGE) == expected


def test_the_storage_backdoor_guard_lets_the_shared_vocabulary_through():
    """共用词汇与自己的兄弟模块照旧放行——守卫拦的是**本地库那一支**。"""
    source = (
        "from dida.vocabulary import PendingChange, WriteKind\n"
        "from dida.sync.view import ListSnapshot\n"
        "from dida.sync.engine import SyncEngine\n"
    )

    assert _storage_imports(source, package=SYNC_PACKAGE) == ()


# ---------------------------------------------------------------------------
# 能力在构造那一处定下来：业务路径一处运行时探测都不许有（工单 #85）
#
# 在 #85 之前，「这份副本会不会做某件事」被拆成 13 处运行时探测散在业务路径上
# （``isinstance(…, 某个 Protocol)`` / ``getattr`` + ``callable``），而「做不到」有四种说法。
# 现在只有一个地方问：``Capabilities.of``（引擎构造那一处）。这条守卫盯着它不会被写回来——
# 形状是「谁再临场探一次就当场红」，而不是「现在这几处对不对」。
# ---------------------------------------------------------------------------

CAPABILITY_PROTOCOLS = frozenset(
    {
        "ViewSource",
        "WriteTarget",
        "ListWriteTarget",
        "RefreshTarget",
        "ViewStore",
        "ViewReader",
        "PayloadReader",
        "ProjectReader",
        "TaskWriter",
        "ProjectWriter",
        "TagReader",
        "CompletedReader",
        "PushQueue",
    }
)
"""「这份副本 / 这个客户端会不会做某件事」那几个能力协议（工单 #85）。

``isinstance(…, 这里的一个名字)`` 就是一次临场探测。``isinstance(payload, Mapping)`` 那种
**形状**检查不算——它问的不是「谁会做什么」，是「这份数据长什么样」。
"""

CAPABILITY_HOME = "src/dida/sync/capabilities.py"
"""唯一允许问「谁会做什么」的地方（工单 #85）：构造那一处的那一张表。"""


def _is_a_capability(node: ast.AST) -> bool:
    """``isinstance`` 的第二个参数是不是一个能力协议（裸名字，或者元组里的一个）。"""
    if isinstance(node, ast.Name):
        return node.id in CAPABILITY_PROTOCOLS
    if isinstance(node, ast.Tuple):
        return any(_is_a_capability(item) for item in node.elts)
    return False


def _capability_probes(source: str) -> list[str]:
    """这份源码里的临场能力探测，写成 ``行号: 形状``（空 = 干净）。"""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name == "isinstance" and len(node.args) >= 2 and _is_a_capability(node.args[1]):
            found.append(f"{node.lineno}: isinstance")
        elif name == "callable":
            found.append(f"{node.lineno}: callable")
    return found


def test_the_capability_is_decided_in_exactly_one_place():
    """业务路径上**一处**运行时能力探测都没有——只有构造那一处问一次（工单 #85）。

    两种形状都拦：``isinstance(source, ListWriteTarget)`` 与 ``getattr`` + ``callable``
    （``pending_error`` 原来那种「装作没这回事」的写法）。业务路径读
    :attr:`~dida.sync.engine.SyncEngine._caps` 里已经定下来的那一格。
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{where}"
        for path in sorted((ROOT / "src" / "dida" / "sync").rglob("*.py"))
        if str(path.relative_to(ROOT)) != CAPABILITY_HOME
        for where in _capability_probes(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "「谁会做什么」只许在构造那一处问一次（dida/sync/capabilities.py）：\n"
        + "\n".join(offenders)
    )


def test_the_capability_probe_guard_catches_a_second_copy():
    """上一条守卫自己也要有人守（仓库惯例）：两种形状逐条钉住，形状检查不算。"""
    assert _capability_probes("if isinstance(source, ListWriteTarget):\n    return ()\n") == [
        "1: isinstance"
    ]
    assert _capability_probes(
        "pending = getattr(source, 'pending', None)\nif not callable(pending):\n    return None\n"
    ) == ["2: callable"], "``getattr`` + ``callable`` 那种写法也要拦得住"
    assert _capability_probes(
        "if isinstance(source, (WriteTarget, ListWriteTarget)):\n    pass\n"
    ) == ["1: isinstance"], "元组里的一个也算"
    assert _capability_probes("if isinstance(payload, Mapping):\n    pass\n") == [], (
        "形状检查不是「谁会做什么」"
    )
    assert _capability_probes("if isinstance(created, Mapping) or not created.get('id'):\n    pass\n") == []

