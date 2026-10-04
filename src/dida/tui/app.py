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

from dida.sync.engine import Engine, UnknownTaskError, filter_groups
from dida.tui.panes import (
    DetailPane,
    FilterInput,
    ListPane,
    RescheduleInput,
    StatusBar,
    TaskPane,
    format_status,
)

FLASH_SECONDS = 0.45
"""完成后那一行高亮多久：够看清这一下生效了，又不至于拖住下一次分诊。"""

NO_DATE_MESSAGE = "没写日期：改期要说清改到哪一天，可以写「明天」或「3-15」"
"""改期输入框里一个日期都没写时的话。新建可以没有日期，改期不行——那等于什么都没改。"""

UNKNOWN_TASK_MESSAGE = "没有改成：这条任务已经不在本地缓存里了，刷新之后再试一次"
"""引擎拒绝写入（本地没有这条任务的底稿，工单 #25）时的话：如实说没改成。"""


class DidaApp(App[None]):
    """三栏 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助归 t18
    # 非 priority：焦点在输入框里时 q 应当是普通字符（t15/t17 的输入框）
    BINDINGS = [
        Binding("q", "quit", "退出"),
        # 完成在服务端不可逆（ADR-0002）：x 在主键区下面那一行，与 j/k 隔着整行。防误按
        # 是这个动作唯一的补偿；footer 上带标签显示，看得见才按得准。
        Binding("x", "complete", "完成"),
        Binding("g", "defer", "顺延"),
        Binding("G", "defer_week", "顺延一周"),
        Binding("e", "reschedule", "改期"),
        Binding("p", "priority", "优先级"),
        Binding("/", "filter", "过滤"),
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
        self._query = ""
        """当前生效的过滤词（空串 = 不过滤）。框里的原文由 :class:`FilterInput` 拿着。"""

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            yield ListPane(id="list-pane")
            yield TaskPane(id="task-pane")
            yield DetailPane(id="detail-pane")
        # 改期输入框：默认收起，按 e 才出现（新建输入框归 t15，在顶部）
        yield RescheduleInput(id="reschedule-input")
        # 过滤框：默认收起，按 / 才出现
        yield FilterInput(id="filter-input")
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
        """读引擎的视图模型，重画三栏与状态栏。t09/t10/t11 在数据变化后调用。

        当前的过滤词在这里生效：筛是引擎那份纯函数（:func:`~dida.sync.view.filter_groups`）
        干的，TUI 只是把筛过的分区交给中栏——所以刷新、完成、改期之后过滤都不会掉，
        光标也不会落到一个已经被筛掉的任务上。
        """
        view = self.engine.view()
        self.query_one(ListPane).render_lists(view.lists)
        self.query_one(TaskPane).render_groups(
            filter_groups(view.groups, self._query),
            view.completed,
            empty=TaskPane.NO_MATCH_TEXT if self._query else None,
        )
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

    # ---------------------------------------------------------------- 改期（t14）

    def action_reschedule(self) -> None:
        """``e``：打开改期输入框（工单 #14）。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g`` 同一条口径）。
        瞄准的是**按下 e 那一刻**光标下那条任务：输入框拿到焦点之后 j/k 都成了文本，
        不再移动光标，所以这一次改期永远落在那一条上。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.query_one(RescheduleInput).open(task_id)

    def on_reschedule_input_submitted(self, event: RescheduleInput.Submitted) -> None:
        """改期输入框按了 ``Enter``：先解析，再决定提不提交（工单 #14）。

        三条规矩：

        - **非空 ``diagnostics`` 一律提示、绝不提交**。不按 code 名单挑着报：``invalid_date``
          与 #23 的 ``duplicate_priority`` 一样重要——「13-45」被当成标题的一部分静默吞掉，
          用户三天后才发现任务没有日期，正是「如实呈现」要消灭的那类安静错误。
        - 一个日期都没写（``due is None``）也拒绝：改期不写日期等于什么都没改。新建可以没有
          日期，改期不行。
        - 引擎拒绝写入（本地已经没有这条任务的底稿，#25）时如实说一句「没有改成」，不崩、
          也不拿一个猜来的清单 id 硬发。

        被拒绝时输入框留在原地、原文一个字不删——用户改一改再按 Enter 就行。
        """
        box = self.query_one(RescheduleInput)
        parsed = self.engine.plan(event.text)
        if parsed.diagnostics:
            box.show_message("；".join(item.message for item in parsed.diagnostics))
            return
        if parsed.due is None:
            box.show_message(NO_DATE_MESSAGE)
            return
        try:
            self.engine.reschedule(event.task_id, due=parsed.due, all_day=parsed.all_day)
        except UnknownTaskError:
            box.show_message(UNKNOWN_TASK_MESSAGE)
            return
        box.close()
        self.query_one(TaskPane).focus()
        self.refresh_view()

    # ---------------------------------------------------------------- 优先级（t17）

    def action_priority(self) -> None:
        """``p``：把光标下那条任务的优先级推进一档（无 → 低 → 中 → 高 → 无）。

        推进哪一档由引擎定：线上编码 ``0/1/3/5`` 是 API 的事实，TUI 不认识优先级取值，
        只说「推进这一条」（与 ``x`` / ``g`` 一样）。光标下没有任务就什么都不做——
        空屏上按键不该报错。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.engine.cycle_priority(task_id)
        self.refresh_view()

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

    def on_task_pane_selection_changed(self, event: TaskPane.SelectionChanged) -> None:
        """光标换了一条任务：右栏跟着换（过滤期间因此不会指着一个被筛掉的任务）。"""
        self.query_one(DetailPane).show(event.item)
