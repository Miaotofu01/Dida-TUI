"""第四种页面：个人主页——**不在导航路径上**（ADR-0010、工单 #91）。

从清单列表页按 ``A`` 旁路进来，``←`` / ``esc`` 回原处；顶栏在这一页只有「个人主页」一段。
它不回答「我在哪一叠任务里」，而是回答「这个 app 连着哪个账号」——所以它放账号绑定，
不放任务。

**它不是 ``Stage`` 上的第四页**：横向平移是导航栈的表达，而这一页不在那条栈上。所以它
与 ``#stage`` 并列挂在 app 上，用 ``display`` 藏 / 显；平移轨道始终停在原来那一页，
退回来时清单列表页的光标也还在原来那一行。

**这一页只说组合根交给它的那一位事实**（已绑定 / 未绑定）。官方 API 没有「当前用户」
端点（GLOSSARY 的「账号绑定」），所以这一页说不出、也不画「绑到了谁身上」——没有用户名、
邮箱、头像、token 片段。
"""

from __future__ import annotations

from textual.message import Message

from dida.tui.keys import LAYER_PROFILE, bindings_for
from dida.tui.pages.base import CursorPage, Row, rule_row

__all__ = ["ProfilePage"]


class ProfilePage(CursorPage):
    """个人主页：一条页面规则线 + 账号绑定那一行（换绑入口是 #92，这一页不预留）。"""

    LAYER = LAYER_PROFILE
    BINDINGS = bindings_for(LAYER)

    class Back(Message):
        """用户按了 ``←`` / ``esc``：退回清单列表页（个人主页不在导航栈上，只回原处）。"""

    def on_mount(self) -> None:
        """把这一页的内容铺上屏（它不随刷新变，所以只铺一次）。"""
        self.set_rows(self._build())

    def _build(self) -> tuple[Row, ...]:
        """这一页只有一条通栏细线（与另外几页同一个开头），内容行在下一票里长出来。"""
        return (rule_row(),)

    def action_back(self) -> None:
        """``←``（``left``）与 ``esc``：回清单列表页。

        这一页不在导航路径上，所以没有「上一层」可弹——回的是**原处**（ADR-0010 一）。
        光标留在原来那一行由 ``app.back_to_index()`` 保证：清单列表页一直留在 DOM 里。
        """
        self.post_message(self.Back())
