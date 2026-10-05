"""浮层：盖在当前那一层上面的一小块界面。

**这个文件是 #42 / #36 的接缝**（wave-plan：「谁先落地谁把浮层壳子搭出来」——#42 先落地，
所以清单的建/改表单与视图的过滤条件表单由它在**这个文件**里搭出来，#36 复用）。
#34 先在这里放下所有人都要用的那两件：

- :class:`MessageOverlay` —— 一块只读的正文（``?`` 的键位帮助就挂在它上面）；
- :class:`ConfirmOverlay` —— 一句提示 + ``y`` / ``n``（删除、退出那两处「先问一句」）。

浮层是**模态**的：它开着的时候 ``j``/``k`` 到不了下面那一层。所以 ``esc`` 在浮层里是
「关掉浮层」，不是「退回上一层」——两者不能混。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from dida.tui import theme

__all__ = ["ConfirmOverlay", "MessageOverlay"]

# 浮层的全部外观只在 dida.tui.theme 一处（工单 #51）：这个文件从此不出现任何颜色值。
# 抬起来的面（槽 0）、那条 ascii 细边框、以及「不做遮罩压暗」的理由都写在主题里；
# 每个类各自向主题要一份壳子（theme.overlay_css(<类名>)），两个类因此长得一模一样。


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
