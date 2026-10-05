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
from dida.tui.keys import QUIT_ACTION, QUIT_KEYS

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

FORM_QUIT_BINDINGS = [
    Binding(key, f"app.{QUIT_ACTION}", "退出", priority=True, show=False)
    for key in QUIT_KEYS
    if not (len(key) == 1 and key.isalpha())
]
"""表单上的退出键：**只取** :data:`~dida.tui.keys.QUIT_KEYS` 里不是字母的那些（工单 #42）。

与 :data:`QUIT_BINDINGS`（上面那两张只读浮层用的那一份）差两处，两处都是故意的：

- **字母不绑**：表单里 ``q`` 必须是一个字母（清单可以叫 ``quizzes``），而字母属于那一格。
  出口是 ``Esc``。真按 ``q`` 的地方（选择框上）如果突然退出，用户填了一半的东西就没了。
- **``priority=True``**：``Screen.BINDINGS`` 上那条 ``ctrl+c`` = ``screen.copy_text`` 在绑定
  链里更靠前，没选中东西时它抛 ``SkipAction``、有选中就复制——不压过它，同一个键就会看
  「用户有没有选中文字」行事。表单里 Ctrl+C 因此**永远是退出**，代价是没有 Ctrl+C 复制。
"""

FORM_HINT = "Tab 换一格 / 选择框用左右方向键 / Enter 确认 / Esc 取消 / Ctrl+C 退出"
"""表单底部那行提示：写的都是终端一定传得上来的键（spec 的键位表）。

分隔符用 ASCII 的 ``/``、方向键写「左右方向键」而不是 ``←``/``→``：那两个字是东亚**歧义**
宽度（rich 量 1 格、CJK 字体下终端可能画 2 格），浮层这一行没有对齐列，但同一条规矩在这里
一样成立——#48 已经因为同样的理由把 ``↑↓`` 从帮助里拿掉了。
"""


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

    ### 退出键：``Ctrl+C`` 照旧退出，``q`` 不绑

    表单开着时 ``Ctrl+C`` **照旧退出**（有待推送改动时照旧先问一句），与程序里其它每一屏
    一致；代价是表单里没有 ``Ctrl+C`` 复制。理由是同一条：**一个键在整个程序里只有一个
    意思**比在表单里多一个复制功能值钱，而且这里没有「有时复制、有时退出」的中间状态——
    靠的是 ``priority=True``（没有它，焦点在输入框上时 ``Input`` 自己那条 ``ctrl+c`` =
    复制会先赢，同一个键就成了看焦点行事）。

    这一条与 Textual 的默认取舍相反：它把 ``ctrl+c`` 绑给 ``copy``（``Screen.BINDINGS``
    上那条 ``screen.copy_text``，在绑定链里**更靠前**——没选中东西时它抛 ``SkipAction``，
    于是才轮得到后面那条），理由正是「文本框里的 Ctrl+C 不要杀 app」。:data:`FORM_QUIT_BINDINGS`
    的 ``priority=True`` 让这一层不参与那条竞争：**表单里 Ctrl+C 永远是退出**，既不随焦点走，
    也不随「用户有没有选中东西」走（两种状态都测：``tests/test_list_overlay.py``，其中一态
    就是 ``e`` 打开时那一格的 ``select_on_focus`` 全选）。代价是表单里没有 ``Ctrl+C`` 复制
    ——一个键在整个程序里只有一个意思，比在表单里多一个复制功能值钱。这里选的是反过来的
    那一半，所以它**必须**写下来——#36 复用这张壳子时，默认就是这一条。

    ``q`` **不绑**：表单里 ``q`` 必须是一个字母（清单可以叫 ``quizzes``），而字母属于那一格。
    表单自己的出口是 ``Esc``（底部那行提示写着它）：``Input`` 的绑定里没有 ``escape``，
    所以它在输入框拿到焦点时照样到达这一层。
    """

    DEFAULT_CSS = theme.form_css("FormOverlay")

    BINDINGS = [
        # 退出键必须**绑在这一层**：浮层的键位解析截断在最后一个浮层控件上，app 的绑定在
        # 浮层开着时够不着（#47 实测）。表单只取其中不是字母的那些，理由见 FORM_QUIT_BINDINGS。
        *FORM_QUIT_BINDINGS,
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

QUIT_BINDINGS = [Binding(key, f"app.{QUIT_ACTION}", "退出", show=False) for key in QUIT_KEYS]
"""浮层上的退出键（工单 #47）——转发到 **app 上那一个** :meth:`~dida.tui.app.DidaApp.action_quit`。

**为什么浮层要自己绑这一遍**：浮层是模态的，Textual 按键解析只走
``_modal_binding_chain``，而那条链在**最后一个模态控件**处截断——``app._bindings`` 因此
进不来。``q`` 在浮层上按不出来（它只绑在页面与 ``Screen`` 上），``Ctrl+C`` 更绕：Textual
把 ``ctrl+c`` 绑在 ``Screen.BINDINGS`` 的 ``screen.copy_text`` 上，而 ``Screen`` 恰好不在
模态链里——于是同一个键在清单列表页上能退出、在 ``?`` 帮助浮层上只是「复制一段文字」。
**那正是本票要关掉的第二条退出路径**：按了退出键，什么都没发生。

``app.`` 前缀是 Textual 的动作命名空间（``App._action_targets`` 里的 ``app`` 指向 app
自己），所以这一条与页面上的 ``q`` / ``Ctrl+C`` 落到**同一个方法**上——不是第二个判断。
只写动作名（``"quit"``）不行：那会解析到浮层自己头上，而浮层没有 ``action_quit``，于是
这条绑定「命中但什么也不做」——按键被吃掉，app 反而再也收不到它。
"""


class MessageOverlay(ModalScreen[None]):
    """一块只读正文的浮层：``Esc`` / ``Enter`` 关掉，退出键照旧交回 app。

    正文由调用方拼好原样交给它（``?`` 那一屏是 :func:`dida.tui.keys.help_body`），所以
    控件不认识键位表、也不认识任务——换一块正文不必动它。
    """

    TITLE = ""

    DEFAULT_CSS = theme.overlay_css("MessageOverlay")

    BINDINGS = [
        *QUIT_BINDINGS,
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
        *QUIT_BINDINGS,
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
