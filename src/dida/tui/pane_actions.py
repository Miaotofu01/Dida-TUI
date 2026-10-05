"""屏幕上摆哪一栏、哪一层浮层，以及右栏里放什么。

**一个变化原因**：「现在看得见什么」。这一片不写任何数据（读写都在引擎里），只决定
哪一栏显示、哪一层浮层弹出来、以及光标换了一条之后右栏跟着换成谁。

- 右栏详情：宽档（≥110 列）就地开合，更窄的两档弹浮层（工单 #18）。
- 清单浮层（``l``）与键位帮助（``?``）（工单 #18）。
- 过滤框（``/``）：v1 的模糊过滤，v2 没有它（#34 删）。
- 右栏那份子任务列表：延迟挂载、``s`` 进出、``t`` 交给引擎（工单 #20）。v2 的子任务是
  **只看不勾**（spec 用户故事 76），所以 ``t`` 那一条会随 #43 一起变。

各张工单接下来各取一片：#34 拿走浮层与右栏、#43 拿走子任务那份只读显示、#48 拿走帮助。
"""

from __future__ import annotations

from dida.sync.engine import DidaError, TaskItem, UnknownTaskError
from dida.tui.messages import (
    SUBTASK_ELSEWHERE_MESSAGE,
    SUBTASK_GONE_MESSAGE,
    SUBTASK_READ_FAILED_MESSAGE,
    UNKNOWN_SUBTASK_MESSAGE,
)
from dida.tui.panes import (
    DetailPane,
    DetailScreen,
    FilterInput,
    HelpScreen,
    ListsScreen,
    SubtaskPane,
    TaskPane,
    detail_body,
    key_help_body,
    lists_body,
)


