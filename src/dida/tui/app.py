"""今日执行台的三栏外壳。

TUI 只通过 :class:`~dida.sync.engine.Engine` 读写；分组、排序、逾期判定、截止时间
读法全部留在引擎里，这里只把视图模型画出来，不做任何业务判断。

一启动就读本地缓存渲染（:meth:`DidaApp.refresh_view`）——网络不是这一屏的前置条件。
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer

from dida.sync.engine import Engine
from dida.tui.panes import DetailPane, ListPane, StatusBar, TaskPane, format_status


class DidaApp(App[None]):
    """三栏 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助归 t18
    # 非 priority：焦点在输入框里时 q 应当是普通字符（t15/t17 的输入框）
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("g", "defer", "顺延"),
        Binding("G", "defer_week", "顺延一周"),
    ]
    CSS = """
    #panes {
        height: 1fr;
    }
    Pane {
        border: round ansi_cyan;
        height: 1fr;
    }
    #list-pane {
        width: 20;
    }
    #task-pane {
        width: 1fr;
    }
    #detail-pane {
        width: 34;
    }
    #status-bar {
        height: 1;
        color: ansi_cyan;
    }
    """

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            yield ListPane(id="list-pane")
            yield TaskPane(id="task-pane")
            yield DetailPane(id="detail-pane")
        yield Footer()
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self.refresh_view()
        self.query_one(TaskPane).focus()  # 一进来 j/k 就能过任务；Tab 换到左栏

    def refresh_view(self) -> None:
        """读引擎的视图模型，重画三栏与状态栏。t09/t10/t11 在数据变化后调用。"""
        view = self.engine.view()
        self.query_one(ListPane).render_lists(view.lists)
        self.query_one(TaskPane).render_groups(view.groups)
        self.update_status()

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏。t05/t09/t21 在数据变化后调用。"""
        self.query_one(StatusBar).update(format_status(self.engine.status()))

    def action_defer(self) -> None:
        """``g``：把光标下那条任务顺延到下一个逻辑日。"""
        self._defer(days=1)

    def action_defer_week(self) -> None:
        """``G``：顺延到下周同一天（同一个星期几）。"""
        self._defer(days=7)

    def _defer(self, *, days: int) -> None:
        """顺延光标下那条任务，然后重画。

        落点由引擎按逻辑日算（TUI 不碰日界）；光标下没有任务就什么都不做——空屏上按键
        不该报错。``days`` 是逻辑日数：``g`` 1 天、``G`` 7 天。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.engine.defer(task_id, days=days)
        self.refresh_view()
