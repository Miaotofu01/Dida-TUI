"""今日执行台的三栏外壳。

TUI 只通过 :class:`~dida.sync.engine.Engine` 读写；分组、排序、逾期判定、截止时间
读法全部留在引擎里，这里只把视图模型画出来，不做任何业务判断。

一启动就读本地缓存渲染（:meth:`DidaApp.refresh_view`）——网络不是这一屏的前置条件。
"""

from __future__ import annotations

from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Resize
from textual.widgets import Footer

from dida.sync.engine import Engine, UnknownTaskError, filter_groups
from dida.tui.escape import open_in_browser, task_url
from dida.tui.panes import (
    ConfirmScreen,
    DetailPane,
    DetailScreen,
    FilterInput,
    HelpScreen,
    ListPane,
    ListsScreen,
    QuickAddInput,
    RescheduleInput,
    StatusBar,
    TaskPane,
    detail_body,
    format_status,
    key_help_body,
    lists_body,
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


NO_BROWSER_PREFIX = "打不开浏览器：把这条链接自己粘到浏览器里 "
"""没有浏览器可用时那句话的开头（工单 #19）。

与 :func:`no_browser_message` 分开写：测试要断的是「出声了没有」，而那句话后面还挂着
一条随时会变的 URL。措辞里**不假装**有桌面客户端可以切——ADR-0002 已核实官方客户端
不接受任务深链，所以这里只有浏览器这一条路。
"""


def no_browser_message(url: str) -> str:
    """没有浏览器可用时状态栏里的话：说清楚打不开，并把 URL 原样给人抄（工单 #19）。

    这里**绝不能**静默：完成在服务端不可逆（ADR-0002），``o`` 是它的补偿，按下去什么都
    没发生比吵一句坏得多。也不说「重试一下就好」——``webbrowser`` 找不到浏览器时重试
    还是找不到，用户该做的是自己把这条链接粘走。
    """
    return f"{NO_BROWSER_PREFIX}{url}"


def delete_prompt(title: str) -> str:
    """删除确认浮层上的那句话（工单 #16）。

    措辞是这一屏最要紧的一行字：**不许暗示还能找回来**。滴答清单 Open API 里没有
    undelete、没有回收站、没有「已删除」列表（``api-contracts.md``），所以这里只说删了
    就没有了，绝不说「可恢复」「稍后可找回」「已移入回收站」——那种话会让用户在按 ``y``
    的时候以为还有退路，而实际上没有。
    """
    return f"删除「{title}」？\n删掉就找不回来了，滴答清单没有回收站。\n\ny 确认删除 · n / Esc 取消"


WIDE_MIN_WIDTH = 110
"""三栏常驻的最小列数（工单 #18）。"""

MEDIUM_MIN_WIDTH = 80
"""收掉左栏的最小列数：比这更窄就连清单也进浮层。"""


def pane_tier(width: int) -> str:
    """按终端列数分档（工单 #18）：``wide`` / ``medium`` / ``narrow``。

    - ``wide``（≥110）：三栏常驻。
    - ``medium``（80–109）：收起右栏，``Enter`` 以浮层打开详情。
    - ``narrow``（<80）：再收起左栏，清单也进浮层。

    纯函数：宽度进来、档位出去。分档是布局的事，跟终端里有什么数据无关，所以它在这里
    而不是在引擎里——也不需要在测试里开一个 app 才能问「95 列算哪一档」。
    """
    if width >= WIDE_MIN_WIDTH:
        return "wide"
    if width >= MEDIUM_MIN_WIDTH:
        return "medium"
    return "narrow"


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
        Binding("p", "priority", "优先级"),
        Binding("/", "filter", "过滤"),
        # 逃生舱（t19）：把光标下那一条交给系统浏览器。完成在服务端不可逆（ADR-0002），
        # 官方客户端又不接受任务深链，所以按错之后唯一能走的路就是这个键。
        Binding("o", "open", "浏览器"),
        # 右栏详情的开合（t18）。三档语义一致：右栏在屏上就收放它，收起了就弹浮层。
        # 不抢输入框：焦点在 Input 里时 Enter 归 Input（提交），到不了这里。
        Binding("enter", "toggle_detail", "详情"),
        # 清单浮层（t18）。窄档（<80 列）左栏不在屏上，清单只能从这里看；
        # 更宽的两档左栏本来就在，这个键照样能开——同一个键在哪里都做同一件事。
        Binding("l", "lists", "清单"),
        # 键位帮助（t18）。footer 只显示得下头几个键，这张表才是找键的地方。
        Binding("question_mark", "help", "帮助"),
    ]

    _tier = "wide"
    """当前宽度档位（工单 #18）：``wide`` / ``medium`` / ``narrow``。``on_mount`` 与
    ``on_resize`` 各算一次。"""

    _detail_open = True
    """右栏详情是不是开着。``Enter`` 开合它（工单 #18）：宽档下它决定右栏在不在屏上，
    更窄的两档里它不参与布局——那两档的详情走浮层。尺寸变化后保留这个姿势，不重置。"""
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

    def __init__(self, engine: Engine, *, open_url: Callable[[str], bool] = open_in_browser) -> None:
        """``open_url`` 是**注入**的浏览器开手（工单 #19）。

        生产默认值 :func:`~dida.tui.escape.open_in_browser` 会真的叫起系统浏览器；测试
        塞一个假的进来，于是「交给浏览器的是哪条 URL」能当场断言，而没有一个标签页被
        打开。它回 ``False`` 或抛异常都表示这台机器上开不了浏览器。
        """
        super().__init__()
        self.engine = engine
        self._open_url = open_url
        self._query = ""
        """当前生效的过滤词（空串 = 不过滤）。框里的原文由 :class:`FilterInput` 拿着。"""

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
        # 过滤框：默认收起，按 / 才出现
        yield FilterInput(id="filter-input")
        yield Footer()
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._apply_tier(self.size.width)
        self.refresh_view()
        self.query_one(TaskPane).focus()  # 一进来 j/k 就能过任务；Tab 换到左栏

    # ---------------------------------------------------------------- 窄屏降级（t18）

    def on_resize(self, event: Resize) -> None:
        """窗口换了大小就重新分档（验收标准 #7：不用重启）。

        拖动窗口、切分屏、Rotate 手机终端都会走到这里。分档只看宽度，所以重新分一次
        是幂等的——档位没变时 :meth:`_apply_tier` 也不动屏幕上的东西。

        宽度取 ``event.size`` 而**不是** ``self.size``：事件派发到这儿的时候 app 自己的
        尺寸还没更新，读 ``self.size`` 拿到的是上一档的宽度，于是拖窄之后一直停在旧档位。
        """
        self._apply_tier(event.size.width)

    def _apply_tier(self, width: int) -> None:
        """按 ``width`` 列把三栏收放到位（工单 #18）。

        右栏在 ``wide`` 档由 :attr:`_detail_open` 决定（``Enter`` 开合）；在更窄的两档
        一律收起来，``Enter`` 改成弹浮层——所以窄档下这里显示的是 ``False``，浮层归
        :meth:`action_toggle_detail` 管。
        """
        self._tier = pane_tier(width)
        self.query_one("#list-pane").display = self._tier != "narrow"
        self.query_one("#detail-pane").display = self._tier == "wide" and self._detail_open

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

    # ---------------------------------------------------------------- 逃生舱（t19）

    def action_open(self) -> None:
        """``o``：把光标下那条任务交给系统浏览器（工单 #19）。

        **只有浏览器这一条路。** ADR-0002 的「逃生舱的确切形态（已核实）」记着：官方
        桌面客户端不接受任务深链（``dida365://`` 不存在、Linux 的 ``.desktop`` 没注册
        协议处理器、主进程也不处理 argv），能做出来的就是厂商自己在「复制任务链接」里
        生成的那条网页版路由。所以这里不试任何 ``xxx://``，也不假装能切到桌面 App。

        URL 由 :func:`~dida.tui.escape.task_url` 拼（纯函数，含收集箱那条字面量替换），
        清单 id 取**光标下那一条**的：TUI 不做判断，只把视图模型里已经有的事实交出去。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g``/``e`` 同一条口径）。

        交不出去时**必须出声**：完成在服务端不可逆，这个键是它的补偿，静默失败比吵一句
        坏得多。两种失败都报同一句话（状态栏），并且把 URL 原样给人抄——``webbrowser``
        找不到浏览器时抛 ``webbrowser.Error``，``open()`` 回 ``False`` 也是一种失败。
        """
        item = self.query_one(TaskPane).selected_item
        if item is None:
            return
        url = task_url(item.list_id, item.task_id)
        try:
            opened = self._open_url(url)
        except Exception:
            # 开手当场抛（``webbrowser.Error`` 就是这一种）：按上面那条规矩如实说，
            # 不让一个找不到浏览器的机器把整个界面带走。
            opened = False
        if not opened:
            self.query_one(StatusBar).update(no_browser_message(url))

    # ---------------------------------------------------------------- 详情开合（t18）

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
