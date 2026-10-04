"""同步引擎（第 4 个深模块）。

TUI 读写一切只能走本模块；分组、排序、逾期判定、冲突裁决都发生在这里，不在 TUI 里。
公开接口：

- ``status() -> SyncStatus`` —— 读：状态栏所需的全部信息。
- ``view() -> TodayView`` —— 读：分组视图模型（类型见 :mod:`dida.sync.view`）。
- ``refresh() -> RefreshReport`` —— 写：全量刷新（逐清单拉取 → 本地 diff → 只写变化），t09。
  它是 **async** 的：网络等待不能阻塞界面，而写本地库必须发生在创建那条 sqlite 连接的
  同一个线程上（t08 的线程亲和）。异步协程跑在事件循环那一个线程里，两条同时满足——
  但**不许**把它丢进 ``threading.Thread`` 工人里跑。
- ``write(task_id, changes=, kind=)`` —— 写：乐观写，本地当场生效并入队，**立即返回**，t10。
  推送排在事件循环上（同一个线程），失败按注入的钟指数退避重试；``wait_for_pushes()``
  是等它的确定性入口（给测试用）。
- ``complete(task_id)`` —— 写：完成并立即推送（服务端不可逆），t11。
- ``defer(task_id)`` —— 写：顺延到下一个逻辑日，t13。
- ``refresh_completed() -> CompletedReport`` —— 写：已完成流（按完成时间游标拉窗口），t12。

协作者都是注入的：``source`` 是本地副本（生产是 t08 的 ``Store``，只读的 ``ViewSource``
照样能跑读路径，只是刷新与写入会大声报错），``client`` 是 t07 的 ``DidaApiClient``
（刷新与推送都要它）。组合根 ``dida.bootstrap`` 负责接线，本模块不自己 new 任何东西。

「现在」永远取自注入的 ``Clock``，「一天结束的时刻」是注入的 ``day_end``——
本模块里没有 ``datetime.now()``，也没有自己算的日界（交给 :mod:`dida.logical_day`）。

视图模型与分组纯函数都在 :mod:`dida.sync.view`，这里再导出一份，好让 TUI 只 import
``dida.sync.engine`` 一个东西（``tests/test_architecture.py`` 守着这条）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError, MalformedResponseError
from dida.api.guards import api_date
from dida.clock import Clock
from dida.logical_day import LogicalDay, logical_day
from dida.sync.view import (
    INBOX_ID,
    NO_DUE_TEXT,
    CompletedItem,
    CompletedSection,
    GroupKind,
    ListSnapshot,
    ListSummary,
    SyncState,
    TaskGroup,
    TaskItem,
    TaskSnapshot,
    TodayView,
    ViewSource,
    completed_section,
    format_due,
    group_tasks,
    priority_mark,
    summarize_lists,
)

if TYPE_CHECKING:  # 只为了标注：storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import (
        ChangeKind,
        PendingChange,
        RefreshReport,
        StoredSyncState,
    )

__all__ = [
    "INBOX_ID",
    "NO_DUE_TEXT",
    "CompletedItem",
    "CompletedReader",
    "CompletedReport",
    "CompletedSection",
    "Engine",
    "GroupKind",
    "ListSnapshot",
    "ListSummary",
    "ProjectReader",
    "RefreshTarget",
    "SyncEngine",
    "SyncState",
    "SyncStatus",
    "TaskGroup",
    "TaskItem",
    "TaskSnapshot",
    "TodayView",
    "UnknownTaskError",
    "ViewSource",
    "WriteKind",
    "WriteTarget",
    "backoff_delay",
    "completed_section",
    "format_due",
    "group_tasks",
    "priority_mark",
    "summarize_lists",
]

DEFAULT_DAY_END = "00:00"
"""配置注入之前的默认日界：零偏移，逻辑日等于自然日（规范形式见 ADR 0003）。"""

DEFAULT_BACKOFF_BASE = timedelta(seconds=2)
"""第一次推送失败之后等多久再试（之后每次翻倍）。"""

DEFAULT_BACKOFF_CAP = timedelta(minutes=5)
"""两次重试之间最多等多久。断网一整天也不该攒出一个巨大的间隔。"""

DEFAULT_COMPLETED_WINDOW_HOURS = 24
"""已完成流往回看多少小时（配置键 ``completed_window_hours`` 的兜底值）。"""

COMPLETED_PAGE_LIMIT = 200
"""``POST /open/v1/task/completed`` 一次最多回多少条（api-contracts.md）。"""


def backoff_delay(
    attempts: int,
    *,
    base: timedelta = DEFAULT_BACKOFF_BASE,
    cap: timedelta = DEFAULT_BACKOFF_CAP,
) -> timedelta:
    """失败 ``attempts`` 次之后再推要等多久：``base``、``2×base``、``4×base``…封顶 ``cap``。

    ``attempts`` 是**已经失败过的次数**（存储层在 ``record_attempt`` 里 +1），所以第一次
    失败等 ``base``。纯函数，参数从外面进来：没有时钟、没有随机抖动——抖动会让测试变成
    掷骰子，而这个 app 的重试节奏本来就由用户的下一次按键与状态栏那个数兜着。
    """
    return min(base * 2**attempts, cap)


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


class WriteKind(Enum):
    """一次乐观写的种类；值与存储层的 ``ChangeKind`` 一一对应（见 :func:`_storage_kind`）。

    引擎有自己的这一份词汇，是因为 TUI 只 import ``dida.sync.engine``，而 ``ChangeKind``
    住在存储层；至于为什么不在模块顶层直接 import 它，:func:`_storage_kind` 里写了。
    新建（``create``）不在其中：这条写路径服务的是**已有任务**的改 / 完成 / 删。
    """

    UPDATE = "update"
    """改字段：把 ``changes`` 推给 ``POST /open/v1/task/{taskId}``。"""

    COMPLETE = "complete"
    """完成：本地立刻标记完成，推送走 ``POST .../task/{taskId}/complete``（无请求体）。"""

    DELETE = "delete"
    """删除：本地立刻摘掉快照，推送走 ``DELETE .../task/{taskId}``。"""


@runtime_checkable
class Engine(Protocol):
    """TUI 眼里的引擎：它只用得到这五个。t11/t13 的实现必须仍然满足它。"""

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        ...

    def view(self) -> TodayView:
        """读：分组后的视图模型。"""
        ...

    async def refresh(self) -> RefreshReport:
        """写：全量刷新。**要 await**：它不是一次纯本地操作。"""
        ...

    def complete(self, task_id: str) -> None:
        """写：完成并立即推送。"""
        ...

    def defer(self, task_id: str, *, days: int = 1) -> None:
        """写：顺延 ``days`` 个逻辑日（``g`` 是 1 天、``G`` 是 7 天）。"""
        ...


class ProjectReader(Protocol):
    """全量刷新要的那两次网络调用；t07 的 ``DidaApiClient`` 满足它。

    故意只有两个方法：刷新路径不需要客户端的写操作，也不需要它认识领域概念。
    """

    async def list_projects(self) -> list[dict[str, Any]]:
        """``GET /open/v1/project``：清单索引（服务端说了算，不是本地缓存）。"""
        ...

    async def get_project_data(self, project_id: str) -> dict[str, Any]:
        """``GET /open/v1/project/{id}/data``：该清单的未完成任务全量。"""
        ...


@runtime_checkable
class TaskWriter(Protocol):
    """推送要的那三个写操作；t07 的 ``DidaApiClient`` 满足它。

    故意不认识领域概念：引擎给它清单 id、任务 id 与（更新时的）底稿，它只管按文档发。
    """

    async def update_task(
        self,
        project_id: str,
        task_id: str,
        changes: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> Any:
        """``POST /open/v1/task/{taskId}``：更新任务（文档里没有 PATCH）。"""
        ...

    async def complete_task(self, project_id: str, task_id: str) -> None:
        """``POST /open/v1/project/{projectId}/task/{taskId}/complete``：无请求体。"""
        ...

    async def delete_task(self, project_id: str, task_id: str) -> None:
        """``DELETE /open/v1/project/{projectId}/task/{taskId}``。"""
        ...


@runtime_checkable
class RefreshTarget(ViewSource, Protocol):
    """本地副本在刷新路径上要会的那几件事；t08 的 ``Store`` 满足它。

    比 :class:`~dida.sync.view.ViewSource` 宽：全量刷新要能把原文写进去。
    只读的替身（``InMemorySource``）不满足它——刷新时会大声报错，不假装刷过了。
    """

    def stored_sync_state(self) -> StoredSyncState:
        """同步状态原样读回：已完成流游标不能被我这次刷新清掉（那是 t12 的）。"""
        ...

    def apply_refresh(
        self,
        *,
        lists: Sequence[Mapping[str, Any]] = (),
        tasks: Sequence[Mapping[str, Any]] = (),
    ) -> RefreshReport:
        """只写变化地落一次全量刷新，返回这次到底写了什么。"""
        ...

    def set_sync_state(
        self,
        *,
        completed_cursor: str | None = None,
        last_refresh_at: datetime | None = None,
        logical_day: date | None = None,
    ) -> None:
        """整体写入同步状态（``None`` 是清空）。"""
        ...


@runtime_checkable
class WriteTarget(ViewSource, Protocol):
    """本地副本在写路径上要会的那几件事；t08 的 ``Store`` 满足它。

    比 :class:`~dida.sync.view.ViewSource` 宽：乐观写要能把改动落进本地库并入队，
    推送要能读回完整原文（未知字段的底稿）、按 id 出队、记一次失败。
    只读的替身（``InMemorySource``）不满足它——写入会大声报错，不假装写成功了。
    """

    def task_payload(self, task_id: str) -> dict[str, Any] | None:
        """一条任务的完整原文，喂给 ``update_task(snapshot=)`` 的那一份。"""
        ...

    def enqueue(
        self,
        *,
        task_id: str,
        kind: ChangeKind,
        payload: Mapping[str, Any],
        now: datetime,
        list_id: str | None = None,
    ) -> PendingChange:
        """入队一条待推送改动，并让它在本地立刻生效。"""
        ...

    def pending(self) -> Sequence[PendingChange]:
        """还没推成功的改动，按发生顺序。"""
        ...

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次推送失败：尝试次数 +1、最后一次错误、下次重试时刻。"""
        ...

    def resolve(self, change_id: int) -> None:
        """这条改动已经推到服务端了，出队。"""
        ...


