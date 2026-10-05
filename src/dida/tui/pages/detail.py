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

from dataclasses import dataclass

from rich.text import Text
from textual.message import Message

from rich._wrap import divide_line

from dida.sync.engine import TaskDetail
from dida.tui import messages, theme
from dida.tui.keys import LAYER_DETAIL, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = ["DetailPage", "Field", "field_line", "fields_of", "read_only_rows"]

SUBTASK_DONE_MARK = theme.SUBTASK_DONE_MARK
SUBTASK_TODO_MARK = theme.SUBTASK_TODO_MARK
"""子任务的两种状态标记（只读显示：v2 不在客户端里勾子任务）。"""


@dataclass(frozen=True)
class Field:
    """字段列表里的一行：它能被编辑成什么（``wire``）与怎么画（``label`` + ``display``）。

    ``key`` 是这一行的身份，也是光标认回来的凭据（后台刷新之后光标要留在同一个字段上）。
    ``wire`` 是写回服务端的字段名：三个自由文本字段各有自己的一个（``title`` /
    ``content`` / ``desc``——**描述 = content、备注 = desc**，GLOSSARY），``None`` 表示
    「这一票还不改它」：#44 接截止时间、#45 接清单/优先级/标签那三个挑选型字段。
    **它们扩展这一张表，不重写这一页**：给 ``wire`` 填上值、把 ``action_enter`` 的分派
    接过去就够了（光标、折行、保存行都不必再动）。
    """

    key: str
    label: str
    """屏幕上那个标签（标题 / 描述 / 备注 / 清单 / 截止 / 优先级 / 标签）。"""

    value: str = ""
    """这个字段的**原文**（进编辑器的初始值）；空值是空串，占位符只是画出来的。"""

    display: str = ""
    """画在标签右边的那一串（一般是 ``value``；空值给一句 ``（空）``）。"""

    style: str = ""
    wire: str | None = None
    multiline: bool = False
    """编辑它时用多行输入（描述与备注是多行文本，标题是单行）。"""


def field_line(label: str, value: str, *, style: str = "") -> Text:
    """详情页的一行字段：``标签  值``。

    值可以折行（ADR-0007 的「截断与折行按页分工」：详细页换行），所以这里只拼**一个 Text**，
    行数由 ``divide_line`` 现算（:meth:`DetailPage._row_lines`）。
    """
    text = Text()
    text.append(f"{label}  ", style=EMPTY_STYLE)
    text.append(value, style=style)
    return text


def _free_text(
    key: str, label: str, value: str, *, wire: str, multiline: bool
) -> Field:
    """一个自由文本字段：空的时候画一句占位符，但它**照样在字段列表里**（要能进去写）。"""
    return Field(
        key=key,
        label=label,
        value=value,
        display=value or messages.EMPTY_FIELD_TEXT,
        style="" if value else EMPTY_STYLE,
        wire=wire,
        multiline=multiline,
    )


def fields_of(detail: TaskDetail) -> tuple[Field, ...]:
    """详情页的字段列表，按 spec 的顺序（标题、描述、备注、所属清单、截止时间、优先级、标签）。

    后四个今天只读显示：改它们要打**另外三个形状**的端点（改期 / 搬运 / 优先级），
    归 #44（截止）与 #45（清单、优先级、标签）。它们的顺序、标签与光标位置就在这里定下来，
    那两张工单只把 ``wire`` 与编辑器接上。
    """
    return (
        _free_text("title", "标题", detail.title, wire="title", multiline=False),
        _free_text("content", "描述", detail.content, wire="content", multiline=True),
        _free_text("desc", "备注", detail.desc, wire="desc", multiline=True),
        Field("list", "清单", value=detail.list_name, display=detail.list_name),
        Field(
            "due",
            "截止",
            value=theme.NO_VALUE if detail.due is None else detail.due_text,
            display=theme.NO_VALUE if detail.due is None else detail.due_text,
            style="" if detail.due is not None else EMPTY_STYLE,
        ),
        Field(
            "priority",
            "优先级",
            value=messages.priority_name(detail.priority),
            display=messages.priority_name(detail.priority),
        ),
        Field("tags", "标签", value=detail.tags_text, display=detail.tags_text or messages.EMPTY_FIELD_TEXT),
    )


def _screen_lines(text: str, width: int) -> int:
    """这段文字在 ``width`` 格下折成几**屏行**。

    折点来自 rich 的 ``divide_line``——**Textual 自己就是用这个函数折行的**
    （``textual/content.py`` 拼一行时调它，参数是同一个 ``fold``）。所以这里量出来的行数
    与屏幕上真实的行数是同一份账：正文按 wrap + fold 排版（``theme`` 里那条
    ``DetailPage #page-body``），一个字段因此可能占好几屏行。

    ``fold=True`` 是照着 ``text-overflow: fold`` 来的：少了它，比页宽还长的词会被裁掉而不是
    折下来，账就错了。私有模块那个下划线是不好看，但另一条路（自己重写一套折行）要与
    Textual 逐格对齐，那是把一件已经实现过的事再实现一遍。
    """
    return sum(len(divide_line(line, width, fold=True)) + 1 for line in text.split("\n"))


