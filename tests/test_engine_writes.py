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
from dida.sync.engine import NO_DUE_TEXT, SyncEngine
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


# ---------------------------------------------------------------- 截止时间的请求形状（#44）


async def test_clearing_the_due_date_writes_an_explicit_null_and_no_date_at_all(store):
    """清除截止时间：``dueDate`` 显式写 **null**，``isAllDay`` 写 false（工单 #44）。

    形状是**这一票自己定的**：api-shapes §A2 记着「省略的字段是被保留还是被清空，文档
    没说」——所以清除**不能**靠「不发这个字段」。显式 null 是唯一一个把「清空」说出来的
    形状；``isAllDay`` 一起写 false，是为了不留下一个「全天、但没有日期」的组合。

    两件事一起断：请求体里那个 null，以及**本地**那份原文里的 ``dueDate`` 也真的没了
    （不复原、不留一个 1970 年的时刻），于是详细页那一格回到「无」。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=None, all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task/t1")
    body = transport.last_json
    assert "dueDate" in body, "清除必须显式写 null，不能靠省略字段（文档没说省略是清空）"
    assert body["dueDate"] is None
    assert body["isAllDay"] is False
    payload = store.task_payload("t1")
    assert payload is not None
    assert payload["dueDate"] is None, "本地那一份也不许留着旧日期"
    assert engine.task_detail("t1").due is None
    assert engine.task_detail("t1").due_text == NO_DUE_TEXT, "读法回到「没有日期」"
    assert store.pending_count() == 0, "推成功了就该出队"


async def test_a_write_of_the_due_date_leaves_start_date_exactly_as_the_server_gave_it(store):
    """写截止时间**不动** ``startDate``（工单 #44 明确决定，见下面的理由）。

    文档说 ``dueDate`` 与 ``startDate`` 是两个**独立**字段，从没说写一个会带上另一个
    （api-shapes §D17 :2277/:2285）；ticket #44 那句「服务端会自动补一个同值的开始时间」是
    v1 的观察，而文档里**没有这句话**（§E2(d)）。所以客户端不替服务端编一个开始时间：

    - 自己造一份 ``startDate`` 就是把用户从来没设过的字段写进他的账号；
    - 拿 ``dueDate`` 覆盖一份**已经存在**的 ``startDate`` 更糟——那正好是「静默位移」，
      而且会动到用户没在改的那一格。

    ``startDate`` 照旧**在请求体里**：它来自整份底稿（``merge_snapshot``），一个字都不变
    ——「回写带上服务端给的未知字段」与这一条是同一件事的两面。
    """
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            dueDate="2026-03-10T18:00:00+0800",
            startDate="2026-03-09T09:00:00+0800",
        ),
    )
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    body = transport.last_json
    assert body["dueDate"] == "2026-03-20T14:00:00+0800"
    assert body["startDate"] == "2026-03-09T09:00:00+0800", (
        "截止时间改了，开始时间一个字都不许动（我们也不给它补一个新的）"
    )
    assert store.task_payload("t1")["startDate"] == "2026-03-09T09:00:00+0800"


async def test_a_task_without_a_start_date_does_not_grow_one(store):
    """没有 ``startDate`` 的任务：改截止时间也**不**凭空长出一个开始时间。

    与上一条是同一个决定的两面：观察到的「服务端会补一个同值的开始时间」既不能假定它总会
    发生、也不能假定它不会（ticket 自己的措辞）。客户端能做的是**不表态**——把开始时间留给
    服务端，而不是替它写一个。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert "startDate" not in transport.last_json, (
        f"底稿里没有开始时间，客户端不该自己造一个：{transport.last_json}"
    )


async def test_a_write_of_the_due_date_carries_the_time_zone_and_the_unknown_fields_verbatim(store):
    """回写带上服务端给的未知字段，且 ``timeZone`` **原样**回去（工单 #44 验收标准 7 + 8）。

    ``timeZone`` 是「写错就静默位移」那个坑的落点（api-shapes §D17），所以这里断的是**逐字
    相等**：``+0800`` 不许变成 ``+08:00``、不许换成 UTC。手机端设的那些我们不认识的字段
    （``focusSummaries`` / ``repeatFrom``）也一起回去——它们靠整份底稿（``merge_snapshot``）
    才在请求体里，只发 ``{id, projectId, dueDate}`` 的话它们就没了。
    """
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            dueDate="2026-03-10T18:00:00+0800",
            startDate="2026-03-10T18:00:00+0800",
            timeZone="Asia/Shanghai",
            repeatFrom="1",
            focusSummaries=[{"pomoCount": 2}],
        ),
    )
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    body = transport.last_json
    assert body["timeZone"] == "Asia/Shanghai", "时区字段原样回写，不许被换成推算出来的东西"
    assert body["repeatFrom"] == "1", "服务端给的、我们不认识的字段要一起带回去"
    assert body["focusSummaries"] == [{"pomoCount": 2}], body
    assert body["dueDate"] == "2026-03-20T14:00:00+0800", (
        "写出去的那一刻是用户墙钟上的 14:00+0800——不是被换成 UTC 的 06:00"
    )