class SyncEngine:
    """今日执行台的数据与写入入口。"""

    def __init__(
        self,
        *,
        clock: Clock,
        day_end: str = DEFAULT_DAY_END,
        source: ViewSource | None = None,
        client: ProjectReader | TaskWriter | CompletedReader | None = None,
        completed_window_hours: int = DEFAULT_COMPLETED_WINDOW_HOURS,
    ) -> None:
        self._clock = clock
        self._day_end = day_end
        self._source = source
        self._client = client
        self._completed_window_hours = completed_window_hours
        # 推送串行化：一次写会顺手排一轮推送，别让同一批改动被两个协程同时推两遍
        # （完成与删除不是幂等的）。锁本身不绑事件循环，第一次 acquire 时才绑。
        self._push_lock = asyncio.Lock()
        # 已在飞的推送轮次；wait_for_pushes() 等它们。
        self._inflight: set[asyncio.Task[int]] = set()

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        now = self._clock.now()
        state = self._source.sync_state() if self._source is not None else SyncState()
        return SyncStatus(
            checked_at=now,
            pending_count=state.pending_count,
            last_refresh_at=state.last_refresh_at,
            logical_day=logical_day(now, self._day_end).label,
        )

    def view(self) -> TodayView:
        """读：从本地缓存分组出的视图模型。缓存不在就是空视图，不是错误。"""
        if self._source is None:
            return TodayView(lists=(), groups=())
        lists = tuple(self._source.lists())
        tasks = tuple(self._source.tasks())
        now = self._clock.now()
        return TodayView(
            lists=summarize_lists(lists, tasks),
            groups=group_tasks(tasks, lists, now=now, day_end=self._day_end),
            completed=completed_section(
                tasks,
                lists,
                now=now,
                day_end=self._day_end,
                window_hours=self._completed_window_hours,
            ),
        )

    async def refresh(self) -> RefreshReport:
        """全量刷新（ADR 0001）：逐清单取回未完成 → 与本地快照 diff → 只写变化。

        返回 :class:`~dida.storage.store.RefreshReport`：这次到底写了什么、服务端盖掉了
        哪些本地值、哪些被待推送改动挡回去了（ADR-0002 要求覆盖能被看见）。

        **网络层没有「增量」**：日期窗口会静默漏掉「日期在很久以后、但刚被改过」的任务，
        所以未完成任务的唯一来源是逐清单全量。增量发生在本地：同一份数据拉第二次时，
        ``written_lists`` 与 ``written_tasks`` 都是 0，界面因此不闪、光标因此不丢。

        取数顺序：先清单索引（服务端的），再逐个清单的 data。**全部取回之后才落库**，
        所以中途任何一次失败都不会留下半份刷新：要么整份落地，要么本地库一动不动。
        失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，不吞。
        """
        target = self._refresh_target()
        reader = self._reader()

        index = _project_index(await reader.list_projects())
        known = {str(item["id"]) for item in index}
        fetched = [str(item["id"]) for item in index]
        if INBOX_ID not in known:
            # 收集箱：文档允许用字面量 "inbox" 当 projectId，而它是默认清单——索引里没有它也
            # 得拉，否则「随手记」的任务永远不出现，还不报错。
            fetched.append(INBOX_ID)

        lists: list[Mapping[str, Any]] = list(index)
        tasks: list[Mapping[str, Any]] = []
        for project_id in fetched:
            payload = await reader.get_project_data(project_id)
            tasks.extend(_unfinished_tasks(payload, project_id))
            # 索引没提过的清单，用它自己的原文补一行，左栏才不会漏掉一个装着任务的清单。
            if project_id not in known:
                project_payload = payload.get("project") if isinstance(payload, Mapping) else None
                if isinstance(project_payload, Mapping) and project_payload.get("id"):
                    lists.append(project_payload)
                    known.add(project_id)

        report = target.apply_refresh(lists=lists, tasks=tasks)

        # 只有整份落地了才记「刷新成功」：失败的那次不算已同步。游标原样带回去——
        # set_sync_state 的 None 是清空，顺手抹掉的话 t12 的已完成流会从头再拉一遍。
        now = self._clock.now()
        target.set_sync_state(
            completed_cursor=target.stored_sync_state().completed_cursor,
            last_refresh_at=now,
            logical_day=logical_day(now, self._day_end).label,
        )
        return report

    def complete(self, task_id: str) -> None:
        """写：完成任务并立即推送（ADR 0002，服务端不可逆）。

        就是 :meth:`write` 的一个预置：本地当场标记完成（``status`` 由引擎补 ``2``），
        推送走没有请求体的 ``complete`` 端点。没有「取消完成」这条路径——服务端没有
        这个接口，本地自己造一个只会在下一次刷新时被服务端权威抹掉（ADR-0002）。
        """
        self.write(task_id, kind=WriteKind.COMPLETE)

    def defer(self, task_id: str, *, days: int = 1) -> None:
        """写：顺延到下一个逻辑日（``g``）；``days=7`` 是下周同一天（``G``）。t13 实现。

        落点由 :func:`dida.logical_day.logical_day` 决定，TUI 不重算任何日界：先找出目标
        逻辑日（``[start, end)`` 的 ``end`` 就是下一个逻辑日），再把这条任务**原来的墙钟
        时刻**放进那个逻辑日。所以边界配成 ``04:00`` 时，凌晨两点的「明天」是用户作息里的
        明天，而不是机器日历日 +1——顺延过的任务醒来时仍然读作「今日」，不会被判成逾期。

        只改 ``dueDate`` 一个字段（GLOSSARY 的「顺延」），而且走 :meth:`write` 那条写路径：
        本地当场生效、立即推送、推不动就留在队列里按注入的钟退避重试。
        """
        target = self._write_target()
        payload = target.task_payload(task_id)
        if payload is None:  # 本地没有这条任务，就没有「当前的截止时间」可挪
            return
        changes = _defer_changes(payload, now=self._clock.now(), day_end=self._day_end, days=days)
        if changes is None:  # 没有截止时间可挪：无日期的任务不凭空长出一个日期来
            return
        self.write(task_id, changes=changes)

    # ---------------------------------------------------------------- 写路径

    def write(
        self,
        task_id: str,
        *,
        changes: Mapping[str, Any] | None = None,
        kind: WriteKind = WriteKind.UPDATE,
    ) -> None:
        """乐观写：本地当场生效并入队，然后**立即返回**；网络结果不是它的前置条件。

        ADR-0002 的口径：一次按键的手感比可撤销性值钱，所以本地先动，服务端随后到。
        改动进 :class:`~dida.storage.store.PendingChange` 队列后，会立刻在事件循环上
        推一次；推不动就留在队列里按注入的钟退避重试，绝不让这一屏等网络。

        ``changes`` 是这次要盖上去的字段（本地与请求体同一份，未知字段的底稿由存储层
        拼好交给 :meth:`~dida.api.client.DidaApiClient.update_task` 的 ``snapshot=``）。
        写失败（服务端说不行、网络断了）在 UI 上表现为状态栏那个待推送数量，而不是异常。

        t11 的完成、t13 的顺延都从这一个入口走；它俩只需要给 ``task_id`` 与 ``changes``。

        本地缓存里没有这条任务（或者那份底稿没有 ``projectId``）时不入队，当场抛
        :class:`UnknownTaskError`：请求的清单 id 只存在于底稿里，凭空入队只会留下一条
        **永远推不出去**的改动，让状态栏那个数一直非零（工单 #25）。
        """
        target = self._write_target()
        snapshot = target.task_payload(task_id)
        if snapshot is None or not snapshot.get("projectId"):
            raise UnknownTaskError(task_id)
        target.enqueue(
            task_id=task_id,
            kind=_storage_kind(kind),
            payload=self._local_effect(kind, changes),
            now=self._clock.now(),
        )
        self._schedule_push()

    async def push_pending(self) -> int:
        """推一轮：把**到期**的待推送改动依次推给服务端，返回推成功的条数。

        这是重试队列唯一的泵。「什么时候该重试」由注入的钟判定：没排过重试的立刻推，
        排过的要等到 ``next_retry_at``。**没有 ``time.sleep``、没有真时钟、没有后台线程**
        ——等待发生在调用方（t14 那种定时器或下一次写），引擎只负责算清楚什么时候能推。

        一条失败不影响后面那些：队列按发生顺序走完，失败的留在队列里等下一次。
        """
        target = self._write_target()
        writer = self._writer()
        pushed = 0
        async with self._push_lock:
            for change in target.pending():
                now = self._clock.now()
                if not _is_due(change, now):
                    continue
                try:
                    await self._send(writer, target, change)
                except DidaError as exc:
                    # 推不动就留在队列里，记下这次失败与下一次的时刻。本地那份改动照旧
                    # 生效——ADR-0002 的豁免看的就是这个队列，用户的操作不会因为一次
                    # 网络抖动被撤销。
                    target.record_attempt(
                        change.id,
                        error=str(exc),
                        next_retry_at=now + backoff_delay(change.attempts),
                    )
                    continue
                target.resolve(change.id)
                pushed += 1
        return pushed

    async def wait_for_pushes(self) -> None:
        """等 :meth:`write` 排下的那几轮推送跑完。

        **确定性接缝**：``write()`` 不等网络，所以想知道「推完了没有」的一律是测试
        （生产路径上没有人 await 它）。它只等已经在飞的那几轮，自己不发起推送——
        到点该重试的由 :meth:`push_pending` 或下一次写来触发。
        """
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight))

    # ---------------------------------------------------------------- 内部

    def _schedule_push(self) -> None:
        """把「立刻推一轮」排到事件循环上（ADR-0002：写操作立即推送）。

        这就是「不等网络」的实现：写的人当场返回，推送在事件循环**同一根线程**上跑
        ——t08 的 sqlite 连接有线程亲和，写必须发生在创建连接的那条线程；协程正好满足，
        而新起一个 ``threading.Thread`` 会把它打破。

        没有事件循环（同步调用，比如纯本地测试）时不排：改动留在队列里，
        等下一次 :meth:`push_pending`（或下一次写）再推。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.push_pending())
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _send(self, writer: TaskWriter, target: WriteTarget, change: PendingChange) -> None:
        """把一条待推送改动交给客户端。失败是结构化错误，照旧往外抛（由调用方退避）。"""
        from dida.storage.store import ChangeKind  # 延迟 import，理由见 _storage_kind

        if change.kind is ChangeKind.UPDATE:
            await writer.update_task(
                change.list_id,
                change.task_id,
                change.payload,
                # 底稿是本地那份完整原文：不认识的字段靠它才能一个不丢地回写。
                snapshot=target.task_payload(change.task_id),
            )
        elif change.kind is ChangeKind.COMPLETE:
            await writer.complete_task(change.list_id, change.task_id)
        elif change.kind is ChangeKind.DELETE:
            await writer.delete_task(change.list_id, change.task_id)
        else:
            raise NotImplementedError(f"推送还没有实现「{change.kind.value}」这一种改动")

    def _writer(self) -> TaskWriter:
        """推送要的那个客户端。没接上就大声报错——绝不假装推过了。"""
        if not isinstance(self._client, TaskWriter):
            raise RuntimeError("推送需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client

    def _local_effect(self, kind: WriteKind, changes: Mapping[str, Any] | None) -> dict[str, Any]:
        """这次改动在本地要盖上去的那一份字段。

        完成是唯一的例外：本地要让这条任务立刻从今日视图里消失，就得写 ``status``，
        而这个值只有 API 知道（Completed 是 ``2``，api-contracts.md 第 2 条）——由引擎补，
        不让 t11 自己记一个魔法数。它不会进请求体：完成走的是没有请求体的 ``complete``
        端点，而且 ``status`` 本来也不是新建/更新接受的字段（同文件第 5 条）。
        """
        merged = dict(changes or {})
        if kind is WriteKind.COMPLETE:
            merged.setdefault("status", _completed_status())
        return merged

    def _write_target(self) -> WriteTarget:
        """写路径要写的那个本地副本。没接上就大声报错——绝不假装写成功了。"""
        if not isinstance(self._source, WriteTarget):
            raise RuntimeError(
                "写路径需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 存不下待推送改动"
            )
        return self._source

    def _refresh_target(self) -> RefreshTarget:
        """刷新要写的那个本地副本。没接上就大声报错——绝不假装刷过了。"""
        if not isinstance(self._source, RefreshTarget):
            raise RuntimeError(
                "全量刷新需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 写不进去"
            )
        return self._source

    def _reader(self) -> ProjectReader:
        """取数要的那个客户端。"""
        if self._client is None:
            raise RuntimeError("全量刷新需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client

    # ---------------------------------------------------------------- 已完成流（t12）

    async def refresh_completed(self) -> CompletedReport:
        """拉一次已完成流：窗口 ``[游标, 现在]``，把任务写进本地副本，并推进游标。

        这是**唯一**允许用时间游标做增量的数据流（ADR-0001）：``completedTime`` 是任务上
        唯一能被服务端过滤的变化时间戳。未完成任务照旧走逐清单全量，别把窗口学到这里来。

        窗口的两条边：

        - 往回看不早于 ``now - completed_window_hours``（配置给的，t12）；
        - 不早于上次拉到的位置（同步状态里的游标），所以连着刷两次不会把同一段拉两遍。

        请求体里**不带** ``projectIds``：文档说三个字段都可选，而「哪些清单里完成过」正是
        本地索引可能还不知道的事（用户在手机上往一个新清单里记了一笔并完成）。不筛清单，
        就不会漏。

        失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，并且**不推进游标**：
        没拉到的窗口下次还会再拉，绝不会被静默跳过。
        """
        target = self._refresh_target()
        reader = self._completed_reader()
        now = self._clock.now()
        state = target.stored_sync_state()
        start = self._completed_window_start(now, state.completed_cursor)

        payload = await reader.list_completed(
            # 日期一律写成文档形式（``+0800``）并且不换时区：游标就是这么存回来的。
            start_date=api_date(start, field="startDate"),
            end_date=api_date(now, field="endDate"),
        )
        tasks = _completed_tasks(payload)
        # 与全量刷新同一套落库路径：只写变化，服务端权威，待推送改动豁免（t08）。
        report = target.apply_refresh(tasks=tasks)
        # 游标只在成功之后前进；上次刷新时间与逻辑日原样带回去（None 是清空）。
        target.set_sync_state(
            completed_cursor=self._completed_cursor(now, tasks),
            last_refresh_at=state.last_refresh_at,
            logical_day=state.logical_day,
        )
        return CompletedReport(
            start=start,
            end=now,
            written_tasks=report.written_tasks,
            truncated=len(tasks) >= COMPLETED_PAGE_LIMIT,
        )

    def _completed_window_start(self, now: datetime, cursor: str | None) -> datetime:
        """这次拉取从哪一刻开始：配置窗口与持久化游标里**较晚**的那个。"""
        start = now - timedelta(hours=self._completed_window_hours)
        resumed = _parse_moment(cursor)
        return resumed if resumed is not None and resumed > start else start

    def _completed_cursor(self, now: datetime, tasks: Sequence[Mapping[str, Any]]) -> str:
        """拉完之后游标推到哪一刻。

        正常情况就是 ``now``：``[上次的 now, 这次的 now]`` 首尾相接，不重不漏。**满 200 条
        时例外**：文档把 200 写成上限，而没有任何分页或游标参数可续，所以「一次拿全了」
        是站不住的。这时退到这批里最新的那个完成时刻，把没拿到的部分留给下一次——宁可
        重一点，也不静默丢掉用户完成的任务。
        """
        if len(tasks) < COMPLETED_PAGE_LIMIT:
            return now.isoformat()
        newest = max(
            (
                moment
                for moment in (_parse_moment(task.get("completedTime")) for task in tasks)
                if moment is not None
            ),
            default=None,
        )
        return (newest if newest is not None else now).isoformat()

    def _completed_reader(self) -> CompletedReader:
        """已完成流要的那个客户端。没接上就大声报错——绝不假装拉过了。"""
        if not isinstance(self._client, CompletedReader):
            raise RuntimeError(
                "已完成流需要 API 客户端：SyncEngine(client=DidaApiClient(...))"
            )
        return self._client


def _is_due(change: PendingChange, now: datetime) -> bool:
    """这条改动现在能不能推：没排过重试的立刻推，排过的要等到点。

    「到点」是 ``<=``：注入的钟刚好走到 ``next_retry_at`` 时就算到期。
    """
    return change.next_retry_at is None or change.next_retry_at <= now


def _storage_kind(kind: WriteKind) -> ChangeKind:
    """引擎的写词汇 → 存储层的改动种类。

    **必须延迟 import**：``dida.storage.store`` 在模块级 import 了 ``dida.sync.view``，
    而 import 子模块会先跑父包的 ``__init__``（那里 import 了本模块）——顶层互相 import
    时总有一方拿到半成品模块，先 import 存储的那条路径直接 ImportError。
    """
    from dida.storage.store import ChangeKind

    return ChangeKind(kind.value)


def _completed_status() -> int:
    """任务「已完成」的 ``status`` 值（``2``，api-contracts.md 第 2 条）。

    从存储层取而不是在这里再写一个字面量：同一份 API 事实只留一处。
    """
    from dida.storage.store import COMPLETED_STATUS

    return COMPLETED_STATUS


def _project_index(payload: Any) -> list[Mapping[str, Any]]:
    """``GET /open/v1/project`` 的响应体 → 清单索引。

    **空数组是合法的**（就是没有清单），不是失败。形状不对则结构化报错：把 ``{}`` 当成
    「没有清单」会静默地什么都不刷新，那正是 ADR-0001 要挡的那类安静。
    """
    if not isinstance(payload, list) or any(
        not isinstance(item, Mapping) or not item.get("id") for item in payload
    ):
        raise MalformedResponseError(
            f"清单索引不是「带 id 的清单数组」：收到 {type(payload).__name__}"
        )
    return list(payload)


def _unfinished_tasks(payload: Any, project_id: str) -> list[Mapping[str, Any]]:
    """``ProjectData`` → 未完成任务原文。

    ``tasks`` 缺席或为空数组都是「这个清单没有未完成任务」（有效数据，不是错误）；
    形状不对才报错——一个形状不对的 ``tasks`` 被当成空数组，用户看到的就是「我的任务
    不见了」，而且不报错。

    服务端漏写 ``projectId`` 时补上取数用的那个清单 id：这条任务是从哪个清单拉回来的，
    只有引擎知道；不补的话 t08 会把它算进收集箱（左栏徽标与清单名全错，且不报错）。
    """
    if not isinstance(payload, Mapping):
        raise MalformedResponseError(
            f"清单 {project_id} 的 data 不是对象：收到 {type(payload).__name__}"
        )
    raw = payload.get("tasks")
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(task, Mapping) for task in raw):
        raise MalformedResponseError(
            f"清单 {project_id} 的 tasks 不是任务数组：收到 {type(raw).__name__}"
        )
    return [task if task.get("projectId") else {**task, "projectId": project_id} for task in raw]


# ------------------------------------------------------------------ 已完成流（t12）


@runtime_checkable
class CompletedReader(Protocol):
    """已完成流要的那一次网络调用；t07 的 ``DidaApiClient`` 满足它。

    三个字段都可选（文档如此），引擎只给窗口两端——清单筛选恰恰是本地索引可能还不知道
    的事，见 :meth:`SyncEngine.refresh_completed`。
    """

    async def list_completed(
        self,
        *,
        project_ids: Sequence[str] | None = None,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """``POST /open/v1/task/completed``：按完成时间窗口取已完成任务（一次最多 200 条）。"""
        ...


@dataclass(frozen=True)
class CompletedReport:
    """一次已完成流拉取的账目：窗口两端、写进本地库的条数、有没有撞上 200 条上限。"""

    start: datetime
    """这次窗口的左端（配置窗口与游标里较晚的那个）。"""

    end: datetime
    """这次窗口的右端，也是下次的游标（除非撞上限）。"""

    written_tasks: int = 0
    """真正写进本地副本的条数：同一份数据拉第二次是 0（ADR-0001 的「只写变化」）。"""

    truncated: bool = False
    """响应正好 200 条：文档说 200 是上限且无分页可续，所以别声称这一窗已经拿全了。"""


def _completed_tasks(payload: Any) -> list[Mapping[str, Any]]:
    """``POST /open/v1/task/completed`` 的响应体 → 任务原文。

    **空数组是合法的**（这个窗口里没人完成过任务），不是失败。形状不对则结构化报错：
    ``{}`` 被当成「没有已完成的任务」，用户看到的是「我的已完成不见了」而且不报错。
    """
    if not isinstance(payload, list) or any(
        not isinstance(task, Mapping) or not task.get("id") for task in payload
    ):
        raise MalformedResponseError(
            f"已完成流不是「带 id 的任务数组」：收到 {type(payload).__name__}"
        )
    return list(payload)


def _parse_moment(value: Any) -> datetime | None:
    """服务端的日期串（``completedTime``）或同步状态里的游标 → 时刻。

    解析不了、或者没有时区偏移，都算 ``None``：前者当作「没拉到过」从头按窗口拉，后者
    不该拿去和带时区的「现在」比较。宁可多拉一次，也不让一个脏游标把窗口悄悄挪走。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


