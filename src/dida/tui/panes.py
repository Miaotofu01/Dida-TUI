"""三栏的内容渲染。

渲染规则（内容全部来自引擎的视图模型，这里不做任何业务判断——分组、排序、
逾期判定、截止时间读法都在 :mod:`dida.sync.engine` 那边定好了）：

- 清单行：``❯ 清单名  未完成条数``。
- 分区标题：``── 逾期 · 3 项``；逾期区标红，且排在最前（顺序由引擎给）。
- 任务行：``❯ ! 标题  清单名  今天 18:00``，光标行反色；逾期区的行整行标红
  （与分区标题标红是同一件事的两半，见 :data:`OVERDUE_ROW_STYLE`）。
- 刚完成的那一行再叠一层高亮（:data:`FLASH_STYLE`）：完成不可逆，这一下必须看得见。
- 已完成区（底部，默认收起）：标题 ``▸ 已完成 N 项``，``c`` 展开/收起；展开后每行
  ``标题  清单名  完成时间``，整行暗灰 + 删除线。已完成行不参与光标移动。
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
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Static

from dida.sync.engine import (
    NO_DUE_TEXT,
    CompletedItem,
    CompletedSection,
    GroupKind,
    ListSummary,
    SubtaskItem,
    SyncStatus,
    TaskGroup,
    TaskItem,
)

CURSOR_MARK = "❯"
"""光标行的行首标记；光标行同时反色。"""

BLANK_MARK = " "
"""非光标行的行首占位，保证列不错位。"""

PRIORITY_STYLES = {"!": "red", "~": "yellow", "·": "dim"}
"""优先级标记的配色：高红、中黄、低与无暗灰。"""

GROUP_HEADER_STYLE = "bold"
GROUP_HEADER_OVERDUE_STYLE = "bold red"
"""逾期区标题标红——逾期置顶之外的另一半。"""

OVERDUE_ROW_STYLE = "red"
"""逾期**行**标红（故事 14「逾期任务置顶并标红」）：标题之外，行本身也要认得出来。

「这一行算逾期」是引擎的判断（``TaskGroup.kind`` 是 ``OVERDUE``）：栏位只是把引擎给的那
一个事实画成红色，自己不碰日期、不比重。整行铺一层底红，标题与截止时间因此都红；清单名
那一层 ``dim`` 叠在红上（读作暗红，仍是背景信息），优先级标记仍是它自己的颜色，完成后
的 :data:`FLASH_STYLE` 照旧盖在红上（绿比红后加）。终端 16 色里的 ``red``，不写死 hex。
"""

FLASH_STYLE = "bold green"
"""刚完成的那一行的高亮（几秒后由 ``TaskPane.flash(None)`` 收起）。

