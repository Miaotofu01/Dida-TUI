"""层三：任务详细页——字段列表、逐字段编辑、只读的那几段（工单 #43）。

spec 的三层状态机里，任务列表页 ``enter`` 进这一页、``esc`` 退回任务列表页。这一页上：
``j``/``k`` 在**字段之间**走（永远不停在折行块内部）、``enter`` 进当前字段的编辑、编辑中
``esc`` **结束这次编辑并回到字段列表**（改动已经生效，没有「取消」）。

**两个字段不许标反**（``GLOSSARY.md``：描述 = ``content``、备注 = ``desc``；v1 标反了，
这份 spec 纠正它）：这一页按术语表写，``TaskDetail.content`` 画在「描述」那一行。

**这一页折行**（ADR-0007 的「截断与折行按页分工」：列表页截断、详细页换行）：一个长描述
占好几屏行，于是「第几个字段」与「第几屏行」分家——光标与滚动一律按**屏幕行偏移表**算
（:meth:`DetailPage._row_lines`，折点用 Textual 自己那个 ``divide_line``）。

可编辑的三个字段是**自由文本**（标题单行、描述与备注多行）；所属清单、优先级、标签是
**挑选型**（#45）——``enter`` 开一张挑选浮层，由 app 把选项凑齐；截止时间（#44）是结构化
的日期 + 时刻编辑器。三者各自的端点形状见 :meth:`DetailPage.action_enter` 上那段次序说明。

**截止时间（#44）是第四种编辑器**：它不是自由文本，而是一个结构化的日期 + 时刻（外加一个
「全天」开关），因为改它要打的是另一个形状的端点（``POST /task/{id}`` 上的 ``dueDate`` +
``isAllDay``，见 :mod:`dida.sync.schedule`）。它复用这一页已经摆好的那块编辑区
（``#detail-edit``）与那条保存行，自己只多两个格子和一个开关——折行、光标、滚动都不动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from typing import Sequence

from rich._wrap import divide_line
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from dida.sync.engine import ListRow, TaskDetail
from dida.tui import messages, theme
from dida.tui.keys import LAYER_DETAIL, bindings_for
from dida.tui.overlays import FORM_HINT, MULTI_SEPARATOR, FormField, FormOption
from dida.tui.pages import due
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = [
    "DetailPage",
    "Field",
    "Picker",
    "field_line",
    "fields_of",
    "LIST_PICKER_TITLE",
    "PRIORITY_PICKER_TITLE",
    "TAGS_PICKER_TITLE",
    "list_picker",
    "priority_picker",
    "picker_spec",
    "read_only_rows",
    "reminder_text",
    "tags_picker",
]

SUBTASK_DONE_MARK = theme.SUBTASK_DONE_MARK
SUBTASK_TODO_MARK = theme.SUBTASK_TODO_MARK
"""子任务的两种状态标记（只读显示：v2 不在客户端里勾子任务）。"""

DUE_FIELD_KEY = "due"
"""「截止」那一行在字段列表里的 key（编辑器按它分派，不按标签文字认）。"""

DUE_WIRE = "dueDate"
"""截止时间写回服务端的字段名（``Field.wire`` 上的值）。

它是**结构化编辑器**的分派凭据：``action_enter`` 看到这个值就走
:meth:`DetailPage._begin_due_edit`，不走那个自由文本框。写成服务端的字段名而不是一个
自造的标记，是因为这一页的字段表说的就是「这一格对应请求体上的哪一个字段」。
"""

DUE_TOGGLE_KEY = "x"
"""「全天」开关的键（工单 #44 验收标准 2）。

它**不进** ``keys.py`` 的绑定表：那一张表的守卫（``tests/test_keymap.py``）要求
「帮助里列出的键 === 真实绑定的键」，而这一页的绑定必须整份从表里来
（``test_every_page_binds_its_own_layer_from_the_one_table``）。

