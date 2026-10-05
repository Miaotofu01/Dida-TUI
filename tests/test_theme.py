"""视觉地基：视觉常量只有一个出处，且只说的是终端 ANSI 槽位（工单 #51）。

**这个文件是「渲染级」的**：颜色到底发出什么字节，只有真跑一遍 app 才看得见。所以这里的
头等断言不是「源码里写了什么」，而是「屏幕上发出去的 SGR 是什么」——
``Text("x", style="cyan")`` 那类写法**看起来**用的是 ANSI 名，实际走的是 Textual 的 CSS
颜色解析器（那里 ``cyan`` 是 ``#00FFFF``），一条真彩色就把「跟随终端主题」毁掉，而且不报错。
源码扫描抓不住它，只有 SGR 抓得住。``tests/test_architecture.py`` 那条源码扫描是它的补集：
一条管屏幕上的字节，一条管源码里的字面量。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone

import pytest
from rich.cells import cell_len
from rich.color import ColorType
from rich.style import Style

from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp
from support import screen_sgr

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

TRUE_COLOUR = (ColorType.TRUECOLOR, ColorType.EIGHT_BIT)
"""真彩色与 256 色：这两个一出现，「颜色由终端主题决定」就不成立了。"""


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 shell 的 ``NO_COLOR`` 摘掉——否则这些断言测的是 shell，不是 app。

    ``App.__init__`` 见到 ``NO_COLOR`` 会给自己挂一层 ``Monochrome`` 滤镜（Textual 8.2.8
    实测），于是屏幕上一切都是灰的、一条真彩色都不会有：**测试会假绿**。跑测试的 shell
    里正好设着 ``NO_COLOR=1``。用户自己的会话里没有它（环境审计 §5），所以摘掉才是真相。
    """
    monkeypatch.delenv("NO_COLOR", raising=False)


