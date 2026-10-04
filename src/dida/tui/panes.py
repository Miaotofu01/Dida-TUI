"""三栏的内容渲染。

渲染规则（内容全部来自引擎的视图模型，这里不做任何业务判断——分组、排序、
逾期判定、截止时间读法都在 :mod:`dida.sync.engine` 那边定好了）：

- 清单行：``❯ 清单名  未完成条数``。
- 分区标题：``── 逾期 · 3 项``；逾期区标红，且排在最前（顺序由引擎给）。
- 任务行：``❯ ! 标题  清单名  今天 18:00``，光标行反色。
- 刚完成的那一行再叠一层高亮（:data:`FLASH_STYLE`）：完成不可逆，这一下必须看得见。
- 颜色只用终端 16 色对应的名字（``red`` / ``yellow`` / ``dim``），不写死 hex；
  ``tests/test_app_view.py`` 扫源码守着这条。

一个栏位的内容是一整块 ``Static``（Rich ``Text`` 逐行拼出来），光标移动只是重画这块文本。
换成逐行 widget 不会影响栏位的公开接口（``render_lists`` / ``render_groups``）。
"""

from __future__ import annotations

from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from dida.sync.engine import GroupKind, ListSummary, SyncStatus, TaskGroup, TaskItem

CURSOR_MARK = "❯"
"""光标行的行首标记；光标行同时反色。"""

BLANK_MARK = " "
"""非光标行的行首占位，保证列不错位。"""

PRIORITY_STYLES = {"!": "red", "~": "yellow", "·": "dim"}
"""优先级标记的配色：高红、中黄、低与无暗灰。"""

GROUP_HEADER_STYLE = "bold"
GROUP_HEADER_OVERDUE_STYLE = "bold red"
"""逾期区标题标红——逾期置顶之外的另一半。"""

FLASH_STYLE = "bold green"
"""刚完成的那一行的高亮（几秒后由 ``TaskPane.flash(None)`` 收起）。

完成在服务端不可逆（ADR-0002），所以这一下必须看得见：绿色是「做完了」，而光标的反色
仍然是光标自己的那一层，两者叠在一起。终端 16 色里的名字，不写死 hex。
"""

EMPTY_STYLE = "dim"

PLACEHOLDER = "（未接入）"
"""详情栏的占位：内容归 t18。"""


def group_header(group: TaskGroup) -> Text:
    """分区标题行。"""
    style = GROUP_HEADER_OVERDUE_STYLE if group.kind is GroupKind.OVERDUE else GROUP_HEADER_STYLE
    return Text(f"── {group.title} · {group.count} 项", style=style)


def task_line(item: TaskItem, *, selected: bool, flash: bool = False) -> Text:
    """任务行：优先级标记 + 标题 + 清单名 + 人类可读的截止时间。

    ``flash`` 是「刚完成」的那一层额外高亮（见 :data:`FLASH_STYLE`），与 ``selected``
    独立：完成的那一行通常正是光标行，两层要能叠。
    """
    text = Text()
    text.append(f"{CURSOR_MARK if selected else BLANK_MARK} ")
    text.append(item.priority_mark, style=PRIORITY_STYLES.get(item.priority_mark, ""))
    text.append(f" {item.title}  ")
    text.append(item.list_name, style=EMPTY_STYLE)
    text.append("  ")
    text.append(item.due_text, style=EMPTY_STYLE if item.due is None else "")
    if selected:
        text.stylize("reverse")
    if flash:
        text.stylize(FLASH_STYLE)
    return text


def list_line(summary: ListSummary, *, selected: bool) -> Text:
    """清单行：名字 + 未完成条数徽标。"""
    text = Text()
    text.append(f"{CURSOR_MARK if selected else BLANK_MARK} {summary.name}  ")
    text.append(str(summary.unfinished), style=EMPTY_STYLE)
    if selected:
        text.stylize("reverse")
    return text


def pane_body(lines: Sequence[Text], *, empty: str) -> Text:
    """把一个栏位的所有行拼成一块文本；没有行时给空状态的说明。"""
    if not lines:
        return Text(empty, style=EMPTY_STYLE)
    body = Text()
    for index, line in enumerate(lines):
        if index:
            body.append("\n")
        body.append_text(line)
    return body


