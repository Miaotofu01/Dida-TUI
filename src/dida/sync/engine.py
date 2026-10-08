"""同步引擎（第 4 个深模块）——公开面与读路径。

TUI 读写一切只能走本模块；排序、逾期判定、视图求值、冲突裁决都发生在这一层，不在 TUI 里。
公开接口：

- ``status() -> SyncStatus`` —— 读：状态栏所需的全部信息。
- ``logical_day() -> date`` —— 读：现在是哪个逻辑日（工单 #46 的心跳）。
- ``read_model() -> ReadModel | None`` —— 读：从当下这一份本地副本**一次装配**出整份读模型
  （#81，没有缓存）；下面三种读形状都是它的投影。
- **三种读形状**（#33）：``list_index() -> tuple[ListRow, ...]``（清单索引）、
  ``tasks_in(container_id) -> TaskList``（某个容器的任务列表）、
  ``task_detail(task_id) -> TaskDetail | None``（单条任务的详情）。
- ``refresh() -> RefreshReport`` —— 写：全量刷新，**async**（:mod:`dida.sync.refresh`；清单索引
  翻页翻到底、远端已经没有的清单与任务顺手剪掉，#41）。
- ``write(task_id, changes=, kind=)`` —— 写：乐观写，本地当场生效、立即推送（:mod:`dida.sync.push`）。
- ``complete(task_id)`` / ``uncomplete(task_id)`` / ``delete(task_id)`` —— 写：完成、取消完成
  与删除的三个预置（:mod:`dida.sync.push`；取消完成走 ``task/batch``，工单 #38）。
- ``refresh_completed() -> CompletedReport`` —— 写：已完成流，**async**（:mod:`dida.sync.completed`）。
- ``defer(task_id)`` / ``reschedule(...)`` —— 写：顺延与改期（:mod:`dida.sync.schedule`）。
- ``create(title, ...)`` —— 写：新建，落在收集箱（:mod:`dida.sync.create`）。
- ``create_list(name, color=)`` / ``update_list(id, name=, color=)`` / ``delete_list(id)`` ——
  写：清单的建 / 改 / 删，乐观写 + 立即推送 + 失败进重试队列（:mod:`dida.sync.lists`，#42）。
- ``cycle_priority(task_id)`` —— 写：优先级推进一档（:mod:`dida.sync.priority`）。
- ``subtasks(task_id)`` —— 读：子任务那几行（只读，:mod:`dida.sync.subtasks`；spec 的
  「子任务只看不勾」）。

**v1 那条读路径在 #58 里删掉了**：``view() -> TodayView`` 与它硬编码的三个分区、左栏的
``ListSummary`` 徽标、``/`` 的模糊过滤、以及子任务的勾选写路径（``toggle_subtask``）都只
为「今日执行台」服务，spec 要求读模型重写、模糊过滤不迁移、子任务只看不勾。三种读形状是
v2 的全部读面。

**这个文件只做两件事：组装，以及读路径。** ``SyncEngine`` 由几片职责各自独立的 mixin 拼成，
每片一个模块、一个变化原因（t32 拆的）：

============================  ==========================================
模块                            负责什么
============================  ==========================================
:mod:`dida.sync.writes`         写词汇（唯一的定义处）与写路径共享的协议 / 错误
:mod:`dida.sync.refresh`        全量刷新怎么取数、怎么落库（t09 / #41）
:mod:`dida.sync.push`           乐观入队、立即推送、退避重试（t10 / t11 / t16）
:mod:`dida.sync.completed`      已完成流的窗口与游标（t12）
:mod:`dida.sync.schedule`       顺延与改期：截止时间怎么挪（t13 / t14 / #40 / #44）
:mod:`dida.sync.create`         新建一条任务要发什么（t15 / #39）
:mod:`dida.sync.priority`       优先级推进一档（t17 / #45）
:mod:`dida.sync.subtasks`       子任务的先读后写（t20）
============================  ==========================================

各片的方法挂在同一个 ``SyncEngine`` 上（它们用 ``self._clock`` / ``self._source`` / 彼此的
公开入口），所以**单独一个 mixin 不完整**——一份完整的引擎就是这里的组装结果。

协作者都是注入的：``source`` 是本地副本（生产是 t08 的 ``Store``，只读的 ``ViewSource``
照样能跑读路径，只是刷新与写入会大声报错），``client`` 是 t07 的 ``DidaApiClient``
（刷新与推送都要它）。组合根 ``dida.bootstrap`` 负责接线，本模块不自己 new 任何东西。

「现在」永远取自注入的 ``Clock``，「一天结束的时刻」是注入的 ``day_end``——
这一层里没有 ``datetime.now()``，也没有自己算的日界（交给 :mod:`dida.logical_day`）。

行的字段与纯读法都在 :mod:`dida.sync.view`，这里再导出一份，好让 TUI 只 import
``dida.sync.engine`` 一个东西（``tests/test_architecture.py`` 守着这条）——**TUI 的允许表
只有这一个模块**，所以新增的公开类型都要在这里转出去。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Sequence, runtime_checkable

from dida.api.errors import AuthError, DidaError
from dida.api.guards import all_day_date
from dida.clock import Clock
from dida.logical_day import logical_day
from dida.sync.completed import (
    DEFAULT_COMPLETED_WINDOW_HOURS,
    CompletedReader,
    CompletedReport,
    CompletedStreamMixin,
)
from dida.sync.create import CreateMixin
from dida.sync.lists import (
    LIST_COLORS,
    ListColor,
    ListMixin,
    ListWriteKind,
    ListWriteTarget,
    ProjectWriter,
    UnknownListError,
    is_list_edit,
)
from dida.sync.priority import PriorityMixin
from dida.sync.push import PushMixin, TaskWriter, backoff_delay
from dida.sync.read import (
    ListKind,
    ListRow,
    PayloadReader,
    ReadModel,
    TaskDetail,
    TaskList,
    ViewReader,
    ViewRow,
    assemble_read_model,
    builtin_view_rows,
    custom_view_rows,
    detail_of,
    is_inbox_id,
    list_rows,
    resolve_lists,
    task_detail,
)
from dida.sync.refresh import ProjectReader, RefreshMixin, RefreshTarget
from dida.sync.schedule import ScheduleMixin
from dida.sync.subtasks import SubtaskMixin
from dida.sync.tags import TagMixin, TagReader
from dida.sync.view import (
    INBOX_ID,
    NO_DUE_TEXT,
    PRIORITY_NAMES,
    SUBTASK_COMPLETED_STATUS,
    CompletedItem,
    CompletedSection,
    ListSnapshot,
    SubtaskItem,
    SyncState,
    TaskItem,
    TaskSnapshot,
    ViewSource,
    completed_section,
    format_due,
    next_priority,
    priority_mark,
    subtask_items,
)
from dida.sync.writes import (
    LOCAL_LIST_PREFIX,
    LOCAL_TASK_PREFIX,
    LocalEffect,
    UnclaimedListError,
    UnclaimedTaskError,
    UnknownTaskError,
    WireCall,
    WriteKind,
    WriteTarget,
    is_a_change,
    is_a_move,
    is_local_id,
    is_local_list_id,
)
from dida.sync.views import (
    ANY_VALUE,
    BUILTIN_VIEW_DAYS,
    COMPLETED_DAYS_CHOICES,
    COMPLETION_CHOICES,
    DUE_CHOICES,
    PRIORITY_CHOICES,
    VIEW_COMPLETED_DAYS_FIELD,
    VIEW_COMPLETION_FIELD,
    VIEW_DUE_FIELD,
    VIEW_LISTS_FIELD,
    VIEW_NAME_FIELD,
    VIEW_PRIORITY_FIELD,
    VIEW_TAGS_FIELD,
    Completion,
    DueWindow,
    UnknownViewError,
    ViewChoice,
    ViewDefinition,
    ViewFormProblem,
    ViewMixin,
    ViewStore,
    ViewTask,
    builtin_view_definitions,
    due_window_of,
    evaluate_view,
    implied_due_for,
    is_view_edit,
    order_key,
    parse_view_form,
    view_form_values,
    view_from_payload,
    view_payload,
)
from dida.vocabulary import RefreshReport

__all__ = [
    "ANY_VALUE",
    "COMPLETED_DAYS_CHOICES",
    "COMPLETION_CHOICES",
    "DUE_CHOICES",
    "INBOX_ID",
    "LOCAL_LIST_PREFIX",
    "LOCAL_TASK_PREFIX",
    "NO_DUE_TEXT",
    "PRIORITY_CHOICES",
    "PRIORITY_NAMES",
    "VIEW_COMPLETED_DAYS_FIELD",
    "VIEW_COMPLETION_FIELD",
    "VIEW_DUE_FIELD",
    "VIEW_LISTS_FIELD",
    "VIEW_NAME_FIELD",
    "VIEW_PRIORITY_FIELD",
    "VIEW_TAGS_FIELD",
    "AuthError",
    "BUILTIN_VIEW_DAYS",
    "CompletedItem",
    "CompletedReader",
    "CompletedReport",
    "CompletedSection",
    "Completion",
    "DidaError",
    "DueWindow",
    "Engine",
    "LIST_COLORS",
    "ListColor",
    "ListKind",
    "ListRow",
    "ListSnapshot",
    "ListWriteKind",
    "ListWriteTarget",
    "LocalEffect",
    "PayloadReader",
    "ProjectReader",
    "ReadModel",
    "RefreshTarget",
    "SUBTASK_COMPLETED_STATUS",
    "SubtaskItem",
    "SyncEngine",
    "SyncState",
    "SyncStatus",
    "TaskDetail",
    "TaskItem",
    "TaskList",
    "ProjectWriter",
    "TagReader",
    "TaskSnapshot",
    "UnclaimedListError",
    "UnclaimedTaskError",
    "UnknownListError",
    "UnknownTaskError",
    "UnknownViewError",
    "ViewChoice",
    "ViewDefinition",
    "ViewFormProblem",
    "ViewReader",
    "ViewRow",
    "ViewSource",
    "ViewStore",
    "ViewTask",
    "WireCall",
    "WriteKind",
    "WriteTarget",
    "all_day_date",
    "backoff_delay",
    "builtin_view_definitions",
    "builtin_view_rows",
    "completed_section",
    "custom_view_rows",
    "due_window_of",
    "evaluate_view",
    "format_due",
    "implied_due_for",
    "is_a_change",
    "is_a_move",
    "is_inbox_id",
    "is_list_edit",
    "is_local_id",
    "is_local_list_id",
    "is_view_edit",
    "logical_day",
    "next_priority",
    "order_key",
    "parse_view_form",
    "pending_error",
    "priority_mark",
    "resolve_lists",
    "subtask_items",
    "task_detail",
    "view_form_values",
    "view_from_payload",
    "view_payload",
]

DEFAULT_DAY_END = "00:00"
"""配置注入之前的默认日界：零偏移，逻辑日等于自然日（规范形式见 ADR 0003）。"""


def pending_error(source: object) -> str | None:
    """本地队列里最后一条推不出去的改动报的错（源上没有队列就是 ``None``）。

    读的是一条**已经记下来的事实**：``Store.record_attempt`` 把失败原因写在那一行上
    （``last_error``），这里只是把它带到 :class:`SyncStatus` 上，让它到得了界面（用户故事
    81：保存失败要说具体原因）。

    只读替身（``InMemorySource`` 那种）没有队列，于是没有这个信息——与写路径上那几个
    ``isinstance`` 门同一条口径：不知道就说不知道，不猜一个。
    """
    pending = getattr(source, "pending", None)
    if not callable(pending):
        return None
    errors = [str(change.last_error) for change in pending() if getattr(change, "last_error", None)]
    return errors[-1] if errors else None


@dataclass(frozen=True)
class SyncStatus:
    """状态栏快照。"""

    checked_at: datetime
    """生成本状态时的时刻，来自注入的 ``Clock``。"""

    pending_count: int = 0
    """待推送改动数量：常驻显示，非零就说明本地比服务端新（ADR-0002 的豁免代价）。"""

    last_refresh_at: datetime | None = None
    """上次全量刷新完成时刻（t09 填充）。"""

    logical_day: date | None = None
    """当前逻辑日（t04 提供纯函数，t09 接入）。"""

    last_error: str | None = None
    """队列里最后一条**推不出去**的改动报的错；都推上去了就是 ``None``。

    详细页底部那一行靠它说出**具体**原因（用户故事 81）：一个「保存失败」了事的话，用户不
    知道该刷新、该重连、还是该重新粘 token。推成功的那条改动会出队，这里自然回到 ``None``。
    """


@runtime_checkable
class Engine(Protocol):
    """TUI 眼里的引擎：它只用得到这几个。t11/t13/t14 的实现必须仍然满足它。"""

    def set_day_end(self, day_end: str) -> bool:
        """配置：换掉「一天结束的时刻」（工单 #46），真变了才返回 ``True``。

        界面上的「现在」永远是注入的钟给的，而**日界是配置给的、随时可能被用户改掉**。三处
        消费者（「今天」视图 / 逾期判定 / 顺延）读的都是这一个字段，所以换这一处就够——它们
        不可能各看一个日界。
        """
        ...

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        ...

    def logical_day(self) -> date:
        """读：现在是哪个逻辑日（工单 #46 的心跳靠它判断屏幕上那一份过期了没有）。

        与 :meth:`status` 分开是**故意的**：那一份是状态栏的快照，为它要读一次本地存储
        （同步状态 + 待推送条数，实测 13.7 µs、两条 SQL），而「今天是哪天」是纯算术
        （注入的钟 + 当前日界，实测 5.5 µs、零 I/O）。一秒问一次的那一跳走这一口。
        """
        ...

    def read_model(self) -> ReadModel | None:
        """读：从**当下这一份**本地副本一次装配出整份读模型（#81）。

        界面一次重画调它一次，三种读形状（清单索引 / 某个容器的任务 / 单条任务详情）都是
        它的投影——读一次只算一遍。没有本地副本时给 ``None``（降级态）。
        """
        ...

    def list_index(self) -> tuple[ListRow, ...]:
        """读：清单索引（内置视图、自定义视图、真实清单三种行）。"""
        ...

    def tasks_in(self, container_id: str) -> TaskList:
        """读：某个容器的任务列表（一个清单，或一个视图）。"""
        ...

    def move_targets(self) -> tuple[ListRow, ...]:
        """读：能把任务搬进去的那些清单（工单 #45 的挑选器只给这一份）。

        真实清单、进得去、**而且服务端已经见过它**——三条判据写在
        :meth:`SyncEngine.move_targets` 上。
        """
        ...

    def task_detail(self, task_id: str) -> TaskDetail | None:
        """读：单条任务的详情；本地没有这条任务时 ``None``。"""
        ...

    def tags(self) -> tuple[str, ...]:
        """读：现在能挑的那些标签名（服务端给过的那一份 ∪ 本地任务上出现过的，#45）。"""
        ...

    async def load_tags(self) -> tuple[str, ...]:
        """读：拉一次标签列表（``GET /open/v1/tag``）——**要 await**（#45）。"""
        ...

    async def refresh(self) -> RefreshReport:
        """写：全量刷新。**要 await**：它不是一次纯本地操作。"""
        ...

    async def push_pending(self, *, manual: bool = False) -> int:
        """写：推一轮待推送改动（该试的才推），返回推成功的条数。

        手动同步（``r``）与周期泵（t21）都从这里过；等待与定时都不在引擎里，
        引擎只负责说清楚「现在哪些能试」。

        ``manual=True`` 只该由 ``r`` 传：自动路径（写完之后立刻那次、周期泵、启动刷新）
        跳过已经放弃的改动，手动再试它们一次（工单 #71）。默认 ``False`` 就是自动。
        """
        ...

    async def refresh_completed(self) -> CompletedReport:
        """写：拉一次已完成流（按完成时间游标拉窗口）。**要 await**。"""
        ...

    def write(
        self,
        task_id: str,
        *,
        changes: Mapping[str, Any] | None = None,
        kind: WriteKind = WriteKind.UPDATE,
    ) -> bool:
        """写：把 ``changes`` 里那几个字段盖上去（本地当场生效 + 立刻推送）。

        **回报这次到底改了没有**（工单 #79）：改了回 ``True``；一位都没变时什么都不写（不入队、
        不排推送、不动本地那一份）回 ``False``。完成 / 删除那几条预置写没有这一档，照旧回
        ``True``（它们的对外动作是不可逆的，再来一次就是再来一次）。

        改一个字段（详细页 #43 的标题 / 描述 / 备注）与完成、删除走的是同一条乐观写路径；
        本地没有这条任务的底稿时当场抛 :class:`~dida.sync.writes.UnknownTaskError`——界面
        据此说出**具体**原因，而不是一个笼统的「保存失败」（用户故事 81）。
        """
        ...

    def complete(self, task_id: str) -> None:
        """写：完成并立即推送。"""
        ...

    def uncomplete(self, task_id: str) -> None:
        """写：取消完成并立即推送（``space`` 的第二个方向，工单 #38）。

        本地当场把 ``status`` 写回「未完成」那一档（完成时间戳不动），推送走
        ``task/batch`` 的 ``update``——那是实测确认能生效的唯一一条路。
        """
        ...

    def defer(self, task_id: str, *, days: int = 1) -> bool:
        """写：顺延 ``days`` 个逻辑日（``g`` 是 1 天、``G`` 是 7 天）。

        回报这次到底挪了没有（#79）：落点与本地那一份相同时回 ``False``（什么都不写）。
        """
        ...

    def delete(self, task_id: str) -> None:
        """写：删除一条任务（服务端没有撤销，t16）。"""
        ...

    def move_task(self, task_id: str, *, to_list_id: str) -> bool:
        """写：把这条任务搬到另一个清单——走**搬运端点**，不是普通字段更新（#45）。

        回报这次到底搬了没有（#79）：搬到它已经在的那个清单回 ``False``（什么都不写）。
        """
        ...

    def reschedule(self, task_id: str, *, due: datetime | None, all_day: bool = False) -> bool:
        """写：改期——把截止时间换成 ``due``，只动 ``dueDate`` 与 ``isAllDay``。

        ``due=None`` 是**清除**（详细页 #44 的「把任务变回没有日期」）：写显式的
        ``dueDate: null``，让服务端知道这一格空了，而不是「别动它」。

        回报这次到底改了没有（#79）：那一刻与本地那一份相同（同一个时刻的两种写法也算相同）
        回 ``False``——详细页「一个字都没改就提交」那一次空写走的就是这里。
        """
        ...

    def create(
        self,
        title: str,
        list_id: str,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：新建一条任务到 ``list_id``（``n``），返回本地那条的 id（#39）。

        ``list_id`` **必填**：在清单里建就传那个清单的 id，在视图里建传收集箱
        （``INBOX_ID``——视图不是容器），视图隐含的日期由调用方从
        ``tasks_in(视图).implied_due`` 读出来交给 ``due=``。
        """
    def create_list(self, name: str, *, color: str | None = None) -> str:
        """写：新建一个清单（``n``），返回本地那一行的 id（#42）。"""
        ...

    def update_list(
        self, list_id: str, *, name: str | None = None, color: str | None = None
    ) -> bool:
        """写：改清单的名字与颜色（``e``）——没给的字段不动（#42）。

        回报这次到底改了没有（#79）：给的字段都与本地那一行相同时回 ``False``。
        """
        ...

    def delete_list(self, list_id: str) -> None:
        """写：删一个清单（``d``，TUI 已经问过一句）。服务端没有撤销（#42）。"""
        ...

    def create_view(self, definition: ViewDefinition) -> str:
        """写：建一个自定义视图（``n`` → 「视图」），返回本地那一行的 id（#36）。

        **只在本地落库**：视图不推服务端（API 没有「保存一组过滤条件」这个接口），
        所以它不入队、待推送数量不动。
        """
        ...

    def update_view(self, definition: ViewDefinition) -> bool:
        """写：改一个自定义视图的条件与名字（``e``）；本地没有这一行时抛
        :class:`~dida.sync.views.UnknownViewError`（#36）。

        回报这次到底改了没有（#79）：交回来的那份与本地那一行一样时回 ``False``。
        """
        ...

    def delete_view(self, view_id: str) -> None:
        """写：删一个自定义视图（``d``，TUI 已经问过一句）。**一条任务都不碰**（#36）。"""
        ...

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """读：一个自定义视图的定义；本地没有就是 ``None``（``e`` 的表单要拿它填当前值）。"""
        ...

    def cycle_priority(self, task_id: str) -> None:
        """写：优先级推进一档（``p``）——无 → 低 → 中 → 高 → 无。"""
        ...

    def subtasks(self, task_id: str) -> tuple[SubtaskItem, ...]:
        """读：这条任务的子任务行（详情页的只读那一段），标题与完成状态都已经是可以直接画的成品。

        **只读**：spec 的「子任务只看不勾（也不能增删改）」，所以没有对应的写入口（#58 把
        v1 的 ``toggle_subtask`` 删掉了）。
        """
        ...


class SyncEngine(
    ListMixin,
    ViewMixin,
    RefreshMixin,
    PushMixin,
    CompletedStreamMixin,
    ScheduleMixin,
    CreateMixin,
    PriorityMixin,
    SubtaskMixin,
    TagMixin,
):
    """通用客户端的数据与写入入口（组装各片；读路径在本模块）。

    :class:`~dida.sync.lists.ListMixin` 排在第一位，所以引擎的 ``push_pending()`` 是它那一份
    （先推清单改动，再把任务那一份交给 :class:`~dida.sync.push.PushMixin`）；其余方法照旧
    按名字解析，各自的 ``self._…`` 都落在同一个实例上。:class:`~dida.sync.views.ViewMixin`
    在它们后面：自定义视图的建 / 改 / 删**只在本地落库**，与推送那几片没有交集。
    """

    def __init__(
        self,
        *,
        clock: Clock,
        day_end: str = DEFAULT_DAY_END,
        source: ViewSource | None = None,
        client: ProjectReader | TaskWriter | CompletedReader | TagReader | None = None,
        completed_window_hours: int = DEFAULT_COMPLETED_WINDOW_HOURS,
        push_on_change: bool = True,
    ) -> None:
        self._clock = clock
        self._day_end = day_end
        self._source = source
        self._client = client
        self._completed_window_hours = completed_window_hours
        # 标签列表：用户打开挑标签那一格时拉一次，只活在内存里（见 dida.sync.tags 的模块文档）。
        self._tags: tuple[str, ...] = ()
        # 「界面上的改动立即推送」（配置键 push_on_change，spec 的配置 schema）。关掉它只是
        # 不排那一轮**立刻**的推送：改动照样入队、照样在本地生效，等下一次 push_pending
        # （手动同步 r，或 t21 的周期泵）再出去。默认开着，ADR-0002 要的就是立刻推。
        self._push_on_change = push_on_change
        # 推送串行化：一次写会顺手排一轮推送，别让同一批改动被两个协程同时推两遍
        # （完成与删除不是幂等的）。锁本身不绑事件循环，第一次 acquire 时才绑。
        self._push_lock = asyncio.Lock()
        # 已在飞的推送轮次；wait_for_pushes() 等它们。
        self._inflight: set[asyncio.Task[int]] = set()

    def set_day_end(self, day_end: str) -> bool:
        """换掉「一天结束的时刻」（工单 #46）；真变了才返回 ``True``。

        日界是**唯一**一处「现在」之外的配置性输入，而它随时可能被用户改（#46：改完立刻
        生效，不用重启）。读路径每一处都读 :attr:`_day_end`，所以换在这里，下一次读就整体
        按新的逻辑日重算。

        返回值是给调用方省一次重画的：``False`` = 递进来的值与现在这个一样，什么都不用做。
        """
        if day_end == self._day_end:
            return False
        self._day_end = day_end
        return True

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        now = self._clock.now()
        state = self._source.sync_state() if self._source is not None else SyncState()
        return SyncStatus(
            checked_at=now,
            pending_count=state.pending_count,
            last_refresh_at=state.last_refresh_at,
            logical_day=logical_day(now, self._day_end).label,
            last_error=pending_error(self._source),
        )

    def logical_day(self) -> date:
        """读：现在是哪个逻辑日（工单 #46 的心跳靠它判断屏幕上的那一份过期了没有）。

        与 :meth:`status` 分开是**故意的**：那一份是状态栏的快照，为它要读一次本地存储
        （同步状态 + 待推送条数，实测 13.7 µs、两条 SQL），而「今天是哪天」是纯算术
        （注入的钟 + 当前日界，实测 5.5 µs、零 I/O）。一秒问一次的那一跳走这一口。
        """
        return logical_day(self._clock.now(), self._day_end).label

    def read_model(self) -> ReadModel | None:
        """读：从**当下这一份**本地副本一次装配出整份读模型（#81）。

        界面一次重画调它一次，三种读形状都是它的投影：进一个视图不再为了找那一行把索引
        重建一遍，同一个视图的条数与进去看到的成员也来自同一次求值。**没有缓存**——每一次
        重画都从当下这一份本地副本重新装配，所以旧值不会留下来，也就没有过期问题。

        没有本地副本（``SyncEngine(source=None)`` 那种降级态）时给 ``None``：读不出东西是
        「没有」，不是「空缓存」——空缓存照样有收集箱与三个内置视图那几行。
        """
        source = self._source
        if source is None:
            return None
        tasks = tuple(source.tasks())
        return assemble_read_model(
            tuple(source.lists()),
            tasks,
            self._view_definitions(),
            now=self._clock.now(),
            day_end=self._day_end,
            window_hours=self._completed_window_hours,
            resolve_id=source.resolve_id,
            payload_of=self._payload_of,
        )

    def list_index(self) -> tuple[ListRow, ...]:
        """读：清单索引——收集箱置顶，然后内置视图、自定义视图、真实清单（#33）。

        它是 :meth:`read_model` 那一份读模型的投影（#81）。缓存不在就是空索引，不是错误
        （空缓存给空行，而不是抛）。
        """
        model = self.read_model()
        return () if model is None else model.list_index()

    def tasks_in(self, container_id: str) -> TaskList:
        """读：某个容器的任务列表——它的**全部**未完成任务（未来的也在）+ 已完成的那部分。

        认不出来的容器给空列表（清单被删了、光标停在一条已经不在的行上）。

        ``container_id`` 可能是**认领换名之前**的那个清单 id（人正站在一个刚建好的清单里，
        工单 #75）：读模型那一层先解析成它现在的名字，不然这一屏会是空的（读法见
        ``ViewSource.resolve_id``）。
        """
        model = self.read_model()
        if model is None:
            return TaskList(container_id=container_id)
        return model.tasks_in(container_id)

    def task_detail(self, task_id: str) -> TaskDetail | None:
        """读：单条任务的详情（重复规则、提醒、子任务、原文里的未知字段都在这）。

        ``task_id`` 可能是**认领换名之前**的那个临时 id（屏幕上那一行就是旧的，工单 #75）：
        先解析成它现在的名字，不然这一页会说「这条任务已经不在本地缓存里了」——而那正是用户
        报上来的那句话。

        **不走** :meth:`read_model` 那一份整装配：详情形状不要视图成员，为它把所有视图求值
        一遍是白付的（实测 500 条任务 / 8 个视图：0.11ms → 31ms）。它读的仍是当下这一份本地
        副本，用的是同一条 :func:`~dida.sync.read.detail_of`——少装配一样东西，不是另一份实现。
        """
        source = self._source
        if source is None:
            return None
        tasks = tuple(source.tasks())
        return detail_of(
            resolve_lists(tuple(source.lists()), tasks),
            tasks,
            task_id,
            now=self._clock.now(),
            day_end=self._day_end,
            resolve_id=source.resolve_id,
            payload_of=self._payload_of,
        )

    def move_targets(self) -> tuple[ListRow, ...]:
        """读：能把任务搬进去的那些清单（工单 #45 的挑选器只给这一份）。

        三条判据缺一不可：

        - **是真实清单**（``ListKind.LIST``）：内置视图与自定义视图不是清单，搬不进去。
        - **进得去**（:attr:`~dida.sync.read.ListRow.enterable`）：``kind`` 是 ``NOTE`` 的
          装不了任务、没有写权限的改不动（用户故事 23 / 24）——搬进去只会被服务端拒掉。
        - **服务端已经见过它**：本地刚建、还没推上去的那一行 id 是**本地临时的**
          （``local-list-N``），拿它当 ``toProjectId`` 会 404，而那条改动**永远推不出去**
          ——状态栏那个数从此一直非零（#53/#54 是同一类）。判据是队列里还有没有这一行的
          ``CREATE``，不是 id 长什么样。

        **不装配视图**（#81）：挑清单只要真实清单那几行（:func:`~dida.sync.read.list_rows`
        那一份，与清单索引同一处组装），而视图成员是这一格里用不到的东西。
        """
        source = self._source
        if source is None:
            return ()
        unseen = self._unseen_list_ids()
        tasks = tuple(source.tasks())
        rows = list_rows(resolve_lists(tuple(source.lists()), tasks), tasks)
        return tuple(
            row
            for row in rows
            if row.kind is ListKind.LIST and row.enterable and row.id not in unseen
        )

    def _unseen_list_ids(self) -> frozenset[str]:
        """服务端还没见过的清单 id（本地还有一笔没推成功的 ``CREATE``）。

        读的是一条**已经记下来的事实**（``Store.pending_lists`` 那张队列表），不是 id 的
        形状。只读替身没有队列，于是没有这个信息——与写路径上那几个 ``isinstance`` 门
        同一条口径：不知道就说不知道，不猜一个。
        """
        source = self._source
        if not isinstance(source, ListWriteTarget):
            return frozenset()
        return frozenset(
            change.list_id
            for change in source.pending_lists()
            if change.kind is ListWriteKind.CREATE
        )

    def _view_definitions(self) -> tuple[ViewDefinition, ...]:
        """本地库里那些自定义视图的**定义**（#36）；装配读模型时交给那一层去求值。

        本地副本只给**定义**（它手上没有逻辑日，不读时钟），求值走
        :func:`~dida.sync.read.custom_view_rows`——与内置视图那三个是同一个
        ``evaluate_view``。源上没有这个能力就是「没有自定义视图」，不是错误——与写路径上
        那几个 ``isinstance`` 门同一条口径。
        """
        source = self._source
        if not isinstance(source, ViewReader):
            return ()
        return tuple(source.view_definitions())

    def _write_target(self) -> WriteTarget:
        """写路径要写的那个本地副本。没接上就大声报错——绝不假装写成功了。"""
        if not isinstance(self._source, WriteTarget):
            raise RuntimeError(
                "写路径需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 存不下待推送改动"
            )
        return self._source
