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

FLASH_SECONDS = 0.45
"""完成后那一行高亮多久：够看清这一下生效了，又不至于拖住下一次分诊。"""


class DidaApp(App[None]):
    """三栏 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助归 t18
    # 非 priority：焦点在输入框里时 q 应当是普通字符（t15/t17 的输入框）
    BINDINGS = [
        Binding("q", "quit", "退出"),
        # 完成在服务端不可逆（ADR-0002）：x 在主键区下面那一行，与 j/k 隔着整行。防误按
        # 是这个动作唯一的补偿；footer 上带标签显示，看得见才按得准。
        Binding("x", "complete", "完成"),
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

    def action_complete(self) -> None:
        """完成光标下的任务并立即推送（`x`）。

        ADR-0002：服务端**没有**「取消完成」接口，这一次按键是不可撤销的事实，所以这里
        只做一条路——交给引擎（乐观写 + 立即推送 + 进重试队列），不做任何本地的反向操作。

        完成之后分两步走：先让这一行亮一下（``flash``），再按本地结果重画（真引擎下这一行
        已经不在未完成里了）。两步都在这根线程上，读的都是同一份本地状态。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:  # 空屏上按 x：什么都不做，不是错误
            return
        self.engine.complete(task_id)
        self.query_one(TaskPane).flash(task_id)
        self.set_timer(FLASH_SECONDS, self._settle_after_complete)

    def _settle_after_complete(self) -> None:
        """收起高亮，并按引擎的当前视图重画。定时器到点就调这一次。"""
        self.query_one(TaskPane).flash(None)
        self.refresh_view()

    def refresh_view(self) -> None:
        """读引擎的视图模型，重画三栏与状态栏。t09/t10/t11 在数据变化后调用。"""
        view = self.engine.view()
        self.query_one(ListPane).render_lists(view.lists)
        self.query_one(TaskPane).render_groups(view.groups)
        self.update_status()

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏。t05/t09/t21 在数据变化后调用。"""
        self.query_one(StatusBar).update(format_status(self.engine.status()))
