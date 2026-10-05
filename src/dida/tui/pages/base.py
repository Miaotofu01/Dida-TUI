"""一页的公共骨架：一列行 + 一个光标 + 让它留在可见区里。

三层页面共用的东西只有这三件：一列已经画好的行、光标停在哪一行、以及「清单很多时要能
滚动」（用户故事 22）。行**怎么画**归各自的页面（前缀字符、条数、字段……），这里只管
「哪些行可以停光标」与「光标动了之后屏幕跟着动」。

**光标按行 id 认，不按行号**：后台刷新回来之后行的条数与顺序都可能变，而用户故事 21/57
要的是「刷新不把我踢回第一行」。所以 :meth:`CursorPage.set_rows` 记着光标那一行的 id，
换一份行之后再认回它；那一行真的没了（清单被删了）才退回夹紧后的位置。

``id=None`` 的行是**不可停光标**的（项目组小标题、空状态、已完成行那种）：光标越过它们，
``enter`` 也因此永远落不到小标题上。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

__all__ = ["CURSOR_MARK", "BLANK_MARK", "EMPTY_STYLE", "CursorPage", "Row", "empty_row"]

CURSOR_MARK = "❯"
"""光标那一行的行首标记；光标行同时反色。"""

BLANK_MARK = " "
"""非光标行的行首占位，保证列不错位。"""

EMPTY_STYLE = "dim"
"""空状态、小标题这一类「不是可选项」的文字用的暗灰。"""


@dataclass(frozen=True)
class Row:
    """一页里的一行：身份 + 已经画好的文字。

    ``id`` 是这一行的身份（清单 id / 任务 id），也是光标认回来的凭据；``None`` 表示这一行
    不可停光标（小标题、空状态）。
    """

    id: str | None
    text: Text


def empty_row(text: str) -> Row:
    """空状态那一行：不可停光标，暗灰。

    「一行都没有」在屏幕上必须是一句**说得明白的话**，而不是一片什么都没有的黑
    （用户故事 53）。一行都没组出来时 :class:`CursorPage` 也画 :attr:`CursorPage.EMPTY_TEXT`，
    但那一页若还有别的东西要画（标题、字段），就得自己把这一行摆进去。
    """
    return Row(id=None, text=Text(text, style=EMPTY_STYLE))


class CursorPage(VerticalScroll):
    """一列可选行 + 一个光标。子类只回答「有哪些行」。"""

    can_focus = True
    EMPTY_TEXT = "（这里什么都没有）"
    """一行都没有时屏幕上的那句话（各页自己写明白一点）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: tuple[Row, ...] = ()
        self._cursor = 0
        """光标在第几个**可停**的行上（不是第几行文本）。"""
        self._selected_id: str | None = None
        """光标那一行的 id；没有可停的行时是 ``None``。"""

    def compose(self) -> ComposeResult:
        yield Static(Text(self.EMPTY_TEXT, style=EMPTY_STYLE), classes="page-body")

    # ---------------------------------------------------------------- 数据

    @property
    def selected_id(self) -> str | None:
        """光标停在哪一行（``None`` = 这一页没有可停的行）。

        这是页面给外层的口子：``o`` 要知道当前是哪条任务，``enter`` 要知道进哪一个清单。
        """
        return self._selected_id

    def set_rows(self, rows: Sequence[Row]) -> None:
        """换一份行，并按**行 id** 把光标认回原来那一行。

        刷新走的就是这条路：行的条数会变、顺序会变，唯独光标不该跳（用户故事 21/57）。
        """
        previous = self._selected_id
        self._rows = tuple(rows)
        selectable = [row.id for row in self._rows if row.id is not None]
        if previous is not None and previous in selectable:
            self._cursor = selectable.index(previous)
        elif selectable:
            self._cursor = min(self._cursor, len(selectable) - 1)
        else:
            self._cursor = 0
        self._selected_id = selectable[self._cursor] if selectable else None
        self._redraw()

    def select(self, row_id: str) -> None:
        """把光标摆到某一行上（那一行不在时什么都不做）。"""
        selectable = [row.id for row in self._rows if row.id is not None]
        if row_id not in selectable:
            return
        self._cursor = selectable.index(row_id)
        self._selected_id = row_id
        self._redraw()
        self._scroll_cursor_into_view()

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
        self._cursor = min(max(self._cursor + step, 0), len(selectable) - 1)
        self._selected_id = selectable[self._cursor]
        self._redraw()
        self._scroll_cursor_into_view()

    # ---------------------------------------------------------------- 画

    def _redraw(self) -> None:
        """按当前光标重画这一块文本。"""
        if not self._rows:
            self.query_one(Static).update(Text(self.EMPTY_TEXT, style=EMPTY_STYLE))
            return
        body = Text()
        for index, row in enumerate(self._rows):
            if index:
                body.append("\n")
            selected = row.id is not None and row.id == self._selected_id
            line = Text()
            line.append(f"{CURSOR_MARK if selected else BLANK_MARK} ")
            line.append_text(row.text)
            if selected:
                line.stylize("reverse")
            body.append_text(line)
        self.query_one(Static).update(body)

    def _line_of_cursor(self) -> int:
        """光标那一行画在这一块文本的第几行上。"""
        for index, row in enumerate(self._rows):
            if row.id is not None and row.id == self._selected_id:
                return index
        return 0

    def _scroll_cursor_into_view(self) -> None:
        """让光标行留在可见区里（清单比一屏长时这一步就是「能滚动」）。"""
        if not self._rows:
            return
        line = self._line_of_cursor()
        top = self.scroll_offset.y
        height = max(1, self.size.height)
        if line < top:
            self.scroll_to(y=line, animate=False)
        elif line >= top + height:
            self.scroll_to(y=line - height + 1, animate=False)