# ------------------------------------------------------------------ 顺延（t13）


def _defer_changes(
    payload: Mapping[str, Any], *, now: datetime, day_end: str, days: int
) -> dict[str, Any] | None:
    """顺延要写进 ``write(changes=)`` 的那一份字段：只动 ``dueDate``。

    落点是**逻辑日**而不是自然日：目标逻辑日由 :func:`_logical_day_after` 逐步问纯函数得来，
    再把这条任务原来的墙钟时刻放进去。没有合用的截止时间时返回 ``None``——顺延不改其它
    字段，也绝不凭空补一个日期（服务端对没日期的任务是另一套静默行为）。
    """
    raw = payload.get("dueDate")
    if not isinstance(raw, str):
        return None
    subject = _parse_due(raw)
    if subject is None or subject.tzinfo is None:
        # 脏日期或没有时区的日期：当作挪不动。替它猜一个时区就是「静默位移」那个 trap。
        return None
    target = _logical_day_after(logical_day(now, day_end), day_end, days)
    landed = datetime.combine(target.label, subject.timetz())
    if not payload.get("isAllDay") and landed < target.start:
        # 墙钟时刻比日界早（如 ``04:00`` 边界上的 02:00）：那个时刻按逻辑日属于**前一天**，
        # 落在目标逻辑日之外，顺延就等于没顺延。往后挪一天，让落点真的在 ``[start, end)`` 里
        # ——夜猫子的「明天凌晨两点」是后天 02:00，不是今天的深夜。
        #
        # 全天任务不走这一步：它的截止是**日期标记**（看 ``due.date()``），00:00 只是标记
        # 的形状；套偏移会把它整天推到再下一天。
        landed += timedelta(days=1)
    return {"dueDate": api_date(landed, field="dueDate")}


