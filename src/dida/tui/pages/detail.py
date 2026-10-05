"""层三：任务详细页——**这一票只留接缝，#43 接手扩展**。

spec 的三层状态机里，任务列表页 ``enter`` 进这一页、``esc`` 退回任务列表页。字段列表
（标题、描述、备注、所属清单、截止时间、优先级、标签、只读的子任务/提醒/重复）与「按
``enter`` 进某个字段编辑」是 #43 的工单；这里先把这一层立起来并如实显示**已经读得到的**
那些字段（``Engine.task_detail`` 在 #33 就返回了它们的成品读法），好让用户按得进来、
退得回去，也让 #43 有一个自己的文件可改。

**两个字段不许标反**（``GLOSSARY.md``：描述 = ``content``、备注 = ``desc``；v1 标反了，
这份 spec 纠正它）：这一页按术语表写，``TaskDetail.content`` 画在「描述」那一行。

空的那一行不画（v1 的结论，工单 #20）：标签、描述、备注、子任务、重复、提醒没有内容时
整个不出现，而不是留一个空标签行——判断依据是引擎给的空串/空元组，不是这一层去问。
"""

from __future__ import annotations

from rich.text import Text
from textual.message import Message

from dida.sync.engine import TaskDetail
from dida.tui import messages, theme
from dida.tui.keys import LAYER_DETAIL, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = ["DetailPage", "field_line", "field_lines"]

SUBTASK_DONE_MARK = theme.SUBTASK_DONE_MARK
SUBTASK_TODO_MARK = theme.SUBTASK_TODO_MARK
"""子任务的两种状态标记（只读显示：v2 不在客户端里勾子任务）。"""


def field_line(label: str, value: str, *, style: str = "") -> Text:
    """详情页的一行字段：``标签  值``。"""
    text = Text()
    text.append(f"{label}  ", style=EMPTY_STYLE)
    text.append(value, style=style)
    return text


def field_lines(detail: TaskDetail) -> tuple[Text, ...]:
    """详情页的字段行，按 spec 的字段顺序；空的那一行不画。"""
    lines = [
        field_line("清单", detail.list_name),
        field_line("截止", detail.due_text, style=EMPTY_STYLE if detail.due is None else ""),
        field_line("优先级", detail.priority_mark),
    ]
    if detail.tags_text:
        lines.append(field_line("标签", detail.tags_text))
    if detail.content:
        lines.append(field_line("描述", detail.content))
    if detail.desc:
        lines.append(field_line("备注", detail.desc))
    if detail.subtasks:
        # 子任务只读（spec 的 Out of Scope：能看不能勾），所以这里没有可停光标的行。
        lines.append(
            field_line(
                "子任务",
                "  ".join(
                    f"{SUBTASK_DONE_MARK if row.completed else SUBTASK_TODO_MARK} {row.title}"
                    for row in detail.subtasks
                ),
            )
        )
    if detail.repeat_flag:
        lines.append(field_line("重复", detail.repeat_flag))
    if detail.reminders:
        lines.append(field_line("提醒", "  ".join(detail.reminders)))
    return tuple(lines)


class DetailPage(CursorPage):
    """任务详细页：一条任务的字段（这一票只读；编辑归 #43）。

    .. note::

       **折行的那一半在这里**（ADR-0007 的「截断与折行按页分工」）：这一页要能读完整的
       描述与备注，所以 #43 接手时把 :attr:`CursorPage.CLIP_ROWS` 关掉。今天这一页没有
       可停光标的行（所有 ``Row`` 都是 ``id=None``），所以折行还不会破坏光标算术；
       #43 开字段光标时，光标与滚动要一起换成**屏幕行偏移表**——一个字段不再等于一屏一行。
    """

    LAYER = LAYER_DETAIL
    BINDINGS = bindings_for(LAYER)
    EMPTY_TEXT = messages.EMPTY_DETAIL_MESSAGE

    class Back(Message):
        """用户按了 ``esc``：退回任务列表页（光标还原到进来的那条任务）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._task_id: str | None = None

    @property
    def task_id(self) -> str | None:
        """这一页正在说的是哪条任务（``o`` 要知道）。"""
        return self._task_id

    def show_detail(self, detail: TaskDetail | None) -> None:
        """铺开一条任务的字段；``None`` = 它已经不在本地缓存里了。"""
        self._task_id = None if detail is None else detail.task_id
        if detail is None:
            self.set_rows((empty_row(self.EMPTY_TEXT),))
            return
        # 标题下面一条通栏细线，然后才是字段（ADR-0007 的「分区标题下面一条线」）。
        rows = [Row(id=None, text=theme.styled(detail.title, theme.HEADING)), rule_row()]
        rows += [Row(id=None, text=line) for line in field_lines(detail)]
        self.set_rows(rows)

    def action_back(self) -> None:
        """``esc``：退回任务列表页。"""
        self.post_message(self.Back())
