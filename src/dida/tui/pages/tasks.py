"""层二：任务列表页——当前清单（或视图）里的全部任务。

行的样子归这一页：优先级标记、标题、人类可读的截止时间、标签、重复与提醒标记；已完成的
划掉、沉在列表最底（工单 #37）。**判断不在这一层**：哪条算逾期、截止时间读作什么、按什么
顺序排，全是引擎给的成品（``Engine.tasks_in``），这一页只把成品画成一行行文字。

## 一行 = 一屏一行

列表页的行**截断不折行**（ADR-0007 的「截断与折行按页分工」）：宽度不够时按顺序丢东西，
而不是折行——折行会让光标字形与标题分家、把「第几个 Row」与「第几屏行」算错。

丢弃顺序（用户定的，见工单 #37 的评论）：**标签 → 重复/提醒标记 → 截止时间 → 所属清单名
→ 最后才截标题**。标题是内容，其余是注解：终端只有 30 列时，用户最需要知道的是「这条是
什么」，不是「它什么时候到期」。注解块**贴着行的右边缘**，所以截止时间自成一列。

宽度按**格**算（``rich.cells`` / :mod:`dida.tui.theme` 的 ``clip``），不按字符数：用户
``LANG=zh_CN.UTF-8``，一个汉字 2 格。

## 清单名：清单里不重复，视图里必须显示

清单视图里每一行都写着同一个清单名——重复一百遍是噪音；视图不是容器，行里的清单名是
真信息（工单 #37 的验收标准）。哪一种是哪一种由读模型给（``TaskList.shows_list_name``），
这一页不自己推。
"""

from __future__ import annotations

from typing import Sequence

from rich.cells import cell_len
from rich.text import Text
from textual.message import Message

from dida.sync.engine import CompletedItem, TaskItem, TaskList
from dida.tui import messages, theme
from dida.tui.keys import LAYER_TASKS, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, heading_row, rule_row

__all__ = [
    "ANNOTATION_GAP",
    "COMPLETED_STYLE",
    "DONE_MARK",
    "TITLE_GAP",
    "TasksPage",
    "completed_line",
    "task_line",
]

DONE_MARK = theme.DONE_MARK
"""已完成那一行的前缀字符（它站在优先级标记那一列上）。"""

COMPLETED_STYLE = theme.DONE
"""已完成的行：暗灰 + 删除线（用户故事 54）。

删除线是「这条已经做完了」的读法，而它**还在列表里**——用户才有机会对它的取消完成
（那是 #38 的 ``space``，本页先把它们摆出来并让光标越过：已完成的行不参与光标移动）。
"""

TITLE_GAP = 2
"""标题与注解之间至少留几格：贴在一起读不出「注解属于这一行」。"""

ANNOTATION_GAP = "  "
"""注解之间那两格（清单名 / 标签 / 标记 / 截止时间）。"""

_DROP_ORDER = ("tags", "marks", "due", "list")
"""宽度不够时依次丢掉的注解身份（用户定的顺序）。

``list`` 是**视图里**才有的所属清单名：它是验收标准要求「必须显示」的东西，所以它排在
截止时间后面才丢——注解里它留得最久。标题不在这个序列里：它是最后才截的那一样。
"""


def _marks(item: TaskItem) -> str:
    """重复与提醒的记号（都没有就是空串）。"""
    marks = []
    if item.repeat_flag:
        marks.append(theme.REPEAT_MARK)
    if item.reminders:
        marks.append(theme.REMINDER_MARK)
    return " ".join(marks)


def _annotations(item: TaskItem, *, show_list_name: bool) -> list[tuple[str, str]]:
    """一行的注解，按**显示顺序**：所属清单名 → 标签 → 重复/提醒标记 → 截止时间。

    显示顺序与 :data:`_DROP_ORDER` 不是同一个顺序，这是有意的：显示上截止时间在最右
    （它因此是一列），而丢弃时它排在所属清单名前面（视图里「这条在哪个清单」比「什么时候
    到期」更不该消失——清单名是验收标准里要求必须显示的那一样）。
    """
    parts: list[tuple[str, str]] = []
    if show_list_name and item.list_name:
        parts.append(("list", item.list_name))
    if item.tags_text:
        parts.append(("tags", item.tags_text))
    marks = _marks(item)
    if marks:
        parts.append(("marks", marks))
    parts.append(("due", item.due_text))
    return parts