所以它绑在**那两个格子自己**身上（:class:`DueInput`）：焦点在格子里时 Textual 先看那个
控件的绑定表，而 ``Input`` 自己那张表里没有 ``x``（它绑的是光标、删除、剪贴板那几组），
所以这个键到得了这里、也**只**在编辑器里有效。页面自己的绑定表一个字不动。"""

DUE_STEPS = ("date", "time")
"""截止时间编辑器的两步：先日期、再时刻（验收标准 1）。"""

DUE_EMPTY_REASON = "日期那一格还是空的"
"""日期那一格没被碰过时的说法（``enter`` 与 ``esc`` 共用一句）。

两处共用一份：这是同一个事实（没有改动可提交），两个键说的不该是两句不同的话。
"""


class DueInput(Input):
    """截止时间编辑器里的一个格子：多一个「全天」开关的键（工单 #44 验收标准 2）。

    绑在控件上而不是页面上（见 :data:`DUE_TOGGLE_KEY`）。动作名**不带前缀**：Textual 拿
    **拥有这条绑定的那个控件**当默认命名空间，所以 ``toggle_all_day`` 解析到这个格子自己身上
    ——这也正是它必须在这里有个 ``action_`` 方法的原因（不带前缀而目标没有那个方法时，键会被
    **静默吃掉**）。``app.`` 前缀在这里**不行**：它解析到的是 ``DidaApp``，而 ``action_``
    长在页面上（实测：``run_action`` 返回 ``False``，键什么都不做）。
    """

    BINDINGS = [Binding(DUE_TOGGLE_KEY, "toggle_all_day", "全天", show=False, priority=True)]
    """``priority=True`` 是**承重**的（实测）。

    普通绑定只在「从焦点往上找」那一趟里被查，而那一趟发生在焦点控件**已经**处理完这个键
    之后：``Input._on_key`` 见到可打印字符就把它插进去（``event.stop()``），于是用户按下
    ``x`` 得到的是 ``2026-03-15x``（实测屏幕上的原文）。优先级那一趟在事件被转发给焦点
    控件**之前**跑，所以这一个键在这里被吃掉、不会变成正文——与 ``#42`` 的 ``Ctrl+C``
    要用 ``priority=True`` 是同一个道理（少了它，键的含义跟着焦点跑）。
    """

    def action_toggle_all_day(self) -> None:
        """把这一下交给这一页（真正的开关状态在 :class:`DetailPage` 上）。

        沿祖先往上找那一页，而不是认 ``self.parent``：这一格被挂在一个 ``Vertical`` 里
        （与提示行同一块），父节点不是页面（实测：按了 ``x`` 什么都不发生，因为
        ``isinstance(self.parent, DetailPage)`` 是假）。状态只有一份，就在这里转交，不在
        控件上再复制一份——复制出来的那一份迟早与页面上那一份说不一样的话。
        """
        node = self.parent
        while node is not None:
            if isinstance(node, DetailPage):
                node._toggle_all_day()
                return
            node = node.parent


@dataclass(frozen=True)
class Field:
    """字段列表里的一行：它能被编辑成什么（``wire``）与怎么画（``label`` + ``display``）。

    ``key`` 是这一行的身份，也是光标认回来的凭据（后台刷新之后光标要留在同一个字段上）。
    ``wire`` 是写回服务端的字段名：三个自由文本字段各有自己的一个（``title`` /
    ``content`` / ``desc``——**描述 = content、备注 = desc**，GLOSSARY），截止时间是
    ``dueDate``（#44：它走 :meth:`DetailPage._begin_due_edit` 那个结构化编辑器，不是自由
    文本框），``None`` 表示「这一票还不改它」：#45 接清单/优先级/标签那三个挑选型字段。
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


def _due_field(detail: TaskDetail) -> Field:
    """「截止」那一行：显示照旧，但 ``wire`` 上挂的是**结构化编辑器**（工单 #44 的接缝）。

    ``value`` 与 ``display`` 都还是引擎给的读法（``今天 18:00`` / ``无``）：这一行画什么
    由读模型说了算，这里只是把「``enter`` 该进哪个编辑器」补上——``#43`` 把这一格留成
    ``wire=None`` 正是为了这件事。
    """
    shown = theme.NO_VALUE if detail.due is None else detail.due_text
    return Field(
        DUE_FIELD_KEY,
        "截止",
        value=shown,
        display=shown,
        style="" if detail.due is not None else EMPTY_STYLE,
        wire=DUE_WIRE,
    )


