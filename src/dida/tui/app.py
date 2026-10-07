"""外壳：组装、三层页面的进出、状态栏、同步泵、退出流——**这是一个薄 app**。

一栏、三层页面（ADR-0004）：启动落在清单列表页，``→`` 向下、``←`` 向上。三层各是
一个控件（:mod:`dida.tui.pages`），这里管的是**它们之间的进出**：谁在屏上、焦点在哪、
光标从哪来、``o`` 说的是哪条任务。

TUI 只通过 :class:`~dida.sync.engine.Engine` 读写；分组、计数、逾期判定、截止时间读法
全部留在引擎里，页面只把引擎给的成品画出来。一启动就读本地缓存渲染——网络不是这一屏的
前置条件（用户故事 3/4）。

留在这里的是**组装与生命周期**：``__init__`` / ``compose`` / ``on_mount``、上下两行的
分工（顶栏说「你在哪」、状态栏说「数据怎么样」）、同步泵（``r`` 与周期重试）、三层进出、
以及退出流。按 wave-plan 的约定，#34 之后 #46（逻辑日立刻生效）与 #47（退出拦截）各自
扩展的就是这一片。

**外观一行都不在这里**：颜色、字形、间距、动效时长全在 :mod:`dida.tui.theme`（工单 #51）。
这里只决定**什么时候**动（换层平移、同步转圈、toast）。

⚠ **``await`` 之后动 DOM 的每一处都要先问 ``self.is_running``**（``_write_status`` /
``refresh_view`` 就是那两个口子）。这不是洁癖：``Timer._tick`` 会把回调里的异常吞给自己
的 handler，于是「关窗那一刻回来晚了」的那一次会以**拆屏期**的报错冒出来，离现场很远。
``App._shutdown`` 先置 ``_running = False``、然后才 await ``_close_all()``（拆 widget），
所以 ``is_running`` 这一个判断足以关掉那个窗口——这是 Textual 8.2.8 的**内部次序**，不是
写在文档里的契约：**升级 Textual 之后要重新核实这一条**（见 ``notes/progress.md`` 的
"pump teardown race"）。Textual 8.2.8 也没有 ``Shutdown`` 事件，``on_unmount`` 是唯一
可用的收尾钩子。
"""

from __future__ import annotations

import os
from datetime import date
from functools import partial
from typing import TYPE_CHECKING, Callable, Sequence

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import HorizontalScroll
from textual.widgets import Static

from dida.sync.engine import (
    DidaError,
    Engine,
    INBOX_ID,
    ListKind,
    ListRow,
    SyncStatus,
    TaskDetail,
    UnknownTaskError,
    ViewDefinition,
    ViewFormProblem,
    is_a_move,
    is_list_edit,
    is_view_edit,
    parse_view_form,
)
from dida.tui import messages, theme
from dida.tui.escape import open_in_browser, task_url
from dida.tui.keys import (
    GLOBAL,
    LAYER_DETAIL,
    LAYER_INDEX,
    LAYER_TASKS,
    LAYER_TITLES,
    bindings_for,
    help_body,
)
from dida.tui.overlays import ConfirmOverlay, FormOverlay, MessageOverlay, multi_values
from dida.tui.pages import DetailPage, IndexPage, TasksPage
from dida.tui.pages.detail import LIST_FIELD, PRIORITY_FIELD, TAGS_FIELD, picker_spec
from dida.tui.pages.index import (
    KIND_VIEW,
    LIST_COLOR_FIELD,
    LIST_NAME_FIELD,
    NEW_KIND_FIELD,
    list_form_fields,
    list_scope_ids,
    list_write_refusal,
    new_kind_fields,
    view_form_fields,
    view_form_hint,
    view_write_refusal,
)
from dida.tui.pages.tasks import NEW_TASK_TITLE_FIELD, new_task_form_fields

if TYPE_CHECKING:  # 只为了标注周期泵那个句柄，运行时用不到
    from textual.timer import Timer

__all__ = [
    "DidaApp",
    "PENDING_STYLE",
    "Stage",
    "StatusBar",
    "TopBar",
    "format_status",
    "status_line",
    "top_line",
]

SYNC_GROUP = "sync"
"""同步 worker 的组名：``exclusive=True`` 靠它保证同时只有一轮同步在跑。"""

PENDING_STYLE = theme.PENDING
"""待推送数量非零时的高亮（用户故事 100）。

十六色的名字只有一个出处（:mod:`dida.tui.theme`），这里留一个别名给老读者。
"""


class TopBar(Static):
    """顶栏：词标 + 当前导航路径（GLOSSARY 的「顶栏」：它说「你在哪」）。"""


class StatusBar(Static):
    """状态栏：已同步时刻、待推送数量（非零时高亮）、当前逻辑日。

    它与顶栏是分工关系（ADR-0007 四）：**顶栏说「你在哪」，状态栏说「数据怎么样」**。
    """


class Stage(HorizontalScroll):
    """三层页面并排停在这里；换层就是把它横向滚过一整屏。

    换层 = 平移不是装饰：``→`` 压栈、``←`` 出栈本来就是**导航栈**（GLOSSARY 的
    「导航路径」），左右平移正是这个语义的标准表达。页面底色必须不透明（:data:`CSS_PAGE`），
    否则滑走的那块会漏出后面的东西。

    ## 它是**程序驱动**的，用户推不动它（工单 #59）

    它是个真能横向滚动的容器（实测 ``virtual_size`` 300×28、``container_size`` 100×28、
    ``max_scroll_x`` 200），于是继承了一整套用户滚动入口。实测三条路都能把它推走，而且都是
    「屏幕和状态对不上」那一类：

    - **滚动键**（``←`` / ``→`` / ``home`` / ``end`` / ``ctrl+pageup``…）：挪一格，那条铺满
      整幅的规则线当场少一格；``home`` 在详细页直接跳回第一页，而 app 仍认为你在第三层。
    - **滚轮**：普通滚轮不动（这一轴纵向没得滚），但 ``shift`` / ``ctrl`` / 横向倾斜滚轮
      走的是 ``scroll_*`` **方法**（``Widget._on_mouse_scroll_down`` → ``_scroll_right_for_pointer``），
      照样推得动——而这正是滚列表时最容易手滑撞上的那个手势。
    - **聚焦**：``tab`` 去聚焦**别层的页面**，Textual 出于好意把那个控件「滚进可见区」
      （``Screen.set_focus`` → ``scroll_to_center``），一次就把轨道拖走**整整一页**。

    **挡住它们的不是一张键位清单，而是让这一轴对用户不可滚**：``#stage`` 的
    ``overflow-x: hidden``（:func:`dida.tui.theme.app_css`，那里的注释写了为什么）。于是
    ``allow_horizontal_scroll`` 为假，上面三个入口在 Textual 自己那一层就不成立——action
    开头就 ``SkipAction``、滚轮处理器连条件都不进、``scroll_to_region`` 把 x 抹成 0。
    这比逐个记住哪些键要挡住强：**将来 Textual 再长出一个滚动入口也一并挡着**，而
    ``tests/test_visual_identity.py`` 里那条守卫断言的是这个总闸本身（谁把 ``overflow-x``
    改回 ``scroll`` / ``auto``，它当场变红）。

    程序滚动走的是同一个闸上的正当口子：:meth:`show` 与 :meth:`on_resize` 用
    ``scroll_to(force=True)``——``force`` 就是「我知道这一轴不许用户滚，但这是我自己要滚」。

    为什么不是「别用 :class:`HorizontalScroll`」：``scroll_to`` 与三页并排的版式都建在它
    上面，换掉它不是修一个缺陷，是把 ADR-0007 的平移重做一遍。滚动条那条路本来就是关着的
    （``scrollbar-size-*`` 都是 0，何况现在 overflow 也不是 scroll 了）。

    ## 它自己也**不是个焦点目标**（工单 #60）

    轨道上没有键位（``j`` / ``→`` / … 全在页面上），可它继承了 ``ScrollableContainer``
    的 ``can_focus = True``，于是 ``tab`` 会在它身上停一站。聚焦它什么也不会发生，却让焦点
    离开了页面——而页面才是按键该去的地方：``_show`` 每次都把焦点交给当前那一页，用户按下
    去的键就该落在那一页上。所以这里把 ``can_focus`` 关掉。
    """

    can_focus = False
    """轨道不给聚焦：换层时 ``_show`` 会把焦点交给**页面**，而这一格自己没有键位（工单 #60）。"""

    def show(self, index: int, *, animate: bool = True) -> None:
        """把第 ``index`` 页滑到眼前（``animate=False`` 就是直接到）。

        ``force=True`` 是**承重的**：这一轴对用户不可滚（见类文档），不加 force 的话
        ``scroll_to`` 会把 x 丢掉——换层就再也不动了。
        """
        self._index = index
        target = float(index * self.size.width)
        if not animate or self.size.width <= 0:
            self.scroll_to(x=target, animate=False, immediate=True, force=True)
        else:
            self.scroll_to(
                x=target,
                animate=True,
                duration=theme.PAN_MS / 1000,
                easing="out_cubic",
                force=True,
            )

    def on_resize(self) -> None:
        """窗口宽度变了：每一页的位置跟着变，得把当前那一页重新对齐（不滑，也不加动效）。"""
        index = getattr(self, "_index", 0)
        if self.size.width > 0:
            self.scroll_to(x=float(index * self.size.width), animate=False, immediate=True, force=True)

    _index = 0
    """当前该对齐在第几页；``show()`` 记下来，宽度变了由 :meth:`on_resize` 用它重算。"""


