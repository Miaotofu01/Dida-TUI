"""顺延（t13）：``g`` 到下一个逻辑日、``G`` 到下周同一天。

接缝是引擎的公开入口 ``defer()``（写）与 ``tasks_in()`` / ``task_payload()``（看），接真的
``Store``（t08）与真的 ``DidaApiClient``（t07，网络钉在接缝二上），「现在」来自注入的钟。

钉死的规矩（工单 #13 + GLOSSARY 的「顺延」）：

- 落点是**逻辑日**：边界配成 ``04:00`` 时，凌晨两点顺延落到用户作息里的「明天」，
  而不是机器日历日 +1；
- 只改截止时间，任务的其它字段一个都不动；
- 顺延立即推送，失败进重试队列（与 t10 的写路径同一条口径）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.logical_day import logical_day
from dida.testing import FakeTransport, InMemorySource, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。03-14 是周六。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(15, 2, 0)
"""默认的「现在」：凌晨两点。``day_end = "04:00"`` 时它还是前一个逻辑日。"""


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
    """直接把一份缓存摆进库里（顺延的测试不必先跑一遍刷新）。"""
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


def test_deferring_at_2am_lands_on_the_next_logical_day(store):
    """验收标准 #3：边界 04:00、凌晨两点顺延 → 落到用户的「明天」，且醒来时读作今日。

    任务截止是 03-14 23:00（逻辑日 03-14，凌晨两点的「今天」）。顺延要把它挪到**下一个
    逻辑日** 03-15 23:00：机器日历日 +1 会给出 03-16，那条任务在用户的 03-15 就变成
    「明天」而不在「今日」区里了——这一条同时钉住落点与「不逾期」。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T23:00:00+0800"))
    clock = ManualClock(at(15, 2, 0))  # 逻辑日仍是 03-14
    engine = make_engine(store, clock=clock)

    engine.defer("t1")

    assert due_of(store) == "2026-03-15T23:00:00+0800"
    assert [change.payload for change in store.pending()] == [
        {"dueDate": "2026-03-15T23:00:00+0800"}
    ]

    clock.set(at(15, 9, 0))  # 用户醒了：逻辑日是 03-15
    items = engine.tasks_in("work").items
    assert [(item.title, item.overdue, item.due_text) for item in items] == [
        ("写周报", False, "今天 23:00")
    ], "顺延过的任务不许判成逾期，而且要读作今天"


def test_a_task_due_in_the_small_hours_lands_inside_the_next_logical_day(store):
    """02:00 的截止按逻辑日属于**前一天**，所以顺延必须把它放进目标逻辑日的区间里。

    照搬墙钟时刻是错的：03-15 02:00 属于逻辑日 03-14，任务会原地留在用户当前的「今日」，
    等于没顺延。夜猫子的「明天凌晨两点」是 03-16 02:00——它在下一个逻辑日
    ``[03-15 04:00, 03-16 04:00)`` 里。
    """
    seed(store, task(id="t1", title="夜跑", dueDate="2026-03-14T02:00:00+0800"))
    clock = ManualClock(at(15, 2, 0))  # 逻辑日 03-14
    engine = make_engine(store, clock=clock)

    engine.defer("t1")

    assert due_of(store) == "2026-03-16T02:00:00+0800"
    landing = datetime.fromisoformat(str(due_of(store)))
    assert logical_day(landing, "04:00").start == logical_day(clock.now(), "04:00").end, (
        "落点必须真的在下一个逻辑日里，而不是用户当前的那个"
    )

    clock.set(at(15, 9, 0))  # 逻辑日 03-15
    items = engine.tasks_in("work").items
    assert [(item.title, item.due_text) for item in items] == [("夜跑", "今天 02:00")]


def test_deferring_a_week_lands_on_the_same_weekday_next_week(store):
    """验收标准 #2：``G`` 到下周同一天——03-14（周六）→ 03-21（还是周六）。

    起步同样是**逻辑日**：凌晨两点的「下周同一天」是用户作息的 03-21，而不是机器日历
    日 +7 的 03-22。``G`` 与 ``g`` 只差前进几个逻辑日。
    """
    seed(store, task(id="t1", title="周会材料", dueDate="2026-03-14T23:00:00+0800"))
    clock = ManualClock(at(15, 2, 0))  # 逻辑日 03-14
    engine = make_engine(store, clock=clock)

    engine.defer("t1", days=7)

    assert date(2026, 3, 14).weekday() == date(2026, 3, 21).weekday() == 5, "周六 → 周六"
    assert due_of(store) == "2026-03-21T23:00:00+0800"


def test_deferring_an_all_day_task_moves_its_date_marker(store):
    """全天任务的截止是**日期标记**（服务端写当天 00:00），顺延只换日期、不套时刻那套偏移。

    全天任务的归属看 ``due.date()``（t06/t14 的约定），所以 03-14 顺延到 03-15 的全天；
    拿区间去推会把它挪成 03-16，用户会以为「交房租」晚了一天。
    """
    seed(store, task(id="t1", title="交房租", dueDate="2026-03-14T00:00:00+0800", isAllDay=True))
    clock = ManualClock(at(15, 2, 0))  # 逻辑日 03-14
    engine = make_engine(store, clock=clock)

    engine.defer("t1")

    assert due_of(store) == "2026-03-15T00:00:00+0800"
    assert store.task_payload("t1")["isAllDay"] is True, "全天标记本身不动"

    clock.set(at(15, 9, 0))  # 逻辑日 03-15：这条全天任务读作今天
    items = engine.tasks_in("work").items
    assert [(item.title, item.due_text) for item in items] == [("交房租", "今天")]