async def test_clearing_the_due_date_keeps_the_time_zone_and_the_unknown_fields(store):
    """清除截止时间也走整份底稿：``timeZone`` 与陌生字段照旧在请求体里。

    「清除」不是「清空整条任务」：只有 ``dueDate`` 与 ``isAllDay`` 这两笔变，其余一个字不动。
    """
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            dueDate="2026-03-10T18:00:00+0800",
            timeZone="Asia/Shanghai",
            repeatFrom="1",
        ),
    )
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=None, all_day=False)
    await engine.wait_for_pushes()

    body = transport.last_json
    assert body["timeZone"] == "Asia/Shanghai"
    assert body["repeatFrom"] == "1"
    assert body["title"] == "写周报", "标题这些没被改的字段照旧在底稿里"
    assert body["dueDate"] is None


async def test_changing_the_due_date_sends_the_repeat_rule_byte_for_byte(store):
    """改一条**重复任务**的截止时间：``repeatFlag`` 在请求体里与底稿逐字相等（验收标准 4）。

    断在**请求体**上，不是断本地对象：重复规则是一个客户端完全不解释的字符串，任何一个
    字节变了（大小写、分号、加一个 ``;INTERVAL=1``）都是另一条规则。它能原样出去，靠的是
    「整份底稿 ⊕ 改动」那条既有策略——只发 ``{id, projectId, dueDate}`` 的请求体里根本没
    有这个字段，服务端会按它自己的省略语义处理（文档没说）。
    """
    rule = "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH;WKST=SU"
    seed(
        store,
        task(
            id="t1",
            title="周会",
            dueDate="2026-03-10T18:00:00+0800",
            repeatFlag=rule,
        ),
    )
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    body = transport.last_json
    assert body["repeatFlag"] == rule, f"重复规则被动过了：{body.get('repeatFlag')!r}"
    assert body["dueDate"] == "2026-03-20T14:00:00+0800", "只该有截止时间变"
    assert store.task_payload("t1")["repeatFlag"] == rule


async def test_a_task_without_a_date_never_carries_a_repeat_rule_out(store):
    """没有日期的任务：清除截止时间的那一笔请求里**没有**重复规则（验收标准 5）。

    服务端对「没有日期却有重复规则」的处理是**静默清空**（spec :158；``guards`` 的
    ``DatelessRepeatError`` 就是拦它的）。一条从来没有日期的任务不该有任何规则可带——这里
    断的就是请求体里连那个 key 都不出现，而不是「带了一个空串」。
    """
    seed(store, task(id="t1", title="写周报"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=None, all_day=False)
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1
    assert "repeatFlag" not in transport.last_json, transport.last_json


async def test_clearing_a_repeating_tasks_due_date_is_refused_before_any_request(store):
    """清除一条**重复任务**的截止时间：本地守卫在请求出门前拦下（工单 #44 验收标准 6 的推广）。

    这一条不是「想让它红」写出来的——它是把两道既有守卫摆在一起之后**实测**出来的行为，
    所以在这里钉住、并由详细页如实说出来：

    - 清除要发的是 ``dueDate: null``（上一条测试钉的形状）；
    - ``guards.guard_repeat_rule``（api/guards.py:144）在同一个请求体上看到
      「``repeatFlag`` 非空、而 ``dueDate`` 与 ``startDate`` 都是 ``None``」，于是抛
      ``DatelessRepeatError``——它防的是那个**服务端静默清空重复规则**的坑（spec :158）。

    于是：**零请求**，改动留在队列里，状态与详细页底部那一行拿到的是那句说得清原因的话。
    「零请求」与「服务端静默改掉用户的重复规则」之间，这一票选前者——后者在这个客户端里
    没有任何办法被看见（服务端清空规则不发通知，下一次刷新才显形）。
    """
    rule = "RRULE:FREQ=DAILY;INTERVAL=1"
    seed(store, task(id="t1", title="吃药", dueDate="2026-03-10T18:00:00+0800", repeatFlag=rule))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=None, all_day=False)
    await engine.wait_for_pushes()

    assert transport.requests == [], "守卫该在请求出门前拦下它（api_date 同一类拦法）"
    reason = engine.status().last_error
    assert reason and "重复规则" in reason, f"拦下的原因要说得出名字：{reason!r}"
    assert engine.status().pending_count == 1, "改动留在队列里，本地那一份照旧生效"
    assert store.task_payload("t1")["repeatFlag"] == rule, "客户端一个字都不许动重复规则"


async def test_clearing_a_due_date_does_not_touch_a_repeat_rule_that_is_not_there(store):
    """一条没有日期、也没有重复规则的任务：清除也是一笔正常的、发得出去的写。

    与上一条成对：拦住的是「有规则要保住」这一种，不是「清除」本身。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    engine.reschedule("t1", due=None, all_day=False)
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1, "没有重复规则要保住，这一笔正常出门"
    assert transport.last_json["dueDate"] is None
    assert "repeatFlag" not in transport.last_json
