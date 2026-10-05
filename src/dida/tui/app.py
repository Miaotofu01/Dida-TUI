"""外壳：组装、重画、同步泵、退出流——**这是一个薄 app**。

TUI 只通过 :class:`~dida.sync.engine.Engine` 读写；分组、排序、逾期判定、截止时间读法
全部留在引擎里，这里只把视图模型画出来，不做任何业务判断。一启动就读本地缓存渲染
（:meth:`DidaApp.refresh_view`）——网络不是这一屏的前置条件。

**这个文件为什么这么短**（t32 拆的，每一片一个变化原因、一个模块）：

==============================  ==========================================================
模块                              负责什么
==============================  ==========================================================
:mod:`dida.tui.keys`              键位表：收哪些键、每个键做什么（#48 的接缝）
:mod:`dida.tui.messages`          对用户说的每一句话（文案）
:mod:`dida.tui.layout`            窄屏降级的三档（v1 的，v2 删）
:mod:`dida.tui.task_actions`      光标下那一条的写动作：完成 / 顺延 / 优先级 / 删除 / 浏览器
:mod:`dida.tui.form_actions`      两个输入框的流程：改期 / 新建
:mod:`dida.tui.pane_actions`      哪一栏、哪一层浮层、右栏里放什么（含子任务那份只读列表）
==============================  ==========================================================

留在这里的是**组装与生命周期**：``__init__`` / ``compose`` / ``on_mount`` / 尺寸变化、
状态栏的重画、同步泵（``r`` 与周期重试）、以及退出流。按 wave-plan 的约定，
#34 之后 #46（逻辑日立刻生效）与 #47（退出拦截）各自扩展的就是这一片。

写动作的四个 mixin 挂在同一个 :class:`DidaApp` 上，所以 ``DidaApp(engine)`` 仍然是**唯一
的注入点**（几十个测试文件与组合根都从这一个口子进来）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.events import Resize
from textual.widgets import Footer

from dida.sync.engine import DidaError, Engine, filter_groups
from dida.tui.escape import open_in_browser
from dida.tui.form_actions import FormActionsMixin
from dida.tui.keys import BINDINGS
from dida.tui.layout import MEDIUM_MIN_WIDTH, WIDE_MIN_WIDTH, pane_tier
from dida.tui.messages import (
    NO_BROWSER_PREFIX,
    SUBTASK_ELSEWHERE_MESSAGE,
    SUBTASK_GONE_MESSAGE,
    SUBTASK_READ_FAILED_MESSAGE,
    SYNCING_MESSAGE,
    UNKNOWN_SUBTASK_MESSAGE,
    completed_failed_message,
    delete_prompt,
    no_browser_message,
    overwritten_message,
    quit_prompt,
    refresh_failed_message,
)
from dida.tui.pane_actions import PaneActionsMixin
from dida.tui.panes import (
    ConfirmScreen,
    DetailPane,
    FilterInput,
    ListPane,
    QuickAddInput,
    RescheduleInput,
    StatusBar,
    TaskPane,
    format_status,
)
from dida.tui.task_actions import FLASH_SECONDS, TaskActionsMixin

if TYPE_CHECKING:  # 只为了标注周期泵那个句柄，运行时用不到
    from textual.timer import Timer

__all__ = [
    "MEDIUM_MIN_WIDTH",
    "PUSH_TICK_SECONDS",
    "SYNCING_MESSAGE",
    "WIDE_MIN_WIDTH",
    "DidaApp",
    "FLASH_SECONDS",
    "completed_failed_message",
    "delete_prompt",
    "no_browser_message",
    "overwritten_message",
    "pane_tier",
    "quit_prompt",
    "refresh_failed_message",
]
"""这个模块对外给出去的名字。

被拆出去的那些**原样再导出一次**：``dida.bootstrap`` 从这里拿 ``PUSH_TICK_SECONDS`` 与
``DidaApp``，v1 的测试文件从这里拿 ``FLASH_SECONDS`` / ``delete_prompt`` /
``NO_BROWSER_PREFIX`` / ``SUBTASK_*`` 那几句文案。那些测试文件随 #34 一起删，删完之后
这一串就可以缩成 ``DidaApp`` 与 ``PUSH_TICK_SECONDS`` 两项（后者也要搬去别处——#34 会删
``app.py``，而组合根 import 的正是它）。"""

PUSH_TICK_SECONDS = 1.0
"""周期泵的间隔（工单 #21）：每秒问一次「有没有到点该重试的待推送改动」。

这是 t10 明确留给这一层的那件事——退避算得再准，也得有人**定期**来问一句。间隔只决定
「什么时候看一眼」，到没到点依然由引擎那口注入的钟判定（见 :meth:`DidaApp.push_tick`）。
1 秒的粒度对「按完 x 断网了、网络回来自动补上」这个体验足够，而每秒一次本地队列查询是免费的。

