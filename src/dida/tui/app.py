"""今日执行台的三栏外壳。

TUI 只通过 :class:`~dida.sync.engine.SyncEngine` 读写；分组、排序、逾期判定
全部留在引擎里，这里不做任何业务判断。内容由 t05/t18 替换。
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer

from dida.sync.engine import SyncEngine
from dida.tui.panes import DetailPane, ListPane, StatusBar, TaskPane, format_status


class DidaApp(App[None]):
    """三栏 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助归 t18
    # 非 priority：焦点在输入框里时 q 应当是普通字符（t15/t17 的输入框）
    BINDINGS = [Binding("q", "quit", "退出")]
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
    .pane-placeholder {
        color: ansi_bright_black;
    }
    #status-bar {
        height: 1;
        color: ansi_cyan;
    }
    """

    def __init__(self, engine: SyncEngine) -> None:
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
        self.update_status()

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏。t05/t09/t21 在数据变化后调用。"""
        self.query_one(StatusBar).update(format_status(self.engine.status()))