完成在服务端不可逆（ADR-0002），所以这一下必须看得见：绿色是「做完了」，而光标的反色
仍然是光标自己的那一层，两者叠在一起。终端 16 色里的名字，不写死 hex。
"""

EMPTY_STYLE = "dim"

DETAIL_EMPTY_TEXT = "（没有选中任务）"
"""详情栏没指着任何任务时的话。光标停在哪一条都没有（空屏、或不剩一条）时用它。"""


def group_header(group: TaskGroup) -> Text:
    """分区标题行。"""
    style = GROUP_HEADER_OVERDUE_STYLE if group.kind is GroupKind.OVERDUE else GROUP_HEADER_STYLE
    return Text(f"── {group.title} · {group.count} 项", style=style)


def task_line(item: TaskItem, *, selected: bool, flash: bool = False, overdue: bool = False) -> Text:
    """任务行：优先级标记 + 标题 + 清单名 + 人类可读的截止时间。

    ``flash`` 是「刚完成」的那一层额外高亮（见 :data:`FLASH_STYLE`），与 ``selected``
    独立：完成的那一行通常正是光标行，两层要能叠。

    ``overdue`` 是「这一行在逾期区」这一个事实（见 :data:`OVERDUE_ROW_STYLE`）。它是
    **栏位从分区的 ``kind`` 读出来传进来的**，不是这一层自己判的——逾期判定归引擎。
    文字一个字都不变，只是多一层底色：标红是样式，不是往行里塞字符。
    """
    text = Text(style=OVERDUE_ROW_STYLE if overdue else "")
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


def detail_body(item: TaskItem | None) -> Text:
    """详情栏的内容：标题 + 清单 / 优先级 / 截止 + 描述 / 标签 / 备注（工单 #18、#20）。

    七样字段全部取视图模型里的**成品**：``list_name``、``priority_mark``、``due_text``、
    ``tags_text`` 都是引擎算好的读法（人类可读的截止时间、优先级的标记符号、标签的
    ``#`` 写法），这里一个都不重算——分组、排序、逾期判定、截止读法都在
    :mod:`dida.sync.engine` 那边（TUI 不做业务判断）。

    优先级只画那个标记符号、不翻译成「高/中/低」：中栏那一列画的就是这个符号，两处
    必须长得一样，而「哪个符号算高」是引擎的事实，不该在这里再写一份。

    描述 / 标签 / 备注是**空的那一行不画**（工单 #20 的右栏要的是常驻显示，不是三个空
    标签）：判断的依据就是视图模型给的空串，不是这一层去问「有没有」。
    """
    if item is None:
        return Text(DETAIL_EMPTY_TEXT, style=EMPTY_STYLE)
    body = Text()
    body.append(item.title, style="bold")
    body.append("\n\n")
    body.append("清单  ", style=EMPTY_STYLE)
    body.append(f"{item.list_name}\n")
    body.append("优先级  ", style=EMPTY_STYLE)
    body.append(item.priority_mark, style=PRIORITY_STYLES.get(item.priority_mark, ""))
    body.append("\n")
    body.append("截止  ", style=EMPTY_STYLE)
    body.append(item.due_text, style=EMPTY_STYLE if item.due is None else "")
    for label, value in (("描述", item.desc), ("标签", item.tags_text), ("备注", item.content)):
        if not value:
            continue
        body.append(f"\n{label}  ", style=EMPTY_STYLE)
        body.append(value)
    return body


def lists_body(summaries: Sequence[ListSummary]) -> Text:
    """清单浮层的内容：与左栏同一份清单行（工单 #18）。

    行由 :func:`list_line` 画——与左栏逐字同一份写法（名字 + 未完成条数），所以浮层里看到的
    「工作 2」与左栏那一行是一个意思。没有清单时给左栏那一句空状态。
    """
    return pane_body(
        [list_line(item, selected=False) for item in summaries],
        empty=ListPane.EMPTY_TEXT,
    )


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
    """任务列（中）：分组后的任务 + 底部的已完成区。"""

    BORDER_TITLE = "今日"
    EMPTY_TEXT = "（今天没有未完成的任务）"
    NO_MATCH_TEXT = "（没有匹配的任务）"
    """过滤后一条都不剩时的话：与「今天真的没有任务」必须分得开。"""
    # `c`（已完成）收放底部那一区；键位表里没派给别的工单，也不是输入用的字符
    # `c`（已完成）收放底部那一区；键位表里没派给别的工单，也不是输入用的字符。
    # 光标键必须**重列一遍**：Textual 里子类的 BINDINGS 是覆盖而不是追加，只写 `c` 会把
    # 继承来的 j/k/↑/↓ 一起屏蔽掉，任务列就再也动不了光标了。
    BINDINGS = [
        Binding("j", "cursor_down", "下移", show=False),
        Binding("down", "cursor_down", "下移", show=False),
        Binding("k", "cursor_up", "上移", show=False),
        Binding("up", "cursor_up", "上移", show=False),
        Binding("c", "toggle_completed", "已完成"),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._groups: tuple[TaskGroup, ...] = ()
        self._completed = CompletedSection()
        self._completed_expanded = False
        self._line_of_row: tuple[int, ...] = ()
        self._flashed: str | None = None
        self._empty_text = self.EMPTY_TEXT

    def render_groups(
        self,
        groups: Sequence[TaskGroup],
        completed: CompletedSection = CompletedSection(),
        *,
        empty: str | None = None,
    ) -> None:
        """接住引擎给的分区与已完成区，光标留在原来那条任务上。

        重画不该动光标（工单 #21「只写变化」那一面）：全量刷新拉回来的是同一批任务，每次都把
        光标推回第一行，等于每刷一次就打断一次分诊。原来那条**不在**新行表里了（完成了、
        删了、被过滤词筛掉了）才回到第一行——这也正是下面那条「光标不许停在看不见的任务上」
        的落点。

        展开与否**不**在这里重置：那是用户的看的姿势，不是数据；刷新之后把人家摊开的
        那一区又收回去，正是「刷新让屏幕闪」的另一副面孔。

        ``groups`` 是**筛过**的那一份（过滤词由 ``DidaApp`` 交给
        :func:`~dida.sync.view.filter_groups`）：光标底下的行表就是屏上那几行，所以光标
        永远不会停在一个已经被筛掉的任务上（验收标准 #4）。

        ``empty`` 是「一行都没有」时的那句话；省略就用本栏默认的那一句。过滤期间由
        ``DidaApp`` 换成 :data:`NO_MATCH_TEXT`——屏上明明有任务却写「今天没有未完成的任务」
        是在骗人。
        """
        selected = self.selected_task_id
        self._groups = tuple(groups)
        self._completed = completed
        self._empty_text = empty if empty is not None else self.EMPTY_TEXT
        self._rows = tuple(item for group in self._groups for item in group.items)
        self._cursor = self._index_of(selected)
        self._redraw()
        self._announce_selection()

    def _index_of(self, task_id: str | None) -> int:
        """``task_id`` 在新行表里的位置；它不在（或本来就没有光标）就回第一行。"""
        for index, item in enumerate(self._rows):
            if item.task_id == task_id:
                return index
        return 0

    def _move_cursor(self, step: int) -> None:
        """光标上下移：移完告诉外面现在指着谁（右栏据此跟着走）。"""
        super()._move_cursor(step)
        self._announce_selection()

    def _announce_selection(self) -> None:
        """发一条「光标指着谁」：空屏或过滤后一条不剩时指的是 ``None``。"""
        self.post_message(self.SelectionChanged(self.selected_item))

    def flash(self, task_id: str | None) -> None:
        """让刚完成的那一行高亮；``None`` 收起高亮。

        只改「怎么画」，不改数据：高亮由 ``DidaApp`` 的定时器收起（ADR-0002 的补偿——
        完成不可逆，按下去必须看得见）。任务 id 存在栏位里，所以中途重画不会把高亮弄丢。
        """
        self._flashed = task_id
        self._redraw()

    def toggle_completed(self) -> None:
        """展开/收起已完成区；窗口里没有已完成的任务时什么都不做。"""
        if not self._completed.count:
            return
        self._completed_expanded = not self._completed_expanded
        self._redraw()

    def action_toggle_completed(self) -> None:
        """``c``：展开/收起已完成区。"""
        self.toggle_completed()

    @property
    def selected_task_id(self) -> str | None:
        """光标下的任务 id；没有任务时是 ``None``。"""
        return self._rows[self._cursor].task_id if self._rows else None

    @property
    def selected_title(self) -> str | None:
        """光标下那条任务的标题；没有任务时是 ``None``（删除确认要点名是哪一条）。"""
        return self._rows[self._cursor].title if self._rows else None

    @property
    def selected_item(self) -> TaskItem | None:
        """光标下那一行的成品（右栏画的就是它）；没有任务时是 ``None``。"""
        item = self._rows[self._cursor] if self._rows else None
        return item if isinstance(item, TaskItem) else None

    class SelectionChanged(Message):
        """光标换了一条任务（也包含「换成了没有」）：右栏据此跟着走。"""

        def __init__(self, item: TaskItem | None) -> None:
            self.item = item
            super().__init__()

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
                        # 行红不红跟着它所在的分区走（引擎的判断），栏位不自己比日期
                        overdue=group.kind is GroupKind.OVERDUE,
                    )
                )
                line_of_row.append(len(lines) - 1)
        if not line_of_row and self._completed.count:
            # 今天全做完了：空状态那句话不能因为底下的已完成区把内容撑起来，就不说了
            lines.append(Text(self._empty_text, style=EMPTY_STYLE))
        if self._completed.count:
            lines.append(completed_header(self._completed, expanded=self._completed_expanded))
            if self._completed_expanded:
                lines.extend(completed_line(item) for item in self._completed.items)
        self._line_of_row = tuple(line_of_row)
        self.set_body(pane_body(lines, empty=self._empty_text))


class DetailPane(Pane):
    """详情栏（右）：当前任务。

    内容归 t18（宽度分档、浮层、字段铺开）；t17 只放出**最小的一层**「指着谁」：
    :meth:`show` 接住光标下那一行，:attr:`task_id` 说清现在指着哪条。这一层存在的原因
    是验收标准 #4——过滤期间右栏绝不许指着一个已经被筛掉的任务，而「指着谁」必须有个
    能测的出口。光标换一条由 :class:`TaskPane.SelectionChanged` 通知（t18 说的
    「光标移动更新详情栏」就是这条线）。
    """

    BORDER_TITLE = "详情"
    EMPTY_TEXT = DETAIL_EMPTY_TEXT
    can_focus = False  # Tab 只在左栏与中栏之间切换

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._item: TaskItem | None = None

    @property
    def task_id(self) -> str | None:
        """右栏现在指着哪条任务；没有就是 ``None``（屏上是占位）。"""
        return None if self._item is None else self._item.task_id

    def show(self, item: TaskItem | None) -> None:
        """把右栏指到 ``item`` 上；``None`` = 没有任务可指（空屏或过滤后一条不剩）。"""
        self._item = item
        self._redraw()

    def _redraw(self) -> None:
        if self._item is None:
            self.set_body(Text(self.EMPTY_TEXT, style=EMPTY_STYLE))
            return
        # 字段铺开在 :func:`detail_body` 里，浮层形态（窄屏）复用同一份内容
        self.set_body(detail_body(self._item))


class StatusBar(Static):
    """状态栏：已同步时刻、待推送数量、当前逻辑日。"""


def format_status(status: SyncStatus) -> str:
    """状态栏文本。措辞按 GLOSSARY：逻辑日 / 待推送 / 已同步。"""
    logical_day = status.logical_day.strftime("%m-%d") if status.logical_day else "—"
    last_refresh = status.last_refresh_at.strftime("%H:%M") if status.last_refresh_at else "—"
    return f"已同步 {last_refresh} · 待推送 {status.pending_count} · 逻辑日 {logical_day}"


FOLDED_MARK = "▸"
UNFOLDED_MARK = "▾"
"""已完成区标题前的折叠标记：收起 / 展开一眼可分。"""

COMPLETED_STYLE = "dim strike"
"""已完成行：暗灰 + 删除线。"""

COMPLETED_HEADER_STYLE = "bold"


def completed_header(section: CompletedSection, *, expanded: bool) -> Text:
    """已完成区标题行：``▸ 已完成 N 项``；标记随展开状态变。"""
    mark = UNFOLDED_MARK if expanded else FOLDED_MARK
    return Text(f"{mark} 已完成 {section.count} 项", style=COMPLETED_HEADER_STYLE)


def completed_line(item: CompletedItem) -> Text:
    """已完成行：``标题  清单名  完成时间``，整行暗灰 + 删除线。

    行首留一个空位与任务行对齐；已完成的行不参与光标移动，所以永远不是光标行。
    """
    text = Text(style=COMPLETED_STYLE)
    text.append(f"{BLANK_MARK} {item.title}  ")
    text.append(item.list_name)
    text.append("  ")
    text.append(item.completed_text)
    return text


# ------------------------------------------------------------------ 改期输入框（t14）

RESCHEDULE_PLACEHOLDER = "改期：周五 14:00 / +3d"
"""改期输入框的占位文案：一眼看出这里写的是自然语言日期。"""


class RescheduleInput(Vertical):
    """改期输入框（``e``）：一行提示语 + 一行输入；``Enter`` 提交、``Esc`` 取消。

    这一层只做两件事：把用户写的**原文**原样交出去，把引擎回来的那句话显示出来。一个字都
    不解析——语法归 :mod:`dida.date_parser`（与新建共用同一套），「现在」与逻辑日归引擎。
    控件不认识任务、也不认识截止时间，只发一条 :class:`RescheduleInput.Submitted`。

    默认收起（``display: none``）：没按 ``e`` 时不占一行，也不进布局。
    """

    DEFAULT_CSS = """
    RescheduleInput {
        display: none;
        height: auto;
        border: round ansi_cyan;
        padding: 0 1;
    }
    RescheduleInput #reschedule-message {
        color: yellow;
        height: auto;
    }
    """
    # 焦点落在里面那个 Input 上。Esc 由这一层收：t17 的过滤与 t18 的浮层各有各的
    # Esc 语义，所以不往 app 上挂一个全局绑定去抢。``show=True`` 是为了让状态栏那一行
    # 在输入框开着的时候显示「取消」——Textual 的 Footer 只列焦点链上的绑定，
    # 输入框一拿到焦点，app 那些键位提示就都不显示了。
    can_focus = False
    BINDINGS = [Binding("escape", "cancel", "取消")]

    class Submitted(Message):
        """用户按了 ``Enter``：改哪条任务 + 他写的那一行原文。"""

        def __init__(self, task_id: str, text: str) -> None:
            self.task_id = task_id
            self.text = text
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._task_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="reschedule-message")
        yield Input(placeholder=RESCHEDULE_PLACEHOLDER)

    def open(self, task_id: str) -> None:
        """打开输入框并聚焦，瞄准 ``task_id`` 这条任务。

        每次打开都从空的开始：上一次写了一半的那一行留着，下一次按 ``e`` 就得先删掉它。
        """
        self._task_id = task_id
        self.show_message("")
        self.query_one(Input).value = ""
        self.display = True
        self.query_one(Input).focus()

    def close(self) -> None:
        """收起输入框（提交成功或取消）。"""
        self.display = False
        self._task_id = None

    @property
    def text(self) -> str:
        """输入框里的原文。"""
        return self.query_one(Input).value

    def show_message(self, text: str) -> None:
        """显示一句要告诉用户的话；空串 = 收起那一行。

        提交被拒绝时**不改**输入框里的原文：用户写的东西不能因为我们看不懂就吞掉。
        """
        message = self.query_one(Static)
        message.update(text)
        message.display = bool(text)

    def action_cancel(self) -> None:
        """``Esc``：取消这次改期，什么都不提交。"""
        self.close()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """内层输入框按了 ``Enter``：转成本控件自己的提交消息（原文照递）。"""
        event.stop()
        if self._task_id is not None:
            self.post_message(self.Submitted(self._task_id, event.value))


# ------------------------------------------------------------------ 新建输入框（t15）

QUICK_ADD_PLACEHOLDER = "新建：明天下午3点交季度报告 !高 #工作"
"""新建输入框的占位文案：把一行能装下的四样东西（标题、日期、优先级、标签）示范一遍。"""


class QuickAddInput(Vertical):
    """新建输入框（``a``）：一行提示语 + 一行输入；``Enter`` 提交、``Esc`` 取消。

    与 :class:`RescheduleInput` 是同一副骨架，差别只有位置与含义：这个在**三栏上面**
    （不瞄准任何一条任务），那个在下面（改光标下那一条）。

    这一层只做两件事：把用户写的**原文**原样交出去，把引擎回来的那句话显示出来。一个字都
    不解析——语法归 :mod:`dida.date_parser`（与改期共用同一套），「现在」与逻辑日归引擎。
    控件不认识任务、也不认识日期，只发一条 :class:`QuickAddInput.Submitted`。

    默认收起（``display: none``）：没按 ``a`` 时不占一行，也不进布局。
    """

    DEFAULT_CSS = """
    QuickAddInput {
        display: none;
        height: auto;
        border: round ansi_cyan;
        padding: 0 1;
    }
    QuickAddInput #quick-add-message {
        color: yellow;
        height: auto;
    }
    """
    # 焦点落在里面那个 Input 上。Esc 由这一层收：t17 的过滤与 t18 的浮层各有各的
    # Esc 语义，所以不往 app 上挂一个全局绑定去抢。``show=True`` 是为了让状态栏那一行
    # 在输入框开着的时候显示「取消」——Textual 的 Footer 只列焦点链上的绑定，
    # 输入框一拿到焦点，app 那些键位提示就都不显示了。
    can_focus = False
    BINDINGS = [Binding("escape", "cancel", "取消")]

    class Submitted(Message):
        """用户按了 ``Enter``：他写的那一行原文。"""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("", id="quick-add-message")
        yield Input(placeholder=QUICK_ADD_PLACEHOLDER)

    def open(self) -> None:
        """打开输入框并聚焦。

        每次打开都从空的开始：上一次写了一半的那一行留着，下一次按 ``a`` 就得先删掉它。
        """
        self.show_message("")
        self.query_one(Input).value = ""
        self.display = True
        self.query_one(Input).focus()

    def close(self) -> None:
        """收起输入框（提交成功或取消）。"""
        self.display = False

    @property
    def text(self) -> str:
        """输入框里的原文。"""
        return self.query_one(Input).value

    def show_message(self, text: str) -> None:
        """显示一句要告诉用户的话；空串 = 收起那一行。

        提交被拒绝时**不改**输入框里的原文：用户写的东西不能因为我们看不懂就吞掉。
        """
        message = self.query_one(Static)
        message.update(text)
        message.display = bool(text)

    def action_cancel(self) -> None:
        """``Esc``：取消这次新建，什么都不提交。"""
        self.close()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """内层输入框按了 ``Enter``：转成本控件自己的提交消息（原文照递）。"""
        event.stop()
        self.post_message(self.Submitted(event.value))



# ------------------------------------------------------------------ 模糊过滤（t17）

FILTER_PLACEHOLDER = "过滤：打几个字"
"""过滤框的占位文案：一眼看出这里写的是要筛的那几个字。"""


class FilterInput(Vertical):
    """模糊过滤框（``/``）：一行输入，边打边筛；``Esc`` 清空并收起。

    这一层只做两件事：把用户写的**原文**原样交出去（每次改动发一条
    :class:`FilterInput.Changed`），把 ``Esc`` 这一步交出去（:class:`FilterInput.Cancelled`）。
    它不认识任务，也不认识模糊匹配——那是 :func:`dida.sync.view.fuzzy_match`，由 ``DidaApp``
    拿着引擎那份纯函数去筛。

    默认收起（``display: none``）：没按 ``/`` 时不占一行，也不进布局。``Esc`` 由这一层收：
    过滤框与 t14 的改期框、t18 的浮层各有各的 Esc 语义，不往 app 上挂一个全局绑定去抢。
    """

    DEFAULT_CSS = """
    FilterInput {
        display: none;
        height: auto;
        border: round ansi_cyan;
        padding: 0 1;
    }
    """
    can_focus = False
    BINDINGS = [Binding("escape", "cancel", "取消")]

    class Changed(Message):
        """框里的字变了：原文照递（空串 = 清空过滤）。"""

        def __init__(self, query: str) -> None:
            self.query = query
            super().__init__()

    class Cancelled(Message):
        """用户按了 ``Esc``：清空过滤、恢复完整列表。"""

    def compose(self) -> ComposeResult:
        yield Input(placeholder=FILTER_PLACEHOLDER)

    def open(self) -> None:
        """打开过滤框并聚焦。每次打开都从空的开始（上一次筛的字不留着）。"""
        self.query_one(Input).value = ""
        self.display = True
        self.query_one(Input).focus()

    def close(self) -> None:
        """收起过滤框（``Esc``）；框里的字一并清掉，下次打开是干净的。"""
        self.query_one(Input).value = ""
        self.display = False

    @property
    def query(self) -> str:
        """框里的原文。"""
        return self.query_one(Input).value

    def action_cancel(self) -> None:
        """``Esc``：清空过滤、收起过滤框（恢复完整列表是 app 的事）。"""
        self.close()
        self.post_message(self.Cancelled())

    def on_input_changed(self, event: Input.Changed) -> None:
        """内层输入框每改一个字 → 自己的 ``Changed``：边打边筛。"""
        event.stop()
        self.post_message(self.Changed(event.value))

# ------------------------------------------------------------------ 删除确认（t16）


class ConfirmScreen(ModalScreen[bool]):
    """一句提示 + 一个 Yes/No：``y`` 确认、``n`` 与 ``Esc`` 取消（工单 #16）。

    这一层只负责**问**：提示语原文进来、按键结果 ``dismiss(True/False)`` 出去。删除动作
    归 :meth:`dida.tui.app.DidaApp._finish_delete`，提示语归
    :func:`dida.tui.app.delete_prompt` 拼——控件不认识任务，也不认识引擎，所以换一个
    「确认」场景时不必动它。

    为什么是一次浮层：删除是**这一屏唯一不可挽回的动作**。滴答清单的 Open API 里没有
    undelete、没有回收站、没有「已删除」列表（``api-contracts.md`` 通篇没有这一类端点），
    所以这一次确认就是全部的防线——按错就是永久少一条任务，多一次按键是这笔账里最便宜的
    那一头。``Esc`` 也走取消：默认答案永远是「没删」。
    """

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen #confirm-box {
        width: auto;
        max-width: 80%;
        height: auto;
        border: round ansi_yellow;
        padding: 1 2;
        background: $surface;
    }
    ConfirmScreen #confirm-prompt {
        width: auto;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "确认删除"),
        Binding("n", "cancel", "取消"),
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt
        """提示语原文，原样显示。"""

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.prompt, id="confirm-prompt")

    def action_confirm(self) -> None:
        """``y``：确认，浮层关掉并把 ``True`` 交回调用方。"""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """``n`` / ``Esc``：取消，什么都不做（``False``）。"""
        self.dismiss(False)


# ------------------------------------------------------------------ 子任务（t20）

SUBTASK_HEADER = "子任务"
"""右栏子任务列表的标题；后面跟着 ``已完成/总数``。"""

SUBTASK_DONE_MARK = "☑"
SUBTASK_TODO_MARK = "☐"
"""子任务的两种状态标记：勾上 / 没勾。与已完成区的 ``▸``/``▾`` 一样，一眼可分。"""

SUBTASK_DONE_STYLE = "dim"
"""勾上的子任务整行暗灰。