async def test_deferring_pushes_the_new_due_and_leaves_every_other_field_alone(store):
    """验收标准 #1 与 #4：只改截止时间，而且立即推送。

    推送的是「服务端权威的底稿 + 本次改动」（t10 的写路径）：``dueDate`` 换了，其余字段
    一个都不许变——尤其 t07 不认识的 ``kind``，丢了手机端的设置就没了（CONTEXT 的 trap 4）。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={}))
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            priority=5,
            tags=["工作"],
            kind="TEXT",
            dueDate="2026-03-14T23:00:00+0800",
        ),
    )
    engine = make_engine(store, clock=ManualClock(at(15, 2, 0)), transport=transport)
    before = dict(store.task_payload("t1"))

    engine.defer("t1")

    assert transport.requests == [], "写返回的那一刻还没碰网络：顺延不等网络（ADR-0002）"
    assert engine.status().pending_count == 1, "状态栏那个数立刻变了"
    after = dict(store.task_payload("t1"))
    assert {k: v for k, v in after.items() if k != "dueDate"} == {
        k: v for k, v in before.items() if k != "dueDate"
    }, "顺延只改截止时间，其它字段一个都不动"

    await engine.wait_for_pushes()

    body = transport.last_json
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/task/t1"
    assert body["dueDate"] == "2026-03-15T23:00:00+0800"
    assert body["title"] == "写周报"
    assert body["priority"] == 5
    assert body["tags"] == ["工作"]
    assert body["kind"] == "TEXT", "不认识的服务端字段必须原样回写"
    assert engine.status().pending_count == 0, "推成功就出队"


async def test_a_failed_defer_push_stays_in_the_retry_queue(store):
    """验收标准 #4：推不动就进重试队列，本地那份顺延照旧生效（ADR-0002 的豁免）。

    一次网络抖动不许撤销用户刚按下的顺延，也不许让它在下次刷新时被服务端盖回去。
    """
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T23:00:00+0800"))
    engine = make_engine(store, clock=ManualClock(at(15, 2, 0)), transport=transport)

    engine.defer("t1")
    await engine.wait_for_pushes()

    assert due_of(store) == "2026-03-15T23:00:00+0800", "推失败不许撤销顺延"
    queued = store.pending()
    assert [(change.task_id, change.kind, change.attempts) for change in queued] == [
        ("t1", ChangeKind.UPDATE, 1)
    ]
    assert engine.status().pending_count == 1
    assert await engine.push_pending() == 0, "还没到退避的点，一次都不许再发"


def test_a_task_without_a_due_date_is_left_alone(store):
    """没有截止时间就没有可挪的东西：顺延不凭空给任务长出一个日期。

    给无日期任务补日期是 t15「新建」的事；顺延只动**已有**的截止时间，其它字段一律不碰。
    """
    seed(store, task(id="t1", title="随手记"))
    engine = make_engine(store, clock=ManualClock(at(15, 2, 0)))

    engine.defer("t1")

    assert store.pending() == (), "没有改动就不该入队"
    assert store.task_payload("t1").get("dueDate") is None
    assert engine.status().pending_count == 0


def test_deferring_a_task_the_cache_does_not_know_does_nothing(store):
    """本地没有这条任务就什么都不写：绝不凭空造一条快照出来。"""
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T23:00:00+0800"))
    engine = make_engine(store, clock=ManualClock(at(15, 2, 0)))

    engine.defer("查无此任务")

    assert store.pending() == ()
    assert store.task_payload("查无此任务") is None


def test_deferring_without_a_local_store_fails_loudly():
    """只读的替身存不下待推送改动：大声报错，不假装顺延成功了。"""
    engine = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00", source=InMemorySource())

    with pytest.raises(RuntimeError, match="本地存储"):
        engine.defer("t1")


async def test_a_queued_defer_survives_a_full_refresh(store):
    """顺延还没推成功时，一次全量刷新不许把服务端的旧截止时间盖回来（ADR-0002 的豁免）。

    服务端那一份还是旧的（那次推送失败了），但 ``dueDate`` 是待推送改动碰过的字段，本地赢；
    被挡回去这件事同时进 ``RefreshReport.suppressed``——ADR-0002 要求这种覆盖能被看见。
    """
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T23:00:00+0800"))
    engine = make_engine(store, clock=ManualClock(at(15, 2, 0)), transport=transport)
    engine.defer("t1")
    await engine.wait_for_pushes()

    transport.enqueue(httpx.Response(200, json=[inbox(), project()]))
    transport.enqueue(httpx.Response(200, json={"project": inbox(), "tasks": []}))
    transport.enqueue(
        httpx.Response(
            200,
            json={
                "project": project(),
                "tasks": [task(id="t1", title="写周报", dueDate="2026-03-14T23:00:00+0800")],
            },
        )
    )

    report = await engine.refresh()

    assert due_of(store) == "2026-03-15T23:00:00+0800", "还没推成功的顺延不许被刷新撤销"
    assert [(item.field, item.local, item.server) for item in report.suppressed] == [
        ("dueDate", "2026-03-15T23:00:00+0800", "2026-03-14T23:00:00+0800")
    ]