class Pane(VerticalScroll):
    """三栏中的一栏：边框标题 + 一块可重画的内容 + 一个光标。

    光标的行为（``j``/``k``、方向键、走到边界就停住、始终留在可见区里）在这里统一实现；
    子类只回答三件事：有哪些行、第 i 行画在内容块的第几行、内容怎么画。
    """

    BORDER_TITLE = ""
    EMPTY_TEXT = ""
    can_focus = True
    # 栏位自己收 j/k 与方向键；子类的绑定排在父类的滚动绑定前面，所以上下键归光标
    BINDINGS = [
        Binding("j", "cursor_down", "下移", show=False),
        Binding("down", "cursor_down", "下移", show=False),
        Binding("k", "cursor_up", "上移", show=False),
        Binding("up", "cursor_up", "上移", show=False),
    ]
    BORDER_LINES = 2
    """边框吃掉的行数，用来看光标是否还在可见区里。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.border_title = self.BORDER_TITLE
        self._rows: tuple[object, ...] = ()
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(Text(self.EMPTY_TEXT, style=EMPTY_STYLE), classes="pane-body")

    def set_body(self, body: Text) -> None:
        """重画内容（Rich 文本，逐行拼好）。"""
        self.query_one(Static).update(body)

    def action_cursor_down(self) -> None:
        """下移一行（``j`` / ``↓``）。"""
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        """上移一行（``k`` / ``↑``）。"""
        self._move_cursor(-1)

    def _move_cursor(self, step: int) -> None:
        if not self._rows:
            return
        self._cursor = min(max(self._cursor + step, 0), len(self._rows) - 1)
        self._redraw()
        self._scroll_line_into_view(self._line_of_cursor(self._cursor))

    def _line_of_cursor(self, index: int) -> int:
        """第 ``index`` 行画在内容块的第几行文本上。默认一一对应。"""
        return index

    def _redraw(self) -> None:
        """按 ``self._cursor`` 重画内容块。"""
        raise NotImplementedError

    def _scroll_line_into_view(self, line: int) -> None:
        """让光标行留在可见区里。"""
        top = self.scroll_offset.y
        height = max(1, self.size.height - self.BORDER_LINES)
        if line < top:
            self.scroll_to(y=line, animate=False)
        elif line >= top + height:
            self.scroll_to(y=line - height + 1, animate=False)


class ListPane(Pane):
    """清单栏（左）：清单与未完成条数。"""

    BORDER_TITLE = "清单"
    EMPTY_TEXT = "（还没有清单）"

    def render_lists(self, summaries: Sequence[ListSummary]) -> None:
        """接住引擎给的清单，光标回到第一行。"""
        self._rows = tuple(summaries)
        self._cursor = 0
        self._redraw()

    @property
    def selected_list_id(self) -> str | None:
        """光标下的清单 id；没有清单时是 ``None``。"""
        return self._rows[self._cursor].id if self._rows else None

    def _redraw(self) -> None:
        lines = [list_line(item, selected=index == self._cursor) for index, item in enumerate(self._rows)]
        self.set_body(pane_body(lines, empty=self.EMPTY_TEXT))


class TaskPane(Pane):
    """任务列（中）：分组后的任务。"""

    BORDER_TITLE = "今日"
    EMPTY_TEXT = "（今天没有未完成的任务）"

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._groups: tuple[TaskGroup, ...] = ()
        self._line_of_row: tuple[int, ...] = ()
        self._flashed: str | None = None

    def render_groups(self, groups: Sequence[TaskGroup]) -> None:
        """接住引擎给的分区，光标回到第一行。"""
        self._groups = tuple(groups)
        self._rows = tuple(item for group in self._groups for item in group.items)
        self._cursor = 0
        self._redraw()

    def flash(self, task_id: str | None) -> None:
        """让刚完成的那一行高亮；``None`` 收起高亮。

        只改「怎么画」，不改数据：高亮由 ``DidaApp`` 的定时器收起（ADR-0002 的补偿——
        完成不可逆，按下去必须看得见）。任务 id 存在栏位里，所以中途重画不会把高亮弄丢。
        """
        self._flashed = task_id
        self._redraw()

    @property
    def selected_task_id(self) -> str | None:
        """光标下的任务 id；没有任务时是 ``None``。"""
        return self._rows[self._cursor].task_id if self._rows else None

    def _line_of_cursor(self, index: int) -> int:
        return self._line_of_row[index]

    def _redraw(self) -> None:
        lines: list[Text] = []
        line_of_row: list[int] = []
        for group in self._groups:
            lines.append(group_header(group))
            for item in group.items:
                lines.append(
                    task_line(
                        item,
                        selected=len(line_of_row) == self._cursor,
                        flash=item.task_id == self._flashed,
                    )
                )
                line_of_row.append(len(lines) - 1)
        self._line_of_row = tuple(line_of_row)
        self.set_body(pane_body(lines, empty=self.EMPTY_TEXT))


class DetailPane(Pane):
    """详情栏（右）：当前任务。内容归 t18，本工单只占位。"""

    BORDER_TITLE = "详情"
    EMPTY_TEXT = PLACEHOLDER
    can_focus = False  # Tab 只在左栏与中栏之间切换


class StatusBar(Static):
    """状态栏：已同步时刻、待推送数量、当前逻辑日。"""


def format_status(status: SyncStatus) -> str:
    """状态栏文本。措辞按 GLOSSARY：逻辑日 / 待推送 / 已同步。"""
    logical_day = status.logical_day.strftime("%m-%d") if status.logical_day else "—"
    last_refresh = status.last_refresh_at.strftime("%H:%M") if status.last_refresh_at else "—"
    return f"已同步 {last_refresh} · 待推送 {status.pending_count} · 逻辑日 {logical_day}"