def fields_of(detail: TaskDetail) -> tuple[Field, ...]:
    """详情页的字段列表，按 spec 的顺序（标题、描述、备注、所属清单、截止时间、优先级、标签）。

    所属清单、优先级、标签这三格是**挑选型**（``picker=True``，#45）：它们的 ``wire`` 仍是
    ``None``（不打 ``POST /task/{id}``），``enter`` 只把「哪条任务、哪一格」说给 app，由 app
    把选项凑齐再开那张共用的浮层。顺序、标签与光标位置在这里定下来，两条工单各自接编辑器。
    """
    return (
        _free_text("title", "标题", detail.title, wire="title", multiline=False),
        _free_text("content", "描述", detail.content, wire="content", multiline=True),
        _free_text("desc", "备注", detail.desc, wire="desc", multiline=True),
        Field("list", "清单", value=detail.list_name, display=detail.list_name, picker=True),
        _due_field(detail),
        Field(
            "priority",
            "优先级",
            value=messages.priority_name(detail.priority),
            display=messages.priority_name(detail.priority),
            picker=True,
        ),
        Field(
            "tags",
            "标签",
            value=detail.tags_text,
            display=detail.tags_text or messages.EMPTY_FIELD_TEXT,
            picker=True,
        ),
    )


LIST_FIELD = "list"
PRIORITY_FIELD = "priority"
TAGS_FIELD = "tags"
"""三个挑选型字段在 ``{字段名: 值}`` 里的键（就是字段注册表里那几个 ``key``）。"""

LIST_PICKER_TITLE = "搬到哪个清单"
PRIORITY_PICKER_TITLE = "优先级"
TAGS_PICKER_TITLE = "标签"
"""三张挑选浮层顶上那一行（工单 #45）。

写成常量而不是散在 :func:`picker_spec` 里：它们与上面那三个键一样，是这一页对外说得出口的
东西（宽度守卫 ``tests/test_picker_fields.py`` 也照着它们查歧义字形）。
"""


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


def priority_picker(detail: TaskDetail) -> tuple[FormField, ...]:
    """「优先级」那一格：无 / 低 / 中 / 高（工单 #45）。

    选项的**值**是线上编码 ``0/1/3/5``、**标签**是 ``messages.PRIORITY_NAMES`` 那张表里的
    名字——那一张表是用户语言的唯一一份（``priority_name`` 读的也是它），这里不再抄第二份，
    ``0/1/3/5`` 也不进文案：那是服务端的编码，不是用户说的档位。

    表外的取值（服务端理论上不该给的 ``2`` / ``4``）读作「无」——与 ``priority_name`` /
    ``priority_mark`` 同一条口径。不这么归一的话，``ChoiceField`` 会把那个认不出来的值原样
    加成一档，屏幕上就出现一个**裸数字**（那正是这一票不许有的东西）；而归一之后「挑无」
    与屏幕上写着的那一档一致，所以打开不动直接 ``Enter`` 也不会凭空写一笔。
    """
    names = messages.PRIORITY_NAMES
    current = detail.priority if detail.priority in names else 0
    return (
        FormField(
            name=PRIORITY_FIELD,
            label="优先级",
            options=tuple(FormOption(str(code), name) for code, name in names.items()),
            value=str(current),
        ),
    )


