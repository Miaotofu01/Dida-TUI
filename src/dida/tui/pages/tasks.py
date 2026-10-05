"""层二：任务列表页——当前清单（或视图）里的全部任务。

**这一页只画标题（+ 视图里的清单名 + 逾期标红，#35）**：行的其余样子（优先级标记、人类可读的
截止时间、标签、重复与提醒标记、窄屏下的丢弃顺序）归 #37 那张工单。这里定下来的是**结构**：进得来、光标能在任务之间
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
from dida.tui import messages, theme
from dida.tui.keys import LAYER_TASKS, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, heading_row, rule_row

__all__ = ["COMPLETED_STYLE", "DONE_MARK", "TasksPage", "task_line"]

DONE_MARK = theme.DONE_MARK
"""已完成那一行的前缀字符。"""

COMPLETED_STYLE = theme.DONE
"""已完成的行：暗灰 + 删除线（用户故事 54）。

删除线是「这条已经做完了」的读法，而它**还在列表里**——用户才有机会对它的取消完成
（那是 #38 的 ``space``，本页先把它们摆出来并让光标越过：已完成的行不参与光标移动）。
"""


def task_line(item: TaskItem, *, list_name: bool = False) -> Text:
    """一条任务的行：标题（+ 视图里的所属清单名），逾期的整条标红。

    #35 在这一票里只加**求值需要写出来的那两件**：视图里的所属清单名（视图不是容器，
    同一个清单名重复显示是必要信息）与逾期标红（谁逾期是引擎判的 ``TaskItem.overdue``，
    这一层只上色——它自己不许比日期）。行的其余样子（优先级标记、人类可读的截止时间、
    标签、重复与提醒标记、窄屏下的丢弃顺序）归 #37，那时这个函数是它的起点。

    颜色进 **span**（``stylize``），不是 ``Text(title, style="red")``：后者把样式放进 base
    style，Textual 拿 CSS 颜色解析器去读——那里 ``red`` 是 ``#FF0000`` 真彩色，会把「跟随
    终端主题」静默毁掉（ADR-0007 一）。
    """
    text = Text(item.title)
    if item.overdue:
        text.stylize(theme.OVERDUE)
    if list_name:
        text.append(f"  {item.list_name}", style=EMPTY_STYLE)
    return text


def completed_line(item: CompletedItem) -> Text:
    """已完成的一条：暗 + 删除线（沉在列表最底，见 ``Engine.tasks_in`` 的排序）。"""
    return theme.styled(f"{DONE_MARK} {item.title}", COMPLETED_STYLE)


class TasksPage(CursorPage):
    """任务列表页：某个清单或视图里的全部任务。"""

    LAYER = LAYER_TASKS
    BINDINGS = bindings_for(LAYER)
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
        self._container_id: str | None = None
        """当前铺开的是哪个容器（清单 / 视图的 id）：换了一个就把光标放回第一行。"""

    @property
    def container_name(self) -> str:
        """屏幕上这条标题里写的容器名（视图名或清单名）。"""
        return self._name

    def show_tasks(self, task_list: TaskList, *, name: str) -> None:
        """铺开一个容器的任务：标题 + 未完成任务 + 已完成的那几条。

        换了一个容器就把光标放回第一条；**同一个**容器再进来（``esc`` 回去又进来）则按行 id
        认回原来那条任务——那是「回到我刚才看的地方」，是好事。
        """
        same_container = task_list.container_id == self._container_id
        self._container_id = task_list.container_id
        self._name = name
        # 抬头是「哪一屏 + 两条暗线夹着」：顶上一条（顶栏下面的分隔）、抬头本身、
        # 抬头下面一条（分区标题的细线）。分隔与留白承担层级，不靠框线。
        rows: list[Row] = [
            rule_row(),
            heading_row(theme.styled(name, theme.HEADING)),
            rule_row(),
        ]
        rows += [
            Row(id=item.task_id, text=task_line(item, list_name=task_list.shows_list_name))
            for item in task_list.items
        ]
        rows += [Row(id=None, text=completed_line(item)) for item in task_list.completed.items]
        if len(rows) == 3:
            # 空清单要说一句明确的话，而不是只留一个标题（用户故事 53）。
            rows.append(empty_row(self.EMPTY_TEXT))
        self.set_rows(rows, keep_cursor=same_container)

    def action_enter(self) -> None:
        """``enter``：进光标下那条任务的详细页。"""
        if self.selected_id is not None:
            self.post_message(self.Entered(self.selected_id))

    def action_back(self) -> None:
        """``esc``：退回清单列表页。"""
        self.post_message(self.Back())
