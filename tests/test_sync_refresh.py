"""同步引擎的读路径（t09）：全量刷新 → 本地 diff → 只写变化（ADR-0001）。

接缝：``SyncEngine.refresh()`` 这个公开入口，配上真的 ``Store``（t08）与真的
``DidaApiClient``（t07）。网络钉在既有的**接缝二**（传输层可注入）上，所以这里断言的是
「引擎发了哪些请求」与「刷新之后本地库与视图长什么样」，不碰任何私有方法。

头条那条：同一份数据拉第二次，本地库零写入——那是「刷新」与「刷新且屏幕不闪」的区别。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import MalformedResponseError, NetworkError, ServerRejectionError
from dida.storage.store import ChangeKind, RefreshReport, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport, InMemorySource, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def project(id: str = "work", name: str = "工作", sort_order: int = 1, **extra: object) -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": sort_order, **extra}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def inbox(name: str = "收集箱") -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return project(id="inbox", name=name, sort_order=0)


def data(project_payload: dict, tasks: list[dict] | None = None) -> dict:
    """一份 ``ProjectData``：``{"project": ..., "tasks": [...]}``。"""
    return {"project": project_payload, "tasks": [] if tasks is None else tasks}


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def make_engine(
    store,
    transport,
    *,
    now: datetime = T0,
    day_end: str = "24:00",
    clock: ManualClock | None = None,
) -> SyncEngine:
    """接上真存储与真客户端（网络走假传输）。``clock`` 传进来就能摆布「现在」。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(now),
        day_end=day_end,
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


def serve(transport: FakeTransport, *, index: list[dict], data: list[dict]) -> None:
    """排好一轮刷新的响应：先清单索引，再逐个清单的 data（顺序与引擎取数顺序一致）。"""
    transport.enqueue(httpx.Response(200, json=index))
    for payload in data:
        transport.enqueue(httpx.Response(200, json=payload))


def urls(transport: FakeTransport) -> list[str]:
    """这一轮发出去的全部 URL。"""
    return [str(request.url) for request in transport.requests]


async def test_refresh_fetches_every_list_and_writes_the_first_payload(store):
    transport = FakeTransport()
    serve(
        transport,
        index=[inbox(), project()],
        data=[data(inbox()), data(project(), [task()])],
    )
    engine = make_engine(store, transport)

    report = await engine.refresh()

    assert urls(transport) == [
        "https://api.dida365.com/open/v1/project",
        "https://api.dida365.com/open/v1/project/inbox/data",
        "https://api.dida365.com/open/v1/project/work/data",
    ]
    assert (report.written_lists, report.written_tasks) == (2, 1)
    assert [(item.name, item.unfinished) for item in engine.view().lists] == [
        ("收集箱", 0),
        ("工作", 1),
    ]


async def test_the_second_identical_refresh_writes_nothing(store):
    """头条：同一份数据拉第二次，本地库零写入——「刷新」与「刷新且屏幕不闪」的区别。

    证明用的是 ``apply_refresh`` 自己给的 ``RefreshReport``（t08 的口径），不是去翻
    数据库内部：报告整份为空，就说明清单、任务、覆盖、豁免四样都没动。
    """
    transport = FakeTransport()
    index = [inbox(), project()]
    payloads = [
        data(inbox()),
        data(
            project(),
            [
                task(id="t1", dueDate="2026-03-14T18:00:00+0800", priority=5),
                task(id="t2", title="买牛奶"),
            ],
        ),
    ]
    serve(transport, index=index, data=payloads)
    serve(transport, index=index, data=payloads)
    engine = make_engine(store, transport)

    first = await engine.refresh()
    cached = (store.lists(), store.tasks())
    second = await engine.refresh()

    assert (first.written_lists, first.written_tasks) == (2, 2)
    assert second == RefreshReport()
    assert (store.lists(), store.tasks()) == cached
    assert len(transport.requests) == 6, "第二次也真的重新拉了全量，不是空转"


