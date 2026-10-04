"""已完成流（t12）：唯一允许时间游标的数据流（ADR-0001）。

接缝：``SyncEngine.refresh_completed()`` 这个公开入口，配上真 ``Store``（t08）与真
``DidaApiClient``（t07）；网络钉在既有的**接缝二**（传输层可注入）上，所以这里断言的是
「引擎发了哪些请求」与「拉完之后视图长什么样」，不碰任何私有方法。

这一条流的整个理由是**手机上完成的任务**：它不在任何清单的未完成任务里，逐清单全量
永远拉不到它，只有按 ``completedTime`` 过滤的窗口能把它带回来。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import MalformedResponseError, ServerRejectionError
from dida.storage.store import Store
from dida.sync.engine import SyncEngine, completed_section
from dida.sync.view import ListSnapshot, TaskSnapshot
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def project(id: str = "work", name: str = "工作", sort_order: int = 1, **extra: object) -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": sort_order, **extra}


def data(project_payload: dict, tasks: list[dict] | None = None) -> dict:
    """一份 ``ProjectData``：``{"project": ..., "tasks": [...]}``。"""
    return {"project": project_payload, "tasks": [] if tasks is None else tasks}


def completed(
    id: str = "t9",
    title: str = "手机上做完的",
    *,
    project_id: str = "work",
    completed_time: str = "2026-03-14T12:05:00+0800",
    **extra: object,
) -> dict:
    """一份 ``POST /open/v1/task/completed`` 那样的任务原文（已完成，``status`` 是 2）。"""
    return {
        "id": id,
        "projectId": project_id,
        "title": title,
        "status": 2,
        "completedTime": completed_time,
        **extra,
    }


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
    completed_window_hours: int = 24,
) -> SyncEngine:
    """接上真存储与真客户端（网络走假传输）。``clock`` 传进来就能摆布「现在」。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(now),
        day_end=day_end,
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
        completed_window_hours=completed_window_hours,
    )


async def test_a_task_completed_on_the_phone_reaches_the_view(store):
    """手机上完成的任务不在未完成清单里，只有按完成时间窗口拉才拿得到。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[project()]))
    transport.enqueue(httpx.Response(200, json=data(project())))  # 该清单没有未完成任务
    transport.enqueue(httpx.Response(200, json=data(project(id="inbox", name="收集箱"))))
    transport.enqueue(httpx.Response(200, json=[completed()]))
    engine = make_engine(store, transport)

    await engine.refresh()
    report = await engine.refresh_completed()

    # 请求形状（接缝二）：POST 到已完成端点，只带窗口两端——不带 projectIds，因为
    # 「哪些清单里完成过」恰恰是本地索引可能还不知道的事。
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/task/completed"
    assert transport.last_json == {
        "startDate": "2026-03-13T12:03:00+0800",
        "endDate": "2026-03-14T12:03:00+0800",
    }
    assert report.written_tasks == 1
    section = engine.view().completed
    assert [(item.title, item.list_name) for item in section.items] == [("手机上做完的", "工作")]
    assert section.count == 1


async def test_the_window_is_sized_by_the_configured_hours(store):
    """窗口大小跟随 ``completed_window_hours``（默认 24 由上一条守着）。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[]))
    engine = make_engine(store, transport, completed_window_hours=6)

    await engine.refresh_completed()

    assert transport.last_json == {
        "startDate": "2026-03-14T06:03:00+0800",
        "endDate": "2026-03-14T12:03:00+0800",
    }


async def test_the_cursor_turns_two_refreshes_into_one_continuous_span(store):
    """游标持久化：第二次从第一次的终点接着拉，不把已经拉过的那一段再拉一遍。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[]))
    transport.enqueue(httpx.Response(200, json=[]))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)

    await engine.refresh_completed()
    first = transport.last_json
    clock.advance(timedelta(hours=1))
    await engine.refresh_completed()

    assert first["endDate"] == "2026-03-14T12:03:00+0800"
    assert transport.last_json["startDate"] == first["endDate"], "同一段被拉了两遍"
    assert transport.last_json["endDate"] == "2026-03-14T13:03:00+0800"
    assert store.stored_sync_state().completed_cursor == at(14, 13, 3).isoformat()


async def test_a_cursor_older_than_the_window_does_not_widen_it(store):
    """放了几天的游标不该把窗口拉成几天：窗口大小永远由配置说了算。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[]))
    store.set_sync_state(completed_cursor=at(9, 12, 3).isoformat())
    engine = make_engine(store, transport)

    await engine.refresh_completed()

    assert transport.last_json["startDate"] == "2026-03-13T12:03:00+0800"


