"""层三：任务详细页——字段列表、逐字段编辑、只读的那几段（工单 #43）。

spec 的三层状态机里，任务列表页 ``enter`` 进这一页、``esc`` 退回任务列表页。这一页上：
``j``/``k`` 在**字段之间**走（永远不停在折行块内部）、``enter`` 进当前字段的编辑、编辑中
``esc`` **结束这次编辑并回到字段列表**（改动已经生效，没有「取消」）。

**两个字段不许标反**（``GLOSSARY.md``：描述 = ``content``、备注 = ``desc``；v1 标反了，
这份 spec 纠正它）：这一页按术语表写，``TaskDetail.content`` 画在「描述」那一行。

**这一页折行**（ADR-0007 的「截断与折行按页分工」：列表页截断、详细页换行）：一个长描述
占好几屏行，于是「第几个字段」与「第几屏行」分家——光标与滚动一律按**屏幕行偏移表**算
（:meth:`DetailPage._row_lines`，折点用 Textual 自己那个 ``divide_line``）。

可编辑的三个字段是**自由文本**（标题单行、描述与备注多行）；所属清单、截止时间、优先级、
标签今天只读显示——改它们要打另外三个形状的端点，归 #44（截止）与 #45（清单/优先级/标签）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from rich._wrap import divide_line
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from dida.sync.engine import ListRow, TaskDetail
from dida.tui import messages, theme
from dida.tui.keys import LAYER_DETAIL, bindings_for
from dida.tui.overlays import FORM_HINT, FormField, FormOption
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = [
    "DetailPage",
    "Field",
    "Picker",
    "field_line",
    "fields_of",
    "list_picker",
    "picker_spec",
    "read_only_rows",
]

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

    picker: bool = False
    """这一格是**挑选型**的（工单 #45）：``enter`` 开一个挑选浮层，不是进文本编辑器。

    三个挑选型字段（清单 / 优先级 / 标签）的选项要引擎的数据才拼得出来，所以这一页不自己
    开浮层——它只把「哪条任务、哪一格」说出去（:class:`DetailPage.PickRequested`），由 app
    把选项凑齐再开那张共用的 :class:`~dida.tui.overlays.FormOverlay`。
    """


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
        Field("list", "清单", value=detail.list_name, display=detail.list_name, picker=True),
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


LIST_FIELD = "list"
PRIORITY_FIELD = "priority"
TAGS_FIELD = "tags"
"""三个挑选型字段在 ``{字段名: 值}`` 里的键（就是字段注册表里那几个 ``key``）。"""


@dataclass(frozen=True)
class Picker:
    """一张挑选浮层要开成什么样（工单 #45）：顶上那一行、几格、底下那行提示。

    字段与提示由这一页给（它才知道「清单」这一格该画什么），开浮层与写回去由 app 管
    （那一层才认识引擎）。所以这里传的是一份**成品**，app 拿去直接 ``push_screen``。
    """

    title: str
    fields: tuple[FormField, ...]
    hint: str = FORM_HINT


def list_picker(detail: TaskDetail, lists: Sequence[ListRow]) -> tuple[FormField, ...]:
    """「清单」那一格：从**能搬进去**的清单里挑一个（工单 #45）。

    ``lists`` 是引擎的 ``move_targets()``：真实清单、进得去、**服务端已经见过**的那些
    （本地刚建还没推上去的清单不能当搬运目标，理由写在那一处）。选项的值是清单 id、
    标签是清单名——写回去的是 id，屏幕上看到的是名字。

    当前所在的那一行如果不在这一份里（只读清单、备注清单、或者远端刚删掉），**补在最前面**：
    不补的话 ``ChoiceField`` 会把那个认不出来的值原样加成一档，用户看到的是自己现在在哪
    都认不出来（那一档的标签就是清单 id）。补上之后「挑它自己」是一次空操作（引擎不写），
    所以它不会把任务搬去一个搬不进去的地方。
    """
    options = [FormOption(row.id, row.name) for row in lists]
    if not any(option.value == detail.list_id for option in options):
        options.insert(0, FormOption(detail.list_id, detail.list_name))
    return (
        FormField(
            name=LIST_FIELD,
            label="清单",
            options=tuple(options),
            value=detail.list_id,
        ),
    )


def picker_spec(
    key: str,
    detail: TaskDetail,
    *,
    lists: Sequence[ListRow] = (),
    tags: Sequence[str] = (),
) -> Picker | None:
    """哪一格开哪一张挑选浮层（``key`` 是字段注册表里的 ``key``）。

    认不出来的 ``key`` 给 ``None``（什么都不开）：这一页只认识自己那几格，别人传错名字时
    静默什么都不做，比开一张空浮层好。
    """
    if key == "list":
        return Picker("搬到哪个清单", list_picker(detail, lists))
    return None


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

    class PickRequested(Message):
        """用户按了 ``enter``（挑选型字段上）：要挑一个新的值（工单 #45）。

        这一页**不开浮层**：清单要引擎的清单索引、标签要引擎那份名单，页面不认识引擎。
        它只把「哪条任务、哪一格」说清楚，由 app 把选项凑齐再开那张共用的表单浮层
        （与 ``FieldEdited`` 同一条分工：页面说事实，认识引擎的是 app）。
        """

        def __init__(self, task_id: str, field: str) -> None:
            self.task_id = task_id
            self.field = field
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._task_id: str | None = None
        self._detail: TaskDetail | None = None
        self._fields: tuple[Field, ...] = ()
        self._editing: Field | None = None
        """正在编辑哪个字段；``None`` = 光标停在字段列表上（这一页的常态）。"""

    def compose(self) -> ComposeResult:
        """正文 + 装饰光标条（页面的那两块），加上这一页自己的两块：底部那一行 + 编辑器。

        编辑器（单行 / 多行两个框）**预先摆好、平时藏着**（``display: none``）：进编辑就是把
        正文藏起来、把它亮出来，退出反过来。挂载与卸载留到按键那一刻做的话，一次 ``esc``
        要跨两次布局，用户看到的是闪一下。
        """
        yield from super().compose()
        yield Static(id="detail-save")
        with Vertical(id="detail-edit"):
            yield Static(id="detail-edit-label")
            yield Input(id="detail-input")
            yield TextArea(id="detail-text", show_line_numbers=False)

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
            self._end_edit()
            self.set_rows((empty_row(self.EMPTY_TEXT),))
            return
        self._fields = fields_of(detail)
        if self._editing is not None:
            # 正在编辑时**不重铺**：用户手里那段文字是他的，后台刷新（周期泵）不该把它换掉。
            # 这一次编辑结束后由 app 再刷新一遍，字段列表拿到的是最新那一份。
            return
        # 换了一条任务就从**标题**开始（前一条任务停在第几个字段与这一条无关）；同一条任务
        # 再铺一遍（后台刷新回来）则按行 id 认回原来那个字段。
        self.set_rows(self._field_rows(), keep_cursor=detail.task_id == previous)

    def show_save(self, text: str) -> None:
        """页面底部那一行常驻的话（「已保存」/「待推送（N）」/保存失败的具体原因）。

        它不随正文滚动（``dock: bottom``，钉在这一页自己的底边）：正在改描述的用户要能一眼
        看见刚才那一下到底出去没有（用户故事 65）。
        """
        self.query_one("#detail-save", Static).update(theme.styled(text, theme.MUTED))

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
        if self._rows and self._editing is None:
            self._land_bar()
            self.scroll_cursor_into_view()

    # ---------------------------------------------------------------- 编辑

    def action_enter(self) -> None:
        """``enter``：进当前字段的编辑（验收标准 3）。

        两种字段两种走法：**挑选型**的三格（清单 / 优先级 / 标签，工单 #45）把「要挑」说给
        app（它才认识引擎），三个**自由文本**字段（:attr:`Field.wire`）进这一页自己的编辑器；
        剩下 ``wire`` 与 ``picker`` 都没有的（截止时间，归 #44）今天什么都不做。
        编辑态下这个键归编辑器自己（多行框里它是换行）。
        """
        if self._editing is not None:
            return
        field = self._current_field()
        if field is None:
            return
        if field.picker:
            if self._task_id is not None:
                self.post_message(self.PickRequested(self._task_id, field.key))
            return
        if field.wire is None:
            return
        self._begin_edit(field)

    def _current_field(self) -> Field | None:
        """光标停在哪个字段上（字段列表那一份，不是 Row）。"""
        return next((field for field in self._fields if field.key == self.selected_id), None)

    def _begin_edit(self, field: Field) -> None:
        """把正文藏起来、把编辑器亮出来，光标交给它。"""
        self._editing = field
        self.query_one("#detail-edit-label", Static).update(
            theme.styled(f"编辑{field.label}", theme.HEADING)
        )
        single = self.query_one("#detail-input", Input)
        multi = self.query_one("#detail-text", TextArea)
        single.display = not field.multiline
        multi.display = field.multiline
        if field.multiline:
            multi.text = field.value
            # 光标停在**末尾**：改动通常是接着写。多行框的落点是 (行, 列)，最后一行就是
            # ``value`` 里最后那一段——Textual 没有「全选」，光标丢在开头会让人一按退格
            # 什么都没发生（原型里那条 30 列的实测就是这么来的）。
            lines = field.value.split("\n")
            multi.move_cursor((len(lines) - 1, len(lines[-1])))
            multi.focus()
        else:
            single.value = field.value
            # 单行框同理：光标停在末尾，改动接着写。
            single.cursor_position = len(field.value)
            single.focus()
        self._body().styles.display = "none"
        self._bar().styles.visibility = "hidden"
        self.query_one("#detail-edit").styles.display = "block"

    def _editor_value(self) -> str:
        """编辑器里当前那段文字。"""
        field = self._editing
        if field is not None and field.multiline:
            return self.query_one("#detail-text", TextArea).text
        return self.query_one("#detail-input", Input).value

    def _end_edit(self) -> None:
        """收起编辑器，把这一页还给字段列表。"""
        if self._editing is None:
            return
        self._editing = None
        self.query_one("#detail-edit").styles.display = "none"
        self._body().styles.display = "block"
        self._redraw()
        self.focus(scroll_visible=False)

    def _finish_edit(self) -> None:
        """``esc``（编辑中）：**结束这次编辑，改动已经生效**（验收标准 4）。

        没有「取消」这条路——所以这里只有两种结局：写得出去（发消息给 app，交给引擎乐观写 +
        立刻推送），或者**根本没改**（原样收起）。标题被清空是唯一一种「不能写」的输入：
        空标题不被接受，旧标题原样留着，下面那一行说清是哪一条规矩（不是一个「保存失败」）。
        """
        field = self._editing
        value = self._editor_value()
        self._end_edit()
        if field is None or self._task_id is None or value == field.value:
            return
        if field.key == "title" and not value.strip():
            self.show_save(messages.NO_TITLE_EDIT_MESSAGE)
            return
        self.post_message(self.FieldEdited(self._task_id, field.wire or field.key, value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """单行输入里按 ``enter`` ＝ 提交（键位表：``enter`` 是「进入下一层 / 提交输入」）。"""
        if self._editing is not None and not self._editing.multiline:
            self._finish_edit()

    def action_back(self) -> None:
        """``esc``：编辑中结束这次编辑，字段列表上退回任务列表页（验收标准 4 + 5）。"""
        if self._editing is not None:
            self._finish_edit()
            return
        self.post_message(self.Back())