def _parse_due(value: str) -> datetime | None:
    """解析服务端的日期字符串；吃不下就当作没有截止时间（与存储层同一口径）。"""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _logical_day_after(day: LogicalDay, day_end: str, days: int) -> LogicalDay:
    """往后数 ``days`` 个逻辑日（``g`` 是 1 天、``G`` 是 7 天）。

    每一步都问纯函数：``[start, end)`` 的 ``end`` 恰好是下一个逻辑日。不拿自然日加加减减
    ——跨 DST 时一个逻辑日的墙钟长度不是 24 小时，加天数会悄悄挪走用户的墙钟时刻。
    """
    for _ in range(days):
        day = logical_day(day.end, day_end)
    return day


# ------------------------------------------------------------------ 写路径的守卫（t25）


class UnknownTaskError(DidaError):
    """这条写推不出去，所以拒绝它：本地没有一条能拼出请求的底稿（工单 #25）。

    三条写端点都要任务**真实的清单 id**：更新是 ``POST /open/v1/task/{taskId}``，请求体
    要求 ``id`` + ``projectId``（api-contracts.md）；完成与删除把它写在路径里。这份清单
    只存在于本地那份 ``task_payload`` 底稿里——**没有别处可查**，引擎手里只有 ``task_id``。

    没有底稿还硬写下去的实测后果：存储层拿收集箱兜底（``Store._list_of``），凭空造出一行
    没有 ``projectId`` 的快照，请求带着一个**猜来的**清单发出去，失败后那条改动永远留在
    队列里。状态栏那个数一直非零，用户读到的是「等一下就好」，而它永远不会好——这正是
    规范「如实呈现」要消灭的那类安静错误。所以宁可当场大声拒绝。
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(
            f"本地缓存里没有任务 {task_id} 的可用底稿（写操作需要它的 projectId）："
            "拒绝入队，因为这条改动永远推不出去"
        )
        self.task_id = task_id
        """请求写入的那条任务 id，UI 可以直接显示出来。"""