**不带删除线**：删除线是「已完成任务」的读法，而子任务的勾选是可逆的（再按一次 ``t``
就退回来），两者必须一眼分得开。
"""


class SubtaskPane(Widget):
    """右栏的子任务列表（工单 #20）：一行一个，``☑``/``☐`` 是完成状态。

    它是**自成一体的一个控件**，挂在详情栏里（``DetailPane`` 的下面），所以详情栏怎么
    排版、窄屏怎么降级都不必动它：详情栏被收起时它跟着一起收起来。

    键盘：``s`` 把焦点移进来（``DidaApp`` 的绑定），``j``/``k``/``↑``/``↓`` 移光标，
    ``t`` 勾选光标下那一个，``Esc`` 回任务列。光标行只在**有焦点**时显示：没进来的时候
    这里是一份只读的列表，与 mockup 里「详情只读」的样子一致。

    它只做两件事：把光标下那一个子任务的 **(任务 id, 子任务 id)** 交出去（
    :class:`SubtaskPane.Toggled`），把 ``Esc`` 交出去（:class:`SubtaskPane.Dismissed`）。
    重读、合并、写回、退避重试全在引擎里——控件不认识 ``items`` 数组，也不认识状态取值。
    """

    can_focus = True
    BINDINGS = [
        Binding("j", "cursor_down", "下移", show=False),
        Binding("down", "cursor_down", "下移", show=False),
        Binding("k", "cursor_up", "上移", show=False),
        Binding("up", "cursor_up", "上移", show=False),
        Binding("t", "toggle", "勾选"),
        Binding("escape", "dismiss", "返回任务列"),
    ]
    DEFAULT_CSS = """
    SubtaskPane {
        height: auto;
        width: 1fr;
    }
    """

    class Toggled(Message):
        """用户按了 ``t``：勾选哪条任务的哪个子任务。"""

        def __init__(self, task_id: str, subtask_id: str) -> None:
            self.task_id = task_id
            self.subtask_id = subtask_id
            super().__init__()

    class Dismissed(Message):
        """用户按了 ``Esc``：焦点回任务列（右栏照旧只读）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._task_id: str | None = None
        self._items: tuple[SubtaskItem, ...] = ()
        self._cursor = 0
        self._armed = False

    def allow_focus(self) -> bool:
        """只有被 ``s`` 叫到时才可聚焦（工单 #20）。

        键位表里 Tab 只在清单栏与任务列之间切换（spec 的用户故事 #19）：多一个可聚焦控件
        就把那个环变成三个，Tab 会从任务列掉进右栏，而用户再也回不到清单栏。所以默认不在
        焦点链里（:meth:`allow_focus` 是 Textual 给的钩子，焦点链正是按它算的），``s``
        进来之前先 :meth:`arm`。
        """
        return self._armed and super().allow_focus()

    def arm(self) -> None:
        """允许被聚焦：``s`` 进来之前调一次。"""
        self._armed = True

    def disarm(self) -> None:
        """收回可聚焦性：焦点走了之后 Tab 的环里不能再有它。"""
        self._armed = False

    def show(self, task_id: str | None, items: Sequence[SubtaskItem]) -> None:
        """接住某条任务的子任务。

        换一条任务时光标回到第一行；**同一条任务**重画（勾选之后那一次）时保留光标——
        用户正在这一条上连着勾几个，勾完一个就跳回第一行是没法用的。
        """
        if task_id != self._task_id:
            self._cursor = 0
        self._task_id = task_id
        self._items = tuple(items)
        if self._items:
            self._cursor = min(self._cursor, len(self._items) - 1)
        # layout=True：行数变了，高度得跟着重算。只 refresh() 的话块会按旧高度被裁掉
        # ——屏幕上是「子任务 1/3」下面一片空白，而数据其实都在。
        self.refresh(layout=True)

    @property
    def count(self) -> int:
        """这条任务有几个子任务。"""
        return len(self._items)

    @property
    def selected(self) -> tuple[str, str] | None:
        """光标下那一个子任务：``(任务 id, 子任务 id)``；没有就是 ``None``。"""
        if self._task_id is None or not self._items:
            return None
        return (self._task_id, self._items[self._cursor].subtask_id)

    def action_cursor_down(self) -> None:
        """下移一行（``j`` / ``↓``）。"""
        self._move(1)

    def action_cursor_up(self) -> None:
        """上移一行（``k`` / ``↑``）。"""
        self._move(-1)

    def _move(self, step: int) -> None:
        if not self._items:
            return
        self._cursor = min(max(self._cursor + step, 0), len(self._items) - 1)
        self.refresh()

    def action_toggle(self) -> None:
        """``t``：把光标下那一个子任务交给外面（勾选是引擎的事）。"""
        picked = self.selected
        if picked is None:  # 没有子任务时按键什么都不做，不是错误
            return
        self.post_message(self.Toggled(*picked))

    def action_dismiss(self) -> None:
        """``Esc``：焦点回任务列。"""
        self.post_message(self.Dismissed())

    def on_focus(self) -> None:
        """焦点进来：光标行显出来（没焦点时这里是一份只读列表）。"""
        self.refresh(layout=True)

    def on_blur(self) -> None:
        """焦点走了：光标行收起来，可聚焦性一并收回（Tab 的环里不能留着它）。"""
        self.disarm()
        self.refresh(layout=True)

    def render(self) -> Text:
        """整块文本：标题 ``子任务 已完成/总数`` + 每个子任务一行。

        没有子任务（或没有选中任务）时渲染空文本：详情栏里不占一行，也不说废话。
        """
        if not self._items:
            return Text("")
        body = Text()
        body.append(f"{SUBTASK_HEADER} {self._done_count}/{len(self._items)}", style="bold")
        for index, row in enumerate(self._items):
            body.append("\n")
            body.append_text(self._line(row, selected=index == self._cursor))
        return body

    @property
    def _done_count(self) -> int:
        return sum(1 for row in self._items if row.completed)

    def _line(self, row: SubtaskItem, *, selected: bool) -> Text:
        """一行子任务：``❯ ☑ 标题  今天 18:00``。

        光标标记只在有焦点时出现；日期只有真的有日期时才写出来——**没有日期不是不显示
        这一行的理由**（工单 #20 的验收标准 #1）。
        """
        focused = selected and self.has_focus
        text = Text()
        text.append(f"{CURSOR_MARK if focused else BLANK_MARK} ")
        text.append(
            SUBTASK_DONE_MARK if row.completed else SUBTASK_TODO_MARK,
            style=SUBTASK_DONE_STYLE if row.completed else "",
        )
        text.append(f" {row.title}", style=SUBTASK_DONE_STYLE if row.completed else "")
        if row.due_text != NO_DUE_TEXT:
            text.append(f"  {row.due_text}", style=EMPTY_STYLE)
        if focused:
            text.stylize("reverse")
        return text