def _annotation_width(kept: Sequence[str]) -> int:
    """注解块占多少格（含它们之间那两格）。"""
    if not kept:
        return 0
    return sum(cell_len(text) for text in kept) + cell_len(ANNOTATION_GAP) * (len(kept) - 1)


def _fits(prefix: str, title: str, kept: Sequence[str], width: int) -> bool:
    """这一行放得下吗（``width <= 0`` = 宽度还不知道，先全画）。"""
    if width <= 0:
        return True
    room = cell_len(prefix) + cell_len(title)
    if kept:
        room += TITLE_GAP + _annotation_width(kept)
    return room <= width


def _assemble(prefix: str, title: str, kept: Sequence[str], *, width: int, style: str) -> Text:
    """把「前缀 + 标题」与右对齐的注解拼成一行（``kept`` 已经定好了留哪些）。"""
    line = Text()
    if not kept:
        # 注解一个都不留：这一行只剩前缀与标题，放不下就按格截断（省略号）。
        text = prefix + title
        line.append(text if width <= 0 else theme.clip(text, width), style=style)
        return line
    left = prefix + title
    gap = TITLE_GAP if width <= 0 else max(TITLE_GAP, width - cell_len(left) - _annotation_width(kept))
    line.append(left + " " * gap, style=style)
    line.append(ANNOTATION_GAP.join(kept), style=style or EMPTY_STYLE)
    return line


def _keep_what_fits(prefix: str, title: str, annotations: list[tuple[str, str]], width: int) -> list[str]:
    """按 :data:`_DROP_ORDER` 丢到放得下为止，返回留下来的注解文本。"""
    dropped: set[str] = set()
    kept = [text for _, text in annotations]
    for kind in _DROP_ORDER:
        if _fits(prefix, title, kept, width):
            break
        dropped.add(kind)
        kept = [text for name, text in annotations if name not in dropped]
    return kept if _fits(prefix, title, kept, width) else []


def task_line(item: TaskItem, *, width: int, show_list_name: bool = False) -> Text:
    """一条任务的行（``width`` 是这一行能用的格数，不含行首的光标空档）。

    注解块贴着右边缘：截止时间因此在每一行都落在同一个位置上（一列），而不是跟着标题的
    长短漂。放不下时按 :data:`_DROP_ORDER` 丢，最后才截标题。
    """
    prefix = f"{item.priority_mark} "
    annotations = _annotations(item, show_list_name=show_list_name)
    kept = _keep_what_fits(prefix, item.title, annotations, width)
    # 逾期整行标红：颜色只有一个出处（theme.OVERDUE = 槽 1），而且它进的是 **span**
    # （``Text("x", style="red")`` 会被 Textual 当成 CSS 具名色解析成真彩色）。
    style = theme.OVERDUE if item.overdue else ""
    return _assemble(prefix, item.title, kept, width=width, style=style)


def completed_line(item: CompletedItem, *, width: int = 0) -> Text:
    """已完成的一条：划掉、沉在列表最底，右边写它是什么时候完成的。

    前缀是 ``☑`` 而不是优先级标记——它已经做完了，优先级不再有意义。完成的时刻与截止
    时间一样是注解：宽度不够时先丢它，标题留着（与未完成的行同一条规矩）。
    """
    prefix = f"{DONE_MARK} "
    annotations = [("due", item.completed_text)] if item.completed_text else []
    kept = _keep_what_fits(prefix, item.title, annotations, width)
    return _assemble(prefix, item.title, kept, width=width, style=COMPLETED_STYLE)


