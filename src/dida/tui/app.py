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

from dida.sync.engine import AuthError, DidaError, Engine, TaskItem, UnknownTaskError, filter_groups
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
    SubtaskPane,
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


SUBTASK_ELSEWHERE_MESSAGE = "这条任务在别处改过：子任务已按服务端为准"
"""重读发现任务在别处被改过时的话（工单 #20）。

ADR-0002 的规矩：服务端权威可以覆盖本地，但**覆盖必须被用户看见**。这句话就是那个
「看见」——不说的话，用户在手机上改的子任务会在这一屏上悄悄消失，而且没有任何痕迹。
"""

SUBTASK_GONE_MESSAGE = "这个子任务在服务端已经没有了：已按服务端为准"
"""重读回来的那一份里已经没有这个子任务（别处删掉了）：以服务端为准，什么都没写回去。"""

UNKNOWN_SUBTASK_MESSAGE = "没有勾成：这条任务已经不在本地缓存里了，刷新之后再试一次"
"""引擎拒绝写入（本地没有这条任务的底稿，工单 #25）时的话：如实说没勾成。"""

SUBTASK_READ_FAILED_MESSAGE = "没勾成：读不到服务端，待会儿再试一次"
"""写前重读失败（断网、凭据被拒、服务端拒绝）时的话（工单 #20）。

重读失败就没有「写回」可言，所以这一句必须说出来，而不是静默什么都不做：用户按了
``t``，屏幕上却什么都没发生，他会以为勾上了。"""


SYNCING_MESSAGE = "同步中…"
"""按下 ``r`` 之后、同步落地之前状态栏里的话（工单 #21）。

只给**手动**同步用：用户主动按了键，得先有个「它动了」的信号；启动时那次后台刷新不写它，
否则每次开屏都会闪一下这句。"""

PUSH_TICK_SECONDS = 1.0
"""周期泵的间隔（工单 #21）：每秒问一次「有没有到点该重试的待推送改动」。

这是 t10 明确留给这一层的那件事——退避算得再准，也得有人**定期**来问一句。间隔只决定
「什么时候看一眼」，到没到点依然由引擎那口注入的钟判定（见
:meth:`~dida.tui.app.DidaApp.push_tick`）。1 秒的粒度对「按完 x 断网了、网络回来自动补上」
这个体验足够，而每秒一次本地队列查询是免费的。"""

SYNC_GROUP = "sync"
"""同步 worker 的组名：``exclusive=True`` 靠它保证同时只有一轮同步在跑。"""


def overwritten_message(count: int) -> str:
    """服务端盖掉本地改动时的话（工单 #21，用户故事 59）。

    ADR-0002 的规矩：服务端权威可以覆盖本地，但**覆盖必须被用户看见**。这一句就是那个
    「看见」——不说的话，用户刚做过的改动会在这一屏上悄悄变回服务端那一份。
    被待推送改动豁免挡回去的那些不算：用户的改动还在，没有任何东西被盖掉。
    """
    return f"{count} 处本地改动被覆盖"


def refresh_failed_message(error: DidaError) -> str:
    """同步失败时状态栏里的话：凭据失效与其它失败分开说（工单 #21，用户故事 6）。

    「凭据失效」必须直接引导重新粘贴 token：说成笼统的网络失败，用户会去查网络，
    而问题在他那把过期或被吊销的 token 上（t03 的 ``Credentials`` 提供了那条重新粘贴的路）。
    """
    if isinstance(error, AuthError):
        return f"凭据失效，请重新粘贴 token：{error}"
    return f"同步失败：{error}"


def completed_failed_message(error: DidaError) -> str:
    """已完成流没拉到，但全量刷新与推送已经落地时的话（工单 #21）。

    这里**不能**说成整次同步都失败了：未完成任务那一份是新的，只有「已完成 N 项」还是旧的。
    """
    return f"已完成流没拉到：{error}"