def top_line(path: Sequence[str]) -> Text:
    """顶栏那一行：词标 + 导航路径。

    颜色只进 span（``Text().append(style=…)``）：``Text("dida", style="cyan")`` 会走 Textual
    的 CSS 颜色解析，把真彩色偷偷放回来（``theme`` 的模块文档里写了这条坑）。
    """
    line = Text()
    line.append(f" {theme.WORDMARK_ICON} dida", style=f"{theme.ACCENT} {theme.HEADING}")
    for index, segment in enumerate(path):
        line.append(f"  {theme.BUILTIN_MARK}  ", style=theme.MUTED)
        line.append(segment, style=theme.HEADING if index == len(path) - 1 else theme.MUTED)
    return line


def status_line(status: SyncStatus, *, spinner: str = "") -> Text:
    """状态栏那一行：待推送非零时那一段高亮，其余不变。

    措辞一个字都没改（``GLOSSARY.md``：已同步 / 待推送 / 逻辑日）——加的是**非零时高亮**
    这一个信号：那个数说明本地比服务端新（ADR-0002 的豁免代价），看不见它就会以为
    「按了就是发出去了」。

    ``spinner`` 是同步超过阈值之后才出现的那一帧（默认空串 = 平时一个字都不多）。
    """
    logical_day = status.logical_day.strftime("%m-%d") if status.logical_day else "—"
    last_refresh = status.last_refresh_at.strftime("%H:%M") if status.last_refresh_at else "—"
    text = Text(f"{spinner}{'同步中 · ' if spinner else ''}已同步 {last_refresh} · ")
    text.append(f"待推送 {status.pending_count}", style=PENDING_STYLE if status.pending_count else "")
    text.append(f" · 逻辑日 {logical_day}")
    return text


def format_status(status: SyncStatus) -> str:
    """状态栏那一行的**纯文本**（措辞的唯一来源还是 :func:`status_line`）。"""
    return status_line(status).plain


def save_line(status: SyncStatus) -> str:
    """详细页底部那一行的纯文本：**这一下到底出去没有**（用户故事 65 + 81）。

    三种读法，一个都不许含糊：推不出去就带**具体**原因（断网、凭据失效、服务端拒绝的原话），
    队列里还有改动就报数，都没有才是「已保存」。只说一句「保存失败」的话，用户不知道该刷新、
    该重连、还是该重新粘 token——那是三种完全不同的下一步。
    """
    if status.last_error:
        return messages.field_save_failed_message(status.last_error)
    if status.pending_count:
        return messages.pending_message(status.pending_count)
    return messages.saved_message()