⚠ 它是**策略**，由组合根按 ``config.toml`` 传给 app；而 ``dida.bootstrap`` 现在
``from dida.tui.app import PUSH_TICK_SECONDS``——#34 删 ``app.py`` 之前必须先把它搬走，
否则 ``dida`` 起不来。
"""

SYNC_GROUP = "sync"
"""同步 worker 的组名：``exclusive=True`` 靠它保证同时只有一轮同步在跑。"""


class DidaApp(TaskActionsMixin, FormActionsMixin, PaneActionsMixin, App[None]):
    """三栏 + 状态栏。

    四个 mixin 各管一片（键位在 :mod:`~dida.tui.keys`，文案在 :mod:`~dida.tui.messages`），
    这个类自己管组装、重画、同步泵与退出流。
    """

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助归 t18

    BINDINGS = BINDINGS
    """键位表在 :mod:`dida.tui.keys`（一个数据结构，两个读者：绑定与 ``?`` 那张表）。"""

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
        self._push_timer: Timer | None = None
        """周期泵的定时器句柄（工单 #21）：``on_unmount`` 里拿它把泵停掉。

        ``set_interval`` 回一个 ``Timer``，丢掉它就没有第二个人能停这一跳——关窗之后
        它还挂在事件循环上。``None`` 表示泵没开（策略没给间隔）或者已经停了。"""
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
            # 句柄留着，关窗时好把它停掉（on_unmount）——泵的开关归这一层管。
            self._push_timer = self.set_interval(self._push_tick_seconds, self.push_tick)

    def on_unmount(self) -> None:
        """关窗：把周期泵停掉——app 都拆了，没有人再需要它问那句「到点了没有」。

        停掉只挡得住**后面**的跳；已经在飞的那一次要等 ``await`` 回来才算数，那由
        :meth:`_write_status` 与 :meth:`refresh_view` 的「屏幕还在不在」守着（见下）。
        """
        if self._push_timer is not None:
            self._push_timer.stop()
            self._push_timer = None

    # ---------------------------------------------------------------- 重画

    def refresh_view(self) -> None:
        """读引擎的视图模型，重画三栏与状态栏。写动作与同步落地后都调它。

        当前的过滤词在这里生效：筛是引擎那份纯函数（:func:`~dida.sync.view.filter_groups`）
        干的，TUI 只是把筛过的分区交给中栏——所以刷新、完成、改期之后过滤都不会掉，
        光标也不会落到一个已经被筛掉的任务上。

        屏幕已经拆掉时整体是空操作（关窗中，这一次回来晚了，见 :meth:`_write_status`）。
        """
        if not self.is_running:
            return
        view = self.engine.view()
        self.query_one(ListPane).render_lists(view.lists)
        self.query_one(TaskPane).render_groups(
            filter_groups(view.groups, self._query),
            view.completed,
            empty=TaskPane.NO_MATCH_TEXT if self._query else None,
        )
        self.update_status()

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏。数据变化后都调它。"""
        self._write_status(format_status(self.engine.status()))

    def _write_status(self, message: str) -> None:
        """把一句话写进状态栏——TUI 里状态栏的**唯一**写入口。

        关窗时丢掉它：``await`` 回来的路上 app 可能已经拆了（用户按 ``q``、或者
        ``run_test`` 收尾），那一刻 widget 已经不在 DOM 里，再往状态栏写就是
        ``NoMatches``。周期泵正好撞在这个窗口上（工单 #41 观察到的偶发红，属地归 #34）；
        凡是 ``await`` 之后写状态栏的路都走这里，省得每处各记一次。

        只在**屏幕已经不在跑**时放过：app 还在跑时状态栏不见了仍然是 bug，照旧让
        ``NoMatches`` 冒出去，不吞。
        """
        if not self.is_running:
            return
        self.query_one(StatusBar).update(message)

    # ---------------------------------------------------------------- 同步泵（t21）

    def action_refresh(self) -> None:
        """``r``：手动同步——全量刷新 + 推待推送改动 + 拉已完成流（用户故事 53）。

        先写「同步中…」再排 worker：用户按了键，得有个「它动了」的信号；真正的活儿在
        事件循环上跑，界面不因为等网络而卡住（引擎那条 ``refresh()`` 是 async 的就是为这个）。
        """
        self._write_status(SYNCING_MESSAGE)
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

        三处 ``await`` 之后动界面的地方都不是裸写：状态栏走 :meth:`_write_status`、
        重画走 :meth:`refresh_view`，两边都认得「关窗了」。
        """
        try:
            report = await self.engine.refresh()
        except DidaError as exc:
            self._write_status(refresh_failed_message(exc))
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
            self._write_status(overwritten_message(len(report.overwritten)))
        elif completed_failed is not None:
            self._write_status(completed_failed_message(completed_failed))

    async def push_tick(self) -> None:
        """推一轮**到点**的待推送改动（工单 #21 的周期泵；也是测试的确定性入口）。

        t10 把退避、``next_retry_at`` 都做好了，缺的是「谁来定期问一句到点了没有」——
        就是这里。间隔只决定**什么时候看一眼**，到没到点依然由引擎那口注入的钟判定：
        所以测试可以把钟摆到任意一刻，再直接 ``await app.push_tick()``，不必等真实时间。
        网络等待跑在事件循环的同一根线程上（t08 的线程亲和）。
        """
        await self.engine.push_pending()
        self.update_status()

    # ---------------------------------------------------------------- 退出流（t21）

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
        :meth:`~dida.tui.pane_actions.PaneActionsMixin.action_toggle_detail` 管。

        档位怎么分在 :func:`dida.tui.layout.pane_tier`；这一段是 v1 的三栏布局，v2 一栏
        三层页面之后整块删掉（#34）。
        """
        self._tier = pane_tier(width)
        self.query_one("#list-pane").display = self._tier != "narrow"
        self.query_one("#detail-pane").display = self._tier == "wide" and self._detail_open