class PaneActionsMixin:
    """右栏、浮层与焦点（t18 / t20）。

    mixin 不是完整的 app：它用 ``self.engine`` / ``self.query_one`` / ``self.refresh_view``
    / ``self.update_status`` / ``self._write_status`` / ``self._query`` / ``self._tier`` /
    ``self._detail_open``——都由 :class:`~dida.tui.app.DidaApp` 提供。状态栏那句一律走
    ``self._write_status``：``t`` 这一条 ``await`` 回来时屏幕可能已经拆了（见 app.py）。
    """

    # ---------------------------------------------------------------- 模糊过滤（t17）

    def action_filter(self) -> None:
        """``/``：打开过滤框，对当前列表做模糊过滤。"""
        self.query_one(FilterInput).open()

    def on_filter_input_changed(self, event: FilterInput.Changed) -> None:
        """框里的字变了：立刻按它重画（边打边筛，不必按 Enter）。"""
        self._query = event.query
        self.refresh_view()

    def on_filter_input_cancelled(self) -> None:
        """``Esc``：清空过滤、恢复完整列表，焦点还给任务列。"""
        self._query = ""
        self.refresh_view()
        self.query_one(TaskPane).focus()

    # ---------------------------------------------------------------- 右栏内容

    def on_task_pane_selection_changed(self, event: TaskPane.SelectionChanged) -> None:
        """光标换了一条任务：右栏跟着换（过滤期间因此不会指着一个被筛掉的任务）。"""
        self.query_one(DetailPane).show(event.item)
        self._show_subtasks(event.item)

    # ---------------------------------------------------------------- 子任务（t20）

    def _show_subtasks(self, item: TaskItem | None) -> None:
        """把右栏那份子任务列表指到 ``item`` 上（工单 #20）。

        子任务数组存在**任务原文**里，读它要走引擎（``engine.subtasks``）：TUI 不认识
        ``items``，也不认识 ``status`` 那对取值。光标没指着任务时给空的一份——右栏就是
        没有子任务可显示，不是错误。
        """
        self._subtask_pane().show(
            None if item is None else item.task_id,
            () if item is None else self.engine.subtasks(item.task_id),
        )

    def _subtask_pane(self) -> SubtaskPane:
        """右栏那份子任务列表；**第一次要用时才挂进详情栏**（工单 #20）。

        延迟挂载而不是写在 ``compose`` 里，图的是两件事：

        - 它排在详情栏自己那块内容**后面**（``mount`` 是追加），屏幕上就是「任务行，然后
          子任务」，与 mockup 一致；
        - 详情栏的排版归 t18，这一份不必去动 ``DetailPane`` 的定义，详情栏一收起它跟着
          收起（它就是详情栏的孩子）。
        """
        found = self.query(SubtaskPane)
        if found:
            return found.first()
        pane = SubtaskPane(id="subtask-pane")
        self.query_one(DetailPane).mount(pane)
        return pane

    def action_subtasks(self) -> None:
        """``s``：把焦点交给右栏那份子任务列表（工单 #20）。

        光标下那条任务没有子任务时什么都不做——空屏上按键不该报错（与 ``x``/``g``/``e``
        同一条口径）。已经在里面时再按一次就是出来：``s`` 是这一处的进出键，不用去记
        ``Esc``（``Esc`` 也行，那是 :meth:`on_subtask_pane_dismissed`）。
        """
        pane = self._subtask_pane()
        if pane.has_focus:
            self.query_one(TaskPane).focus()
            return
        if not pane.count:
            return
        pane.arm()  # 它平时不在焦点链里（Tab 只在清单栏与任务列之间转）
        pane.focus()

    async def on_subtask_pane_toggled(self, event: SubtaskPane.Toggled) -> None:
        """子任务列表按了 ``t``：交给引擎——**先重读该任务，再只写这一次改动**（工单 #20）。

        重读是一次网络调用，所以这一条要 ``await``：写回必须建立在它带回来的底稿上，
        没有底稿的写回会把别处改过的子任务一起抹掉（这正是本工单要挡的那件事）。

        重读发现任务在别处被改过时，引擎按服务端那一份落地并报 ``changed_elsewhere``，
        这里**必须说出来**（状态栏那一句）：服务端权威可以覆盖，但覆盖要看得见（ADR-0002）。

        重画的是右栏这一份（引擎已经把重读回来的那一份给回来了），**不重画整个视图**：
        中栏那些行不会因为一个子任务变了而变，而重画会把任务列的光标推回第一行——用户
        正在这条任务上连着勾子任务，勾一个就跳走是没法用的。状态栏照旧要刷（待推送数量
        会变）。

        ``await`` 回来时屏幕可能已经拆了（用户按完就退出）：那一笔写已经落库，界面这一份
        没有人再看，**不许**再去查 widget（``query_one(DetailPane)`` 会 ``NoMatches``）。
        """
        try:
            report = await self.engine.toggle_subtask(event.task_id, event.subtask_id)
        except UnknownTaskError:
            self._write_status(UNKNOWN_SUBTASK_MESSAGE)
            return
        except DidaError:
            # 重读那一步失败（网络断了、凭据被拒、服务端拒绝）：引擎的失败一律是结构化
            # 错误，这里如实说一句，不让一个断网的机器把整个界面带走。
            self._write_status(SUBTASK_READ_FAILED_MESSAGE)
            return
        if not self.is_running:
            return
        self._subtask_pane().show(report.task_id, report.items)
        self.update_status()
        if not report.written:
            self._write_status(SUBTASK_GONE_MESSAGE)
        elif report.changed_elsewhere:
            self._write_status(SUBTASK_ELSEWHERE_MESSAGE)

    def on_subtask_pane_dismissed(self) -> None:
        """子任务列表按了 ``Esc``：焦点回任务列。"""
        self.query_one(TaskPane).focus()

    # ---------------------------------------------------------------- 浮层（t18）

    def action_toggle_detail(self) -> None:
        """``Enter``：开合右栏详情（工单 #18，验收标准 #4）。

        三档一个语义、两种落地：

        - 右栏**在屏上**（≥110 列）：就地收起 / 显示。收起是用户自己按的，是一时的姿势，
          所以尺寸变化不重置它（与已完成区的展开同一条口径）。
        - 右栏**不在屏上**（<110 列）：把当前任务的详情作为浮层弹出来；再按一次 ``Enter``
          由 :class:`~dida.tui.panes.DetailScreen` 自己收起来（``Esc`` 也一样）。

        浮层里的内容是**按下那一刻**光标下那条的详情：浮层是模态的，j/k 到不了任务列，
        所以它不会在开着的时候偷偷换成别的任务。
        """
        if isinstance(self.screen, DetailScreen):
            self.screen.dismiss(None)
            return
        if self._tier == "wide":
            self._detail_open = not self._detail_open
            self.query_one("#detail-pane").display = self._detail_open
            return
        item = self.query_one(TaskPane).selected_item
        self.push_screen(DetailScreen(detail_body(item)))

    def action_lists(self) -> None:
        """``l``：把清单作为浮层打开（工单 #18，验收标准 #3）。

        窄档（<80 列）左栏不在屏上，清单只能从这里看；更宽的两档左栏本来就在，按 ``l``
        也开同一个浮层——一个键到哪里都做同一件事，不必记两套。

        内容取引擎的清单摘要（与左栏同一份 ``view().lists``）：左栏这会儿可能正被收起，
        但数据一直在引擎里，浮层不是第二份缓存。
        """
        self.push_screen(ListsScreen(lists_body(self.engine.view().lists)))

    def action_help(self) -> None:
        """``?``：打开键位帮助浮层（工单 #18，验收标准 #6）。

        表在 :data:`~dida.tui.panes.KEY_HELP`：帮助里少了哪个键，是那张表少了一行，
        不是这里少了一段布局。``Esc`` 与 ``Enter`` 都能关掉它（浮层自己的绑定）。
        """
        self.push_screen(HelpScreen(key_help_body()))