class DidaApp(App[None]):
    """一栏 + 三层页面 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助是 h
    BINDINGS = bindings_for(GLOBAL)
    """全局那几条（退出 / 同步 / 浏览器 / 帮助）。各层自己的键在各自的页面上——
    所以 ``h`` 列出来的、以及 footer 上显示的，都是**当前这一层真正能按的**那些。"""

    CSS = theme.app_css()
    """外观**一个来源**（:mod:`dida.tui.theme`）：页面底色、顶栏/状态栏、浮层、toast、
    滚动条全在那里。这个类里一行颜色都不写。"""

    def __init__(
        self,
        engine: Engine,
        *,
        open_url: Callable[[str], bool] = open_in_browser,
        refresh_on_start: bool = False,
        push_tick_seconds: float | None = None,
        animations: str | None = None,
        day_boundary: Callable[[], str | None] | None = None,
    ) -> None:
        """``open_url`` 是**注入**的浏览器开手（工单 #19）。

        生产默认值 :func:`~dida.tui.escape.open_in_browser` 会真的叫起系统浏览器；测试塞一个
        假的进来，于是「交给浏览器的是哪条 URL」能当场断言，而没有一个标签页被打开。它回
        ``False`` 或抛异常都表示这台机器上开不了浏览器。

        ``refresh_on_start`` 与 ``push_tick_seconds`` 是**策略**，默认都不开（工单 #21）：
        产品行为由组合根按 ``config.toml`` 决定（``dida.bootstrap`` 传 ``refresh_on_start=``
        与 ``PUSH_TICK_SECONDS``）。这里不写死默认值，是为了让「直接 new 一个 app」的测试
        不必先接上客户端与存储——后台同步需要一个真引擎才跑得起来。

        ``day_boundary`` 是**重新读出当前日界的那只手**（工单 #46）：组合根把配置文件的读手
        （``dida.config.DayEndReader.current``）交给它，app 在两处问它——周期泵那一秒一次的
        心跳、以及用户按 ``r``。它回 ``None``（配置读不了）就沿用引擎里那个。不给就是
        「没有人能告诉我新的日界」：直接 new 一个 app 的测试不必为此准备一个配置文件。

        ``ansi_color=True`` 是**跟随终端主题**那一条决定的落点（ADR-0007 一）：Textual 默认
        会把每个 ``ansi_*`` 改写成 Monokai 的真彩色（``ansi_cyan`` → ``#58D1EB``），用户的
        调色板一眼都用不上；打开之后同一条 CSS 发出的是 ``\\x1b[36m``，由终端说了算。

        ``animations`` 是 ``auto|on|off`` 开关（ADR-0007 三）。不给就走 ``DIDA_ANIM``，
        再不给就是 ``auto``：ssh 与低能力终端上自动关掉。这是**换层平移**的闸（ADR-0008
        四撤掉光标条之后，它是这一档动效唯一的用武之地）：关掉时换层不滑，屏幕一步到位。
        """
        super().__init__(ansi_color=True)
        self.engine = engine
        self._open_url = open_url
        self._refresh_on_start = refresh_on_start
        self._push_tick_seconds = push_tick_seconds
        self._day_boundary = day_boundary
        """重读当前日界的那只手（工单 #46）；``None`` = 没人能告诉它新的日界。"""
        self._view_day: date | None = None
        """屏幕上那些行是按**哪一个逻辑日**算出来的（工单 #46）。

        它是「这一屏过期了没有」的凭据：钟自己走过边界时配置一个字节都没变，只有把这一屏
        是哪一天记下来，心跳才分得清「还是同一天」与「已经翻篇了」。``None`` = 还没画过。"""
        self._push_timer: Timer | None = None
        """周期泵的定时器句柄（工单 #21）：``on_unmount`` 里拿它把泵停掉。

        ``set_interval`` 回一个 ``Timer``，丢掉它就没有第二个人能停这一跳——关窗之后
        它还挂在事件循环上。``None`` 表示泵没开（策略没给间隔）或者已经停了。"""
        self._layer = LAYER_INDEX
        """当前在屏上的是哪一层（``h`` 与 ``o`` 都按它说话）。"""
        self._container_id: str | None = None
        """当前打开的是哪个容器（清单或视图的 id）；``None`` = 还没进过任何一层。"""
        self._detail_task_id: str | None = None
        """详细页正在说的是哪条任务。"""
        self._container_title: str | None = None
        """当前容器（清单或视图）的**名字**——顶栏那段路径要写它。"""
        self._detail_title: str | None = None
        """详细页那条任务的标题——路径的最后一段写它。"""
        self._animations = animations or theme.animations_setting(os.environ)
        self._motion = False
        """这一台机器上换层动不动（``on_mount`` 里按开关与环境定一次）。

        ⚠ 名字**不能**是 ``_animate``：``App._animate`` 是 Textual 自己那个绑好的 animator
        （``App.animate()`` 调的就是它），盖掉之后 ``app.animate(...)`` 会抛
        ``TypeError: 'bool' object is not callable``——报错点在 Textual 的 ``app.py`` 里，离
        现场很远。页面自己不再有第二个动效开关（ADR-0008 四把光标条撤了）。
        """
        self._editing_list: str | None = None
        """正在改的是哪条清单（表单关掉时要用它；``None`` = 那一次是新建）。"""

        self._editing_view: str | None = None
        """正在改的是哪个自定义视图（#36）；``None`` = 那一次是新建视图。"""
        self._announce_sync = False
        """这一轮同步要不要用 toast 报完成——``r`` 要，启动刷新不要（那会每次开屏都弹一下）。"""
        self._manual_sync = False
        """这一轮推送要不要再试已经放弃的改动（工单 #71）——``r`` 要，启动刷新与周期泵不要。

        与 :attr:`_announce_sync` 同一段寿命、同一处写下（:meth:`start_sync`）、同一处读一次
        （:meth:`_sync`）。分开两个名字而不是共用一个：toast 是给用户看的回声，「再试一次」
        是队列的策略，将来其中一个变了不会顺手把另一个带偏。
        """
        self._spinning = False
        self._spinner_frame = 0
        self._spinner_timer: Timer | None = None
        self._spin_delay_timer: Timer | None = None

    # ---------------------------------------------------------------- 组装

    def compose(self) -> ComposeResult:
        yield TopBar(id="top-bar")
        with Stage(id="stage"):
            yield IndexPage(id=LAYER_INDEX)
            yield TasksPage(id=LAYER_TASKS)
            yield DetailPage(id=LAYER_DETAIL)
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        """开屏：**先**把本地缓存画上屏，网络刷新排在事件循环上不等它（用户故事 3/4）。

        动效的开关只在这里定一次：``auto|on|off`` 落到**换层平移**这一个地方（ADR-0008 四
        撤掉光标条之后，页面自己不再有动效开关）。
        """
        self._motion = theme.animations_enabled(self._animations)
        if not self._motion:
            self.animation_level = "none"
        self._show(LAYER_INDEX)
        self.refresh_view()
        if self._refresh_on_start:
            self.start_sync()
        if self._push_tick_seconds is not None:
            # 重试队列的泵（t21）：写失败时改动留在队列里，退避到点了得有谁来推它。
            # 句柄留着，关窗时好把它停掉（on_unmount）——泵的开关归这一层管。
            self._push_timer = self.set_interval(self._push_tick_seconds, self.push_tick)

    def on_unmount(self) -> None:
        """关窗：把周期泵停掉——app 都拆了，没有人再需要它问那句「到点了没有」。

        停掉只挡得住**后面**的跳；已经在飞的那一次要等 ``await`` 回来才算数，那由
        :meth:`_write_status` 与 :meth:`refresh_view` 的「屏幕还在不在」守着。两半都要：
        只停定时器关不掉已经跨过 ``await`` 的那一次。
        """
        if self._push_timer is not None:
            self._push_timer.stop()
            self._push_timer = None
        self._stop_spinner()

    # ---------------------------------------------------------------- 三层进出

    @property
    def layer(self) -> str:
        """当前在屏上的是哪一层。"""
        return self._layer

    def index_page(self) -> IndexPage:
        return self.query_one(IndexPage)

    def tasks_page(self) -> TasksPage:
        return self.query_one(TasksPage)

    def detail_page(self) -> DetailPage:
        return self.query_one(DetailPage)

    def _pages(self) -> dict[str, IndexPage | TasksPage | DetailPage]:
        """三层各是哪个控件，按层名索引。"""
        return {
            LAYER_INDEX: self.index_page(),
            LAYER_TASKS: self.tasks_page(),
            LAYER_DETAIL: self.detail_page(),
        }

    def _show(self, layer: str) -> None:
        """把这一层滑到眼前，并把焦点交给它（``j``/``k``/``→`` 立刻能用）。

        三层**都在 DOM 里**、并排停在 :class:`Stage` 上：它们的行与光标因此原样留着，
        ``←`` 回去时用户看到的就是他离开时那一行（用户故事 20/62）。换层是横向平移——
        ``→`` 压栈、``←`` 出栈本来就是导航栈（ADR-0007 三）。
        """
        previous = self._layer
        self._layer = layer
        page = self._pages()[layer]
        self._write_top()
        self._stage().show(self._layer_index(layer), animate=self._motion and layer != previous)
        # ``scroll_visible=False``：#59 **之前**它是承重的——Textual 交焦点时默认会把那个控件
        # **立刻**滚进可见区，而这一页正好是整个舞台（平移就是把它滑过来），那一下会把动画
        # 当场抹平。现在 ``#stage`` 的 ``overflow-x`` 关成了 ``hidden``，横向根本滚不动，
        # 于是它**已经不是**动画的保障（实测 2×2：闸开着时 ``True`` 照样有 9 帧动画）。
        # 留着它是第二道保险：闸若被改回 ``scroll``，动画与输入会一起坏。
        page.focus(scroll_visible=False)
        # 切回来时光标不止要「还在那一行」，还要看得见（#34 的验收标准 8）。
        page.scroll_cursor_into_view()

    def _stage(self) -> Stage:
        return self.query_one("#stage", Stage)

    @staticmethod
    def _layer_index(layer: str) -> int:
        """这一层是并排三页里的第几页（顺序就是 ``compose`` 的顺序）。"""
        return (LAYER_INDEX, LAYER_TASKS, LAYER_DETAIL).index(layer)

    def open_container(self, container_id: str) -> None:
        """进层二：某个清单或视图里的任务（``→``）。"""
        self._container_id = container_id
        self._detail_task_id = None
        self.refresh_view()
        self._show(LAYER_TASKS)

    def open_detail(self, task_id: str) -> None:
        """进层三：一条任务的详细页（任务列表页上按 ``→``）。"""
        self._detail_task_id = task_id
        self.refresh_view()
        self._show(LAYER_DETAIL)

    def back_to_index(self) -> None:
        """回层一（任务列表页上按 ``←``）——光标照旧停在他进来的那一行。"""
        self._detail_task_id = None
        self._show(LAYER_INDEX)

    def back_to_tasks(self) -> None:
        """回层二（详细页上按 ``←``）——光标照旧停在他进来的那条任务上。"""
        self._show(LAYER_TASKS)

    # ---------------------------------------------------------------- 重画

    def reload_day_boundary(self) -> bool:
        """重新问一次当前日界；屏幕跟不上了就按新的逻辑日重画（工单 #46）。返回「重画过没有」。

        「逻辑日改了立刻生效」有**两条**路，两条都在这里收口，因为它们要挂的是同一个时机：

        - 配置里改了边界值——外部事件（用户在另一个窗口里改），没人通知得了这个进程；
        - 钟自己走过了边界——终端里挂一夜，早上那一屏就是按昨天算的。

        那个时机是**两个键的最前面**：:meth:`push_tick`（周期泵那一秒一次的心跳）与
        :meth:`action_refresh`（``r``），都在任何网络调用之前。第二条尤其靠这一点——
        ``_sync()`` 里的重画在 ``else:``（同步成功）那一支里，刷新抛 :class:`DidaError` 时
        一次都不跑，而收尾的 ``update_status()`` 照样按新逻辑日写状态栏。于是「没网的时候按了
        一下 ``r``」得到的正是那个自相矛盾的屏幕：状态栏是新日子，列表还是旧成员。

        **滚过那条不在下面那个 ``None`` 判断后面**：它不是配置事件，一个没建读手的 app
        （``day_boundary=None``）照样得发现它。

        配置读不到（``None``）时沿用引擎里那个日界：界面不崩，也不替用户按默认值来。

        **光标不归这次重画管**：各页按行 id 把它认回原来那一行（``CursorPage.set_rows``），
        所以重算不会把人踢回第一行（验收标准 3）。
        """
        boundary_moved = False
        if self._day_boundary is not None:
            day_end = self._day_boundary()
            if day_end is not None:
                boundary_moved = self.engine.set_day_end(day_end)
        rolled_over = self._view_day is not None and self.engine.logical_day() != self._view_day
        if not (boundary_moved or rolled_over):
            return False
        self.refresh_view()
        return True

    def refresh_view(self) -> None:
        """读引擎的三种读形状，重画在屏上的那几层与状态栏。

        三层都重画（不在屏上的那两层也重画）：它们的行是**同一份缓存**算出来的，只画在屏
        上的那一层，切回去时会看到一屏过期的东西。光标不归这里管——各页按**行 id** 把光标
        认回原来那一行（用户故事 21/57：刷新不许把人踢回第一行）。

        屏幕已经拆掉时整体是空操作（关窗中，这一次回来晚了，见 :meth:`_write_status`）。
        """
        if not self.is_running:
            return
        # 先记下「这一屏是哪一天的」，再画行：反过来的话，边界正好在这几句里跨过去时，会记下
        # 一个比行更新的日子，那一屏就永远没人认领了（心跳以为它是最新的，见
        # :meth:`reload_day_boundary`）。记早了最多多画一次，记晚了就是一屏昨天的东西。
        self._view_day = self.engine.logical_day()
        rows = self.engine.list_index()
        self.index_page().show_lists(rows)
        self._container_title = (
            None if self._container_id is None else self._container_name(rows, self._container_id)
        )
        if self._container_id is not None:
            self.tasks_page().show_tasks(
                self.engine.tasks_in(self._container_id), name=self._container_title or ""
            )
        if self._detail_task_id is not None:
            detail = self.engine.task_detail(self._detail_task_id)
            self._detail_title = None if detail is None else detail.title
            # 时区提示走**注入的钟**：``status().checked_at`` 就是那只钟给的时刻，它的
            # ``tzinfo`` 是用户墙钟当前的时区。详细页要把用户敲的日期与时刻理解成一个时刻，
            # 而它自己不读时钟（README：「业务代码不许调 datetime.now()」，#58 的 T1）。
            self.detail_page().show_detail(detail, zone=self.engine.status().checked_at.tzinfo)
        self.update_status()
        self._write_top()

    @staticmethod
    def _container_name(rows: tuple[ListRow, ...], container_id: str) -> str:
        """容器在标题里写什么：引擎给的清单名；认不出来（远端刚删掉）就照原样写 id。"""
        row = next((item for item in rows if item.id == container_id), None)
        return row.name if row is not None else container_id

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏与详细页底部那一行。数据变化后都调它。

        两处说的是两件事（ADR-0007 四）：状态栏说「数据怎么样」（已同步 / 待推送 / 逻辑日），
        详细页那一行说「你刚才那一下出去没有」。同一个 ``status()`` 读出来的两份读法，
        所以它们永远不会互相矛盾。
        """
        status = self.engine.status()
        self._write_status(status_line(status, spinner=self._spinner()))
        self._write_save_line(save_line(status))

    def _write_save_line(self, text: str) -> None:
        """把详细页底部那一行写掉——与状态栏同一条规矩：**先问屏幕还在不在**。

        它由 ``await`` 之后的那几次重画调到（逐字段编辑那一条路正好是跨 ``await`` 的），
        关窗时页面已经拆了，再往它上面写就是 ``NoMatches``。
        """
        if not self.is_running:
            return
        self.detail_page().show_save(text)

    def _write_top(self) -> None:
        """把当前导航路径刷进顶栏（GLOSSARY 的「导航路径」：它是走出来的，不是猜的）。"""
        if not self.is_running:
            return
        self.query_one(TopBar).update(top_line(self.nav_path()))

    def nav_path(self) -> tuple[str, ...]:
        """当前导航路径：清单列表页 → 任务列表页 → 任务详细页，最多三级。

        第一段是**页面**（它只有三种），后面两段是走在那一页上的**东西**：容器名与任务名。
        「你在哪」在详细页的答案就是「在哪条任务上」，所以最后一段写的是它。
        """
        index = LAYER_TITLES[LAYER_INDEX]
        if self._layer == LAYER_TASKS:
            return (index, self._container_title or LAYER_TITLES[LAYER_TASKS])
        if self._layer == LAYER_DETAIL:
            return (
                index,
                self._container_title or LAYER_TITLES[LAYER_TASKS],
                self._detail_title or LAYER_TITLES[LAYER_DETAIL],
            )
        return (index,)

    def _write_status(self, message: str | Text) -> None:
        """把一句话写进状态栏——TUI 里状态栏的**唯一**写入口。

        关窗时丢掉它：``await`` 回来的路上 app 可能已经拆了（用户按 ``q``、或者 ``run_test``
        收尾），那一刻 widget 已经不在 DOM 里，再往状态栏写就是 ``NoMatches``。周期泵正好
        撞在这个窗口上（工单 #41 观察到的偶发红，属地归 #34）；凡是 ``await`` 之后写状态栏
        的路都走这里，省得每处各记一次。

        只在**屏幕已经不在跑**时放过：app 还在跑时状态栏不见了仍然是 bug，照旧让
        ``NoMatches`` 冒出去，不吞。
        """
        if not self.is_running:
            return
        self.query_one(StatusBar).update(message)

    # ---------------------------------------------------------------- 页面消息

    def on_index_page_entered(self, event: IndexPage.Entered) -> None:
        """清单列表页上按了 ``→``：进这个容器。"""
        self.open_container(event.container_id)

    def on_index_page_refused(self, event: IndexPage.Refused) -> None:
        """按 ``→`` 进一个进不去的清单：如实说一句，不进下一层。"""
        self._write_status(event.message)

    def on_tasks_page_entered(self, event: TasksPage.Entered) -> None:
        """任务列表页上按了 ``→``：进这条任务的详细页。"""
        self.open_detail(event.task_id)

    def on_tasks_page_back(self, event: TasksPage.Back) -> None:
        """任务列表页上按了 ``←``：回清单列表页。"""
        self.back_to_index()

    def on_tasks_page_toggle_complete(self, event: TasksPage.ToggleComplete) -> None:
        """任务列表页上按了 ``space``：完成 / 取消完成（工单 #38）。

        两个方向都是**乐观写**（ADR-0002）：本地当场生效、立即推送，界面不等网络。
        ``event.completed`` 是按下那一刻读模型里的状态，所以「取消完成」这条路的判据是
        服务端的 ``status``，不是这一层记的什么东西。

        引擎当场拒绝（本地已经没有这条任务的底稿，工单 #25）时如实说一句——按下去什么都
        不发生，用户会以为它成了。**这一条路没有 ``await``**：``complete`` / ``uncomplete``
        都是同步的（推送排在事件循环上，写的人当场返回），所以写完之后碰 DOM 不需要
        「先问 ``is_running``」那道守卫；重画与 toast 各自还有一道，见它们的说明。

        **完成这一下顺手排一次已完成流**（:meth:`pull_completed`，工单 #74）：本地刚完成的任务
        还没有 ``completedTime``（服务端要等推送落地才写），而周期泵只管推、不拉——不去拉它，
        那个时间戳永远回不来。取消完成不用拉：本地的 ``status`` 当场说了算（#38）。
        """
        try:
            if event.completed:
                self.engine.uncomplete(event.task_id)
            else:
                self.engine.complete(event.task_id)
        except DidaError as exc:
            self._write_status(messages.toggle_complete_failed_message(exc))
            return
        self.refresh_view()
        if not event.completed:
            self.pull_completed()
        self._notify_step(
            messages.uncompleted_message(event.title)
            if event.completed
            else messages.completed_message(event.title)
        )

    def on_detail_page_back(self, event: DetailPage.Back) -> None:
        """详细页上按了 ``←``：回任务列表页。"""
        self.back_to_tasks()

    # ---------------------------------------------------------------- 新建任务（#39）

    def on_tasks_page_new_task(self, event: TasksPage.NewTask) -> None:
        """``n``：开「只填标题」的表单（字段见 :func:`~dida.tui.pages.tasks.new_task_form_fields`）。"""
        self.push_screen(
            FormOverlay(title="新建任务", fields=new_task_form_fields()),
            self._finish_new_task,
        )

    def _finish_new_task(self, values: dict[str, str] | None) -> None:
        """表单关掉了：按填的标题建一条（空标题不建，如实说一句）。

        ``None`` 那条分支从 #66 起走不到了（表单没有「取消」，``Esc`` 就是保存），留着只是
        防御——调用方按交回来那一份值判断，这一层不认识表单的键位。

        落点与隐含日期都**读读模型**（:meth:`~dida.sync.engine.Engine.tasks_in`）：

        - 真实清单里建 → 落在当前打开的这个清单里（用户故事 45）；
        - 视图里建 → 落在收集箱（``INBOX_ID``，视图不是容器），视图隐含的日期
          （``TaskList.implied_due``，「今天」才有）跟着带上（用户故事 35）。

        「这个容器是不是视图」只在一处判（``TaskList.shows_list_name``），这一层不自己认识
        视图——两处各判一次就是两处会漂。
        """
        if values is None:
            return
        title = values.get(NEW_TASK_TITLE_FIELD, "").strip()
        if not title:
            self._write_status(messages.NO_TITLE_MESSAGE)
            return
        container = self._container_id
        if container is None:
            return
        task_list = self.engine.tasks_in(container)
        destination = INBOX_ID if task_list.shows_list_name else container
        try:
            self.engine.create(
                title,
                destination,
                due=task_list.implied_due,
                # 隐含日期写成**全天**任务的日期标记：「今天」的意思是「今天要做」，
                # 不是某个时刻（那个逻辑日自己怎么算由引擎给，这一层不算日期）。
                all_day=task_list.implied_due is not None,
            )
        except DidaError as exc:
            self._write_status(messages.create_failed_message(exc))
            return
        self.refresh_view()

    async def on_detail_page_due_changed(self, event: DetailPage.DueChanged) -> None:
        """详细页上提交了截止时间：**改期 / 清除**立刻写出去（工单 #44）。

        与 :meth:`on_detail_page_field_edited` 同一条路（乐观写 + 立刻推 + 刷新），只是这一笔
        的形状是 ``{dueDate, isAllDay}``：走引擎的 ``reschedule``——它只动这两个字段，整份
        底稿照旧回写（重复规则、时区、陌生字段一个不丢）。

        ``event.due is None`` 是**清除**：写显式的 ``dueDate: null``，任务变回「没有日期」。
        写失败与逐字段编辑那一侧同一套说法（用户故事 81）：本地没有底稿的拒绝用现成那句，
        其余带上引擎/服务端说的具体原因。
        """
        try:
            self.engine.reschedule(event.task_id, due=event.due, all_day=event.all_day)
        except UnknownTaskError:
            self.refresh_view()
            if self.is_running:
                self.detail_page().show_save(messages.UNKNOWN_TASK_MESSAGE)
            return
        except DidaError as exc:
            self.refresh_view()
            if not self.is_running:
                return
            self.detail_page().show_save(messages.field_save_failed_message(exc))
            return
        await self.engine.push_pending()
        if not self.is_running:
            return
        self.refresh_view()

    async def on_detail_page_field_edited(self, event: DetailPage.FieldEdited) -> None:
        """详细页上改完一个字段：**立刻写出去**，并把结果留在那一页底部（工单 #43）。

        乐观写（``write``）：本地当场生效、推送排到事件循环上立刻跑——用户按完 ``esc`` 不等
        网络。后面那一次 ``push_pending`` 是**等这一笔落地**的确定性那一次（队列只有一条，
        两次推送不会重复发），它回来之后底部那一行才知道该写「已保存」还是「待推送（N）」。

        写失败分两种，两种都说出**具体**原因（用户故事 81）：引擎当场拒绝（本地没有这条任务
        的底稿）在这里接住，用 ``messages`` 里那一句现成的话；推不出去（断网、服务端拒绝）
        由引擎记在队列上，下一次 :meth:`update_status` 会把它读出来。
        """
        try:
            self.engine.write(event.task_id, changes={event.field: event.value})
        except UnknownTaskError:
            # 「这条任务已经不在本地缓存里了，刷新之后再试一次」——本地没有底稿是一种**说得出
            # 名字**的拒绝，不该混进「保存失败」那一类里（那条留给服务端与网络说的话）。
            self.refresh_view()
            if self.is_running:
                self.detail_page().show_save(messages.UNKNOWN_TASK_MESSAGE)
            return
        except DidaError as exc:
            self.refresh_view()
            if not self.is_running:
                return
            self.detail_page().show_save(messages.field_save_failed_message(exc))
            return
        await self.engine.push_pending()
        if not self.is_running:
            return
        self.refresh_view()

    # ---------------------------------------------------------------- 挑选型字段（#45）

    async def on_detail_page_pick_requested(self, event: DetailPage.PickRequested) -> None:
        """``enter`` 落在挑选型字段上：把选项凑齐，开那张**共用的**表单浮层（工单 #45）。

        三格的选项各有各的来源，都在引擎那一侧：清单是 ``move_targets()``（真实清单、
        进得去、服务端已经见过的那些），优先级是 ``PRIORITY_NAMES`` 那张表（它经引擎的
        公开面转出：``messages.PRIORITY_NAMES``，本体的家在 ``dida.sync.view``，工单 #58），
        标签是 ``tags()``。标签那一份还要**拉一次**（``load_tags``，``GET /open/v1/tag``）
        ——那是这一格里唯一一次网络调用，所以拉不到时照旧开浮层（本地已知的那些照样挑得动），
        只把「没拉到」写在浮层的提示里（浮层是模态的，状态栏在它底下，看不见）。
        """
        detail = self.engine.task_detail(event.task_id)
        if detail is None:
            return
        notice = ""
        if event.field == TAGS_FIELD:
            try:
                await self.engine.load_tags()
            except DidaError as exc:
                # 拉不到就说出来，而且**照旧开浮层**（本地已经见过的那些照样挑得动）；
                # 这一句写在浮层的提示里，不是状态栏上——浮层是模态的，状态栏在它底下。
                notice = messages.tags_load_failed_message(exc)
            if not self.is_running:
                return
        spec = picker_spec(
            event.field,
            detail,
            lists=self.engine.move_targets(),
            tags=self.engine.tags(),
            notice=notice,
        )
        if spec is None:
            return
        self.push_screen(
            FormOverlay(title=spec.title, fields=spec.fields, hint=spec.hint),
            partial(self._finish_pick, event.task_id, event.field),
        )

    async def _finish_pick(
        self, task_id: str, field: str, values: dict[str, str] | None
    ) -> None:
        """挑选浮层关掉了：按挑的那一份写出去（``None`` 从 #66 起走不到，表单没有「取消」）。

        三条路各自走该走的端点——**搬运不是一次普通字段更新**（``move_task``），优先级与
        标签是普通更新（整份底稿带回去那件事由 ``update_task`` 的 ``snapshot=`` 管，
        ``merge_snapshot`` 的既有策略）。写完照旧立刻推一轮、重画、把结果留在底部那一行：
        与逐字段编辑（``on_detail_page_field_edited``）同一条规矩（验收标准 7）。
        """
        if values is None:
            return
        try:
            wrote = self._apply_pick(task_id, field, values)
        except UnknownTaskError:
            self.refresh_view()
            if self.is_running:
                self.detail_page().show_save(messages.UNKNOWN_TASK_MESSAGE)
            return
        except DidaError as exc:
            self.refresh_view()
            if not self.is_running:
                return
            self.detail_page().show_save(messages.field_save_failed_message(exc))
            return
        if not wrote:
            # 挑回原来那一档：没有改动就没有「立刻推送」这回事（队列里本来也不该多出一笔）。
            return
        await self.engine.push_pending()
        if not self.is_running:
            return
        self.refresh_view()

    def _apply_pick(self, task_id: str, field: str, values: dict[str, str]) -> bool:
        """挑完的那一份怎么变成一次写（三条路各自的形状只在这一个地方）。

        **挑回原来那一档 = 没改**（与逐字段编辑那条规矩同一条）：一笔都不写，也**不排推送**。
        写一笔没发生的改动会进待推送队列，离线时状态栏那个数就为一个空操作亮着。

        清单那一路的「同一个清单」**由引擎自己挡**（``move_task`` 里问
        :func:`dida.sync.writes.is_a_move`）——下面这一行问的是**同一个函数**，不是又写一遍
        那个比较：判据只有一份，两个时刻各问一次（与 ``is_addressable_task`` 同一个形状）。
        这里非问不可，是因为 ``move_task`` 回不了话（它的签名是 ``-> None``），不问就会为一次
        根本没发生的改动推一轮（``tests/test_picker_fields.py`` 钉着那句 ``pushes == 0``）；
        工单 #58 的 T6 之前，这里确实是自己又比了一遍，而注释还写着「由引擎自己挡」。
        优先级与标签那两档是**界面自己的**判断：``write()`` 不做同值收敛，所以只有这里能挡。

        ``int(...)`` 那一下是**线上编码**：选项的值是 ``0/1/3/5``、标签是用户语言
        （``PRIORITY_NAMES``，唯一一张表——本体的家在 ``dida.sync.view``，经引擎的公开面
        转出成 ``messages.PRIORITY_NAMES``，所以这句话现在是真的，工单 #58）。表外的值不该
        出现（选项就是从那张表生成的），认不出来就当没挑——不替服务端猜一个档位。

        返回「真的写了一笔吗」：没改的那一条路连推送都不排（队列里不该多出一笔）。
        """
        detail = self.engine.task_detail(task_id)
        if detail is None:
            raise UnknownTaskError(task_id)
        if field == LIST_FIELD:
            if not is_a_move(detail.list_id, values[LIST_FIELD]):
                return False
            self.engine.move_task(task_id, to_list_id=values[LIST_FIELD])
            return True
        if field == PRIORITY_FIELD:
            picked = values[PRIORITY_FIELD]
            if not picked.isdigit() or int(picked) == detail.priority:
                return False
            self.engine.write(task_id, changes={"priority": int(picked)})
            return True
        if field == TAGS_FIELD:
            # 按**集合**比：选项顺序与任务上那一串的顺序不一定一样，而「改了没有」说的是
            # 挑中的那几个标签变没变，不是它们排在第几个。
            picked = multi_values(values[TAGS_FIELD])
            if set(picked) == set(detail.tags):
                return False
            self.engine.write(task_id, changes={"tags": list(picked)})
            return True
        return False

    # ---------------------------------------------------------------- 任务的删除与顺延（#40）

    def on_tasks_page_delete(self, event: TasksPage.Delete) -> None:
        """``d``：删光标那条任务——**先如实问一句**，``y`` 才真的删（验收标准 1、3）。

        确认文案在 :func:`dida.tui.messages.delete_prompt`：整份官方文档里没有回收站、没有
        undelete、也没有「已删除」列表（``api-shapes.md`` §A6），所以那一句话不许承诺任何恢复
        ——这次确认就是全部的防线。删掉的那条任务本地当场摘掉、推送走 ``DELETE``。
        """
        detail = self.engine.task_detail(event.task_id)
        if detail is None:
            return
        self.push_screen(
            ConfirmOverlay(messages.delete_prompt(detail.title), title="删除任务"),
            partial(self._finish_delete_task, event.task_id),
        )

    def _finish_delete_task(self, task_id: str, confirmed: bool | None) -> None:
        """删除确认关掉了：只有 ``True`` 才真的删（``n`` / ``Esc`` 与 ``None`` 都不动）。"""
        if not confirmed:
            return
        try:
            self.engine.delete(task_id)
        except DidaError as exc:
            self._write_status(messages.delete_failed_message(exc))
            return
        self.refresh_view()

    def on_tasks_page_defer(self, event: TasksPage.Defer) -> None:
        """``g`` / ``G``：顺延 ``days`` 个逻辑日，**截止时间以外的字段一个都不动**（验收标准 4–7）。

        落点由引擎按注入的日界算（``sync/schedule.py``），这一层不重算日期、也不经过任何日期
        解析。没有截止时间的任务引擎不动它——顺延不凭空给一条任务长出一个日期来，所以这里
        没有「当场失败」要报的那种情况（引擎那一支没有可抛的结构化错误）。
        """
        self.engine.defer(event.task_id, days=event.days)
        self.refresh_view()

    # ---------------------------------------------------------------- 清单 / 视图的建 / 改 / 删（#42 / #36）

    def on_index_page_new_list(self, event: IndexPage.NewList) -> None:
        """``n``：**先问一句「清单还是视图」**（#36 的验收标准 1），再开对应的那张表单。

        两种东西后面完全是两回事：清单是服务端的容器（建了要推上去），视图只是一组只存在
        本机的过滤条件。问一句比猜一个默认值好——猜错了用户会建出一个自己没想要的东西，
        而且视图建错了在手机上还找不到它。
        """
        self.push_screen(
            FormOverlay(
                title="新建什么？",
                fields=new_kind_fields(),
                hint=messages.NEW_KIND_HINT,
            ),
            self._finish_kind_form,
        )

    def _finish_kind_form(self, values: dict[str, str] | None) -> None:
        """「清单还是视图」答完了：开对应的表单（``None`` 从 #66 起走不到，表单没有「取消」）。"""
        if values is None:
            return
        if values.get(NEW_KIND_FIELD) == KIND_VIEW:
            self._open_view_form(None)
        else:
            self._open_list_form(None)

    def on_index_page_edit_list(self, event: IndexPage.EditList) -> None:
        """``e``：改光标那一行——清单给清单那张表单，自建视图给条件表单（#36）。"""
        row = self.index_page().row(event.row_id)
        if row is None:
            return
        refusal = self._refusal_for(row)
        if refusal is not None:
            self._write_status(refusal)
            return
        if row.kind is ListKind.LIST:
            self._open_list_form(row.id)
        else:
            self._open_view_form(row.id)

    def on_index_page_delete_list(self, event: IndexPage.DeleteList) -> None:
        """``d``：删光标那一行——**先如实问一句**，``y`` 才真的删（验收标准 3、4、5）。

        清单与视图各问各的：删清单那句说「它里面的任务会怎样文档没写」（核实过，见
        :func:`dida.tui.messages.delete_list_prompt`），删视图那句说「不会动任何任务」
        ——视图只是一组过滤条件，这一句是能保证的（#36）。
        """
        row = self.index_page().row(event.row_id)
        if row is None:
            return
        refusal = self._refusal_for(row)
        if refusal is not None:
            self._write_status(refusal)
            return
        if row.kind is ListKind.LIST:
            self.push_screen(
                ConfirmOverlay(messages.delete_list_prompt(row.name), title="删除清单"),
                partial(self._finish_delete_list, row.id),
            )
        else:
            self.push_screen(
                ConfirmOverlay(messages.delete_view_prompt(row.name), title="删除视图"),
                partial(self._finish_delete_view, row.id),
            )

    def _refusal_for(self, row: ListRow) -> str | None:
        """这一行改不动 / 删不掉时的那句话；清单行走 #42 那份判断，视图行走 #36 那份。

        两份判断各自只有一处（``pages/index.py`` 的两个 ``*_write_refusal``）：清单那三种
        改不动的行与视图那一种（内置视图）理由完全不同，合成一句就会说出「没有写权限」这种
        对视图毫无意义的理由。
        """
        if row.kind is ListKind.LIST:
            return list_write_refusal(row)
        return view_write_refusal(row)

    def _open_list_form(self, row_id: str | None) -> None:
        """开清单表单：``row_id`` 是 ``None`` 就是新建，否则是改那一行。

        改不动的行（收集箱、没有写权限的清单）在这里就挡住并说清是哪一种——表单
        开出来再拒绝，用户会以为自己填错了什么。
        """
        row = None if row_id is None else self.index_page().row(row_id)
        if row_id is not None:
            if row is None:
                return
            refusal = list_write_refusal(row)
            if refusal is not None:
                self._write_status(refusal)
                return
        self._editing_list = None if row is None else row.id
        self.push_screen(
            FormOverlay(
                title="新建清单" if row is None else f"改「{row.name}」",
                fields=list_form_fields(row),
            ),
            self._finish_list_form,
        )

    def _open_view_form(
        self, view_id: str | None, values: dict[str, str] | None = None
    ) -> None:
        """开视图的条件表单：``view_id`` 是 ``None`` 就是新建，否则是改那一个。

        ``values`` 是**用户刚填的那一份**：表单被拒（认不出的清单名之类）之后重新打开时
        原样还给他——七个格子重填一遍是这一屏最不该有的惩罚。
        """
        definition = None if view_id is None else self.engine.view_definition(view_id)
        if view_id is not None and definition is None:
            # 那一行已经不在了（另一次删除、刷新之后没了）：什么都不开，也不假装改成功。
            self._write_status(messages.UNKNOWN_VIEW_MESSAGE)
            return
        self._editing_view = view_id
        self.push_screen(
            FormOverlay(
                title="新建视图" if definition is None else f"改「{definition.name}」",
                fields=view_form_fields(
                    definition, rows=self.index_page().rows(), values=values
                ),
                hint=view_form_hint(),
            ),
            self._finish_view_form,
        )

    def _finish_list_form(self, values: dict[str, str] | None) -> None:
        """清单表单关掉了：按填的那一份建 / 改（``None`` 从 #66 起走不到，表单没有「取消」）。

        颜色是空串就**不发** ``color`` 字段（那是「默认」，不是「清空」）；名字空着则
        什么都不做，只如实说一句。

        改的那一路先问 :func:`dida.sync.engine.is_list_edit`：交回来的那一份与本地那一行
        **逐字段相同**就不是一次改动，引擎一个字都不用写（#66 的验收标准 3 / 用户故事 134）。
        ``esc`` 从 #66 起是「保存并退出」，所以「开了表单又没改」这条路真的会走到。判据只有
        引擎那一份，这里问的是**同一个**函数——与 :meth:`_apply_pick` 问 ``is_a_move``
        同一个形状（接缝一上的假后端自己实现写路径，界不问就会为一次没发生的改动记下一笔）。
        """
        if values is None:
            return
        name = values.get(LIST_NAME_FIELD, "").strip()
        if not name:
            self._write_status(messages.EMPTY_LIST_NAME_MESSAGE)
            return
        color = values.get(LIST_COLOR_FIELD) or None
        editing = self._editing_list
        self._editing_list = None
        try:
            if editing is None:
                self.engine.create_list(name, color=color)
            else:
                row = self.index_page().row(editing)
                if row is not None and not is_list_edit(
                    current_name=row.name,
                    current_color=row.color,
                    name=name,
                    color=color,
                ):
                    return
                self.engine.update_list(editing, name=name, color=color)
        except DidaError as exc:
            self._write_status(messages.list_write_failed_message(exc))
            return
        self.refresh_view()

    def _finish_view_form(self, values: dict[str, str] | None) -> None:
        """视图表单关掉了：把那一份值读成定义再落本地库（``None`` 从 #66 起走不到）。

        读不成定义时（认不出的清单名、永远筛不出任务的组合）**不保存**，把理由写进状态栏
        并把用户填的那一份原样还回表单里——七个格子重填一遍是这一屏最不该有的惩罚。

        改的那一路先问 :func:`dida.sync.engine.is_view_edit`：读出来的定义与本地那一行
        **逐字段相同**就不是一次改动，本地那一行不重写（#66 的验收标准 3）。判据只有引擎那
        一份，这里问的是**同一个**函数——与清单那条、以及 :meth:`_apply_pick` 问
        ``is_a_move`` 同一个形状。
        """
        if values is None:
            return
        editing = self._editing_view
        parsed = parse_view_form(
            values,
            view_id=editing or "",
            lists=list_scope_ids(self.index_page().rows()),
        )
        if isinstance(parsed, ViewFormProblem):
            self._write_status(messages.view_form_problem(parsed))
            self._open_view_form(editing, values=dict(values))
            return
        assert isinstance(parsed, ViewDefinition)
        self._editing_view = None
        try:
            if editing is None:
                self.engine.create_view(parsed)
            else:
                current = self.engine.view_definition(editing)
                if current is not None and not is_view_edit(current, parsed):
                    return
                self.engine.update_view(parsed)
        except DidaError as exc:
            self._write_status(messages.view_write_failed_message(exc))
            return
        self.refresh_view()

    def _finish_delete_list(self, list_id: str, confirmed: bool | None) -> None:
        """删除确认关掉了：只有 ``True`` 才真的删（``n`` / ``Esc`` 与 ``None`` 都不动）。"""
        if not confirmed:
            return
        try:
            self.engine.delete_list(list_id)
        except DidaError as exc:
            self._write_status(messages.list_write_failed_message(exc))
            return
        self.refresh_view()

    def _finish_delete_view(self, view_id: str, confirmed: bool | None) -> None:
        """删视图那一次确认：只有 ``True`` 才真的删，而删的**只是那一行**（#36）。

        视图是一组过滤条件，不是容器：它「里面」的任务本来就在各自的清单里，所以这里一条
        任务都不动——验收标准「删视图不删任务」说的就是这一行代码。
        """
        if not confirmed:
            return
        try:
            self.engine.delete_view(view_id)
        except DidaError as exc:
            self._write_status(messages.view_write_failed_message(exc))
            return
        self.refresh_view()

    # ---------------------------------------------------------------- 当前任务 / 浏览器（工单 #19）

    def current_task_id(self) -> str | None:
        """当前这一层说的「这条任务」是谁。

        任务列表页是光标下那一条，详细页是它正在说的那一条，清单列表页没有任务
        （``o`` 在那里什么都不做——空屏上按键不该报错）。
        """
        if self._layer == LAYER_TASKS:
            return self.tasks_page().selected_id
        if self._layer == LAYER_DETAIL:
            return self.detail_page().task_id
        return None

    def action_open(self) -> None:
        """``o``：把当前任务交给系统浏览器（工单 #19）。

        **只有浏览器这一条路。** ADR-0002 的「逃生舱的确切形态（已核实）」记着：官方桌面
        客户端不接受任务深链（``dida365://`` 不存在、Linux 的 ``.desktop`` 没注册协议处理器、
        主进程也不处理 argv），能做出来的就是厂商自己在「复制任务链接」里生成的那条网页版
        路由。所以这里不试任何 ``xxx://``，也不假装能切到桌面 App。

        URL 由 :func:`~dida.tui.escape.task_url` 拼（纯函数，含收集箱那条字面量替换）；清单
        id 取自引擎给的详情——TUI 不做判断，只把已经有的事实交出去。

        交不出去时**必须出声**：完成在服务端不可逆，这个键是它的补偿，静默失败比吵一句坏
        得多。两种失败（开手回 ``False``、开手当场抛）报同一句话，并且把 URL 原样给人抄。
        """
        detail = self._current_detail()
        if detail is None:
            return
        url = task_url(detail.list_id, detail.task_id)
        try:
            opened = self._open_url(url)
        except Exception:  # noqa: BLE001 - 找不到浏览器的机器不该把整个界面带走
            opened = False
        if not opened:
            self._write_status(messages.no_browser_message(url))

    def _current_detail(self) -> TaskDetail | None:
        """当前任务的那一份详情（引擎给的成品），没有就是 ``None``。"""
        if self._layer == LAYER_DETAIL:
            task_id = self.detail_page().task_id
        else:
            task_id = self.current_task_id()
        if task_id is None:
            return None
        return self.engine.task_detail(task_id)

    # ---------------------------------------------------------------- 帮助（工单 #18 / #48）

    def action_help(self) -> None:
        """``h``：当前这一层的键位帮助（跟着绑定表走，不是手抄一份）。"""
        self.push_screen(MessageOverlay(help_body(self._layer)))

    # ---------------------------------------------------------------- 同步泵（t21）

    def action_refresh(self) -> None:
        """``r``：手动同步——全量刷新 + 推待推送改动 + 拉已完成流。

        用户按了键得有反馈，但**不是**靠改状态栏那一行字符串（那正是本票要去掉的）：按下去
        先什么都不说，超过阈值（:data:`~dida.tui.theme.SPINNER_DELAY_MS`）才出现转圈，
        完成或失败用 toast 说一句。真正的活儿在事件循环上跑，界面不因为等网络而卡住
        （引擎那条 ``refresh()`` 是 async 的就是为这个）。

        顺带问一次日界（工单 #46）：「现在再看一眼」这句话里，配置改过、钟自己走过了边界，都
        算在内——而且这是那条检查**唯一不依赖周期泵**的触发。它跑在这里、任何网络调用之前，
        所以这一轮同步成不成功都与它无关（``_sync()`` 里的重画在 ``else:`` 成功分支里）。
        """
        self.reload_day_boundary()
        self.start_sync(announce=True, manual=True)

    def start_sync(self, *, announce: bool = False, manual: bool = False) -> None:
        """把一轮同步排到事件循环上（不等它）。``r`` 与启动刷新都走这里。

        ``exclusive=True``：连按 ``r`` 不会让两轮同步叠在一起（同一份缓存被两个协程交替写）。
        协程 worker 跑在事件循环**同一根线程**上，sqlite 连接有线程亲和，所以这里不能改成
        ``thread=True``。

        ``announce`` 决定这一轮要不要用 toast 报完成：``r`` 要（用户按了键，他在等一个回声），
        启动刷新不要——每天早上开屏弹一下是噪音，那一行的「已同步 HH:MM」本来就是记录。

        ``manual`` 是「这一轮推送要不要再试那些已经放弃的改动」（工单 #71）：只有 ``r`` 传
        ``True``，启动刷新与周期泵都不传。它与 ``announce`` 同一段寿命（都在这里写下、都在
        :meth:`_sync` 里读一次），所以一个读者看到的是同一种走法。
        """
        self._announce_sync = announce
        self._manual_sync = manual
        self._arm_spinner()
        self.run_worker(self._sync(), group=SYNC_GROUP, exclusive=True, description="同步")

    def pull_completed(self) -> None:
        """完成了一笔之后，把服务端认下的 ``completedTime`` 拉回来（工单 #74）。

        周期泵（:meth:`push_tick`）**只管推**，app 里没有任何周期性刷新——所以完成之后没人去
        拉已完成流，服务端的完成时刻永远回不来，除非用户自己按 ``r``。这一下就是那个缺失的
        触发，也是**所有**完成路径的唯一出口（今天只有任务列表页的 ``space`` 一条）。

        **拉最窄的那一条**：``refresh_completed()`` 带回来的就是这个时间戳，而全量刷新要逐清单
        拉一遍未完成任务（ADR-0001）——为一个完成时刻做那么多网络调用不值当。推一轮排在它前面：
        服务端得先认下这一笔（状态真的变成完成），已完成流才会把它带回来；写路径排下的那一轮
        与这里共用引擎那把推送锁，所以「先推后拉」是确定的，不是碰运气。

        与 ``r`` 共用 :data:`SYNC_GROUP` 且 ``exclusive=True``：同步不会叠在一起（同一份缓存被
        两个协程交替写），而且走的还是那条既有的同步路。界面不等它——活干在事件循环上。

        **失败什么都不说**：那一笔留在队列里，状态栏那个「待推送 N」说的就是这件事，而屏幕上
        任务照旧在已完成段里（本地乐观写 + #74 的占位）。用户刚按下的那一下已经有回声了，
        再弹一句是噪音。
        """
        self.run_worker(
            self._pull_completed(), group=SYNC_GROUP, exclusive=True, description="拉已完成流"
        )

    async def _pull_completed(self) -> None:
        """推一轮、再拉一次已完成流，然后重画。失败照旧不假装拉到了（``DidaError`` 收在这里）。"""
        try:
            await self.engine.push_pending()
            await self.engine.refresh_completed()
        except DidaError:
            return
        self.refresh_view()

    async def _sync(self) -> None:
        """一轮同步：全量刷新 → 推待推送改动 → 拉已完成流。

        三件事各报各的失败，而且**不假装做过**：全量刷新失败（断网、凭据失效）时后面两件
        不做——同一个网络问题会让它们一起失败，白跑两趟；已完成流失败时前两件已经落地，
        照旧重画，只是把「没拉到」说出来。缓存从头到尾都在：这一屏不因为没网就不能用。

        三处 ``await`` 之后动界面的地方都不是裸写：状态栏走 :meth:`_write_status`、重画走
        :meth:`refresh_view`，两边都认得「关窗了」。

        「说一句」的话（失败、覆盖告知）**攒到最后**才写：它们比「数据怎么样」更该留在屏幕
        上，而收尾那一次重画（把转圈收掉）会覆盖状态栏——顺序反了就会把话吞掉。
        """
        message: str | None = None
        try:
            report = await self.engine.refresh()
        except DidaError as exc:
            message = messages.refresh_failed_message(exc)
            self._notify_failed(message)
        else:
            # 队列里那些该试的改动顺手推一轮：`r` 是用户能按的那个「现在再试一次」——
            # 它也是唯一把 ``manual=True`` 传下去的人，所以已经放弃的改动这里能再试一次
            # （工单 #71）；启动刷新走的是同一个 ``_sync()``，但那个标记是 ``False``。
            pushed = await self.engine.push_pending(manual=self._manual_sync)
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
                message = messages.overwritten_message(len(report.overwritten))
            elif completed_failed is not None:
                message = messages.completed_failed_message(completed_failed)
            elif self._announce_sync:
                self._notify_done(
                    f"已推送 {pushed} 处改动" if pushed else "本地已是最新"
                )
        finally:
            self._stop_spinner()
            self.update_status()
        if message is not None:
            self._write_status(message)

    # ---------------------------------------------------------------- 瞬时反馈：转圈与 toast

    def _arm_spinner(self) -> None:
        """排一个「阈值到了再看一眼」的定时器——大多数同步在这里之前就结束了。"""
        self._stop_spinner()
        self._spin_delay_timer = self.set_timer(
            theme.SPINNER_DELAY_MS / 1000, self._maybe_spin
        )

    def _maybe_spin(self) -> None:
        """阈值到点：这一轮同步**还在跑**才开始转（跑完了就什么都不显示）。"""
        self._spin_delay_timer = None
        if not self.is_running:
            return
        self._spinning = True
        self._spinner_frame = 0
        self._spinner_timer = self.set_interval(1 / 12, self._tick_spinner)
        self.update_status()

    def _tick_spinner(self) -> None:
        self._spinner_frame += 1
        self.update_status()

    def _stop_spinner(self) -> None:
        """收掉转圈与它的两个定时器（关窗时也走这里）。"""
        for timer in (self._spin_delay_timer, self._spinner_timer):
            if timer is not None:
                timer.stop()
        self._spin_delay_timer = None
        self._spinner_timer = None
        self._spinning = False
        self._spinner_frame = 0

    def _spinner(self) -> str:
        """状态栏上当前那一帧（没在转就是空串——平时一个字都不多）。"""
        if not self._spinning:
            return ""
        return theme.SPINNER_FRAMES[self._spinner_frame % len(theme.SPINNER_FRAMES)]

    def _notify_done(self, message: str) -> None:
        """完成 = **原生 toast**（``App.notify()``），不是改状态栏那一行字符串。"""
        if not self.is_running:
            return
        self.notify(message, title="同步完成", timeout=3)

    def _notify_failed(self, message: str) -> None:
        """失败也走 toast；状态栏那一份照留——它是留在屏幕上的记录。"""
        if not self.is_running:
            return
        self.notify(message, title="同步失败", severity="error", timeout=6)

    def _notify_step(self, message: str) -> None:
        """一笔写当场生效的短暂回声（用户故事 43：完成 / 取消完成要有反馈）。

        toast 自己会走（``timeout`` 就是那个「短暂」），所以它不占状态栏那一行——那一行说的是
        「数据怎么样」（已同步 / 待推送 / 逻辑日，GLOSSARY），不该被一次按键挤掉。推送要是
        失败了，那条改动留在队列里、状态栏那个「待推送 N」照旧顶上：两句话说的是两件事，
        都是真的。
        """
        if not self.is_running:
            return
        self.notify(message, timeout=2)

    async def push_tick(self) -> None:
        """推一轮**到点**的待推送改动（工单 #21 的周期泵；也是测试的确定性入口）。

        退避、``next_retry_at`` 都在引擎里，缺的是「谁来定期问一句到点了没有」——就是这里。
        间隔只决定**什么时候看一眼**，到没到点依然由引擎那口注入的钟判定：所以测试可以把
        钟摆到任意一刻，再直接 ``await app.push_tick()``，不必等真实时间。

        **这一跳也是 app 唯一的心跳**，所以日界在这里顺带问一次（工单 #46）：两条路都是外部
        事件——用户在另一个窗口里改配置、钟自己走过了边界——没人通知得了这个进程，定期看一眼
        是唯一零操作的做法。三件事共用一跳不冲突：一个本地队列查询、两条纯算术。

        ``await`` 之后那一次状态栏重画走 :meth:`update_status` → :meth:`_write_status`：
        关窗时它整个丢掉，但**这一笔推送已经落下去了**——界面没了不代表用户那一下不算数。
        """
        self.reload_day_boundary()
        await self.engine.push_pending()
        self.update_status()

    # ---------------------------------------------------------------- 退出流（t21 / #47）

    async def action_quit(self) -> None:
        """``q`` 与 ``Ctrl+C``：还有待推送改动时先拦一下（用户故事 101 / 工单 #47）。

        用户按退出键的意图通常是「我干完了」，而屏幕底下那个数可能是「我按了 x，但网断了」
        ——待推送改动只存在本地（ADR-0002 的豁免代价），进程一结束这一屏就没了，而服务端
        并不知道用户做过什么。所以这里**多问一句**，并且把「有几处」写在浮层上。

        **两个键走同一个判断**：它们绑在同一个动作上（``keys.py`` 全局那一层的 :class:`Key`），
        所以这里分不出、也不该分出 ``q`` 与 ``Ctrl+C``。这一条在本票之前不成立：Textual 8.2.8
        把 ``Ctrl+C`` 绑在它自己的 ``help_quit`` 上（弹一句「按 q 退出」，**不退出**）——那是
        框架的另一条退出路径，谁也不保证它永远只是弹一句话。两条路合成一条之后，这个分歧
        没有了，而「按了退出键却没被拦」这条静默丢改动的后门也一并关掉。

        **这里不需要 ``is_running`` 守卫，理由要写下来**（不是「忘了加」）：本方法跨过
        ``push_screen`` 之后**没有任何一行再碰 DOM**，而 ``push_screen`` 自己是同步的、
        ``App.exit()`` 也是同步的。真正的 ``await`` 在调用方（``_dispatch_action`` 的
        ``await invoke(...)``），那时这一帧已经做完了。:meth:`_finish_quit` 同理，见它自己的
        说明。
        """
        pending = self.engine.status().pending_count
        if not pending:
            # 没有待推送改动就照旧直接退（``q`` 即结束，spec 的单进程规矩）。
            self.exit()
            return
        if self._confirming_quit():
            # 连按退出键不该叠出一摞确认框——已经问过就不必再问。
            return
        self.push_screen(
            ConfirmOverlay(messages.quit_prompt(pending), title="仍然退出"), self._finish_quit
        )

    def _confirming_quit(self) -> bool:
        """退出浮层已经开着了吗。

        看**整摞** screen，不是只看顶上那一块：确认框上面还能再盖一层（``h`` 的帮助浮层
        就盖得住它），而那时 ``self.screen`` 是**最上面**那一块——只比它一块就会再叠一个
        确认框出来。这个口子是真的：浮层是模态的，键位解析在它那儿就截断了，
        :meth:`action_quit` 照样会跑到。
        """
        return any(isinstance(screen, ConfirmOverlay) for screen in self.screen_stack)

    def _finish_quit(self, confirmed: bool | None) -> None:
        """退出浮层关掉了：只有 ``True`` 才真的退（``n`` / ``Esc`` 与 ``None`` 都留下）。

        本方法**一行 DOM 都不碰**（``exit()`` 是同步的，它只是排一条 ``ExitApp``），所以它
        不需要「``await`` 之后先问 ``is_running``」那道守卫——那条规矩管的是**碰 DOM** 的
        地方。这一句是写给下一个来改它的人的：往这里加任何 ``query_one`` / ``update`` 之
        前，先把守卫补上。
        """
        if confirmed and self.is_running:
            self.exit()
