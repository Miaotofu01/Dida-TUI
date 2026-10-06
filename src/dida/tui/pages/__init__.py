"""三层页面：清单列表页 → 任务列表页 → 任务详细页（``→`` 向下、``←`` 向上）。

一栏、三层页面是 v2 的定位（ADR-0004）：每一层是一个可聚焦的列表，``→`` 进下一层、
``←`` 回上一层（ADR-0008 一：往里走 / 往回走各只有一个键）。v1 的三栏（清单 / 今天 / 详情）连同伴随它的 ``Tab`` 焦点切换、清单浮层与
窄屏降级整块作废——一栏的布局天然不需要降级（用户故事 118）。

每一层自己一个文件、一个变化原因：

============================  ==================================================
模块                            负责什么
============================  ==================================================
:mod:`dida.tui.pages.base`      公共骨架：一列可选行 + 一个跨重画存活的光标 + 滚动
:mod:`dida.tui.pages.index`     层一，清单列表页（#34 建，#35 / #42 扩展）
:mod:`dida.tui.pages.tasks`     层二，任务列表页（#34 建，#37 / #38 / #39 / #40 扩展）
:mod:`dida.tui.pages.detail`    层三，任务详细页（#34 留出接缝，#43 接手）
============================  ==================================================

页面只画**引擎给的事实**：成员与顺序、计数、逾期判定、截止时间读法、可不可进入都在
:mod:`dida.sync.engine` 那边定好了。页面自己不判日期、不数条数、不猜清单 id。
"""

from dida.tui.pages.base import CURSOR_MARK, CursorPage, Row
from dida.tui.pages.detail import DetailPage
from dida.tui.pages.index import IndexPage
from dida.tui.pages.tasks import TasksPage

__all__ = [
    "CURSOR_MARK",
    "CursorPage",
    "DetailPage",
    "IndexPage",
    "Row",
    "TasksPage",
]
