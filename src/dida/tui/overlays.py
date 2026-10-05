"""浮层：盖在当前那一层上面的一小块界面。

**这个文件是 #42 / #36 的接缝**（wave-plan：「谁先落地谁把浮层壳子搭出来」——#42 先落地，
所以清单的建/改表单与视图的过滤条件表单由它在**这个文件**里搭出来，#36 复用）。
#34 先在这里放下所有人都要用的那两件：

- :class:`MessageOverlay` —— 一块只读的正文（``?`` 的键位帮助就挂在它上面）；
- :class:`ConfirmOverlay` —— 一句提示 + ``y`` / ``n``（删除、退出那两处「先问一句」）。

#42 在这里补上第三件：**表单浮层** :class:`FormOverlay`。它与上面两件的分别只有一处：
字段是**参数**。哪些字段、每个字段叫什么、是文本框还是选择框，全部由调用方给——
清单的建/改给「名字 + 颜色」，视图的条件给「清单范围 + 日期区间 + 优先级 + 标签 + 完成状态」
（#36），壳子本身**不认识清单也不认识视图**：它只负责画出来、收下来、把一份
``{字段名: 值}`` 交回去。

浮层是**模态**的：它开着的时候 ``j``/``k`` 到不了下面那一层。所以 ``esc`` 在浮层里是
「关掉浮层」，不是「退回上一层」——两者不能混。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from dida.tui import theme

__all__ = [
    "FORM_HINT",
    "ChoiceField",
    "ConfirmOverlay",
    "FormField",
    "FormOption",
    "FormOverlay",
    "MessageOverlay",
]

# 浮层的全部外观只在 dida.tui.theme 一处（工单 #51）：这个文件从此不出现任何颜色值。
# 抬起来的面（槽 0）、那条 ascii 细边框、以及「不做遮罩压暗」的理由都写在主题里；
# 每个类各自向主题要一份壳子（theme.overlay_css(<类名>) / theme.form_css(<类名>)）。

FORM_HINT = "Tab 换一格 · 选择框用 ←/→ · Enter 确认 · Esc 取消"
"""表单底部那行提示：三个键位都是终端一定传得上来的（spec 的键位表）。"""


@dataclass(frozen=True)
class FormOption:
    """选择框里的一档：``value`` 是交回去的东西，``label`` 是屏幕上写的字。"""

    value: str
    label: str


@dataclass(frozen=True)
class FormField:
    """表单里的一格：名字 + 显示的名字 + （选择框才有）几档可选 + 当前值。

    ``options`` 是空的就画一个文本框；非空就画一个选择框。这是这个壳子唯一认识的
    「字段类型」，加一种类型（比如多选）就是在这里加一个判断——调用方给字段，不给控件。
    """

    name: str
    """这一格的身份：交回去的那份 ``{字段名: 值}`` 用它作键。"""

    label: str
    """屏幕上写的字段名（如「名字」「颜色」）。"""

    options: tuple[FormOption, ...] = ()
    """选择框的几档；空元组 = 文本框。"""

    value: str = ""
    """当前值（文本框是原文，选择框是选中那一档的 ``value``）。

    认不出来的值**照原样留着**（:class:`ChoiceField` 会把它自己加成一档）：清单上那个
    手机端挑的颜色不在客户端这一档里时，改个名字不该顺手把它换掉。
    """

    @property
    def is_choice(self) -> bool:
        """这一格是不是选择框。"""
        return bool(self.options)


class ChoiceField(Static):
    """一格可选值：焦点在它上面时 ``←`` / ``→`` 换一档。"""

    can_focus = True
    """`Static` 默认不可聚焦，而这一格要能拿到方向键。"""

    BINDINGS = [
        Binding("left", "previous", "上一档", show=False),
        Binding("right", "next", "下一档", show=False),
    ]

    def __init__(self, field: FormField) -> None:
        super().__init__(id=f"field-{field.name}")
        self.field = field
        self._options = list(field.options)
        if field.value and field.value not in [option.value for option in self._options]:
            # 认不出来的值自成一档，原样显示、原样交回去：清单上那个颜色是用户在手机端挑的，
            # 客户端这一档里没有它，也不该因此把它换掉（与「改名不许重置 sortOrder」同一条）。
            self._options.append(FormOption(field.value, field.value))
        self._index = next(
            (index for index, option in enumerate(self._options) if option.value == field.value),
            0,
        )
        self._redraw()

    @property
    def value(self) -> str:
        """当前选中的那一档的值（与 ``Input.value`` 同名：表单按字段名取值时一视同仁）。"""
        return self._options[self._index].value

    def action_next(self) -> None:
        """``→``：下一档（到底了绕回第一档）。"""
        self._index = (self._index + 1) % len(self._options)
        self._redraw()

    def action_previous(self) -> None:
        """``←``：上一档。"""
        self._index = (self._index - 1) % len(self._options)
        self._redraw()

    def _redraw(self) -> None:
        """画成 ``< 当前这一档的名字 >``。

        尖括号是 **ASCII**：结构字形不许用东亚歧义宽度的符号（ADR-0007 的字形一节），
        而这一格将来会跟别的字段对齐。
        """
        line = Text()
        line.append("< ", style=theme.MUTED)
        line.append(self._options[self._index].label, style=theme.HEADING)
        line.append(" >", style=theme.MUTED)
        self.update(line)


class FormOverlay(ModalScreen[dict[str, str] | None]):
    """一张表单：字段由调用方给，填完 ``Enter`` 交回 ``{字段名: 值}``。

    ``Esc`` 交回 ``None``（取消），**什么都不写**——这一层不认识引擎，也不认识清单，
    所以「取消」在这里是纯粹的「没发生」；写不写由调用方按 ``None`` 判断。

    字段的顺序就是屏幕上的顺序，第一格自动拿到焦点（打开就能打字）。
    ``Tab`` / ``shift+Tab`` 在字段之间走（Textual 给每一层都绑了这两个键），
    ``Enter`` 在文本框里由 :class:`~textual.widgets.Input` 自己吃掉（它绑了 ``enter``），
    所以这里收的是它发上来的 ``Submitted``——两条路都落到同一个确认上。

    文本框打开时那一格的值是**全选**的（Textual ``Input`` 的 ``select_on_focus``）：直接
    打字就是换掉整个名字，像文件管理器里的重命名；要接着改就先把光标挪进去。

    ## 键位为什么从这里出发

    #47 实测：浮层的键位解析**截断在最后一个浮层控件上**——浮层开着时 ``App.BINDINGS``
    够不着。所以这一层需要的每一个键都必须绑在**它自己**身上，而且要用 Textual 的动作
    命名空间（``"app.quit"`` 那种），写一个裸 ``"quit"`` 会解析到浮层自己身上、没有那个
    ``action_``、**键被静默吃掉**。这里的确认与取消都是本层的动作，所以是裸名字；
    :class:`ChoiceField` 的左右键同理。

    **这张表单刻意不绑退出键**（``q`` / ``Ctrl+C``），与上面两张浮层不同——那两张是只读
    的，``q`` 在那里没有别的意思；而表单里 ``q`` 必须是**一个字母**（清单可以叫
    ``quizzes``），``Ctrl+C`` 在文本框里是 Textual 特意留给**复制**的（它的 ``Input`` 绑了
    ``copy``，理由正是「别让文本框里的 Ctrl+C 杀掉 app」）。派生的规矩是一条、不随焦点变：
    **表单开着时 Ctrl+C 永远不退出**——焦点在输入框就是复制，在选择框上什么都不做。
    出口是 ``Esc``（底部那行提示写着它），回到清单列表页之后 ``q`` 照旧。
    """

    DEFAULT_CSS = theme.form_css("FormOverlay")

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
        Binding("enter", "confirm", "确认"),
    ]

    def __init__(
        self, *, title: str, fields: Sequence[FormField], hint: str = FORM_HINT
    ) -> None:
        super().__init__()
        self.fields = tuple(fields)
        """这一张表单的字段，按屏幕顺序。"""

        self._title = title
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay-box") as box:
            box.border_title = self._title
            for field in self.fields:
                yield Static(field.label, classes="overlay-label")
                if field.is_choice:
                    yield ChoiceField(field)
                else:
                    yield Input(value=field.value, id=f"field-{field.name}")
            yield Static(self._hint, classes="overlay-hint")

    def values(self) -> dict[str, str]:
        """每一格现在的值，按字段名索引（文本框与选择框都读 ``.value``）。"""
        return {
            field.name: self.query_one(f"#field-{field.name}", (Input, ChoiceField)).value
            for field in self.fields
        }

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """在文本框里按 ``Enter``：那就是确认（``Input`` 把那个键绑给了 ``submit``）。"""
        self.action_confirm()

    def action_confirm(self) -> None:
        """``Enter``：交回填好的这一份。"""
        self.dismiss(self.values())

    def action_cancel(self) -> None:
        """``Esc``：取消，交回 ``None``（一个字节都不写）。"""
        self.dismiss(None)


class MessageOverlay(ModalScreen[None]):
    """一块只读正文的浮层：``Esc`` / ``Enter`` 关掉。

    正文由调用方拼好原样交给它（``?`` 那一屏是 :func:`dida.tui.keys.help_body`），所以
    控件不认识键位表、也不认识任务——换一块正文不必动它。
    """

    TITLE = ""

    DEFAULT_CSS = theme.overlay_css("MessageOverlay")

    BINDINGS = [
        Binding("escape", "close", "关闭", show=False),
        Binding("enter", "close", "关闭"),
    ]

    def __init__(self, body: str) -> None:
        super().__init__()
        self.body = body
        """正文，原样显示。"""

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="overlay-box") as box:
            box.border_title = self.TITLE
            yield Static(self.body, classes="overlay-body")

    def action_close(self) -> None:
        """``Esc`` / ``Enter``：收起浮层，回到下面那一层。"""
        self.dismiss(None)


class ConfirmOverlay(ModalScreen[bool]):
    """一句提示 + 一个 Yes/No：``y`` 确认、``n`` 与 ``Esc`` 取消。

    这一层只负责**问**：提示语原文进来、按键结果 ``dismiss(True/False)`` 出去。它不认识
    任务、也不认识引擎，所以「退出前拦一下」与「删除前问一句」用的是同一个控件。

    为什么这值得一次按键：这两处问的都是**不可挽回**的事。滴答清单的 Open API 里没有
    undelete、没有回收站、也没有「已删除」列表，所以删除那一次确认就是全部的防线；
    待推送改动只活在本地库里，退出前问一句才不会让用户以为「按了 q 就等于做完了」。
    ``Esc`` 一律走取消：默认答案永远是「没做」。
    """

    DEFAULT_CSS = theme.overlay_css("ConfirmOverlay")

    BINDINGS = [
        Binding("y", "confirm", "确认"),
        Binding("n", "cancel", "取消"),
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, prompt: str, *, title: str = "确认") -> None:
        super().__init__()
        self.prompt = prompt
        """提示语原文，原样显示。"""
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay-box") as box:
            box.border_title = self._title
            yield Static(self.prompt, classes="overlay-body")

    def action_confirm(self) -> None:
        """``y``：确认，浮层关掉并把 ``True`` 交回调用方。"""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """``n`` / ``Esc``：取消，什么都不做（``False``）。"""
        self.dismiss(False)
