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

from dida.sync.engine import Engine, UnknownTaskError
from dida.tui.panes import (
    ConfirmScreen,
    DetailPane,
    ListPane,
    QuickAddInput,
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

NO_TITLE_MESSAGE = "没写标题：新建至少得有个标题，日期、优先级、标签都可以写在标题后面"
"""新建输入框里只有日期/优先级/标签、一个字的标题都没有时的话。任务得有名字。"""

UNKNOWN_DELETE_MESSAGE = "没有删：这条任务已经不在本地缓存里了"
"""删除没有底稿时的话（工单 #16）。

与改期那句分开写：这里**不能**说「刷新之后再试一次」——刷新会把它拉回来，看着像删掉了
其实没有；而删除这条路径上「本来就没这条」与「删掉了」必须一眼分得清。
"""


def delete_prompt(title: str) -> str:
    """删除确认浮层上的那句话（工单 #16）。

    措辞是这一屏最要紧的一行字：**不许暗示还能找回来**。滴答清单 Open API 里没有
    undelete、没有回收站、没有「已删除」列表（``api-contracts.md``），所以这里只说删了
    就没有了，绝不说「可恢复」「稍后可找回」「已移入回收站」——那种话会让用户在按 ``y``
    的时候以为还有退路，而实际上没有。
    """
    return f"删除「{title}」？\n删掉就找不回来了，滴答清单没有回收站。\n\ny 确认删除 · n / Esc 取消"


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
        Binding("a", "quick_add", "新建"),
        # 删除是这一屏唯一不可挽回的动作：服务端没有 undelete、没有回收站（api-contracts.md），
        # 所以 `d` 不直接删，先弹一次确认（t16）。
        Binding("d", "delete", "删除"),
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
        # 新建输入框：默认收起，按 a 才出现。它在三栏**上面**——它不瞄准任何一条任务，
        # 而 e 的改期框在下面（改的是光标下那一条）。两者共用同一套语法。
        yield QuickAddInput(id="quick-add-input")
        with Horizontal(id="panes"):
            yield ListPane(id="list-pane")
            yield TaskPane(id="task-pane")
            yield DetailPane(id="detail-pane")
        # 改期输入框：默认收起，按 e 才出现（新建输入框归 t15，在顶部）
        yield RescheduleInput(id="reschedule-input")
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
        self.query_one(TaskPane).render_groups(view.groups, view.completed)
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

    # ---------------------------------------------------------------- 新建（t15）

    def action_quick_add(self) -> None:
        """``a``：打开顶部新建输入框（工单 #15）。

        不瞄准任何一条任务，所以光标在哪都无所谓——空屏上按 ``a`` 照样能建。
        """
        self.query_one(QuickAddInput).open()

    def on_quick_add_input_submitted(self, event: QuickAddInput.Submitted) -> None:
        """新建输入框按了 ``Enter``：先解析，再决定建不建（工单 #15）。

        与改期同一条规矩：**非空 ``diagnostics`` 一律提示、绝不提交**，而且不按 code
        名单挑着报——``invalid_date`` 与 #23 的 ``duplicate_priority`` 一样重要。新建这条
        路上它更重：静默建出一个没有日期的任务，服务端还会顺手清掉重复规则，是双重错误，
        用户三天后在手机上才发现这一条根本不是自己写的样子。

        标题全是空的也不行（整行只写了「明天 !高」）：任务总得有个名字。

        被拒绝时输入框留在原地、原文一个字不删——用户改一改再按 Enter 就行。
        """
        box = self.query_one(QuickAddInput)
        parsed = self.engine.plan(event.text)
        if parsed.diagnostics:
            box.show_message("；".join(item.message for item in parsed.diagnostics))
            return
        if not parsed.title:
            box.show_message(NO_TITLE_MESSAGE)
            return
        # 解析出来的四样东西原样交给引擎：TUI 不重算日期、不重排优先级、不动标签。
        self.engine.create(
            parsed.title,
            due=parsed.due,
            all_day=parsed.all_day,
            priority=parsed.priority,
            tags=parsed.tags,
        )
        box.close()
        # 写完必须重画：新建是**多出一条**任务，引擎的视图已经变了，但栏位还端着旧的一份。
        # 少了这一行，任务确实建了、也进了队列，屏幕上却看不见——t14 的改期路径里有这一步
        # （见 on_reschedule_input_submitted），这里当初漏了，整套测试是在与兄弟工单合并后
        # 才把它照出来的。
        self.refresh_view()
        self.query_one(TaskPane).focus()

    # ---------------------------------------------------------------- 删除（t16）

    def action_delete(self) -> None:
        """``d``：先问一句，确认了才删（工单 #16）。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g``/``e`` 同一条口径）。

        **确认是这里唯一的防线**：滴答清单的 Open API 里没有 undelete、没有回收站、也没有
        「已删除」列表，删掉就是删掉了。所以瞄准的是按下 ``d`` 那一刻光标下那条任务，
        提示语里点名是哪一条（``title``），而删除动作只发生在浮层回来 ``True`` 的时候。
        """
        pane = self.query_one(TaskPane)
        task_id = pane.selected_task_id
        if task_id is None:
            return
        self.push_screen(
            ConfirmScreen(delete_prompt(pane.selected_title or task_id)),
            lambda confirmed: self._finish_delete(task_id, confirmed),
        )

    def _finish_delete(self, task_id: str, confirmed: bool | None) -> None:
        """浮层关掉了：只有 ``True`` 才写。

        ``False``（``n``/``Esc``）与 ``None`` 都什么都不做——取消必须一点痕迹都不留：
        没有待推送改动、本地快照照旧、没有请求发出去。引擎拒绝写入（本地已经没有这条任务
        的底稿，#25）时如实说一句，不崩，也不拿一个猜来的清单 id 硬发。
        """
        if not confirmed:
            return
        try:
            self.engine.delete(task_id)
        except UnknownTaskError:
            self.query_one(StatusBar).update(UNKNOWN_DELETE_MESSAGE)
            return
        self.refresh_view()