def tags_picker(detail: TaskDetail, tags: Sequence[str]) -> tuple[FormField, ...]:
    """「标签」那一格：从**已有的**标签里多选（工单 #45）。

    选项是引擎给的那一份（服务端那份名单 ∪ 本地任务上出现过的）。任务上已经打着的标签
    即使不在名单里也照样看得见、去得掉——:class:`~dida.tui.overlays.MultiChoiceField`
    把它们自己补成一档（与 ``ChoiceField`` 对认不出的颜色同一条口径），所以「取消一个标签」
    在名单拉不到时也走得通。

    值那一串用 :data:`~dida.tui.overlays.MULTI_SEPARATOR` 连：标签名里可以有逗号，
    分隔符必须是一个打不出来的字符。

    底部那行提示换成 :data:`~dida.tui.messages.TAGS_PICKER_HINT`：那一格要说清「新建标签
    要回官方客户端」（验收标准 6），而默认那行提示只说键位。
    """
    return (
        FormField(
            name=TAGS_FIELD,
            label="标签",
            options=tuple(FormOption(name, name) for name in tags),
            value=MULTI_SEPARATOR.join(detail.tags),
            multi=True,
        ),
    )


def picker_spec(
    key: str,
    detail: TaskDetail,
    *,
    lists: Sequence[ListRow] = (),
    tags: Sequence[str] = (),
    notice: str = "",
) -> Picker | None:
    """哪一格开哪一张挑选浮层（``key`` 是字段注册表里的 ``key``）。

    ``notice`` 是这一次开浮层前就该说的一句话（挑标签那一格用它说「名单没拉到，这一份是
    本地的」）——写在浮层的提示最前面：浮层是模态的，说在它里面才看得见。

    认不出来的 ``key`` 给 ``None``（什么都不开）：这一页只认识自己那几格，别人传错名字时
    静默什么都不做，比开一张空浮层好。
    """
    if key == "list":
        return Picker(LIST_PICKER_TITLE, list_picker(detail, lists))
    if key == "priority":
        return Picker(PRIORITY_PICKER_TITLE, priority_picker(detail))
    if key == "tags":
        return Picker(TAGS_PICKER_TITLE, tags_picker(detail, tags), messages.tags_picker_hint(notice))
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

    class DueChanged(Message):
        """用户提交了截止时间编辑器：这一刻（或「没有日期」）要写到这条任务上（工单 #44）。

        与 :class:`FieldEdited` 分开，因为它是**另一种形状的写**：不是一个 ``{字段: 文字}``，
        而是一对 ``{dueDate, isAllDay}``，落点是引擎的 ``reschedule``（只动这两个字段、
        不碰重复规则、整份底稿照旧回写）。页面照样不认识引擎——它只说清「哪条任务、哪一刻、
        是不是全天」，``due=None`` 就是清除。
        """

        def __init__(self, task_id: str, due: datetime | None, all_day: bool) -> None:
            self.task_id = task_id
            self.due = due
            self.all_day = all_day
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

        self._due_editing = False
        """截止时间编辑器开着没有（它有自己的两个格子，不是 ``_editing`` 那一档）。"""

        self._due_all_day = False
        """截止时间编辑器里「全天」那一档（``x`` 切换；提交时决定要不要写时刻）。"""

        self._due_step: str = DUE_STEPS[0]
        """编辑器走到哪一步了（先日期、再时刻——验收标准 1）。"""

        self._due_prefill = ""
        """日期那一格进编辑器时回填的值；用户改过它没有，是「还没填」与「要清除」的分界。"""

        self._due_date_touched = False
        """用户动过日期那一格没有（清空也算动过）。程序化回填不算。"""

        self._zone: tzinfo | None = None
        """用户墙钟当前的时区（app 从注入的钟上取来，随 :meth:`show_detail` 递进来）。"""

    def compose(self) -> ComposeResult:
        """正文（页面的那一块），加上这一页自己的两块：底部那一行 + 编辑器。

        编辑器（单行 / 多行 / 截止时间那三套）**预先摆好、平时藏着**（``display: none``）：
        进编辑就是把正文藏起来、把它亮出来，退出反过来。挂载与卸载留到按键那一刻做的话，
        一次 ``esc`` 要跨两次布局，用户看到的是闪一下。

        截止时间那一套（#44）与自由文本框共用 ``#detail-edit`` 这一块：同一个位置、同一条
        标签行、同一条保存行——用户看到的「进编辑器」始终是同一件事。
        """
        yield from super().compose()
        yield Static(id="detail-save")
        with Vertical(id="detail-edit"):
            yield Static(id="detail-edit-label")
            yield Input(id="detail-input")
            yield TextArea(id="detail-text", show_line_numbers=False)
            with Vertical(id="due-edit"):
                yield Static(id="due-hint")
                yield DueInput(id="due-date")
                yield DueInput(id="due-time")

    @property
    def task_id(self) -> str | None:
        """这一页正在说的是哪条任务（``o`` 要知道）。"""
        return self._task_id

    def show_detail(self, detail: TaskDetail | None, *, zone: tzinfo | None = None) -> None:
        """铺开一条任务的字段；``None`` = 它已经不在本地缓存里了。

        ``zone`` 是**用户墙钟当前的时区**，由 app 从注入的钟上取来（工单 #58 的 T1）。这一页
        要把用户敲的 ``2026-03-15`` / ``18:00`` 理解成一个时刻，而任务本来没有截止时间时它
        没有 offset 可借（见 :meth:`_zone_hint`）。页面自己**不读时钟**：``datetime.now()``
        正是业务代码里被禁的那一个（README 与 docs/architecture.md 的硬规则，
        ``tests/test_clock_seam.py`` 用 AST 守着），所以这一份只能是递进来的事实。
        """
        self._zone = zone
        previous = self._task_id
        self._task_id = None if detail is None else detail.task_id
        self._detail = detail
        if detail is None:
            self._fields = ()
            self._end_edit()
            self.set_rows((empty_row(self.EMPTY_TEXT),))
            return
        self._fields = fields_of(detail)
        if self._editing is not None or self._due_editing:
            # 正在编辑时**不重铺**：用户手里那段文字（或者他正在选的那个日期）是他的，
            # 后台刷新（周期泵）不该把它换掉。这一次编辑结束后由 app 再刷新一遍，字段列表
            # 拿到的是最新那一份。
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
        ``_line_of_cursor`` / ``_cursor_lines`` / ``scroll_cursor_into_view`` 读的都是这一张表。
        量的是 :meth:`_line_text` 那一份文字（含行首那两格光标空档），因为它才是画上去的
        东西。宽度还没量出来（第一帧、隐藏时）就先当一行，等 ``on_resize`` 重画再算。
        """
        width = self._row_width()
        if width <= 0:
            return (1,) * len(self._rows)
        return tuple(_screen_lines(self._line_text(row).plain, width) for row in self._rows)

    def on_resize(self) -> None:
        """宽度变了：折行块的行数跟着变，滚动的位置要按新表重算。"""
        super().on_resize()
        if self._rows and self._editing is None:
            self.scroll_cursor_into_view()

    # ---------------------------------------------------------------- 编辑

    def action_enter(self) -> None:
        """``enter``：进当前字段的编辑（验收标准 3）。

        三种字段三种走法，**次序是承重的**（这一页同时接 #44 与 #45）：

        1. 编辑态下这个键归编辑器自己（多行框里它是换行），所以先让开；
        2. **挑选型**的三格（清单 / 优先级 / 标签，工单 #45）把「要挑」说给 app（它才认识
           引擎）。这一支必须在 ``wire`` 那道门**之前**：那三格的 ``wire`` 都是 ``None``
           （它们不打 ``POST /task/{id}``），门在前就成了永远走不到的死代码；
        3. ``wire`` 是 ``None`` 的到此为止（这一格没有编辑器，按下去什么都不做）；
        4. 截止时间（#44）：``wire`` 是 ``dueDate``，进**结构化**编辑器——先日期、再时刻
           （验收标准 1）。它必须在自由文本框**之前**，否则 ``enter`` 会把「今天 18:00」
           这行字塞进 ``Input``、当成 ``dueDate`` 的自由文本写出去；
        5. 其余 ``wire`` 有值的（标题 / 描述 / 备注）进那个文本框。
        """
        if self._editing is not None or self._due_editing:
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
        if field.key == DUE_FIELD_KEY and self._detail is not None:
            self._begin_due_edit(self._detail)
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
        self.query_one("#detail-edit").styles.display = "block"

    # ---------------------------------------------------------------- 截止时间（#44）

    def _begin_due_edit(self, detail: TaskDetail) -> None:
        """把正文藏起来、亮出截止时间那两格，光标交给日期（验收标准 1）。

        两格**回填当前那一刻**：用户看着 ``2026-03-14`` / ``18:00`` 改，而不是对着一片空白
        猜格式。回填的是这条任务当前截止时间在他墙上那一刻（引擎给的 ``due`` 自带时区），
        所以「只改时刻那一位」是可能的。
        """
        self._due_editing = True
        self._due_all_day = detail.all_day
        self._due_step = DUE_STEPS[0]
        self._due_date_touched = False
        self.query_one("#detail-input", Input).display = False
        self.query_one("#detail-text", TextArea).display = False
        self.query_one("#due-edit").display = True
        date_input = self.query_one("#due-date", Input)
        time_input = self.query_one("#due-time", Input)
        self._due_prefill = "" if detail.due is None else detail.due.strftime(due.DATE_FORMAT)
        date_input.value = self._due_prefill
        time_input.value = "" if detail.due is None else due.format_time_input(detail.due.time())
        time_input.display = not self._due_all_day
        self._body().styles.display = "none"
        self.query_one("#detail-edit").styles.display = "block"
        self._show_due_step()
        date_input.focus()

    def _show_due_step(self) -> None:
        """把「现在在第几步」画出来（验收标准 1 的「先日期、再时刻」要看得见）。

        这一行同时是那个**全天开关**的读数（验收标准 2）：它画出 ``[x]`` / ``[ ]``，
        用户按 ``x`` 能看见它翻。整行都是 ASCII 与汉字，没有一个歧义宽度的字形——它要
        与日期、时刻排在一起，宽度含糊就会把后面几格推歪。
        """
        if self._due_step == DUE_STEPS[0]:
            step = f"第 1 步：日期（{messages.DUE_DATE_FORMAT_HINT}）"
        else:
            step = f"第 2 步：时刻（{messages.DUE_TIME_FORMAT_HINT}，留空 = 只有日期）"
        self.query_one("#detail-edit-label", Static).update(
            theme.styled("改截止", theme.HEADING)
        )
        self.query_one("#due-hint", Static).update(
            theme.styled(
                f"{step}  {messages.all_day_toggle_text(self._due_all_day)}（x 切换）", theme.MUTED
            )
        )

    def _typed_day(self) -> date | None:
        """日期那一格敲进去的那一天；**空着给 ``None``**（还没填 / 要清除），不认就抛。

        空串不交给 :func:`~dida.tui.pages.due.parse_date`：那里对空串也说「不是一个日期」，
        而这一页要把「空着」与「写错了」分开——前者是用户按早了或者要清除，后者才是敲错了。
        """
        typed = self.query_one("#due-date", Input).value
        return None if not typed.strip() else due.parse_date(typed)

    def _zone_hint(self) -> tzinfo | None:
        """任务没有截止时间时，用哪一个时区理解用户敲的墙钟。

        有截止时间时不需要它——那一刻自己带着 offset（``due.due_change`` 的 ``reference``）。
        没有的时候只能给一个本地时区，而「本地」是**注入的钟**说的：app 把 ``zone`` 随
        :meth:`show_detail` 递进来（工单 #58 的 T1），这一页自己不读时钟。文档对 ``timeZone``
        字段写错会怎样一个字都没写（api-shapes §D17），所以这里**不**把那个名字换算成 offset
        （那要一整个 tzdata，而算错正是静默位移）。给不出来（``None``）就让 ``datetime`` 是
        naive 的——``guards.api_date`` 会当场拒绝，而不是替它猜一个。
        """
        return self._zone

    def _due_draft(self) -> tuple[date | None, time | None]:
        """编辑器里这两格当前的内容 → （哪一天、哪一刻）；认不出来就抛 :class:`ValueError`。

        两格**一起**读：用户可能只走到第一步（日期敲完、时刻那格还是回填的原值），也可能
        两步都动过。``enter`` 与 ``esc`` 提交的是同一份草稿，所以读法也只有这一处——同一个
        键在同一个页面上不该有两种含义。
        """
        return self._typed_day(), due.parse_time(self.query_one("#due-time", Input).value)

    def _commit_due(self, day: date | None, at: time | None) -> None:
        """把这两格组成的那一刻写出去（``day=None`` 是清除）。

        参考时刻是这条任务**当前**的截止时间：用户敲的墙钟就落在那一刻原来的 offset 上，
        不换算（任务本来没有截止时间时才用 :meth:`_zone_hint`）。``_due_all_day`` 一起走
        ——「全天」是用户在编辑器里切出来的那一档，提交时不许丢。
        """
        entry = due.DueInput(day=day, at=at, all_day=self._due_all_day)
        reference = None if self._detail is None else self._detail.due
        self._finish_due_edit(due.due_change(entry, reference=reference, tz=self._zone_hint()))

    def _submit_due(self) -> None:
        """``enter``：日期那一格提交 → 走到时刻；时刻那一格提交 → 这一次改动出去。

        日期那一格**一个字都没敲过**时在这里被拦下：它是「还没填」还是「要清除」在请求体里
        长得一样（``dueDate: null``），而误按一次就把日期删掉是不可接受的（本地当场生效、
        立刻推送）。所以「清除」要求用户真的**改过**那一格——把里面的日期删掉，那是他的
        明确动作；而一片从来没被碰过的空白只是「还没填」。
        """
        try:
            day, at = self._due_draft()
        except ValueError as exc:
            self.show_save(messages.due_invalid_message(exc))
            return
        if day is None and not self._due_date_touched:
            self.show_save(messages.due_invalid_message(DUE_EMPTY_REASON))
            return
        if day is not None and self._due_step == DUE_STEPS[0] and not self._due_all_day:
            self._due_step = DUE_STEPS[1]
            self._show_due_step()
            self.query_one("#due-time", Input).focus()
            return
        self._commit_due(day, at)

    def _escape_due_edit(self) -> None:
        """``esc``（截止编辑器里）：**结束这次编辑**——改动已经生效，没有「取消」（用户故事 62）。

        与 #43 的自由文本框是同一个含义：把用户已经敲进去的东西交出去、回到字段列表。
        草稿有三种状态，各有明确落点，**没有一条是「悄悄丢掉」**：

        1. **日期敲完了、还没走到时刻那一步**：把两格**一起**提交。时刻那一格要么是回填的
           原值、要么是用户敲的值，「留空 = 只有日期」与 ``enter`` 在时刻那一格上的读法
           是同一条（``_show_due_step`` 就写着这句话）。``esc`` 只是不必再按一次 ``enter``
           往前走一步，它不是「跳到第 2 步」。
        2. **有哪一格认不出来**（``2026-02-30``、``18:75`` 这种）：**一个字都不写**，编辑器
           留在原地，下面那一行说清是哪一格不认。与 ``enter`` 同一个规矩：非法输入在发出前
           被本地拦下（验收标准 6）——既不写垃圾，也不是悄悄什么都不做。
        3. **日期那一格从来没被碰过**（连里面的日期都没删过）：没有改动可言，于是没有改动
           可生效。编辑器收起、回到字段列表，下面那一行说出为什么一个字都没写。这一下
           **不是清除**：「还没填」与「要清除」在请求体里是同一个空值，而清除要求用户真的
           动过那一格（``_due_date_touched`` 就是这条分界）。

        ``x`` 的全天开关在每一条真的提交里都跟着走（``_due_all_day`` 进 :meth:`_commit_due`）。
        """
        try:
            day, at = self._due_draft()
        except ValueError as exc:
            self.show_save(messages.due_invalid_message(exc))
            return
        if day is None and not self._due_date_touched:
            self._dismiss_due_edit()
            self.show_save(messages.due_invalid_message(DUE_EMPTY_REASON))
            return
        self._commit_due(day, at)

    def on_input_changed(self, event: Input.Changed) -> None:
        """日期那一格**被用户改过**（清空也算）。

        这是「还没填」与「要清除」之间唯一的分界：两个在请求体里长得一样，所以只能由
        「用户动过这一格没有」来回答。``Input.Changed`` 在程序化回填时也发（Textual 的行为），
        所以比对的是回填那一刻的值——回填自己不算「动过」。
        """
        if event.input.id == "due-date" and event.value != self._due_prefill:
            self._due_date_touched = True

    def _finish_due_edit(self, landed: datetime | None) -> None:
        """收起编辑器并把这一刻交给 app（``DueChanged``）；``landed=None`` 就是清除。"""
        task_id = self._task_id
        all_day = self._due_all_day
        self._due_editing = False
        self._due_step = DUE_STEPS[0]
        self._end_edit()
        if task_id is None:
            return
        self.post_message(self.DueChanged(task_id, landed, all_day))

    def _dismiss_due_edit(self) -> None:
        """收起编辑器、**一个字都不写**（草稿里没有可提交的东西）。

        这**不是**「取消」那条路（用户故事 62：没有取消）：它是 ``esc`` 在第 3 种状态下唯一
        诚实的落点——用户什么都没改过，于是没有任何改动可生效。调用方负责把「为什么一个字
        都没写」说出来；这里悄悄收起是不行的。
        """
        self._due_step = DUE_STEPS[0]
        self._end_edit()

    def _toggle_all_day(self) -> None:
        """``x``：在「有具体时刻」与「只有日期」之间切换（验收标准 2）。

        由 :class:`DueInput` 上那一条绑定调（它把这一下委托过来），所以只有焦点在那两个格子
        里时它才到得了这里——编辑器收起之后这个动作在屏幕上没有入口。
        """
        if not self._due_editing:
            return
        self._due_all_day = not self._due_all_day
        self.query_one("#due-time", Input).display = not self._due_all_day
        self._show_due_step()

    def _editor_value(self) -> str:
        """编辑器里当前那段文字。"""
        field = self._editing
        if field is not None and field.multiline:
            return self.query_one("#detail-text", TextArea).text
        return self.query_one("#detail-input", Input).value

    def _end_edit(self) -> None:
        """收起编辑器，把这一页还给字段列表（三套编辑器共用这一条退出）。

        **幂等**：三套编辑器的退出路径都调它（自由文本框结束编辑、截止时间提交或收起），
        而其中两条可能连着来（提交之后 ``_finish_due_edit`` 立刻收起、接着 app 又刷一次）。
        没有编辑态时它只是把已经藏着的两块再藏一次——不报错，也不多做一件事。
        """
        self._editing = None
        self._due_editing = False
        self.query_one("#due-edit").display = False
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
        """单行输入里按 ``enter`` ＝ 提交（键位表：``enter`` 是「进入下一层 / 提交输入」）。

        截止时间那两格（#44）与自由文本框走同一条：日期那一格提交就轮到时刻，时刻那一格
        提交就把这一刻写出去（``#detail-input`` 那一路仍然是「结束这次编辑」）。
        """
        if self._due_editing:
            self._submit_due()
            return
        if self._editing is not None and not self._editing.multiline:
            self._finish_edit()

    def action_back(self) -> None:
        """``esc``：编辑中结束这次编辑，字段列表上退回任务列表页（验收标准 4 + 5）。

        三种编辑器**同一个含义**：自由文本框（#43）把文字交出去，截止时间（#44）把两格草稿
        交出去（细节见 :meth:`_escape_due_edit`）——**没有「取消」**（用户故事 62）。只有
        光标已经在字段列表上时，``esc`` 才是「退回上一层」。
        """
        if self._due_editing:
            self._escape_due_edit()
            return
        if self._editing is not None:
            self._finish_edit()
            return
        self.post_message(self.Back())

