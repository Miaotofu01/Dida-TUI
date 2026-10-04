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
from datetime import datetime, timedelta
from typing import Any

import httpx

from dida.clock import Clock
from dida.storage.store import RefreshReport
from dida.sync.engine import SyncEngine, SyncStatus
from dida.sync.view import ListSnapshot, SyncState, TaskSnapshot, TodayView


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

        self.completed: list[str] = []
        """``complete(task_id)`` 收到的任务 id，按调用顺序。"""

        self.deferred: list[str] = []
        """``defer(task_id)`` 收到的任务 id，按调用顺序。"""

        self._engine = SyncEngine(clock=clock, day_end=day_end, source=self.source)

    async def refresh(self) -> RefreshReport:
        """真引擎的 ``refresh()`` 是 async 的（网络等待不阻塞界面），假后端跟着它。

        什么都不写：假后端没有网络也没有库，返回一份空报告就是实话。
        """
        self.refreshes += 1
        return RefreshReport()

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

    def defer(self, task_id: str) -> None:
        self.deferred.append(task_id)