def read_only_rows(detail: TaskDetail) -> tuple[Row, ...]:
    """只读的那几段：子任务（含完成状态）、提醒（含触发时间）、重复（用户故事 76–78）。

    一段内容都没有时**整段不画**（工单 #20 的结论）：它们只是告知，不是入口——不像那三个
    自由文本字段，空的描述仍然要留在字段列表里等人去写。有内容时才在最后补一句
    :data:`~dida.tui.messages.READ_ONLY_NOTE`，说清这几样在这一页改不了。

    这些行 ``id=None``：光标越过它们，``enter`` 永远落不到只读的东西上。
    """
    rows: list[Row] = []
    if detail.subtasks:
        rows.append(
            Row(
                id=None,
                text=field_line(
                    "子任务",
                    "  ".join(
                        f"{SUBTASK_DONE_MARK if row.completed else SUBTASK_TODO_MARK} {row.title}"
                        for row in detail.subtasks
                    ),
                ),
            )
        )
    if detail.reminders:
        rows.append(Row(id=None, text=field_line("提醒", "  ".join(detail.reminders))))
    if detail.repeat_flag:
        rows.append(Row(id=None, text=field_line("重复", detail.repeat_flag)))
    if rows:
        rows.append(empty_row(messages.READ_ONLY_NOTE))
    return tuple(rows)


class DetailPage(CursorPage):
    """任务详细页：一条任务的字段列表 + 逐字段编辑（工单 #43 的那一层）。

    .. note::

       **折行的那一半在这里**（ADR-0007 的「截断与折行按页分工」）：这一页要能读完整的
       描述与备注，所以它 ``CLIP_ROWS = False``——``theme`` 里那条
       ``DetailPage #page-body { text-wrap: wrap }`` 就是它的落点。一个字段因此可能占好几个
       屏幕行（:meth:`_row_lines` 按 rich 的 ``divide_line`` 现算），光标与滚动一律按
       **屏幕行**算（``CursorPage`` 那三个钩子）。
    """

    LAYER = LAYER_DETAIL
    BINDINGS = bindings_for(LAYER)
    EMPTY_TEXT = messages.EMPTY_DETAIL_MESSAGE
    CLIP_ROWS = False
    """这一页**折行**：列表页那两页截断，详细页存在的意义就是「来这里读完整的」。"""

    class Back(Message):
        """用户按了 ``esc``（字段列表上）：退回任务列表页（光标还原到进来的那条任务）。"""

    class FieldEdited(Message):
        """用户按了 ``esc``（编辑中）：这个字段的文字改了，**已经生效、没有取消**。

        发出去之后由 app 交给引擎（乐观写 + 立刻推送）——页面不认识引擎，它只负责把
        「哪条任务、哪个字段、写成了什么」说清楚。
        """

        def __init__(self, task_id: str, field: str, value: str) -> None:
            self.task_id = task_id
            self.field = field
            self.value = value
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._task_id: str | None = None
        self._detail: TaskDetail | None = None
        self._fields: tuple[Field, ...] = ()

    @property
    def task_id(self) -> str | None:
        """这一页正在说的是哪条任务（``o`` 要知道）。"""
        return self._task_id

    def show_detail(self, detail: TaskDetail | None) -> None:
        """铺开一条任务的字段；``None`` = 它已经不在本地缓存里了。"""
        previous = self._task_id
        self._task_id = None if detail is None else detail.task_id
        self._detail = detail
        if detail is None:
            self._fields = ()
            self.set_rows((empty_row(self.EMPTY_TEXT),))
            return
        self._fields = fields_of(detail)
        # 换了一条任务就从**标题**开始（前一条任务停在第几个字段与这一条无关）；同一条任务
        # 再铺一遍（后台刷新回来）则按行 id 认回原来那个字段。
        self.set_rows(self._field_rows(), keep_cursor=detail.task_id == previous)

    def _field_rows(self) -> tuple[Row, ...]:
        """字段列表那一页的行：顶上一线，然后七个字段，最后是只读的那几段。

        顶上那条细线与清单列表页同一条规矩：顶栏说「你在哪」，紧跟着一条暗线把 chrome 与
        内容分开（底部仍然只有状态栏那一行）。
        """
        rows: list[Row] = [rule_row()]
        rows += [
            Row(id=field.key, text=field_line(field.label, field.display, style=field.style))
            for field in self._fields
        ]
        rows += read_only_rows(self._detail)
        return tuple(rows)

    def _row_lines(self) -> tuple[int, ...]:
        """每个字段占几屏行——**这张前缀和表就是详细页的光标与滚动**（#43 的实现后果 2）。

        一个长描述会占五六行，于是「第几个字段」与「第几屏行」分家：``CursorPage`` 里
        ``_line_of_cursor`` / ``_total_lines`` / ``scroll_cursor_into_view`` 读的都是这一张表。
        量的是 :meth:`_line_text` 那一份文字（含行首那两格光标空档），因为它才是画上去的
        东西。宽度还没量出来（第一帧、隐藏时）就先当一行，等 ``on_resize`` 重画再算。
        """
        width = self._row_width()
        if width <= 0:
            return (1,) * len(self._rows)
        return tuple(_screen_lines(self._line_text(row).plain, width) for row in self._rows)

    def on_resize(self) -> None:
        """宽度变了：折行块的行数跟着变，光标条与滚动的位置都要按新表重算。"""
        super().on_resize()
        if self._rows:
            self._land_bar()
            self.scroll_cursor_into_view()

    def action_back(self) -> None:
        """``esc``：退回任务列表页。"""
        self.post_message(self.Back())

