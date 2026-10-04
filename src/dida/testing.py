"""测试替身，供所有工单的测试复用。

- :class:`ManualClock` —— 时钟接缝（``dida.clock.Clock``）。
- :class:`FakeTransport` —— HTTP 传输接缝（``dida.api.transport.Transport``）。
- :class:`InMemorySource` —— 本地缓存的内存替身（``dida.sync.view.ViewSource``）。
- :class:`FakeBackend` —— **接缝一的假后端**：给它内存数据，它用真引擎的纯函数分组，
  写操作只记录。TUI 测试一律 ``DidaApp(FakeBackend(clock=...))`` 这样搭。

这里只放「记录 + 回放」的哑替身，不含任何业务判断：分组、排序、逾期判定都调用
``dida.sync.view`` 的纯函数，和真引擎同一份实现，替身不会跟真货说不一样的话。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Sequence

import httpx

from dida.clock import Clock
from dida.date_parser import ParsedTask
from dida.storage.store import RefreshReport
from dida.sync.engine import CompletedReport, SubtaskWrite, SyncEngine, SyncStatus
from dida.sync.view import (
    INBOX_NAME,
    ListSnapshot,
    SubtaskItem,
    SyncState,
    TaskSnapshot,
    TodayView,
)


class ManualClock:
    """由测试摆布的时钟。"""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        self._now = now


class FakeTransport:
    """假传输：记录每个请求，按队列回放响应（或抛异常）。

    默认回放一个 ``json=`` 的 200 响应；用 :meth:`enqueue` 追加更多响应或异常。
    """

    def __init__(self, *, json: Any = None, status_code: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._responses: deque[httpx.Response | Exception] = deque()
        self._default: httpx.Response = httpx.Response(status_code, json=json)

    def enqueue(self, response: httpx.Response | Exception) -> None:
        self._responses.append(response)

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "没有收到任何请求"
        return self.requests[-1]

    @property
    def last_json(self) -> Any:
        """最后一个请求的请求体（按 JSON 解析）。"""
        return json.loads(self.last_request.content)

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.popleft() if self._responses else self._default
        if isinstance(response, Exception):
            raise response
        return response


class InMemorySource:
    """本地缓存的内存替身：清单、任务快照、同步状态。

    生产实现是 t08 的 ``Store``；在它落地之前，引擎与 TUI 的测试都用这个。
    引用一个不存在的清单名时会顺手建出这条清单，这样测试里加任务不必先建清单。
    """

    def __init__(self) -> None:
        self.state = SyncState()
        """同步状态，测试可以直接摆布（``source.state = SyncState(...)``）。"""

        self._lists: dict[str, ListSnapshot] = {}
        self._tasks: dict[str, TaskSnapshot] = {}
        self._seq = 0

    def add_list(self, name: str, *, id: str | None = None) -> ListSnapshot:
        """加一条清单；``id`` 默认就是名字。"""
        snapshot = ListSnapshot(id=id if id is not None else name, name=name)
        self._lists[snapshot.id] = snapshot
        return snapshot

    def add_task(
        self,
        title: str,
        *,
        list_name: str = "收集箱",
        due: datetime | None = None,
        all_day: bool = False,
        priority: int = 0,
        completed: bool = False,
        id: str | None = None,
        completed_at: datetime | None = None,
    ) -> TaskSnapshot:
        """加一条任务快照；``id`` 默认 ``t1``、``t2``……（按加入顺序）。

        ``completed_at`` 是服务端的完成时刻：已完成区（t12）按它决定谁在窗口里。
        """
        self._seq += 1
        snapshot = TaskSnapshot(
            id=id if id is not None else f"t{self._seq}",
            title=title,
            list_id=list_name,
            due=due,
            all_day=all_day,
            priority=priority,
            completed=completed,
            completed_at=completed_at,
        )
        self._lists.setdefault(snapshot.list_id, ListSnapshot(id=snapshot.list_id, name=list_name))
        self._tasks[snapshot.id] = snapshot
        return snapshot

    def lists(self) -> tuple[ListSnapshot, ...]:
        return tuple(self._lists.values())

    def tasks(self) -> tuple[TaskSnapshot, ...]:
        return tuple(self._tasks.values())

    def sync_state(self) -> SyncState:
        return self.state


class FakeBackend:
    """接缝一的假后端：内存缓存 + 真引擎的读路径，写操作只记录。

    读（``view()`` / ``status()``）委托给真 :class:`~dida.sync.engine.SyncEngine`，
    所以渲染测试跑的是真的分组、排序与逻辑日判定；写（``refresh`` / ``complete`` /
    ``defer``）只把调用记下来，等对应工单落地后由它们决定要不要真的走一遍。

    用法::

        backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
        backend.add_task("写周报", list_name="工作", due=...)
        app = DidaApp(backend)
    """

    def __init__(self, *, clock: Clock, day_end: str = "24:00") -> None:
        self.clock = clock
        self.source = InMemorySource()
        self.refreshes = 0
        """``refresh()`` 被调用的次数。"""

        self.pushes = 0
        """``push_pending()`` 被调用的次数（t21 的周期泵与手动同步）。"""

        self.completed_pulls = 0
        """``refresh_completed()`` 被调用的次数（t21 的 ``r``）。"""

        self.completed: list[str] = []
        """``complete(task_id)`` 收到的任务 id，按调用顺序。"""

        self.deferred: list[str] = []
        """``defer(task_id, days=...)`` 收到的任务 id，按调用顺序。"""

        self.deferred_days: list[int] = []
        """每次顺延前进了几个逻辑日（``g`` 是 1、``G`` 是 7），与 ``deferred`` 一一对应。"""

        self.rescheduled: list[str] = []
        """``reschedule(task_id, due=, all_day=)`` 收到的任务 id，按调用顺序（t14 的改期）。"""

        self.rescheduled_due: list[datetime] = []
        """每次改期改到的截止时刻，与 ``rescheduled`` 一一对应。"""

        self.rescheduled_all_day: list[bool] = []
        """每次改期是不是全天，与 ``rescheduled`` 一一对应。"""

        self.reschedule_error: Exception | None = None
        """摆一个异常进去，``reschedule`` 就抛它（试 TUI 遇到引擎拒绝时的反应）。"""

        self.created: list[str] = []
        """``create(title, ...)`` 收到的标题，按调用顺序（t15 的新建）。"""

        self.created_due: list[datetime | None] = []
        """每次新建写进去的截止时刻，与 ``created`` 一一对应。"""

        self.created_all_day: list[bool] = []
        """每次新建是不是全天，与 ``created`` 一一对应。"""

        self.created_priority: list[int | None] = []
        """每次新建写进去的优先级（API 取值 1/3/5；没写是 ``None``），与 ``created`` 一一对应。"""

        self.created_tags: list[tuple[str, ...]] = []
        """每次新建写进去的标签，与 ``created`` 一一对应。"""

        self.deleted: list[str] = []
        """``delete(task_id)`` 收到的任务 id，按调用顺序（t16 的删除）。"""

        self.delete_error: Exception | None = None
        """摆一个异常进去，``delete`` 就抛它（试 TUI 遇到引擎拒绝时的反应）。"""
        self.cycled: list[str] = []
        """``cycle_priority(task_id)`` 收到的任务 id，按调用顺序（t17 的 ``p``）。"""

        self._subtasks: dict[str, tuple[SubtaskItem, ...]] = {}
        """摆进来的子任务，按任务 id 索引（t20）；:meth:`set_subtasks` 摆，读路径照给。"""

        self.toggled_subtasks: list[tuple[str, str]] = []
        """``toggle_subtask(task_id, subtask_id)`` 收到的调用，按顺序（t20）。"""

        self.subtask_changed_elsewhere: bool = False
        """摆 ``True``，下一次勾选就报「重读发现任务在别处被改过」。"""

        self.subtask_written: bool = True
        """摆 ``False``，下一次勾选就报「服务端已经没有这个子任务了」。"""

        self.subtask_error: Exception | None = None
        """摆一个异常进去，``toggle_subtask`` 就抛它（试 TUI 遇到引擎拒绝时的反应）。"""

        self._engine = SyncEngine(clock=clock, day_end=day_end, source=self.source)

    async def refresh(self) -> RefreshReport:
        """真引擎的 ``refresh()`` 是 async 的（网络等待不阻塞界面），假后端跟着它。

        什么都不写：假后端没有网络也没有库，返回一份空报告就是实话。
        """
        self.refreshes += 1
        return RefreshReport()

    async def push_pending(self) -> int:
        """推一轮待推送改动（t21 的周期泵会调它）。

        假后端没有队列，也没有网络，所以推出去 0 条——但调用本身要记下来，好让「泵真的在泵」
        这件事在接缝一上看得见。
        """
        self.pushes += 1
        return 0

    async def refresh_completed(self) -> CompletedReport:
        """拉一次已完成流（``r`` 的第三件事）。

        同样什么都不写：已完成区的内容由 :meth:`set_sync_state` 与内存缓存摆布，
        这里只记下「拉过几次」。
        """
        self.completed_pulls += 1
        now = self.clock.now()
        return CompletedReport(start=now, end=now)

    def add_list(self, name: str, *, id: str | None = None) -> ListSnapshot:
        return self.source.add_list(name, id=id)

    def add_task(self, title: str, **kwargs: Any) -> TaskSnapshot:
        return self.source.add_task(title, **kwargs)

    def set_sync_state(self, *, last_refresh_at: datetime | None = None, pending_count: int = 0) -> None:
        self.source.state = SyncState(last_refresh_at=last_refresh_at, pending_count=pending_count)

    def view(self) -> TodayView:
        return self._engine.view()

    def status(self) -> SyncStatus:
        return self._engine.status()

    def complete(self, task_id: str) -> None:
        self.completed.append(task_id)

    def defer(self, task_id: str, *, days: int = 1) -> None:
        self.deferred.append(task_id)
        self.deferred_days.append(days)

    def plan(self, text: str) -> ParsedTask:
        """读：委托给真引擎——「现在」、日界、语法都是生产那一份，替身不自己编一套。"""
        return self._engine.plan(text)

    def reschedule(self, task_id: str, *, due: datetime, all_day: bool = False) -> None:
        """写：只记录（与 ``complete`` / ``defer`` 一样，替身不动缓存）。

        摆了 ``reschedule_error`` 就记完这一笔再抛：模拟引擎当场拒绝（#25 的
        ``UnknownTaskError``），好试 TUI 拿到结构化错误时的反应。
        """
        self.rescheduled.append(task_id)
        self.rescheduled_due.append(due)
        self.rescheduled_all_day.append(all_day)
        if self.reschedule_error is not None:
            raise self.reschedule_error

    def create(
        self,
        title: str,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：记下这一笔，**并且真的把它摆进内存缓存**（t15 的新建）。

        与 ``complete`` / ``defer`` 那种「只记录」不一样：新建是凭空多出一条任务，而
        「新建的任务立刻出现在对应分区里」正是工单 #15 的验收标准之一——只记录的话，
        接缝一根本测不到这句话。落点与引擎同一口径：收集箱。
        """
        self.created.append(title)
        self.created_due.append(due)
        self.created_all_day.append(all_day)
        self.created_priority.append(priority)
        self.created_tags.append(tuple(tags))
        return self.source.add_task(
            title,
            list_name=INBOX_NAME,
            due=due,
            all_day=all_day,
            priority=priority or 0,
        ).id

    def delete(self, task_id: str) -> None:
        """写：只记录（t16 的 ``d``；替身不动缓存，与 ``complete`` / ``defer`` 一样）。

        摆了 ``delete_error`` 就记完这一笔再抛：模拟引擎当场拒绝（#25 的
        ``UnknownTaskError``），好试 TUI 拿到结构化错误时的反应。
        """
        self.deleted.append(task_id)
        if self.delete_error is not None:
            raise self.delete_error
    def cycle_priority(self, task_id: str) -> None:
        """写：只记录（与 ``complete`` / ``defer`` 一样，替身不动缓存）。

        「下一档是哪个线上编码」是引擎的判断（``dida.sync.view.next_priority``，0/1/3/5），
        替身不自己再抄一份——抄了就会跟真货说不一样的话。
        """
        self.cycled.append(task_id)

    def set_subtasks(self, task_id: str, *items: SubtaskItem) -> None:
        """摆一条任务的子任务（t20）：右栏渲染与勾选测试的输入。

        ``SubtaskItem`` 是引擎给的成品行（标题、完成状态、截止读法），替身照收不误——
        「怎么从 ``items`` 数组读出这一行」是引擎的判断（``dida.sync.view.subtask_items``），
        替身不自己再抄一份。
        """
        self._subtasks[task_id] = tuple(items)

    def subtasks(self, task_id: str) -> tuple[SubtaskItem, ...]:
        """读：摆进去的那一份（t20）；没摆过就是没有子任务。"""
        return self._subtasks.get(task_id, ())

    async def toggle_subtask(self, task_id: str, subtask_id: str) -> SubtaskWrite:
        """写：记下这一笔，并把摆进去的那一份翻过来（t20）。

        「写前重读、只合并这一次改动」是引擎的判断（``SyncEngine.toggle_subtask``），替身
        不自己再抄一份；它只把结果摆成调用方看得见的样子：右栏要重画，状态栏要说清服务端
        有没有说出别的事。真要断言「重读保护了别处的修改」，走接缝二那份测试。
        """
        self.toggled_subtasks.append((task_id, subtask_id))
        if self.subtask_error is not None:
            raise self.subtask_error
        rows = tuple(
            replace(row, completed=not row.completed) if row.subtask_id == subtask_id else row
            for row in self._subtasks.get(task_id, ())
        )
        self._subtasks[task_id] = rows
        return SubtaskWrite(
            task_id=task_id,
            subtask_id=subtask_id,
            items=rows,
            written=self.subtask_written and any(row.subtask_id == subtask_id for row in rows),
            changed_elsewhere=self.subtask_changed_elsewhere,
        )
