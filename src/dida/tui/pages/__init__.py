"""页面：导航路径上的三层 + 路径之外的个人主页。

三层是清单列表页 → 任务列表页 → 任务详细页（``→`` 向下、``←`` 向上），个人主页从清单
列表页旁路进入、``←`` / ``esc`` 回原处（#91、ADR-0010）——它不在那条平移/压栈的路径上。

一栏、三层导航页是 v2 的定位（ADR-0004）：每一层是一个可聚焦的列表，``→`` 进下一层、
``←`` 回上一层（ADR-0008 一：往里走 / 往回走各只有一个键）。v1 的三栏（清单 / 今天 / 详情）连同伴随它的 ``Tab`` 焦点切换、清单浮层与
窄屏降级整块作废——一栏的布局天然不需要降级（用户故事 118）。

每一页自己一个文件、一个变化原因：

============================  ==================================================
模块                            负责什么
============================  ==================================================
:mod:`dida.tui.pages.base`      公共骨架：一列可选行 + 一个跨重画存活的光标 + 滚动
:mod:`dida.tui.pages.index`     层一，清单列表页（#34 建，#35 / #42 扩展）
:mod:`dida.tui.pages.tasks`     层二，任务列表页（#34 建，#37 / #38 / #39 / #40 扩展）
:mod:`dida.tui.pages.detail`    层三，任务详细页（#34 留出接缝，#43 接手）
:mod:`dida.tui.pages.profile`   第四种页面，个人主页（#91；不在导航路径上）
============================  ==================================================

页面只画**引擎给的事实**：成员与顺序、计数、逾期判定、截止时间读法、可不可进入都在
:mod:`dida.sync.engine` 那边定好了。页面自己不判日期、不数条数、不猜清单 id。个人主页
同理：绑定状态是组合根交给 app 的**一位事实**，页面不读配置、也不判断绑没绑（#91）。
"""

from dida.tui.pages.base import CURSOR_MARK, CursorPage, Row
from dida.tui.pages.detail import DetailPage
from dida.tui.pages.index import IndexPage
from dida.tui.pages.profile import ProfilePage
from dida.tui.pages.tasks import TasksPage

__all__ = [
    "CURSOR_MARK",
    "CursorPage",
    "DetailPage",
    "IndexPage",
    "ProfilePage",
    "Row",
    "TasksPage",
]
