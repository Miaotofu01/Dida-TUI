"""改期（t14）：``e`` 输入框 + 共用的日期解析器 + 只动截止时间的写路径。

两个接缝，与仓库里其它工单一样：

- **引擎的公开入口**（``plan()`` / ``reschedule()``）接真的 ``Store`` 与真的
  ``DidaApiClient``，网络钉在**接缝二**（``FakeTransport``）上——断言的是「引擎发了什么
  请求、本地变成了什么样」。
- **接缝一**：``DidaApp`` + ``FakeBackend``，用 Textual 的 ``Pilot`` 按键驱动——断言的是
  「屏幕文本」与「假后端收到的调用」。

钉死的规矩（工单 #14 + ``date_parser`` 的两条约定）：

- 只改 ``dueDate`` 与 ``isAllDay``，任务的其它字段一个不动；
- 全天任务的截止是**日期标记**：照 ``due.date()`` 写回，不按逻辑日区间挪动它
  （``day_end = "04:00"`` 时把 00:00 挪一天，会让一个「今天」的全天任务掉出今日区）；
- 只写时刻且已过去才顺延到下一个逻辑日；写明了日期（哪怕「今天」）就不顺延；
- ``diagnostics`` 非空一律提示、不提交——不按 code 名单挑着报；
- 改期走 ``write()``：本地当场生效、立即推送、推不动进重试队列。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。03-14 是周六。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(15, 2, 0)
"""默认的「现在」：凌晨两点。``day_end = "04:00"`` 时它还是前一个逻辑日（03-14）。"""


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def project(id: str = "work", name: str = "工作") -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 1}


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（改期的测试不必先跑一遍刷新）。"""
    store.apply_refresh(lists=[inbox(), project()], tasks=list(tasks))


def make_engine(
    store: Store,
    *,
    clock: ManualClock | None = None,
    transport: FakeTransport | None = None,
    day_end: str = "04:00",
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端。日界默认就是那个会咬人的 ``04:00``。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def due_of(store: Store, task_id: str = "t1") -> object:
    """本地那份快照里的截止时间（用户与推送看到的是同一份）。"""
    payload = store.task_payload(task_id)
    assert payload is not None
    return payload.get("dueDate")


async def test_reschedule_changes_only_the_due_date_and_pushes_at_once(store):
    """验收标准 #2 + #5：只改截止时间，并且走 t10 那条「立即推送」的写路径。"""
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            dueDate="2026-03-10T18:00:00+0800",
            priority=3,
            tags=["工作"],
        ),
    )
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport=transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task/t1")
    body = transport.last_json
    assert body["dueDate"] == "2026-03-20T14:00:00+0800"
    assert body["isAllDay"] is False
    # 服务端权威的那份底稿照旧合并回写：改期不碰标题、优先级、标签
    assert body["title"] == "写周报"
    assert body["priority"] == 3
    assert body["tags"] == ["工作"]
    assert due_of(store) == "2026-03-20T14:00:00+0800"
    assert store.pending_count() == 0, "推成功了就该出队"


async def test_an_all_day_due_is_written_verbatim_as_a_date_marker(store):
    """契约一：``all_day=True`` 时 ``due`` 是**日期标记**，照写，不按逻辑日区间挪。

    边界 04:00、凌晨两点（逻辑日仍是 03-14）：解析器给的「今天」是 03-14 00:00。
    把它「挪进当前逻辑日」的那种算法会写成 03-15 00:00 —— 那条任务就变成**未来**的，
    既不在逾期区也不在今日区，等于从这一屏上消失。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-10T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport=transport, clock=ManualClock(at(15, 2, 0)))

    engine.reschedule("t1", due=at(14, 0, 0), all_day=True)
    await engine.wait_for_pushes()

    assert transport.last_json["dueDate"] == "2026-03-14T00:00:00+0800"
    assert transport.last_json["isAllDay"] is True
    items = [item for group in engine.view().groups for item in group.items]
    assert [(item.title, item.due_text, item.all_day) for item in items] == [
        ("还信用卡", "今天", True)
    ], "全天任务的「今天」要落在今日区，且不许读成「今天 00:00」"


async def test_rescheduling_an_all_day_task_to_a_time_clears_the_all_day_flag(store):
    """全天 → 具体时刻：``isAllDay`` 必须显式写回 ``false``，否则服务端照旧当全天。

    只发 ``dueDate`` 的话时刻会被 ``isAllDay = true`` 盖住（t22 的实测问题：省略的字段
    是不是被保留由服务端定），用户写的「14:00」就静默没了。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-14T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport=transport, clock=ManualClock(at(15, 2, 0)))

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_json["isAllDay"] is False
    payload = store.task_payload("t1")
    assert payload is not None and payload["isAllDay"] is False
