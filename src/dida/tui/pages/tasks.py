"""层二：任务列表页——当前清单（或视图）里的全部任务。

**这一页先只画标题**：行的样子（优先级标记、人类可读的截止时间、标签、重复与提醒标记、
已完成划掉沉底、排序）归 #37 那张工单。这里定下来的是**结构**：进得来、光标能在任务之间
走、``enter`` 进详细页、``esc`` 退回清单列表页、空清单有一句明确的空态文案（用户故事 53）、
后台刷新不把光标踢回第一条（用户故事 57）。

「当前清单的全部任务」是引擎给的（``Engine.tasks_in``）：**未来截止的也在**（v1 把它们
整条丢掉了），已完成的那些在窗口里的也在（用户故事 54/55/56），由引擎按 ``status`` 判、
按完成时间裁窗口——这一页不判日期、不数条数。
"""

from __future__ import annotations

from rich.text import Text
from textual.message import Message

from dida.sync.engine import CompletedItem, TaskItem, TaskList
from dida.tui import messages
from dida.tui.keys import LAYER_TASKS, bindings_for
from dida.tui.pages.base import CursorPage, Row, empty_row

__all__ = ["COMPLETED_STYLE", "TasksPage", "task_line"]

COMPLETED_STYLE = "dim strike"
"""已完成的行：暗灰 + 删除线（用户故事 54）。

删除线是「这条已经做完了」的读法，而它**还在列表里**——用户才有机会对它的取消完成
（那是 #38 的 ``space``，本页先把它们摆出来并让光标越过：已完成的行不参与光标移动）。
"""


def task_line(item: TaskItem) -> Text:
    """一条任务的行。

    这一票只画标题——行的样子归 #37。标题是引擎给的成品，这一层不加工。
    """
    return Text(item.title)


def completed_line(item: CompletedItem) -> Text:
    """已完成的一条：暗灰 + 删除线（沉在列表最底，见 ``Engine.tasks_in`` 的排序）。"""
    return Text(f"☑ {item.title}", style=COMPLETED_STYLE)


class TasksPage(CursorPage):
    """任务列表页：某个清单或视图里的全部任务。"""

    LAYER = LAYER_TASKS
    BINDINGS = bindings_for(LAYER_TASKS)
    EMPTY_TEXT = messages.EMPTY_TASKS_MESSAGE

    class Entered(Message):
        """用户按了 ``enter``：进这条任务的详细页。"""

        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            super().__init__()

    class Back(Message):
        """用户按了 ``esc``：退回清单列表页（光标还原到进来的那一行）。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._name = ""

    @property
    def container_name(self) -> str:
        """屏幕上这条标题里写的容器名（视图名或清单名）。"""
        return self._name

    def show_tasks(self, task_list: TaskList, *, name: str) -> None:
        """铺开一个容器的任务：标题 + 未完成任务 + 已完成的那几条。"""
        self._name = name
        rows: list[Row] = [Row(id=None, text=Text(f"── {name} ──", style="bold"))]
        rows += [Row(id=item.task_id, text=task_line(item)) for item in task_list.items]
        rows += [Row(id=None, text=completed_line(item)) for item in task_list.completed.items]
        if len(rows) == 1:
            # 空清单要说一句明确的话，而不是只留一个标题（用户故事 53）。
            rows.append(empty_row(self.EMPTY_TEXT))
        self.set_rows(rows)

    def action_enter(self) -> None:
        """``enter``：进光标下那条任务的详细页。"""
        if self.selected_id is not None:
            self.post_message(self.Entered(self.selected_id))

    def action_back(self) -> None:
        """``esc``：退回清单列表页。"""
        self.post_message(self.Back())
