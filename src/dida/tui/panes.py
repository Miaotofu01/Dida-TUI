"""三栏与状态栏的骨架。

本工单只立起栏位身份与占位内容：内容渲染归 t05（读缓存渲染）、
窄屏降级与详情浮层归 t18。颜色一律用终端 16 色（``ansi_*``），不写死 hex。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from dida.sync.engine import SyncStatus

PLACEHOLDER = "（未接入）"
"""占位内容：栏位身份先立起来，内容由后续工单替换。"""


class Pane(VerticalScroll):
    """三栏中的一栏：边框标题 + 占位内容。"""

    BORDER_TITLE = ""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.border_title = self.BORDER_TITLE

    def compose(self) -> ComposeResult:
        yield Static(PLACEHOLDER, classes="pane-placeholder")


class ListPane(Pane):
    """清单栏（左）：清单与数量。"""

    BORDER_TITLE = "清单"


class TaskPane(Pane):
    """任务列（中）：分组后的任务。"""

    BORDER_TITLE = "今日"


class DetailPane(Pane):
    """详情栏（右）：当前任务。"""

    BORDER_TITLE = "详情"


class StatusBar(Static):
    """状态栏：已同步时刻、待推送数量、当前逻辑日。"""


def format_status(status: SyncStatus) -> str:
    """状态栏文本。措辞按 GLOSSARY：逻辑日 / 待推送 / 已同步。"""
    logical_day = status.logical_day.strftime("%m-%d") if status.logical_day else "—"
    last_refresh = status.last_refresh_at.strftime("%H:%M") if status.last_refresh_at else "—"
    return f"已同步 {last_refresh} · 待推送 {status.pending_count} · 逻辑日 {logical_day}"
