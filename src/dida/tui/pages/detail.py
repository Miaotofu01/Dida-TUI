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

import re
from dataclasses import dataclass

from rich._wrap import divide_line
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from dida.sync.engine import TaskDetail
from dida.tui import messages, theme
from dida.tui.keys import LAYER_DETAIL, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = ["DetailPage", "Field", "field_line", "fields_of", "read_only_rows", "reminder_text"]

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


_TRIGGER_PREFIX = "TRIGGER:"
"""服务端提醒的形状：``TRIGGER:`` 加一段 ISO-8601 时长（``openapi-dida365.md:2280``）。"""

_DURATION = re.compile(
    r"^(?P<sign>[+-])?P"
    r"(?:(?P<weeks>\d+)W"
    r"|(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
    r")$"
)
"""ISO-8601 时长里我们认得的那几种写法。

刻意**不认**年与月（``P1Y`` / ``P1M``）：它们没有固定长度（一个月 28–31 天），换算成人话
必然要四舍五入，而这是只读的一格——说错不如照原样。小数（``PT0.5H``）同理。
"""

_UNITS: tuple[tuple[str, str], ...] = (
    ("days", "天"),
    ("hours", "小时"),
    ("minutes", "分钟"),
    ("seconds", "秒"),
)
"""时长各段与它们的中文读法，按从大到小。零的段不念出来。"""

_DURATION_GROUPS: tuple[str, ...] = ("weeks", *(group for group, _ in _UNITS))
"""时长里每一个可以出现的段名（``weeks`` 单独算，它不与别的段同时出现）。"""


def _is_duration(match: re.Match[str]) -> bool:
    """这一段是不是**写了**时长的某一段：``P`` / ``PT`` 一个数字都没有，不算时长。"""
    return any(match.group(group) is not None for group in _DURATION_GROUPS)


def _duration_words(match: re.Match[str]) -> str:
    """一段时长读成人话：``1 天 2 小时``；写了但全是零就是空串。"""
    parts: list[str] = []
    weeks = int(match.group("weeks") or 0)
    if weeks:
        # ISO-8601 里 ``W`` 不能与别的段同时出现，换算成 7 天没有歧义。
        parts.append(f"{weeks * 7} 天")
    for group, word in _UNITS:
        value = int(match.group(group) or 0)
        if value:
            parts.append(f"{value} {word}")
    return " ".join(parts)


def reminder_text(trigger: str) -> str:
    """一条提醒读成人话：``TRIGGER:P0DT9H0M0S`` → 「提前 9 小时」（工单 #55）。

    这一格是**只读**的，它唯一的职责就是说明白；把 ``TRIGGER:`` 那种编码摊给用户看等于什么
    都没说。三个已知的例子：``P0DT9H0M0S`` → 提前 9 小时、``PT0S`` → 准时、``P1DT2H`` →
    提前 1 天 2 小时（不压成 26 小时）。

    ⚠ **正负号：正时长 = 提前，这个是核对过的；负时长的方向仍未验证。**

    - 正时长读作「提前」**由用户在官方客户端上核对过**（同一条提醒，官方客户端读作「提前」）
      ——不是从服务端文档推出来的：那份文档对符号一个字都没说，只有两个**正**时长的例子
      （``openapi-dida365.md:2280``：``["TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S"]``）。
    - 零时长 = 准时。
    - **负时长不猜方向**，只说「相对截止时间 N」。理由比「文档没写」更硬：TickTick 借了
      iCalendar 的 ``TRIGGER`` 形状、却把符号**反过来**用（见下），那就更没有理由假设它的
      负数落在那套语义里——猜错就是把「提前 30 分钟」显示成「延后 30 分钟」，含糊一句比说
      反了强。

    ⚠ **别照 RFC 5545 去「修正」这里的方向。** iCalendar 的 ``TRIGGER`` 属性（RFC 5545
    §3.8.6.3）说的正相反：「An alarm with a positive duration is triggered after the
    associated start or end… A negative duration is triggered before」，例子
    ``TRIGGER:-PT15M`` ＝ 提前 15 分钟。TickTick 的格式与它同形、语义相反，所以那份 RFC 在
    这里是个**反例**、不是依据——留着它是为了记住「不能拿 iCalendar 的直觉套这一格」。

    解析不了的一律**原样返回**：安静地少显示一个提醒，比显示得难看严重得多。
    """
    if not trigger.startswith(_TRIGGER_PREFIX):
        return trigger
    match = _DURATION.match(trigger[len(_TRIGGER_PREFIX) :])
    if match is None or not _is_duration(match):
        return trigger
    words = _duration_words(match)
    if not words:
        return "准时"
    if match.group("sign") == "-":
        return f"相对截止时间 {words}"
    return f"提前 {words}"


def read_only_rows(detail: TaskDetail) -> tuple[Row, ...]:
    """只读的那几段：子任务（含完成状态）、提醒（含触发时间）、重复（用户故事 76–78）。

    一段内容都没有时**整段不画**（工单 #20 的结论）：它们只是告知，不是入口——不像那三个
    自由文本字段，空的描述仍然要留在字段列表里等人去写。有内容时才在最后补一句
    :data:`~dida.tui.messages.READ_ONLY_NOTE`，说清这几样在这一页改不了。

    这些行 ``id=None``：光标越过它们，``enter`` 永远落不到只读的东西上。

    提醒是**读法**、不是原文（:func:`reminder_text`）：多条之间照旧两个空格——人话变长了，
    但这一页折行，窄终端下它们各占几行而不是被裁掉。
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
        rows.append(
            Row(
                id=None,
                text=field_line(
                    "提醒", "  ".join(reminder_text(item) for item in detail.reminders)
                ),
            )
        )
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

        只有三个自由文本字段有编辑器（:attr:`Field.wire`）；截止时间与那三个挑选型字段今天
        什么都不做——**接缝留给 #44 / #45**：它们给 ``Field.wire`` 填上值、在这里接一个编辑
        器，光标、折行、保存行都不必再动。编辑态下这个键归编辑器自己（多行框里它是换行）。
        """
        if self._editing is not None:
            return
        field = self._current_field()
        if field is None or field.wire is None:
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