def backend(*, lists: int = 14) -> FakeBackend:
    """一份够长的缓存：**清单列表要长到出现滚动条**。

    滚动条是这套颜色里最容易漏掉的一处——它由 Textual 的 ``Widget.DEFAULT_CSS`` 接在
    ``$scrollbar`` 上（``#003054``），没人写它，但每超一屏就画一次真彩色。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_task("写周报", list_name="inbox1", id="t1")
    for index in range(lists):
        fake.add_list(f"清单 {index:02d}", id=f"l{index}")
    return fake


def colour_types_of(style_text: str) -> tuple[ColorType, ...]:
    """一条 Rich 样式里出现的每个颜色的类型（没有颜色就是空元组）。"""
    style = Style.parse(style_text)
    return tuple(colour.type for colour in (style.color, style.bgcolor) if colour is not None)


def sgr_parameters(emitted: str) -> set[int]:
    """屏幕字节里出现过的每一个 SGR 参数（``\\x1b[36;49m`` → ``{36, 49}``）。

    不比对整串 ``\\x1b[36m``：Textual 把同一格的前景与背景拼进**一条**序列（实测
    ``\\x1b[36;49m``），所以「是不是 ANSI 6」要按参数看，不按整串看。
    """
    out: set[int] = set()
    for group in re.findall(r"\x1b\[([0-9;]*)m", emitted):
        out.update(int(part) for part in group.split(";") if part)
    return out


# ------------------------------------------------------------------ 颜色角色


def test_every_rich_colour_role_is_an_ansi_slot():
    """页面用的每个颜色角色，在 Rich 的词汇里都是 16 色之一。

    ``default`` 也放行：那是「终端自己的前景/背景」，正是页面底色要的东西。
    """
    assert theme.RICH_ROLES, "主题里一个颜色角色都没有，这条测试失去意义了"
    for name in theme.RICH_ROLES:
        value = getattr(theme, name)
        types = colour_types_of(value)
        for colour_type in types:
            assert colour_type not in TRUE_COLOUR, (
                f"{name} = {value!r} 是真彩色/256 色（{colour_type}）："
                "它会让终端主题失效——颜色只能写成 ANSI 的名字"
            )


def test_every_css_colour_is_spelled_ansi():
    """CSS 那一半（Textual 的词汇）同理：只许 ``ansi_*`` 名。

    Textual 的 CSS 里 ``cyan`` 是 ``#00FFFF``、``$surface`` 是主题里的真彩色值——同一个词
    在两种语境里是两个颜色（审计 §7.6），所以两种拼法分开命名，都得能一眼认出来。
    """
    assert theme.CSS_ROLES, "主题里没有 CSS 那一半的颜色拼写"
    for name in theme.CSS_ROLES:
        value = getattr(theme, name)
        assert value.startswith("ansi_"), (
            f"{name} = {value!r} 不是 ansi_* 拼法：CSS 具名色与 $ 变量都是真彩色，"
            "会把终端主题顶掉"
        )


def test_the_overdue_token_is_a_token_and_nothing_more():
    """逾期只给一个语义 token，**不实现**标红（#37 给位、#52 接线）。

    这条守的是范围：``TaskItem`` 今天没有 overdue 位，TUI 也不许判断日期（架构的允许表里
    没有 ``dida.logical_day``），所以地基里能做出来的只有这个名字。
    """
    from dida.sync.engine import TaskItem

    assert theme.OVERDUE, "逾期得有个语义 token 让 #52 接线"
    assert not hasattr(TaskItem, "overdue"), (
        "TaskItem 长出 overdue 位了：那是 #37 的工单，本票只提供 token，不实现标红"
    )


# ------------------------------------------------------------------ 字形宽度


def test_structural_glyphs_are_one_cell_and_never_ambiguous():
    """结构字形必须**宽度不含糊**：rich 量 1 格，而且 Unicode 不说它是东亚歧义宽度。

    两件事必须同时成立，因为两边都会错：``☰`` 是「rich 量 2 格而 Unicode 说中性」（每行比
    终端实际画的宽一格），``▣ ★ · ─`` 是「rich 量 1 格而 Unicode 说是歧义宽度」（CJK 字体下
    终端可能画 2 格）。用户 ``LANG=zh_CN.UTF-8``，两种都会把列弄歪——而列一旦歪了，
    「对齐出来的节奏」就没有了。
    """
    assert theme.STRUCTURAL_GLYPHS, "一个结构字形都没有，这条测试失去意义了"
    for glyph in theme.STRUCTURAL_GLYPHS:
        assert len(glyph) == 1, f"{glyph!r} 不是一个字形"
        rich_width = cell_len(glyph)
        east_asian = unicodedata.east_asian_width(glyph)
        assert rich_width == 1, f"{glyph!r} 被 rich 量成 {rich_width} 格——每一行都会错位"
        assert east_asian not in ("A", "W", "F"), (
            f"{glyph!r} 的东亚宽度是 {east_asian}（歧义/全角）：rich 量它 1 格，"
            "但 zh_CN 的终端可能画 2 格——结构位上不许用它"
        )


def test_the_trigram_is_gone_because_rich_measures_it_two_cells_wide():
    """``☰`` 是这条规矩的活证据：rich 量它 **2 格**，而 Unicode 说它中性宽度。

    今天没有对齐列所以看不出来；一旦有列（条数、截止时间），每一行都会比终端画出来的宽
    一格。这条断言量的是**理由**：哪天 rich 改了这张表，这条测试会红，提醒重新决定。
    """
    assert cell_len("☰") == 2, "rich 不再把 ☰ 量成 2 格了——这条理由过期，重新量一遍再删"
    assert "☰" not in theme.STRUCTURAL_GLYPHS


def test_only_end_of_line_glyphs_may_be_ambiguous():
    """歧义宽度只允许出现在**行尾最后一个字形**那种位置（裁断记号）。

    那里后面没有别的东西要对齐，宽度错了也坏不了列。Nerd Font 图标（私用区，EAW=Ambiguous）
    同理只许用在装饰位——这一条把「哪儿可以用」写成了可查的清单。
    """
    assert unicodedata.east_asian_width(theme.ELLIPSIS) == "A", (
        "裁断记号的宽度前提变了，重新决定它还能不能放在行尾"
    )
    assert theme.ELLIPSIS not in theme.STRUCTURAL_GLYPHS
    for glyph in theme.DECORATION_GLYPHS:
        assert len(glyph) == 1


# ------------------------------------------------------------------ 渲染：真跑一遍


def test_the_app_asks_textual_for_native_ansi_colour():
    """``App(ansi_color=True)``——「跟随终端主题」就是靠这一个开关落地的。"""
    app = DidaApp(backend())

    assert app.ansi_color is True
    assert app.native_ansi_color is True, "开了 ansi_color 却没生效：颜色还是会被翻成真彩色"


async def test_the_accent_reaches_the_terminal_as_ansi_six_not_truecolour():
    """**本票最重要的一条**：强调色发出去的是 ANSI 6，而不是 ``38;2;…``。

    真跑一遍 app，读合成器排出来的每一行、逐段渲染成真彩色终端会收到的字节。默认
    （``ansi_color=None``）时同一条 CSS 出去是 Monokai 的 ``38;2;88;209;235``——那正是
    「用户的终端主题一眼都没被用到」的样子。
    """
    app = DidaApp(backend())

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        emitted = screen_sgr(app)

    assert "38;2;" not in emitted, f"前景发出了真彩色：\n{emitted[:400]}"
    assert "48;2;" not in emitted, f"背景发出了真彩色：\n{emitted[:400]}"
    assert 36 in sgr_parameters(emitted), "强调色没有以 ANSI 6（\\x1b[36…）发出去"
    assert 49 in sgr_parameters(emitted), "页面底色不是终端自己的（\\x1b[49m）"
