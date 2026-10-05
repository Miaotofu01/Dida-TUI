"""外壳：组装、三层页面的进出、状态栏、同步泵、退出流——**这是一个薄 app**。

一栏、三层页面（ADR-0004）：启动落在清单列表页，``enter`` 向下、``esc`` 向上。三层各是
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
这里只决定**什么时候**动（换层平移、光标条追赶、同步转圈、toast）。

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
from functools import partial
from typing import TYPE_CHECKING, Callable, Sequence

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import HorizontalScroll
from textual.widgets import Static

from dida.sync.engine import (
    DidaError,
    Engine,
    ListRow,
    SyncStatus,
    TaskDetail,
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
from dida.tui.overlays import ConfirmOverlay, FormOverlay, MessageOverlay
from dida.tui.pages import DetailPage, IndexPage, TasksPage
from dida.tui.pages.index import (
    LIST_COLOR_FIELD,
    LIST_NAME_FIELD,
    list_form_fields,
    list_write_refusal,
)

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

    换层 = 平移不是装饰：``enter`` 压栈、``esc`` 出栈本来就是**导航栈**（GLOSSARY 的
    「导航路径」），左右平移正是这个语义的标准表达。页面底色必须不透明（:data:`CSS_PAGE`），
    否则滑走的那块会漏出后面的东西。
    """

    def show(self, index: int, *, animate: bool = True) -> None:
        """把第 ``index`` 页滑到眼前（``animate=False`` 就是直接到）。"""
        self._index = index
        target = float(index * self.size.width)
        if not animate or self.size.width <= 0:
            self.scroll_to(x=target, animate=False, immediate=True)
        else:
            self.scroll_to(x=target, animate=True, duration=theme.PAN_MS / 1000, easing="out_cubic")

    def on_resize(self) -> None:
        """窗口宽度变了：每一页的位置跟着变，得把当前那一页重新对齐（不滑）。"""
        index = getattr(self, "_index", 0)
        if self.size.width > 0:
            self.scroll_to(x=float(index * self.size.width), animate=False, immediate=True)

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