async def test_refresh_never_uses_a_date_window_for_unfinished_tasks(store):
    """ADR-0001：未完成任务的唯一可靠来源是逐清单 ``GET .../data``。

    ``task/undone``、``task/filter`` 这类日期窗口会**静默**漏掉「日期在很久以后、但刚被
    改过」的任务；漏了不报错，只会在某天表现为「我的任务不见了」。所以这里钉死请求集合：
    只有列清单 + 每个清单恰好一次 data，没有别的路径，也没有任何查询串。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[inbox(), project()],
        data=[data(inbox()), data(project(), [task()])],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    assert [(request.method, str(request.url)) for request in transport.requests] == [
        ("GET", "https://api.dida365.com/open/v1/project"),
        ("GET", "https://api.dida365.com/open/v1/project/inbox/data"),
        ("GET", "https://api.dida365.com/open/v1/project/work/data"),
    ]


async def test_a_list_with_no_unfinished_tasks_is_not_an_error(store):
    """``tasks: []`` 是「这个清单没有未完成任务」，不是失败、也不是「数据没回来」。"""
    transport = FakeTransport()
    serve(
        transport,
        index=[inbox(), project()],
        data=[data(inbox()), data(project(), [])],
    )
    engine = make_engine(store, transport)

    report = await engine.refresh()

    assert (report.written_lists, report.written_tasks) == (2, 0)
    assert store.tasks() == ()
    assert engine.view().groups == ()


async def test_an_empty_project_index_is_not_an_error(store):
    """清单索引回了个空数组：照样是一次成功的刷新，不是异常，也不写脏数据。"""
    transport = FakeTransport()
    serve(
        transport,
        index=[],
        data=[data(inbox())],
    )
    engine = make_engine(store, transport)

    report = await engine.refresh()

    assert report.written_tasks == 0
    assert store.tasks() == ()


async def test_a_payload_of_the_wrong_shape_is_a_structured_error(store):
    """2xx 但字段形状不对（``tasks`` 不是数组）：结构化错误，不是裸 ``TypeError``。

    取数全部成功之后才落库，所以这一次报错之后本地库一动不动。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[project()]))
    transport.enqueue(httpx.Response(200, json={"project": project(), "tasks": {"t1": "写周报"}}))
    engine = make_engine(store, transport)

    with pytest.raises(MalformedResponseError):
        await engine.refresh()

    assert store.lists() == ()
    assert store.tasks() == ()


async def test_a_non_object_project_payload_is_translated_by_the_client(store):
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[project()]))
    transport.enqueue(httpx.Response(200, json=[]))
    engine = make_engine(store, transport)

    with pytest.raises(MalformedResponseError):
        await engine.refresh()


async def test_only_the_task_that_changed_is_written(store):
    """只写变化：没变的行不动，变了的行连同「服务端盖掉了什么」一起报出来（ADR-0002）。"""
    transport = FakeTransport()
    index = [inbox(), project()]
    serve(
        transport,
        index=index,
        data=[data(inbox()), data(project(), [task(id="t1"), task(id="t2", title="买牛奶")])],
    )
    serve(
        transport,
        index=index,
        data=[
            data(inbox()),
            data(
                project(),
                [task(id="t1"), task(id="t2", title="买牛奶（改了）"), task(id="t3", title="交报告")],
            ),
        ],
    )
    engine = make_engine(store, transport)

    await engine.refresh()
    report = await engine.refresh()

    assert report.written_tasks == 2, "t1 没变、t2 改了、t3 是新的"
    assert report.written_lists == 0
    assert [(item.task_id, item.field, item.local, item.server) for item in report.overwritten] == [
        ("t2", "title", "买牛奶", "买牛奶（改了）")
    ]


async def test_a_failed_list_fetch_writes_nothing(store):
    """取数中途失败：本地库一动不动——半份刷新比旧数据更难查（t08 的整事务口径）。"""
    transport = FakeTransport()
    index = [inbox(), project()]
    serve(transport, index=index, data=[data(inbox()), data(project(), [task()])])
    engine = make_engine(store, transport)
    await engine.refresh()
    cached = (store.lists(), store.tasks())

    serve(transport, index=index, data=[data(inbox())])
    transport.enqueue(httpx.Response(500, json={"error": "boom"}))

    with pytest.raises(ServerRejectionError):
        await engine.refresh()

    assert (store.lists(), store.tasks()) == cached
    assert engine.status().last_refresh_at == T0, "失败的那次不算刷新过，不能把时间往前挪"


async def test_refresh_records_the_last_refresh_time_and_the_logical_day(store):
    """刷新成功才记「上次刷新时间」与「上次算出的逻辑日」（spec 的同步状态三件套之二）。"""
    transport = FakeTransport()
    serve(transport, index=[inbox()], data=[data(inbox())])
    engine = make_engine(store, transport, now=at(15, 2, 0), day_end="04:00")

    await engine.refresh()

    status = engine.status()
    assert status.last_refresh_at == at(15, 2, 0)
    assert status.logical_day == date(2026, 3, 14), "凌晨两点、日界 04:00：还算前一天"


async def test_refresh_keeps_the_completed_stream_cursor(store):
    """``set_sync_state(None)`` 是清空：刷新要把 t12 的游标原样带回去，不能顺手抹掉。"""
    store.set_sync_state(completed_cursor="2026-03-14T09:00:00+0800")
    transport = FakeTransport()
    serve(transport, index=[inbox()], data=[data(inbox())])
    engine = make_engine(store, transport)

    await engine.refresh()

    state = store.stored_sync_state()
    assert state.completed_cursor == "2026-03-14T09:00:00+0800"
    assert state.last_refresh_at == T0
    assert state.logical_day == date(2026, 3, 14)


