"""一页的公共骨架：一列行 + 一个光标 + 让它留在可见区里。

三层页面共用的东西只有这几件：一列已经画好的行、光标停在哪一行、「清单很多时要能滚动」
（用户故事 22）、以及本票（#51）加进这一层的两件公共外观：**行首那个「谁在光标上」的记号**
与**通栏细线**那种不可停光标的行。行**怎么画**归各自的页面（前缀字符、条数、字段……），
这里只管「哪些行可以停光标」与「光标动了之后屏幕跟着动」。

**光标按行 id 认，不按行号**：后台刷新回来之后行的条数与顺序都可能变，而用户故事 21/57
要的是「刷新不把我踢回第一行」。所以 :meth:`CursorPage.set_rows` 记着光标那一行的 id，
换一份行之后再认回它；那一行真的没了（清单被删了）才退回夹紧后的位置。

``id=None`` 的行是**不可停光标**的（项目组小标题、通栏细线、空状态、已完成行那种）：
光标越过它们，``enter`` 也因此永远落不到小标题上。

## 一行 = 一屏一行（这是算出来的，不是假设）

列表页的行**裁断不折行**（:attr:`CursorPage.CLIP_ROWS`，ADR-0007 的「截断与折行按页分工」）：
一条长标题不会把自己折成两行、把光标标记挤到单独一行去、把滚动的位置算错。所以「第几个
Row」就是「第几屏行」，滚动与装饰光标条的位置都能直接算出来——**详细页是另一半**（那里
折行，光标与滚动按屏幕行偏移表算），归 #43。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.reactive import reactive
from textual.widgets import Static

from dida.tui import theme

__all__ = [
    "BLANK_MARK",
    "CURSOR_MARK",
    "EMPTY_STYLE",
    "CursorPage",
    "Row",
    "empty_row",
    "heading_row",
    "rule_row",
]

CURSOR_MARK = theme.CURSOR_MARK
"""光标那一行的行首标记；光标行同时换成强调色。"""

BLANK_MARK = theme.BLANK_MARK
"""非光标行的行首占位，保证列不错位。"""

EMPTY_STYLE = theme.MUTED
"""空状态、小标题、通栏细线这一类「不是可选项」的东西用的那一档。"""


@dataclass(frozen=True)
class Row:
    """一页里的一行：身份 + 已经画好的文字。

    ``id`` 是这一行的身份（清单 id / 任务 id），也是光标认回来的凭据；``None`` 表示这一行
    不可停光标（小标题、空状态、通栏细线）。

    ``rule`` 是通栏细线那种「宽度要等排完版才知道」的行：它在重画时按当时的宽度铺满
    （:meth:`CursorPage._redraw`），所以 ``text`` 留空。
    """

    id: str | None
    text: Text
    rule: bool = False


def empty_row(text: str) -> Row:
    """空状态那一行：不可停光标，暗色。

    「一行都没有」在屏幕上必须是一句**说得明白的话**，而不是一片什么都没有的黑
    （用户故事 53）。一行都没组出来时 :class:`CursorPage` 也画 :attr:`CursorPage.EMPTY_TEXT`，
    但那一页若还有别的东西要画（标题、字段），就得自己把这一行摆进去。
    """
    return Row(id=None, text=theme.styled(text, EMPTY_STYLE))


def heading_row(text: Text) -> Row:
    """分区标题那一行：不可停光标，字重（层级靠字重、``dim``、留白——不靠更亮的颜色）。

    各页自己拼标题的文字（抬头、条数），这里只保证**它不可停光标**：光标越过它，
    ``enter`` 永远落不到标题上。
    """
    return Row(id=None, text=text)


def rule_row() -> Row:
    """通栏细线：顶栏下面一条、每个分组标题下面一条（层级就从这些线来）。

    宽度不在这里定：它要等这一页排完版才算得出来，所以只留一个记号，重画时铺满。
    """
    return Row(id=None, text=Text(), rule=True)


class CursorPage(VerticalScroll):
    """一列可选行 + 一个光标。子类只回答「有哪些行」。"""

    can_focus = True
    EMPTY_TEXT = "（这里什么都没有）"
    """一行都没有时屏幕上的那句话（各页自己写明白一点）。"""

    CLIP_ROWS = True
    """行**裁断不折行**（ADR-0007：列表页截断，详细页换行）。

    列表要能一行一条地扫视，而折行会让光标标记与标题分家、并把滚动的位置算错。值日的是
    ``#page-body`` 上的 ``text-wrap: nowrap`` + ``text-overflow: ellipsis``（在
    :func:`dida.tui.theme.app_css` 的样式表里）。**详细页是另一半**：它要折行，所以 #43
    接手时把这个开关关掉，并把光标与滚动换成屏幕行偏移表。
    """

    bar_pos: reactive[float] = reactive(0.0)
    """装饰性光标条当前停在第几屏行（滑动期间是小数）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: tuple[Row, ...] = ()
        self._cursor = 0
        """光标在第几个**可停**的行上（不是第几行文本）。"""
        self._selected_id: str | None = None
        """光标那一行的 id；没有可停的行时是 ``None``。"""
        self._motion = False
        """要不要画那条会追赶的装饰光标条；由 app 按 ``animations`` 开关设一次。

        ⚠ 名字**不能**是 ``_animate``：``Widget._animate`` 是 Textual 自己那个绑好的
        animator，盖掉它之后 ``self.animate(...)`` 会抛 ``TypeError: 'bool' object is not
        callable``——报错点在 Textual 的 widget.py 里，离现场很远。
        """

    def compose(self) -> ComposeResult:
        # 一页只有两块：正文（重画就是换它）与那条装饰光标条。都给 id，好让后来加的控件
        # 不与它们混淆。
        yield Static(theme.styled(self.EMPTY_TEXT, EMPTY_STYLE), id="page-body")
        yield Static(id="cursor-bar")

    # ---------------------------------------------------------------- 数据

    @property
    def selected_id(self) -> str | None:
        """光标停在哪一行（``None`` = 这一页没有可停的行）。

        这是页面给外层的口子：``o`` 要知道当前是哪条任务，``enter`` 要知道进哪一个清单。
        """
        return self._selected_id

    def set_animate(self, enabled: bool) -> None:
        """这一页要不要动（由 app 按 ``auto|on|off`` 开关设一次，见 ADR-0007 三）。"""
        self._motion = enabled

    def set_rows(self, rows: Sequence[Row], *, keep_cursor: bool = True) -> None:
        """换一份行，并按**行 id** 把光标认回原来那一行。

        刷新走的就是这条路：行的条数会变、顺序会变，唯独光标不该跳（用户故事 21/57）。

        ``keep_cursor=False`` 是「换了一份**别的东西**」：光标回到第一行。换了一个清单还把
        上一个清单的行号带过去，落点就是随机的——两个清单的第 3 条毫无关系。
        """
        previous = self._selected_id if keep_cursor else None
        self._rows = tuple(rows)
        selectable = [row.id for row in self._rows if row.id is not None]
        if not keep_cursor:
            self._cursor = 0
        if previous is not None and previous in selectable:
            self._cursor = selectable.index(previous)
        elif selectable:
            self._cursor = min(self._cursor, len(selectable) - 1)
        else:
            self._cursor = 0
        self._selected_id = selectable[self._cursor] if selectable else None
        self._redraw()
        self._land_bar()
        self.scroll_cursor_into_view()

    # ---------------------------------------------------------------- 光标

    def action_cursor_down(self) -> None:
        """``j`` / ``↓``：下移一行。"""
        self._move(1)

    def action_cursor_up(self) -> None:
        """``k`` / ``↑``：上移一行。"""
        self._move(-1)

    def _move(self, step: int) -> None:
        selectable = [row.id for row in self._rows if row.id is not None]
        if not selectable:
            return
        from_line = self._line_of_cursor()
        self._cursor = min(max(self._cursor + step, 0), len(selectable) - 1)
        self._selected_id = selectable[self._cursor]
        # **选中立刻到位**：这一行以下除了那条装饰条，没有任何东西是延迟的。终端无法在两条
        # 之间画半格，所以「滑动」只能是一根独立的条子自己追；若让滑动承担选中，那 ~120ms
        # 里用户看到的位置与实际选中不一致，按下 space 会打中另一条——这不是手感问题，是
        # 看起来像 bug 的正确性问题（ADR-0007 三）。
        self._redraw()
        self.scroll_cursor_into_view()
        self._flash_bar(from_line)

    # ---------------------------------------------------------------- 画

    def _body(self) -> Static:
        return self.query_one("#page-body", Static)

    def _bar(self) -> Static:
        return self.query_one("#cursor-bar", Static)

    def _row_width(self) -> int:
        """正文那一块当前的宽度（格）——通栏细线要铺满它。"""
        return self._body().size.width or self.size.width or 0

    def _redraw(self) -> None:
        """按当前光标重画这一块文本。"""
        if not self._rows:
            self._body().update(theme.styled(self.EMPTY_TEXT, EMPTY_STYLE))
            return
        body = Text()
        for index, row in enumerate(self._rows):
            if index:
                body.append("\n")
            body.append_text(self._line_text(row))
        self._body().update(body)

    def _line_text(self, row: Row) -> Text:
        """一个 Row 画在正文里的那一行（正文与「它占几屏行」用的是同一份文字）。

        ``#43`` 之后这件事有了第二个读者：详细页按这一行算出它折成几屏行（:meth:`_row_lines`）。
        两处各拼一遍的话，量出来的行数迟早与画出来的不一样——而那正是光标与滚动要用的数。
        """
        if row.rule:
            # 细线**自己铺满整幅页面、齐左**：它不可停光标，所以不参与行首那两格的
            # 光标空档。带上空档这一行就是 ``width + 2`` 格，正文的 ``nowrap`` +
            # ``ellipsis`` 会裁掉一格再补一个 ``…``——屏幕上看到的是「缩进两格、以
            # 省略号收尾的一条线」（实测，每一行细线都是）。
            return theme.styled(theme.RULE * max(0, self._row_width()), EMPTY_STYLE)
        line = Text()
        line.append(f"{CURSOR_MARK if self._is_cursor(row) else BLANK_MARK} ")
        line.append_text(row.text)
        if self._is_cursor(row):
            # 光标行 = 强调色 + 字重。层级不靠亮度：用户的槽位 8–15 与 1–6 同值，
            # 「更亮」买不到任何对比（环境审计 §1a）。
            line.stylize(theme.SELECTED)
        return line

    def _is_cursor(self, row: Row) -> bool:
        return row.id is not None and row.id == self._selected_id

    def _row_lines(self) -> tuple[int, ...]:
        """每个 Row 占几**屏行**。

        列表页恒为 1：那里的行裁断不折行（:attr:`CLIP_ROWS`），所以「第几个 Row」就是「第几
        屏行」。**详细页是另一半**：它的字段行是折行块，所以它盖掉这一个钩子，按 rich 的
        ``divide_line`` 现算（#43）。
        """
        return (1,) * len(self._rows)

    def _total_lines(self) -> int:
        """正文一共几屏行——装饰条就排在它下面（:meth:`watch_bar_pos`）。"""
        return sum(self._row_lines())

    def _cursor_lines(self) -> int:
        """光标那个 Row 占几屏行（一张折行块可能有五六行）。"""
        lines = self._row_lines()
        for index, row in enumerate(self._rows):
            if self._is_cursor(row):
                return lines[index]
        return 1

    def _line_of_cursor(self) -> int:
        """光标那一行画在这一块文本的第几**屏行**上（前缀和，不是行号）。"""
        line = 0
        lines = self._row_lines()
        for index, row in enumerate(self._rows):
            if self._is_cursor(row):
                return line
            line += lines[index]
        return 0

    def on_resize(self) -> None:
        """宽度变了：通栏细线得按新的宽度重新铺满（它是按格算的）。"""
        if self._rows:
            self._redraw()

    # ---------------------------------------------------------------- 会追赶的装饰光标条

    def watch_bar_pos(self, value: float) -> None:
        """装饰条的位置变了（滑动期间是小数）：把它挪到那一**屏行**上。

        条子排在正文**后面**（DOM 顺序），所以它的落位本来就是「正文有多高」——正文折行之后
        那是 ``_total_lines()``，不是 ``len(self._rows)``。
        """
        self._bar().styles.offset = Offset(0, int(round(value)) - self._total_lines())

    def _flash_bar(self, from_line: int) -> None:
        """选中已经落地，装饰条**从旧位置**追上来（追到了就自己退场）。

        条子画在正文**后面**（Textual 里后挂的控件画在上面），所以飞行中它会盖住它经过的
        那两格——那正是它要的效果；到站之后它退场，``❯`` 与强调色一直都在。
        """
        if not self._motion or len(self._rows) <= 1:
            return
        bar = self._bar()
        bar.update(theme.styled("  ", theme.BAR))
        bar.styles.visibility = "visible"
        self.bar_pos = float(from_line)
        # ``on_complete`` 是承重的：没有它，条子会永远停在它落到的位置，用自己那两个空格
        # 盖住 ``❯``。原型里真出过这个 bug，用户报的是「上下键选中的行会消失」。
        self.animate(
            "bar_pos",
            float(self._line_of_cursor()),
            duration=theme.CURSOR_BAR_MS / 1000,
            easing="out_cubic",
            on_complete=self._land_bar,
        )

    def _land_bar(self) -> None:
        """条子到站：退场。选中本来就画在正文里，所以它这一退没有任何东西跟着消失。

        用 ``visibility: hidden`` 而不是「透明的背景」：一个还在画的控件会把底下那一行擦掉，
        哪怕它什么都不涂（原型实测）。
        """
        bar = self._bar()
        bar.styles.visibility = "hidden"
        bar.update(Text(""))

    def scroll_cursor_into_view(self) -> None:
        """让光标那一段留在可见区里（清单比一屏长时这一步就是「能滚动」）。

        不在屏上的那一页与还没量过尺寸的那一帧都跳过：那时 ``size`` 是 0，按它算出来的
        偏移毫无意义，还会在切回来的时候留下一屏错位。``esc`` 回来时由外层再叫一次
        （:meth:`dida.tui.app.DidaApp._show`）——光标不只是「还在那一行」，还得看得见。

        滚的是**屏行**：列表页一行就是一屏行（:attr:`CLIP_ROWS`），详细页一个字段是一张
        折行块（#43）——那里「第几个字段」与「第几屏行」是两件事，按行号滚会停在半路。
        一块比一屏还高的（一段很长的描述）两头不能兼顾，那就让它的**头**留在屏上：标签与
        ``❯`` 比正文最后一行值钱。
        """
        if not self._rows or not self.display or self.size.height <= 0:
            return
        first = self._line_of_cursor()
        last = first + self._cursor_lines() - 1
        top = self.scroll_offset.y
        height = max(1, self.size.height)
        if first < top:
            self.scroll_to(y=first, animate=False)
        elif last >= top + height:
            self.scroll_to(y=min(first, last - height + 1), animate=False)
