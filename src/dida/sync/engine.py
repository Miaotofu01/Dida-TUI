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
from dida.clock import Clock
from dida.logical_day import logical_day
from dida.sync.view import (
    INBOX_ID,
    NO_DUE_TEXT,
    GroupKind,
    ListSnapshot,
    ListSummary,
    SyncState,
    TaskGroup,
    TaskItem,
    TaskSnapshot,
    TodayView,
    ViewSource,
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
    "ViewSource",
    "WriteKind",
    "WriteTarget",
    "backoff_delay",
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

    def defer(self, task_id: str) -> None:
        """写：顺延到下一个逻辑日。"""
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
        client: ProjectReader | TaskWriter | None = None,
    ) -> None:
        self._clock = clock
        self._day_end = day_end
        self._source = source
        self._client = client
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
        return TodayView(
            lists=summarize_lists(lists, tasks),
            groups=group_tasks(tasks, lists, now=self._clock.now(), day_end=self._day_end),
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
        """写：完成任务并立即推送（ADR 0002，服务端不可逆）。t11 实现。"""
        raise NotImplementedError("完成由 t11 实现")

    def defer(self, task_id: str) -> None:
        """写：顺延到下一个逻辑日。t13 实现。"""
        raise NotImplementedError("顺延由 t13 实现")

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
        """
        target = self._write_target()
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
