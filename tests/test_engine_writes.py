"""引擎写入口的预置动作（t11/t14/t15/t16/t17）：本地怎么变、推什么、推不动怎么办。

这个文件是**搬过来的**（工单 #32 的「先搬后删」）：这些结论原本钉在四个要按键开屏的测试
文件里（``test_complete.py`` / ``test_delete.py`` / ``test_reschedule.py`` /
``test_quick_add.py``），界面重写会连文件一起删掉。搬过来的是**断言**，一个字都没放松：
接缝仍然只有引擎的公开入口（``complete`` / ``delete`` / ``create`` / ``reschedule`` /
``cycle_priority`` 与 ``push_pending``），接真的 ``Store`` 与真的 ``DidaApiClient``，网络
钉在传输层（接缝二）上——不断言任何私有方法，也不需要起一个 Textual 应用。

日期解析器（``plan()``）是 v2 整体作废的那一块，所以这里的期望值全部**由测试直接给出**
（``due=at(14, 0, 0), all_day=True``），不再绕一圈 ``plan("今天")``。新建那一侧 v2 只填
标题（#39），落点规则改的就是 ``create()`` 那一行——但「本地先有、推什么、临时 id 怎么
落到服务端给的 id 上」这三条结论与界面无关，必须留着。

钉死的规矩（ADR-0002 + api-contracts.md）：

- 写先在本地生效并**立即返回**，网络结果不是它的前置条件；
- 推失败留在队列里按注入的钟退避重试，本地那次改动**不许被撤销**；
- 每个动作发出去的请求形状（方法、路径、请求体）是 API 的事实，逐条钉住。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。03-14 是周六；03-15 凌晨两点仍在逻辑日 03-14（day_end=04:00）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)
"""默认的「现在」。"""


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
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId；新建的落点。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def seed(store: Store, *tasks: dict, lists: list[dict] | None = None) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=lists if lists is not None else [inbox(), project()],
        tasks=list(tasks),
    )


def make_engine(
    store: Store,
    transport: FakeTransport | None = None,
    *,
    clock: ManualClock | None = None,
    day_end: str = "24:00",
) -> SyncEngine:
    """接上真存储与真客户端；网络钉在假传输上。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def titles(engine: SyncEngine) -> list[str]:
    """视图里看得见的任务标题（用户真正看到的那一份）。"""
    return [item.title for group in engine.view().groups for item in group.items]


# ---------------------------------------------------------------- 完成（t11）


async def test_completing_enqueues_exactly_one_complete_change_and_pushes_it(store):
    """``complete()`` 是「完成并立即推送」这一个动作：本地当场不再未完成，推送是这个端点。

    期望值来自 ``api-contracts.md``：``POST /open/v1/project/{listId}/task/{taskId}/complete``
    且**没有请求体**（``status`` 不是写字段，它只是引擎补在本地的那一份）。

    队列里那一条断言的是「一次完成只有一条 COMPLETE 改动」——完成是一个方向的写，本地
    不许另外补一条反向的改动出来（ADR-0002 实测推翻了 v1 的「完成不可逆」，但**这条断言
    本身仍然成立**：取消完成是服务端那条 batch 路径的事，不是本地多出一条改动）。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    seed(store, task(id="t1", title="写周报", project_id="work"))
    engine = make_engine(store, transport)

    engine.complete("t1")

    assert titles(engine) == [], "本地当场就不再是未完成"
    assert store.task_payload("t1")["status"] == 2, "Completed 是 2（api-contracts 第 2 条）"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        ("t1", ChangeKind.COMPLETE)
    ], "一次完成只留一条 COMPLETE 改动，本地不补第二条"

    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1/complete"
    assert request.content == b"", "完成端点没有请求体"
    assert len(transport.requests) == 1, "一次完成只发这一个请求"
    assert engine.status().pending_count == 0, "推成功后那个数回到 0"


# ---------------------------------------------------------------- 删除（t16）


async def test_deleting_enqueues_exactly_one_delete_change_and_pushes_the_verb(store):
    """``delete()``：本地当场摘掉、队列里**只有**一条 DELETE 改动、推送是那个动词。

    这是 TUI 与写路径之间的那根线：删除没有第二个方向（没有「撤销删除」的改动类型）。
    期望值来自 ``api-contracts.md``：``DELETE /open/v1/project/{projectId}/task/{taskId}``。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    seed(store, task(id="t1", title="写周报", project_id="work"))
    engine = make_engine(store, transport)

    engine.delete("t1")

    assert store.task_payload("t1") is None, "本地当场摘掉"
    assert titles(engine) == [], "视图里也没有了"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        ("t1", ChangeKind.DELETE)
    ], "队列里正好一条 DELETE，没有别的"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上"

    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "推成功后回到 0"
    assert transport.last_request.method == "DELETE"
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/project/work/task/t1"
    assert len(transport.requests) == 1, "一次删除一个请求"