async def test_a_failed_pull_does_not_advance_the_cursor(store):
    """拉失败就把游标留在原地：没拉到的窗口下次还得拉，不能被静默跳过。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=[]))
    transport.enqueue(httpx.Response(500))
    transport.enqueue(httpx.Response(200, json=[]))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)
    await engine.refresh_completed()

    clock.advance(timedelta(hours=1))
    with pytest.raises(ServerRejectionError):
        await engine.refresh_completed()

    assert store.stored_sync_state().completed_cursor == T0.isoformat()
    await engine.refresh_completed()
    assert transport.last_json["startDate"] == "2026-03-14T12:03:00+0800", "失败的那一段被跳过了"


async def test_hitting_the_200_task_cap_does_not_claim_the_whole_span(store):
    """满 200 条时不声称这一窗拿全了：游标退到这批里最新的完成时刻。"""
    crowd = [
        completed(
            id=f"t{index:03d}",
            title=f"第 {index} 条",
            completed_time=f"2026-03-14T10:{index % 60:02d}:00+0800",
        )
        for index in range(200)
    ]
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json=crowd))
    transport.enqueue(httpx.Response(200, json=[]))
    engine = make_engine(store, transport)

    report = await engine.refresh_completed()
    await engine.refresh_completed()

    assert report.truncated is True
    assert report.written_tasks == 200
    assert transport.last_json["startDate"] == "2026-03-14T10:59:00+0800"


def snapshot(
    id: str,
    title: str,
    *,
    completed_at: datetime | None,
    completed: bool = True,
    list_id: str = "work",
) -> TaskSnapshot:
    """一条缓存里的任务快照。"""
    return TaskSnapshot(
        id=id, title=title, list_id=list_id, completed=completed, completed_at=completed_at
    )


def test_the_section_keeps_the_window_and_puts_the_newest_first():
    """纯函数（无接缝，直接测）：窗口过滤、最近的在前、读法用同一套 format_due。"""
    section = completed_section(
        [
            snapshot("t1", "一小时前做完的", completed_at=at(14, 11, 3)),
            snapshot("t2", "三十小时前做完的", completed_at=at(13, 6, 3)),
            snapshot("t3", "刚做完的", completed_at=at(14, 12, 0)),
            snapshot("t4", "还没做完的", completed_at=at(14, 12, 1), completed=False),
        ],
        [ListSnapshot(id="work", name="工作")],
        now=T0,
        day_end="24:00",
        window_hours=24,
    )

    assert [item.completed_text for item in section.items] == ["今天 12:00", "今天 11:03"]
    assert [(item.task_id, item.title, item.list_name) for item in section.items] == [
        ("t3", "刚做完的", "工作"),
        ("t1", "一小时前做完的", "工作"),
    ]
    assert section.count == 2


def test_a_locally_completed_task_waits_for_the_server_to_say_when():
    """本地刚按了完成的任务还没有完成时刻：它不进已完成区，等已完成流把它带回来。

    这条是**刻意的**：窗口的判据只有服务端的 ``completedTime``（ADR-0001 里唯一能被
    服务端过滤的变化时间戳），本地时间是自己编的。
    """
    section = completed_section(
        [snapshot("t1", "刚按了完成的", completed_at=None)],
        [],
        now=T0,
        day_end="24:00",
        window_hours=24,
    )

    assert section.items == ()
    assert section.count == 0


class StubCompletedReader:
    """只实现已完成流那一次调用的替身。

    走假传输时形状守卫会在 t07 的客户端里先触发（那一层有自己的测试）；这里要守的是
    **引擎自己的契约**：响应形状不对必须结构化报错，而不是漏一个裸 ``TypeError`` 到 UI。
    """

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def list_completed(
        self,
        *,
        project_ids=None,
        start_date=None,
        end_date=None,
    ):
        return self.payload


async def test_a_completed_payload_that_is_not_a_task_array_is_a_structured_error(store):
    """``{}`` 被当成「这个窗口没人完成任务」，用户看到的是「我的已完成不见了」且不报错。"""
    engine = SyncEngine(clock=ManualClock(T0), source=store, client=StubCompletedReader({"tasks": []}))

    with pytest.raises(MalformedResponseError):
        await engine.refresh_completed()

    assert store.stored_sync_state().completed_cursor is None, "失败也把游标推走了"