def quit_prompt(pending: int) -> str:
    """待推送改动还在时退出的话（工单 #21，用户故事 58）。

    必须说出**有几处**：只说「还有改动没推」用户不知道是刚按的那一下，还是攒了一整天的十几笔。
    也必须说清退出之后它们去哪儿——**不能**说「就丢了」。

    待推送改动落在本地库的 ``pending_changes`` 表里（t08），进程结束不等于它们没了：下次
    启动时 ``push_tick`` 的周期泵会照退避到点的时间接着推（t21），实测过一次——断网写一笔、
    退出、重开同一个库，``push_pending()`` 把它推了出去。这里要说的因此是「留在本地、下次
    接着补推」；吓唬用户说丢了，是拿一句不真的话换他一次犹豫。
    """
    return (
        f"还有 {pending} 处改动没推上去。\n"
        "退出不会丢：它们留在本地，下次打开 dida 接着补推。\n\n"
        "y 仍然退出 · n / Esc 留下"
    )


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
        # 键位表这一格是两个键：``x`` / ``Space``，做的是同一个「完成」。防误按的理由与 x
        # 一字不差：它不在栏位的光标键位组里（j/k/↑/↓），也**不是** priority 绑定——焦点
        # 在输入框里时空格仍然是空格（t15/t17 的新建、改期、过滤输入）。footer 上不重复
        # 出现第二次：一个动作一行，两个键的说明都在键位帮助表里。
        Binding("space", "complete", "完成", show=False),
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
        # 手动同步（t21）：全量刷新 + 推待推送改动 + 拉已完成流，一次做完。断网时它只是
        # 如实报一句，缓存照旧读、改动照旧排队——这一屏不因为没网就不能用。
        Binding("r", "refresh", "同步"),
        # 子任务（t20）：s 把焦点移到右栏那份子任务列表上，t 在那里勾选。
        Binding("s", "subtasks", "子任务"),
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

    def __init__(
        self,
        engine: Engine,
        *,
        open_url: Callable[[str], bool] = open_in_browser,
        refresh_on_start: bool = False,
        push_tick_seconds: float | None = None,
    ) -> None:
        """``open_url`` 是**注入**的浏览器开手（工单 #19）。

        生产默认值 :func:`~dida.tui.escape.open_in_browser` 会真的叫起系统浏览器；测试
        塞一个假的进来，于是「交给浏览器的是哪条 URL」能当场断言，而没有一个标签页被
        打开。它回 ``False`` 或抛异常都表示这台机器上开不了浏览器。

        ``refresh_on_start`` 与 ``push_tick_seconds`` 是**策略**，默认都不开（工单 #21）：
        产品行为由组合根按 ``config.toml`` 决定（``dida.bootstrap`` 传 ``refresh_on_start=``
        与 ``PUSH_TICK_SECONDS``）。这里不写死默认值，是为了让「直接 new 一个 app」的测试
        不必先接上客户端与存储——后台同步需要一个真引擎才跑得起来。
        """
        super().__init__()
        self.engine = engine
        self._open_url = open_url
        self._refresh_on_start = refresh_on_start
        self._push_tick_seconds = push_tick_seconds
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
        # 本地缓存**先**上屏，网络从来不挡第一屏（用户故事 3）：刷新排在事件循环上，
        # 它回来之前 j/k 已经在动了。
        if self._refresh_on_start:
            self.start_sync()
        if self._push_tick_seconds is not None:
            # 重试队列的泵（t21）：写失败时改动留在队列里，退避到点了得有谁来推它。
            self.set_interval(self._push_tick_seconds, self.push_tick)

    # ---------------------------------------------------------------- 同步（t21）

    def action_refresh(self) -> None:
        """``r``：手动同步——全量刷新 + 推待推送改动 + 拉已完成流（用户故事 53）。

        先写「同步中…」再排 worker：用户按了键，得有个「它动了」的信号；真正的活儿在
        事件循环上跑，界面不因为等网络而卡住（引擎那条 ``refresh()`` 是 async 的就是为这个）。
        """
        self.query_one(StatusBar).update(SYNCING_MESSAGE)
        self.start_sync()

    def start_sync(self) -> None:
        """把一轮同步排到事件循环上（不等它）。``r`` 与启动刷新都走这里。

        ``exclusive=True``：连按 ``r`` 不会让两轮同步叠在一起（同一份缓存被两个协程交替
        写）。协程 worker 跑在事件循环**同一根线程**上，t08 的 sqlite 连接有线程亲和，
        所以这里不能改成 ``thread=True``。
        """
        self.run_worker(self._sync(), group=SYNC_GROUP, exclusive=True, description="同步")

    async def _sync(self) -> None:
        """一轮同步：全量刷新 → 推待推送改动 → 拉已完成流。

        三件事各报各的失败，而且**不假装做过**：全量刷新失败（断网、凭据失效）时后面两件
        不做——同一个网络问题会让它们一起失败，白跑两趟；已完成流失败时前两件已经落地，
        照旧重画，只是把「没拉到」说出来。缓存从头到尾都在：这一屏不因为没网就不能用
        （用户故事 62）。
        """
        try:
            report = await self.engine.refresh()
        except DidaError as exc:
            self.query_one(StatusBar).update(refresh_failed_message(exc))
            return
        # 队列里那些到点的改动顺手推一轮：`r` 是用户能按的那个「现在再试一次」。
        await self.engine.push_pending()
        try:
            await self.engine.refresh_completed()
        except DidaError as exc:
            completed_failed: DidaError | None = exc
        else:
            completed_failed = None
        self.refresh_view()
        # 覆盖告知排在最后：服务端真的盖掉了用户的东西，这句话比什么都该留在屏幕上
        # （ADR-0002）。被待推送改动挡回去的不算——那些改动还在，没有被盖掉。
        if report.overwritten:
            self.query_one(StatusBar).update(overwritten_message(len(report.overwritten)))
        elif completed_failed is not None:
            self.query_one(StatusBar).update(completed_failed_message(completed_failed))

    async def push_tick(self) -> None:
        """推一轮**到点**的待推送改动（工单 #21 的周期泵；也是测试的确定性入口）。

        t10 把退避、``next_retry_at`` 都做好了，缺的是「谁来定期问一句到点了没有」——
        就是这里。间隔只决定**什么时候看一眼**，到没到点依然由引擎那口注入的钟判定：
        所以测试可以把钟摆到任意一刻，再直接 ``await app.push_tick()``，不必等真实时间。
        网络等待跑在事件循环的同一根线程上（t08 的线程亲和）。
        """
        await self.engine.push_pending()
        self.update_status()

    async def action_quit(self) -> None:
        """``q``：还有待推送改动时先拦一下（工单 #21，用户故事 58）。

        用户按 ``q`` 的意图通常是「我干完了」，而屏幕底下那个数可能是「我按了 ``x``，但网断了」
        ——待推送改动只存在于本地（ADR-0002 的豁免代价），进程一结束就没了，而服务端并不知道
        用户做过什么。所以这里**多问一句**，并且把「有几处」写在浮层上。

        浮层已经开着时什么都不做：连按 ``q`` 不该叠出一摞确认框。没有待推送改动就照旧直接退
        （``q`` 即结束，spec 的单进程规矩）。
        """
        pending = self.engine.status().pending_count
        if not pending:
            self.exit()
            return
        if isinstance(self.screen, ConfirmScreen):
            return
        self.push_screen(ConfirmScreen(quit_prompt(pending)), self._finish_quit)

    def _finish_quit(self, confirmed: bool | None) -> None:
        """退出浮层关掉了：只有 ``True`` 才真的退（``n`` / ``Esc`` 与 ``None`` 都留下）。"""
        if confirmed:
            self.exit()

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
        self._show_subtasks(event.item)

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
        """
        try:
            report = await self.engine.toggle_subtask(event.task_id, event.subtask_id)
        except UnknownTaskError:
            self.query_one(StatusBar).update(UNKNOWN_SUBTASK_MESSAGE)
            return
        except DidaError:
            # 重读那一步失败（网络断了、凭据被拒、服务端拒绝）：引擎的失败一律是结构化
            # 错误，这里如实说一句，不让一个断网的机器把整个界面带走。
            self.query_one(StatusBar).update(SUBTASK_READ_FAILED_MESSAGE)
            return
        self._subtask_pane().show(report.task_id, report.items)
        self.update_status()
        if not report.written:
            self.query_one(StatusBar).update(SUBTASK_GONE_MESSAGE)
        elif report.changed_elsewhere:
            self.query_one(StatusBar).update(SUBTASK_ELSEWHERE_MESSAGE)

    def on_subtask_pane_dismissed(self) -> None:
        """子任务列表按了 ``Esc``：焦点回任务列。"""
        self.query_one(TaskPane).focus()
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