async def test_a_delete_that_cannot_be_pushed_does_not_come_back_on_the_next_refresh(store):
    """推不动的删除留在队列里；紧接着的一次全量刷新**不许**把这条任务放回来。

    没推成功的删除整条豁免于服务端权威（``_exempt_fields`` 对它返回 ``None``）。没有这条
    豁免，用户按了确认、看着它消失了，下一次刷新它又回来了——而服务端那边其实还留着，
    用户会以为删除失败了。
    """
    seed(store, task(id="t1", title="写周报", project_id="work"))
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    engine = make_engine(store, transport)

    engine.delete("t1")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 1, "推不动，留在队列里等退避"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        ("t1", ChangeKind.DELETE)
    ]

    # 服务端那边这条任务还在（删除没推成功），全量刷新会把它拉回来。
    seed(store, task(id="t1", title="写周报", project_id="work"))

    assert store.task_payload("t1") is None, "删掉的任务不许刷新一下又回来"
    assert titles(engine) == []


# ---------------------------------------------------------------- 新建（t15）


async def test_a_create_lands_locally_first_and_pushes_the_whole_line(store):
    """``create()``：本地当场多一条（临时 id），推送是 ``POST /open/v1/task``。

    断言的是请求形状（接缝二）与本地状态：

    - 体里是交进来的每一样东西（``priority`` 是 **5**，不是 3——API 的编码）；
    - ``status`` 不进请求体：文档说新建不接受它，客户端守卫会当场拒绝；
    - **本地先有**：还没等推送回来，这条任务就已经在本地缓存里了（乐观写）；
    - 推成功之后本地那条临时 id 落到服务端给的 id 上，刷新不会把它看成两条。
    """
    seed(store, lists=[inbox()])
    transport = FakeTransport(json={"id": "srv-1", "projectId": "inbox", "title": "交季度报告"})
    engine = make_engine(store, transport)

    local_id = engine.create(
        "交季度报告",
        due=at(15, 15, 0),
        all_day=False,
        priority=5,
        tags=["工作"],
    )

    local = store.task_payload(local_id)
    assert local is not None, "本地当场就要看得见（网络不是这一屏的前置条件）"
    assert local["title"] == "交季度报告"
    assert local["projectId"] == "inbox", "v1 的新建落在收集箱"

    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task")
    assert transport.last_json == {
        "title": "交季度报告",
        "projectId": "inbox",
        "dueDate": "2026-03-15T15:00:00+0800",
        "isAllDay": False,
        "priority": 5,
        "tags": ["工作"],
    }
    assert "status" not in transport.last_json, "status 不是新建接受的字段（api-contracts 第 5 条）"
    assert store.pending() == (), "推成功了就该出队"
    assert engine.status().pending_count == 0
    assert [item.id for item in store.tasks()] == ["srv-1"], (
        "推成功之后那条临时任务要落到服务端给的 id 上：留着两条，下一次全量刷新就会把"
        "同一条任务画两遍"
    )