class TasksPage(CursorPage):
    """任务列表页：某个清单或视图里的全部任务。"""

    LAYER = LAYER_TASKS
    BINDINGS = bindings_for(LAYER)
    EMPTY_TEXT = messages.EMPTY_TASKS_MESSAGE

    class Entered(Message):
        """用户按了 ``enter``：进这条任务的详细页。"""

        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            super().__init__()

    class Back(Message):
        """用户按了 ``esc``：退回清单列表页（光标还原到进来的那一行）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._heading = ""
        """屏幕上那条标题里写的容器名（视图名或清单名）。

        ⚠ 名字**不能**是 ``_name``：``DOMNode._name`` 是 Textual 自己的属性
        （``textual/dom.py:196``），盖掉它之后 ``.name`` 与 ``__rich_repr__`` 读到的就是
        这一页的抬头——与 ``_animate`` 那个坑同一个形状。公开的那一份叫
        :attr:`container_name`。
        """
        self._container_id: str | None = None
        """当前铺开的是哪个容器（清单 / 视图的 id）：换了一个就把光标放回第一行。"""
        self._task_list: TaskList | None = None
        """最近一次铺开的那份数据：宽度变了要按新的宽度重画同一批行（见 :meth:`on_resize`）。"""
        self._show_list_name = False
        """这一页要不要在行里写所属清单名（视图要、清单不要，由读模型的 ``shows_list_name`` 定）。"""

    @property
    def container_name(self) -> str:
        """屏幕上这条标题里写的容器名（视图名或清单名）。"""
        return self._heading

    def show_tasks(self, task_list: TaskList, *, name: str) -> None:
        """铺开一个容器的任务：标题 + 未完成任务 + 已完成的那几条。

        换了一个容器就把光标放回第一条；**同一个**容器再进来（``esc`` 回去又进来）则按行 id
        认回原来那条任务——那是「回到我刚才看的地方」，是好事。
        """
        same_container = task_list.container_id == self._container_id
        self._container_id = task_list.container_id
        self._heading = name
        self._task_list = task_list
        # 视图不是容器：行里必须写出这条任务属于哪个清单；清单里则不重复（工单 #37）。
        self._show_list_name = task_list.shows_list_name
        self.set_rows(self._build(), keep_cursor=same_container)

    def _build(self) -> tuple[Row, ...]:
        """按当前宽度铺一页的行（抬头两条暗线夹着 + 未完成 + 已完成 + 空态）。"""
        task_list = self._task_list
        # 抬头是「哪一屏 + 两条暗线夹着」：顶上一条（顶栏下面的分隔）、抬头本身、
        # 抬头下面一条（分区标题的细线）。分隔与留白承担层级，不靠框线。
        rows: list[Row] = [
            rule_row(),
            heading_row(theme.styled(self._heading, theme.HEADING)),
            rule_row(),
        ]
        if task_list is None:
            return tuple(rows)
        width = self._line_width()
        rows += [
            Row(
                id=item.task_id,
                text=task_line(item, width=width, show_list_name=self._show_list_name),
            )
            for item in task_list.items
        ]
        rows += [
            Row(id=None, text=completed_line(item, width=width))
            for item in task_list.completed.items
        ]
        if len(rows) == 3:
            # 空清单要说一句明确的话，而不是只留一个标题（用户故事 53）。
            rows.append(empty_row(self.EMPTY_TEXT))
        return tuple(rows)

    def _line_width(self) -> int:
        """一条**任务行**能用的格数：正文宽度减去行首那两格（光标字形 + 一个空格）。

        不能改写基类的 :meth:`CursorPage._row_width`：通栏细线按那个宽度铺满整幅页面
        （细线不参与行首空档），跟着减两格的话每一页的细线都会短两格。

        还没排版（宽度是 0）时给 0——:func:`task_line` 把 0 读作「宽度还不知道，先全画」，
        排完版 :meth:`on_resize` 会按真实宽度重画。
        """
        return max(0, self._row_width() - cell_len(f"{theme.CURSOR_MARK} "))

    def on_resize(self) -> None:
        """宽度变了：行是**按格**排的，注解块贴着右边缘，所以得按新宽度重排一遍。

        只重排不换数据：光标按行 id 认回原来那条任务（用户故事 21/57），不因为窗口变窄
        就把人踢回第一行。
        """
        if self._task_list is not None:
            self.set_rows(self._build(), keep_cursor=True)
            return
        super().on_resize()

    def action_enter(self) -> None:
        """``enter``：进光标下那条任务的详细页。"""
        if self.selected_id is not None:
            self.post_message(self.Entered(self.selected_id))

    def action_back(self) -> None:
        """``esc``：退回清单列表页。"""
        self.post_message(self.Back())
