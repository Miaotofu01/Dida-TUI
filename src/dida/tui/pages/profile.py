"""第四种页面：个人主页——**不在导航路径上**（ADR-0010、工单 #91）。

从清单列表页按 ``A`` 旁路进来，``←`` / ``esc`` 回原处；顶栏在这一页只有「个人主页」一段。
它不回答「我在哪一叠任务里」，而是回答「这个 app 连着哪个账号」——所以它放账号绑定，
不放任务。

**它不是 ``Stage`` 上的第四页**：横向平移是导航栈的表达，而这一页不在那条栈上。所以它
与 ``#stage`` 并列挂在 app 上，用 ``display`` 藏 / 显；平移轨道始终停在原来那一页，
退回来时清单列表页的光标也还在原来那一行。

**这一页只说组合根交给它的那一位事实**（已绑定 / 未绑定）。官方 API 没有「当前用户」
端点（GLOSSARY 的「账号绑定」），所以这一页说不出、也不画「绑到了谁身上」——没有用户名、
邮箱、头像、token 片段。换绑入口是它名下唯一的动作（#92）：键位 ``b``，``h`` 里说得出来。
"""

from __future__ import annotations

from rich.text import Text
from textual.message import Message

from dida.tui.keys import LAYER_PROFILE, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, rule_row

__all__ = [
    "BINDING_LABEL",
    "BOUND_TEXT",
    "REBIND_HINT",
    "UNBOUND_TEXT",
    "ProfilePage",
    "account_line",
]

BINDING_LABEL = "账号绑定"
"""这一页唯一那一项的标签（官方 API 分得清的只有这一件事）。"""

BOUND_TEXT = "已绑定"
"""有可用 token。"""

UNBOUND_TEXT = "未绑定"
"""还没有 token（首次运行的信号）。"""

REBIND_HINT = "b 换绑"
"""账号绑定那一行末尾的入口提示（工单 #92）。

写在这一行上而不是只放在 ``h`` 里：``h`` 是「我想知道这一层能按什么」时去查的地方，不是
发现功能的地方。键位本身仍在 :mod:`dida.tui.keys` 的表里，这一句只是同一个事实的展示——
``b`` 与 ``Key(("b",), "rebind", …)`` 对不上会由 ``h`` 那几条守卫当场抓出来。
"""


def account_line(bound: bool, *, rebind_hint: str = REBIND_HINT) -> Text:
    """「账号绑定」那一行：标签 + **这一位事实** + 换绑入口，没有别的。

    值用暗色那一档（它是注解，不是光标行）：这一页没有可停光标的行，整页都不该有一行
    看起来像「可以按进去」。换绑入口也写暗色，它只是一个键位提示，不是一行可选的东西。

    ``rebind_hint=""`` 就是**不写入口**：引擎上没有换绑端口时（直接 new 一个引擎的测试）
    这一页不该放一个按下去没反应的入口（#91 的注意、#92 的契约）。
    """
    line = Text(f"{BINDING_LABEL}  ")
    line.append(BOUND_TEXT if bound else UNBOUND_TEXT, style=EMPTY_STYLE)
    if rebind_hint:
        line.append(f"   {rebind_hint}", style=EMPTY_STYLE)
    return line


class ProfilePage(CursorPage):
    """个人主页：一条页面规则线 + 账号绑定那一行。"""

    LAYER = LAYER_PROFILE
    BINDINGS = bindings_for(LAYER)

    class Back(Message):
        """用户按了 ``←`` / ``esc``：退回清单列表页（个人主页不在导航栈上，只回原处）。"""

    class Rebind(Message):
        """用户按了 ``b``：要换绑账号（工单 #92）。

        这一页只**发消息**，不自己开浮层、不碰引擎：换绑那张表单与后面的验证 / 确认 / 执行
        都在 app 那一层（它才握着引擎上的换绑端口）。与 :class:`Back` 同一种分工。
        """

    def __init__(
        self,
        *,
        account_bound: bool = False,
        can_rebind: bool = True,
        id: str | None = None,
    ) -> None:
        """``account_bound`` 是组合根交进来的**事实**，这一页不判断、也不去读配置。

        ``can_rebind`` 也一样是注入的：引擎上没有换绑端口时它说 ``False``，这一行就不再写
        「b 换绑」这个入口（入口不能是一个按下去没反应的东西——#91 / #92）。
        """
        super().__init__(id=id)
        self.account_bound = account_bound
        self.can_rebind = can_rebind

    def on_mount(self) -> None:
        """把这一页的内容铺上屏（它不随刷新变，所以只铺一次）。"""
        self.set_rows(self._build())

    def _build(self) -> tuple[Row, ...]:
        """顶上一条通栏细线（与另外几页同一个开头），下面是唯一的那一项。

        ``id=None``：这一行**不可停光标**——换绑是一个键（``b``），不是「按进这一行」。
        光标标记与强调色在这里只会让人以为要用 ``→`` 进去。
        """
        hint = REBIND_HINT if self.can_rebind else ""
        return (rule_row(), Row(id=None, text=account_line(self.account_bound, rebind_hint=hint)))

    def action_rebind(self) -> None:
        """``b``：请求换绑（#92）。真正的流程在 app 那一层，这一页只递一句话。"""
        self.post_message(self.Rebind())

    def action_back(self) -> None:
        """``←``（``left``）与 ``esc``：回清单列表页。

        这一页不在导航路径上，所以没有「上一层」可弹——回的是**原处**（ADR-0010 一）。
        光标留在原来那一行由 ``app.back_to_index()`` 保证：清单列表页一直留在 DOM 里。
        """
        self.post_message(self.Back())