async def test_an_all_day_create_writes_the_date_marker_verbatim(store):
    """全天任务的截止是**日期标记**：照 ``due.date()`` 写成那天的 00:00，不按逻辑日区间挪。

    边界 04:00、凌晨两点（逻辑日仍是 03-14）：建一条「今天」的全天任务，请求体里必须是
    ``2026-03-14T00:00:00+0800`` 与 ``isAllDay: true``。把它「挪进当前逻辑日」的那种算法
    会写成 03-15 00:00——那条任务就成了明天的，从今天的屏幕上消失。
    """
    seed(store, lists=[inbox()])
    transport = FakeTransport(
        json={
            "id": "srv-2",
            "projectId": "inbox",
            "title": "还信用卡",
            "dueDate": "2026-03-14T00:00:00+0800",
            "isAllDay": True,
        }
    )
    engine = make_engine(store, transport, clock=ManualClock(at(15, 2, 0)), day_end="04:00")

    engine.create("还信用卡", due=at(14, 0, 0), all_day=True)
    await engine.wait_for_pushes()

    assert transport.last_json["dueDate"] == "2026-03-14T00:00:00+0800"
    assert transport.last_json["isAllDay"] is True
    assert "priority" not in transport.last_json, "没写优先级就不写这个字段，服务端默认就是「无」"
    items = [item for group in engine.view().groups for item in group.items]
    assert [(item.title, item.due_text, item.all_day) for item in items] == [
        ("还信用卡", "今天", True)
    ], "全天任务的「今天」要落在今日区，且不许读成「今天 00:00」"


async def test_a_thin_create_response_does_not_drop_what_the_user_wrote(store):
    """服务端的响应只回了一个 id 时，本地那份不能把用户写的日期弄丢。

    ``_adopt_created`` 的名字就叫「认领」：认领的是**服务端给的 id**，不是拿服务端那份
    整体替换本地那份。响应里没有 ``dueDate`` 就当成「服务端没提」，而不是「服务端说没有」。
    """
    seed(store, lists=[inbox()])
    transport = FakeTransport(json={"id": "srv-3"})
    engine = make_engine(store, transport)

    engine.create("交季度报告", due=at(15, 15, 0), all_day=False)
    await engine.wait_for_pushes()

    payload = store.task_payload("srv-3")
    assert payload is not None
    assert payload["dueDate"] == "2026-03-15T15:00:00+0800"
    assert payload["isAllDay"] is False
    assert payload["projectId"] == "inbox"
    assert [item.id for item in store.tasks()] == ["srv-3"]


async def test_a_failed_create_push_stays_in_the_retry_queue(store):
    """新建立即推送；推不动就留在重试队列里按注入的钟退避重试。

    ADR-0002：本地那条任务照旧在（还挂着临时 id）——一次网络抖动不该让用户刚写下的那条
    任务从屏幕上消失。钟走到点再推成功，才认领服务端给的 id。
    """
    seed(store, lists=[inbox()])
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)

    local_id = engine.create("买牛奶", due=at(15, 0, 0), all_day=True)
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1, "写就是立即推：不等用户再按一次"
    assert engine.status().pending_count == 1
    queued = store.pending()
    assert [change.kind for change in queued] == [ChangeKind.CREATE]
    assert queued[0].task_id == local_id
    assert queued[0].payload["title"] == "买牛奶"
    assert queued[0].next_retry_at == T0 + timedelta(seconds=2), "第一次失败等 2 秒"
    assert store.task_payload(local_id)["title"] == "买牛奶", "推失败不许撤销本地那条"

    clock.advance(timedelta(seconds=2))
    transport.enqueue(httpx.Response(200, json={"id": "srv-9", "projectId": "inbox"}))

    assert await engine.push_pending() == 1, "钟走到点，这一次推成功"
    assert store.pending() == ()
    assert [item.id for item in store.tasks()] == ["srv-9"], "推成功之后才认领服务端的 id"


# ---------------------------------------------------------------- 优先级（t17）