class DidaApp(App[None]):
    """一栏 + 三层页面 + 状态栏。"""

    ENABLE_COMMAND_PALETTE = False  # 命令面板会抢键；键位帮助是 ?
    BINDINGS = bindings_for(GLOBAL)
    """全局那几条（退出 / 同步 / 浏览器 / 帮助）。各层自己的键在各自的页面上——
    所以 ``?`` 列出来的、以及 footer 上显示的，都是**当前这一层真正能按的**那些。"""

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
    ) -> None:
        """``open_url`` 是**注入**的浏览器开手（工单 #19）。

        生产默认值 :func:`~dida.tui.escape.open_in_browser` 会真的叫起系统浏览器；测试塞一个
        假的进来，于是「交给浏览器的是哪条 URL」能当场断言，而没有一个标签页被打开。它回
        ``False`` 或抛异常都表示这台机器上开不了浏览器。

        ``refresh_on_start`` 与 ``push_tick_seconds`` 是**策略**，默认都不开（工单 #21）：
        产品行为由组合根按 ``config.toml`` 决定（``dida.bootstrap`` 传 ``refresh_on_start=``
        与 ``PUSH_TICK_SECONDS``）。这里不写死默认值，是为了让「直接 new 一个 app」的测试
        不必先接上客户端与存储——后台同步需要一个真引擎才跑得起来。

        ``ansi_color=True`` 是**跟随终端主题**那一条决定的落点（ADR-0007 一）：Textual 默认
        会把每个 ``ansi_*`` 改写成 Monokai 的真彩色（``ansi_cyan`` → ``#58D1EB``），用户的
        调色板一眼都用不上；打开之后同一条 CSS 发出的是 ``\\x1b[36m``，由终端说了算。

        ``animations`` 是 ``auto|on|off`` 开关（ADR-0007 三）。不给就走 ``DIDA_ANIM``，
        再不给就是 ``auto``：ssh 与低能力终端上自动关掉。关掉时换层不滑、光标条不飞，
        屏幕一步到位。
        """
        super().__init__(ansi_color=True)
        self.engine = engine
        self._open_url = open_url
        self._refresh_on_start = refresh_on_start
        self._push_tick_seconds = push_tick_seconds
        self._push_timer: Timer | None = None
        """周期泵的定时器句柄（工单 #21）：``on_unmount`` 里拿它把泵停掉。

        ``set_interval`` 回一个 ``Timer``，丢掉它就没有第二个人能停这一跳——关窗之后
        它还挂在事件循环上。``None`` 表示泵没开（策略没给间隔）或者已经停了。"""
        self._layer = LAYER_INDEX
        """当前在屏上的是哪一层（``?`` 与 ``o`` 都按它说话）。"""
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
        """这一台机器上到底动不动（``on_mount`` 里按开关与环境定一次）。

        ⚠ 名字**不能**是 ``_animate``：``App._animate`` 是 Textual 自己那个绑好的 animator
        （``App.animate()`` 调的就是它），盖掉之后 ``app.animate(...)`` 会抛
        ``TypeError: 'bool' object is not callable``——报错点在 Textual 的 ``app.py`` 里，离
        现场很远。页面那一半同名的坑见 :class:`dida.tui.pages.base.CursorPage` 的 ``_motion``。
        """
        self._editing_list: str | None = None
        """正在改的是哪条清单（表单关掉时要用它；``None`` = 那一次是新建）。"""
        self._announce_sync = False
        """这一轮同步要不要用 toast 报完成——``r`` 要，启动刷新不要（那会每次开屏都弹一下）。"""
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

        外观的开关只在这里定一次：动效档位（``auto|on|off``）落到每张页面上——页面自己
        不知道 ``auto`` 是什么意思，它只知道「动不动」。
        """
        self._motion = theme.animations_enabled(self._animations)
        if not self._motion:
            self.animation_level = "none"
        for page in self._pages().values():
            page.set_animate(self._motion)
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
        """把这一层滑到眼前，并把焦点交给它（``j``/``k``/``enter`` 立刻能用）。

        三层**都在 DOM 里**、并排停在 :class:`Stage` 上：它们的行与光标因此原样留着，
        ``esc`` 回去时用户看到的就是他离开时那一行（用户故事 20/62）。换层是横向平移——
        ``enter`` 压栈、``esc`` 出栈本来就是导航栈（ADR-0007 三）。
        """
        previous = self._layer
        self._layer = layer
        page = self._pages()[layer]
        self._write_top()
        self._stage().show(self._layer_index(layer), animate=self._motion and layer != previous)
        # ``scroll_visible=False``：Textual 交焦点时默认会把那个控件**立刻**滚进可见区，
        # 而这一页正好是整个舞台（平移就是把它滑过来）——那一下会把动画当场抹平（实测：
        # 默认交焦点时 ``scroll_x`` 一步到 100，动画一帧都看不到）。
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
        """进层二：某个清单或视图里的任务（``enter``）。"""
        self._container_id = container_id
        self._detail_task_id = None
        self.refresh_view()
        self._show(LAYER_TASKS)

    def open_detail(self, task_id: str) -> None:
        """进层三：一条任务的详细页（任务列表页上按 ``enter``）。"""
        self._detail_task_id = task_id
        self.refresh_view()
        self._show(LAYER_DETAIL)

    def back_to_index(self) -> None:
        """回层一（任务列表页上按 ``esc``）——光标照旧停在他进来的那一行。"""
        self._detail_task_id = None
        self._show(LAYER_INDEX)

    def back_to_tasks(self) -> None:
        """回层二（详细页上按 ``esc``）——光标照旧停在他进来的那条任务上。"""
        self._show(LAYER_TASKS)

    # ---------------------------------------------------------------- 重画

    def refresh_view(self) -> None:
        """读引擎的三种读形状，重画在屏上的那几层与状态栏。

        三层都重画（不在屏上的那两层也重画）：它们的行是**同一份缓存**算出来的，只画在屏
        上的那一层，切回去时会看到一屏过期的东西。光标不归这里管——各页按**行 id** 把光标
        认回原来那一行（用户故事 21/57：刷新不许把人踢回第一行）。

        屏幕已经拆掉时整体是空操作（关窗中，这一次回来晚了，见 :meth:`_write_status`）。
        """
        if not self.is_running:
            return
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
            self.detail_page().show_detail(detail)
        self.update_status()
        self._write_top()

    @staticmethod
    def _container_name(rows: tuple[ListRow, ...], container_id: str) -> str:
        """容器在标题里写什么：引擎给的清单名；认不出来（远端刚删掉）就照原样写 id。"""
        row = next((item for item in rows if item.id == container_id), None)
        return row.name if row is not None else container_id

    def update_status(self) -> None:
        """把引擎的状态刷进状态栏。数据变化后都调它。"""
        self._write_status(status_line(self.engine.status(), spinner=self._spinner()))

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
        """清单列表页上按了 ``enter``：进这个容器。"""
        self.open_container(event.container_id)

    def on_index_page_refused(self, event: IndexPage.Refused) -> None:
        """``enter`` 了一个进不去的清单：如实说一句，不进下一层。"""
        self._write_status(event.message)

    def on_tasks_page_entered(self, event: TasksPage.Entered) -> None:
        """任务列表页上按了 ``enter``：进这条任务的详细页。"""
        self.open_detail(event.task_id)

    def on_tasks_page_back(self, event: TasksPage.Back) -> None:
        """任务列表页上按了 ``esc``：回清单列表页。"""
        self.back_to_index()

    def on_detail_page_back(self, event: DetailPage.Back) -> None:
        """详细页上按了 ``esc``：回任务列表页。"""
        self.back_to_tasks()

    # ---------------------------------------------------------------- 清单的建 / 改 / 删（#42）

    def on_index_page_new_list(self, event: IndexPage.NewList) -> None:
        """``n``：开建清单的表单（字段由选中行的类型决定，见 :mod:`dida.tui.overlays`）。"""
        self._open_list_form(None)

    def on_index_page_edit_list(self, event: IndexPage.EditList) -> None:
        """``e``：改光标那一行的名字与颜色。"""
        self._open_list_form(event.row_id)

    def on_index_page_delete_list(self, event: IndexPage.DeleteList) -> None:
        """``d``：删光标那一行——**先如实问一句**，``y`` 才真的删（验收标准 3、4、5）。

        确认文案在 :func:`dida.tui.messages.delete_list_prompt`：它说的两件事都核实过
        ——删掉一个清单时里面的任务会怎样文档没写，而回收站与撤销删除的接口都不存在。
        """
        row = self.index_page().row(event.row_id)
        if row is None:
            return
        refusal = list_write_refusal(row)
        if refusal is not None:
            self._write_status(refusal)
            return
        self.push_screen(
            ConfirmOverlay(messages.delete_list_prompt(row.name), title="删除清单"),
            partial(self._finish_delete_list, row.id),
        )

    def _open_list_form(self, row_id: str | None) -> None:
        """开清单表单：``row_id`` 是 ``None`` 就是新建，否则是改那一行。

        改不动的行（视图、收集箱、没有写权限的清单）在这里就挡住并说清是哪一种——表单
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

    def _finish_list_form(self, values: dict[str, str] | None) -> None:
        """表单关掉了：``None`` 是取消（一个字节都不写），否则按填的那一份建 / 改。

        颜色是空串就**不发** ``color`` 字段（那是「默认」，不是「清空」）；名字空着则
        什么都不做，只如实说一句。
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
                self.engine.update_list(editing, name=name, color=color)
        except DidaError as exc:
            self._write_status(messages.list_write_failed_message(exc))
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
        """``?``：当前这一层的键位帮助（跟着绑定表走，不是手抄一份）。"""
        self.push_screen(MessageOverlay(help_body(self._layer)))

    # ---------------------------------------------------------------- 同步泵（t21）

    def action_refresh(self) -> None:
        """``r``：手动同步——全量刷新 + 推待推送改动 + 拉已完成流。

        用户按了键得有反馈，但**不是**靠改状态栏那一行字符串（那正是本票要去掉的）：按下去
        先什么都不说，超过阈值（:data:`~dida.tui.theme.SPINNER_DELAY_MS`）才出现转圈，
        完成或失败用 toast 说一句。真正的活儿在事件循环上跑，界面不因为等网络而卡住
        （引擎那条 ``refresh()`` 是 async 的就是为这个）。
        """
        self.start_sync(announce=True)

    def start_sync(self, *, announce: bool = False) -> None:
        """把一轮同步排到事件循环上（不等它）。``r`` 与启动刷新都走这里。

        ``exclusive=True``：连按 ``r`` 不会让两轮同步叠在一起（同一份缓存被两个协程交替写）。
        协程 worker 跑在事件循环**同一根线程**上，sqlite 连接有线程亲和，所以这里不能改成
        ``thread=True``。

        ``announce`` 决定这一轮要不要用 toast 报完成：``r`` 要（用户按了键，他在等一个回声），
        启动刷新不要——每天早上开屏弹一下是噪音，那一行的「已同步 HH:MM」本来就是记录。
        """
        self._announce_sync = announce
        self._arm_spinner()
        self.run_worker(self._sync(), group=SYNC_GROUP, exclusive=True, description="同步")

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
            # 队列里那些到点的改动顺手推一轮：`r` 是用户能按的那个「现在再试一次」。
            pushed = await self.engine.push_pending()
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

    async def push_tick(self) -> None:
        """推一轮**到点**的待推送改动（工单 #21 的周期泵；也是测试的确定性入口）。

        退避、``next_retry_at`` 都在引擎里，缺的是「谁来定期问一句到点了没有」——就是这里。
        间隔只决定**什么时候看一眼**，到没到点依然由引擎那口注入的钟判定：所以测试可以把
        钟摆到任意一刻，再直接 ``await app.push_tick()``，不必等真实时间。

        ``await`` 之后那一次状态栏重画走 :meth:`update_status` → :meth:`_write_status`：
        关窗时它整个丢掉，但**这一笔推送已经落下去了**——界面没了不代表用户那一下不算数。
        """
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

        看**整摞** screen，不是只看顶上那一块：确认框上面还能再盖一层（``?`` 的帮助浮层
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