async def test_a_pending_change_survives_a_full_refresh(store):
    """ADR-0002：待推送改动豁免于服务端权威。

    没有这条豁免，一次推送失败 + 一次全量刷新就会把用户刚做的操作悄悄撤销掉。
    用户看得见的那一份（视图里的标题）必须是本地那份。
    """
    transport = FakeTransport()
    index = [inbox(), project()]
    payload = [data(inbox()), data(project(), [task(title="写周报")])]
    serve(transport, index=index, data=payload)
    engine = make_engine(store, transport)
    await engine.refresh()

    store.enqueue(
        task_id="t1", kind=ChangeKind.UPDATE, payload={"title": "写周报（我改的）"}, now=T0
    )

    serve(transport, index=index, data=payload)
    report = await engine.refresh()

    assert [item.title for group in engine.view().groups for item in group.items] == ["写周报（我改的）"]
    assert store.task_payload("t1")["title"] == "写周报（我改的）"
    assert [(item.task_id, item.field, item.local, item.server) for item in report.suppressed] == [
        ("t1", "title", "写周报（我改的）", "写周报")
    ]


async def test_a_failed_push_survives_a_full_refresh_and_then_succeeds(store):
    """写路径与刷新路径的闭环（t10 + ADR-0002）：一次推失败 + 一次全量刷新之后，
    队列、本地值、状态栏那个数**一样都不能少**；钟走到点，重试成功才归零。

    t08 的豁免是逐字段的，这里把它变成用户看得见的行为：视图里还是本地那份，
    覆盖（``suppressed``）在报告里留痕——服务端权威被挡回去了，这件事不静默。
    """
    clock = ManualClock(T0)
    transport = FakeTransport()
    index = [inbox(), project()]
    payload = [
        data(inbox()),
        data(project(), [task(title="写周报", dueDate="2026-03-14T18:00:00+0800")]),
    ]
    serve(transport, index=index, data=payload)
    engine = make_engine(store, transport, clock=clock)
    await engine.refresh()

    transport.enqueue(NetworkError("连不上"))
    engine.write("t1", changes={"title": "写周报（我改的）"})
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1

    serve(transport, index=index, data=payload)  # 服务端还是旧值
    report = await engine.refresh()

    assert engine.status().pending_count == 1, "刷新不许把还没推成功的改动弄丢"
    assert [item.title for group in engine.view().groups for item in group.items] == ["写周报（我改的）"]
    assert store.task_payload("t1")["title"] == "写周报（我改的）"
    assert [(item.task_id, item.field, item.local, item.server) for item in report.suppressed] == [
        ("t1", "title", "写周报（我改的）", "写周报")
    ]

    transport.enqueue(httpx.Response(200, json={}))
    clock.advance(timedelta(seconds=2))

    assert await engine.push_pending() == 1

    assert transport.last_json["title"] == "写周报（我改的）"
    assert store.pending() == ()
    assert engine.status().pending_count == 0, "推成功了才回到 0"


async def test_the_inbox_is_fetched_even_when_the_project_index_omits_it(store):
    """收集箱是 API 里的字面量 ``"inbox"``，也是默认清单：索引里没有它也得拉。

    漏掉它的表现是「随手记的任务永远不出现」，而且不报错——和日期窗口漏任务是同一类安静。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[project()],
        data=[
            data(project(), []),
            data(inbox(), [task(id="t9", project_id="inbox", title="随手记")]),
        ],
    )
    engine = make_engine(store, transport)

    report = await engine.refresh()

    assert urls(transport) == [
        "https://api.dida365.com/open/v1/project",
        "https://api.dida365.com/open/v1/project/work/data",
        "https://api.dida365.com/open/v1/project/inbox/data",
    ]
    assert (report.written_lists, report.written_tasks) == (2, 1)
    assert [(item.name, item.unfinished) for item in engine.view().lists] == [
        ("收集箱", 1),
        ("工作", 0),
    ]


async def test_refresh_without_a_wired_cache_or_client_fails_loudly(store):
    """没接线的引擎不许假装刷过了：缺本地库、缺客户端都大声报错。"""
    with pytest.raises(RuntimeError, match="本地存储"):
        await SyncEngine(clock=ManualClock(T0)).refresh()

    with pytest.raises(RuntimeError, match="本地存储"):
        await SyncEngine(clock=ManualClock(T0), source=InMemorySource()).refresh()

    with pytest.raises(RuntimeError, match="API 客户端"):
        await SyncEngine(clock=ManualClock(T0), source=store).refresh()


async def test_a_task_without_a_project_id_is_attributed_to_its_list(store):
    """逐清单取回来的任务，归属清单只有引擎知道：服务端漏了 ``projectId`` 时补上。

    不补的话 t08 会把它们全算进收集箱——左栏徽标、清单名、清单过滤全错，而且不报错。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[inbox(), project()],
        data=[data(inbox()), data(project(), [{"id": "t1", "title": "写周报", "status": 0}])],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    assert [item.list_name for group in engine.view().groups for item in group.items] == ["工作"]
    assert store.task_payload("t1")["projectId"] == "work"