# ------------------------------------------------------------------ 窄屏浮层（t18）


class OverlayBox(ModalScreen[None]):
    """一个居中的浮层盒子：边框标题 + 一块正文（工单 #18 的浮层共用）。

    正文由调用方拼好（:func:`detail_body` / :func:`lists_body` / :func:`key_help_body`）
    原样交给这里，所以浮层与它对应的常驻栏位画的是同一份内容——窄屏不是另一个界面，
    只是同一块内容换了地方摆。控件不认识任务、不认识引擎，换一个场景不必动它
    （与 :class:`ConfirmScreen` 同一条口径）。

    ``Esc`` 与 ``Enter`` 都收起来：``Enter`` 就是开合右栏详情的那一个键（验收标准 #4），
    在浮层里再按一次当然是「收起」。浮层是模态的，所以它开着的时候 j/k 到不了任务列。
    """

    TITLE = ""
    """盒子边框上的标题。"""

    DEFAULT_CSS = """
    OverlayBox {
        align: center middle;
    }
    OverlayBox .overlay-box {
        width: 80%;
        max-width: 60;
        height: auto;
        max-height: 80%;
        border: round ansi_cyan;
        padding: 1 2;
        background: $surface;
    }
    OverlayBox .overlay-body {
        width: auto;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "关闭", show=False),
        Binding("enter", "close", "关闭"),
    ]

    def __init__(self, body: Text) -> None:
        super().__init__()
        self.body = body
        """浮层正文，原样显示。"""

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay-box") as box:
            box.border_title = self.TITLE
            yield Static(self.body, classes="overlay-body")

    def action_close(self) -> None:
        """``Esc`` / ``Enter``：收起浮层，回到下面那一屏。"""
        self.dismiss(None)


class DetailScreen(OverlayBox):
    """窄屏下右栏的浮层形态：``Enter`` 把当前任务的详情弹出来（工单 #18）。"""

    TITLE = "详情"