async def test_priority_advances_the_wire_code_locally_and_pushes_it(store):
    """``cycle_priority()`` 走的是 API 的线上编码 ``0 → 1 → 3 → 5``，本地当场生效。

    「当场生效」与「推给服务端」是两件事：本地那份快照在 ``cycle_priority`` 返回时就已经
    是新的（ADR-0002 的乐观写），请求体随后才发出去——两者都钉在这里。期望的四个取值来自
    ``api-contracts.md`` 第 3 条，不是照 ``next_priority`` 再算一遍。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+0800"))
    transport = FakeTransport(json=task(id="t1"))
    engine = make_engine(store, transport)

    for wire in (1, 3, 5, 0):
        engine.cycle_priority("t1")

        assert store.task_payload("t1")["priority"] == wire, "本地当场生效，不等网络"

        await engine.wait_for_pushes()
        assert store.pending() == (), "推成功就该出队"
        assert transport.last_json["priority"] == wire, "请求体里是线上编码"


async def test_a_failed_priority_push_stays_in_the_retry_queue(store):
    """优先级变化立即推送；推不动就留在重试队列里（ADR-0002 的豁免），本地值不撤销。"""
    seed(store, task(id="t1", title="写周报"))
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    engine = make_engine(store, transport)

    engine.cycle_priority("t1")
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1
    queued = store.pending()
    assert [change.kind for change in queued] == [ChangeKind.UPDATE]
    assert queued[0].payload == {"priority": 1}, "本地那次改动照旧生效，不被撤销"
    assert store.task_payload("t1")["priority"] == 1
    assert engine.status().pending_count == 1


# ---------------------------------------------------------------- 改期（t14）


async def test_reschedule_changes_only_the_due_date_and_merges_the_snapshot(store):
    """``reschedule()`` 只改 ``dueDate`` 与 ``isAllDay``，任务的其它字段一个不动。

    服务端权威的那份底稿照旧合并回写：改期不碰标题、优先级、标签。期望值直接写在测试里
    （v2 不再有 ``plan()`` 那一圈）。
    """
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
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task/t1")
    body = transport.last_json
    assert body["dueDate"] == "2026-03-20T14:00:00+0800"
    assert body["isAllDay"] is False
    assert body["title"] == "写周报"
    assert body["priority"] == 3
    assert body["tags"] == ["工作"]
    payload = store.task_payload("t1")
    assert payload is not None and payload["dueDate"] == "2026-03-20T14:00:00+0800"
    # 本地那一份也只有截止时间变了（乐观写与请求体是同一份改动）
    assert (payload["title"], payload["priority"]) == ("写周报", 3)
    assert store.pending_count() == 0, "推成功了就该出队"


async def test_an_all_day_reschedule_is_written_verbatim_as_a_date_marker(store):
    """``all_day=True`` 时 ``due`` 是**日期标记**，照写，不按逻辑日区间挪。

    边界 04:00、凌晨两点（逻辑日仍是 03-14）：「今天」是 03-14 00:00。把它「挪进当前逻辑日」
    的那种算法会写成 03-15 00:00——那条任务就变成**未来**的，既不在逾期区也不在今日区，
    等于从这一屏上消失。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-10T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport, clock=ManualClock(at(15, 2, 0)), day_end="04:00")

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

    只发 ``dueDate`` 的话时刻会被 ``isAllDay = true`` 盖住（省略的字段是不是被保留由服务端
    定），用户写的「14:00」就静默没了。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-14T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport, clock=ManualClock(at(15, 2, 0)), day_end="04:00")

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_json["isAllDay"] is False
    payload = store.task_payload("t1")
    assert payload is not None and payload["isAllDay"] is False


async def test_a_failed_reschedule_push_stays_in_the_retry_queue(store):
    """改期立即推送；推不动就留在重试队列里按注入的钟退避重试。

    ADR-0002：本地那次改期**照旧生效**——一次网络抖动不该撤销用户刚按下的那一下。
    队列里那份 payload 必须正好是 ``{dueDate, isAllDay}``：多写一个字段就是把用户没改的
    东西也发上去。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1
    payload = store.task_payload("t1")
    assert payload is not None
    assert payload["dueDate"] == "2026-03-20T14:00:00+0800", "推失败不许撤销用户刚做的改期"
    queued = store.pending()
    assert [change.kind for change in queued] == [ChangeKind.UPDATE]
    assert queued[0].payload == {"dueDate": "2026-03-20T14:00:00+0800", "isAllDay": False}
    assert queued[0].next_retry_at == T0 + timedelta(seconds=2), "第一次失败等 2 秒"
    assert engine.status().pending_count == 1

    clock.advance(timedelta(seconds=2))
    transport.enqueue(httpx.Response(200, json={"id": "t1"}))

    assert await engine.push_pending() == 1, "钟走到点，这一次推成功"
    assert store.pending() == ()
    assert engine.status().pending_count == 0