class ListsScreen(OverlayBox):
    """窄屏下左栏的浮层形态：``l`` 把清单弹出来（工单 #18，验收标准 #3）。

    窄档（<80 列）左栏不在屏上，清单只能从这里看——「清单通过浮层切换」就是这一条。
    """
    TITLE = "清单"


KEY_HELP: tuple[tuple[str, str], ...] = (
    ("j / k", "上下移动光标"),
    ("Enter", "开合右栏详情（窄屏：浮层）"),
    ("q", "退出"),
    ("x / Space", "完成这条"),
    ("g", "顺延一天"),
    ("G", "顺延一周"),
    ("e", "改期"),
    ("a", "新建"),
    ("d", "删除"),
    ("p", "优先级推进一档"),
    ("/", "过滤"),
    ("c", "已完成区展开/收起"),
    ("o", "在浏览器打开"),
    ("r", "同步（刷新 + 推送 + 已完成流）"),
    ("l", "清单浮层"),
    ("?", "这份帮助"),
    ("Esc", "关闭浮层"),
    # 评审修 A3：下面几行是「已经能按、帮助里却找不到」的键。``↑``/``↓`` 与 ``j``/``k``
    # 是同一件事的两种写法，``Tab`` 是左栏与中栏之间的焦点环（spec 用户故事 #19），
    # ``s``/``t`` 是子任务那两步（t20）。
    ("↑ / ↓", "上下移动光标（同 j / k）"),
    ("Tab", "切换左栏 / 中栏的焦点"),
    ("s", "子任务（焦点移进右栏）"),
    ("t", "勾选子任务"),
)
"""键位表：一个键一行，第二列是它干什么（工单 #18，验收标准 #6）。

**新绑一个键就要往这里加一行。** 这张表是这些键唯一被写下来的地方：footer 只显示得下
头几个，而且它只显示 :class:`~dida.tui.app.DidaApp` 自己绑的键——``c`` 是任务列绑的
（:class:`~dida.tui.panes.TaskPane`），footer 根本不提它；``j``/``k`` 是栏位绑的，
footer 也不提。漏一行的后果不是「少个说明」，是那个功能没人找得到。

这条规矩现在有测试守着：``tests/test_key_help.py`` 拿 ``DidaApp`` / ``TaskPane`` /
``SubtaskPane`` 的绑定逐键对这张表，少一行就点名那个键。同一个动作绑两个键（``x`` /
``Space``）写成一格，用 `` / `` 分隔——与 ``j`` / ``k`` 同一写法，测试就是按这个分隔符
把一格拆成两个键的（所以裸的 ``/`` 那个键不会被拆坏）。
"""


def key_help_body() -> Text:
    """键位帮助浮层的内容（工单 #18）：键一列、说明一列，键加粗。"""
    width = max(len(key) for key, _ in KEY_HELP)
    body = Text()
    for index, (key, what) in enumerate(KEY_HELP):
        if index:
            body.append("\n")
        body.append(key.ljust(width), style="bold")
        body.append(f"  {what}")
    return body


class HelpScreen(OverlayBox):
    """键位帮助浮层：``?`` 打开、``Esc``（或 ``Enter``）关闭（工单 #18）。

    表在 :data:`KEY_HELP` 里，这里只负责摆出来——所以「帮助里少了某个键」永远是一个
    数据问题（表里没有那一行），不是一个布局问题。
    """

    TITLE = "键位"
