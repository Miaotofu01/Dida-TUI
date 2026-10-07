"""同步引擎的写路径（t10）：乐观写 → 待推送改动 → 指数退避重试。

接缝是 ``SyncEngine`` 的两个公开入口：``write()``（写）与 ``push_pending()``（推），
接真的 ``Store``（t08）与真的 ``DidaApiClient``（t07）。网络钉在**接缝二**（传输层可注入）上，
所以这里断言的是「引擎发了哪些请求、请求体长什么样」与「本地库、视图、状态栏看到什么」，
不碰任何私有方法，也不 mock 我们自己的模块。

钉死的规矩（ADR-0002 + CONTEXT）：

- 写先在本地生效并**立即返回**，网络结果不是它的前置条件；
- 推失败留在队列里，按**注入的时钟**指数退避重试——没有 ``time.sleep``、没有真时钟、
  也没有测试管不住的线程；
- 待推送数量是状态栏常驻的那一个数，它从 0 变 1，推成功后再变回 0。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import ListKind, SyncEngine, WriteKind, backoff_delay
from dida.testing import FakeTransport, InMemorySource, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def project(id: str = "work", name: str = "工作") -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 1}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


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
    now: datetime = T0,
    clock: ManualClock | None = None,
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(now),
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def titles(engine: SyncEngine) -> list[str]:
    """屏幕上看得见的任务标题（用户真正看到的那一份）。

    v1 的 ``view().groups`` 问的是同一句话；#58 把它换成 v2 的读形状——库里每个真实清单的
    成员，按清单索引的顺序。这些测试摆的任务都在 ``work`` 里且没有未来截止的，两种问法
    逐字相同。
    """
    return [
        item.title
        for row in engine.list_index()
        if row.kind is ListKind.LIST
        for item in engine.tasks_in(row.id).items
    ]


def test_a_write_lands_locally_and_queues_immediately(store):
    """乐观写：本地当场生效、改动入队、``created_at`` 来自注入的钟。

    这一条是同步的（没有事件循环）：写路径里**没有**任何东西需要等网络。
    """
    seed(store, task(id="t1", title="写周报", project_id="work"))
    engine = make_engine(store)

    engine.write("t1", changes={"title": "写周报（我改的）"})

    assert titles(engine) == ["写周报（我改的）"]
    assert store.task_payload("t1")["title"] == "写周报（我改的）"
    assert engine.status().pending_count == 1, "状态栏常驻的那个数必须已经变了"
    queued = store.pending()
    assert [(change.task_id, change.list_id, change.kind) for change in queued] == [
        ("t1", "work", ChangeKind.UPDATE)
    ]
    assert queued[0].payload == {"title": "写周报（我改的）"}
    assert queued[0].created_at == T0, "入队时刻来自注入的钟，不是 datetime.now()"


def test_a_write_without_a_cache_fails_loudly(store):
    """没接本地库的引擎不许假装写成功了（与刷新路径同一条口径）。"""
    engine = SyncEngine(clock=ManualClock(T0), source=InMemorySource())

    with pytest.raises(RuntimeError, match="本地存储"):
        engine.write("t1", changes={"title": "写周报"})


async def test_the_queued_update_is_pushed_with_the_unknown_fields_still_on_it(store):
    """队列里的改动真的发得出去，且**原样回写不认识的服务端字段**。

    请求形状按 ``api-contracts.md`` 钉：更新是 ``POST /open/v1/task/{taskId}``（没有 PATCH），
    请求体至少要有 ``id`` 与 ``projectId``。``kind`` / ``reminders`` 这种 t07 不认识的字段
    必须跟着底稿一起回去（CONTEXT 的 trap 第 4 条：不认识的字段丢了，手机端设置的东西就没了）。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={}))
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            project_id="work",
            kind="TEXT",
            reminders=["TRIGGER:P0DT9H0M0S"],
        ),
    )
    engine = make_engine(store, transport)

    engine.write("t1", changes={"title": "写周报（我改的）"})
    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/t1"
    assert request.headers["Authorization"] == "Bearer tok"
    body = transport.last_json
    assert body["id"] == "t1"
    assert body["projectId"] == "work"
    assert body["title"] == "写周报（我改的）"
    assert body["kind"] == "TEXT", "不认识的服务端字段必须原样回写"
    assert body["reminders"] == ["TRIGGER:P0DT9H0M0S"]
    assert "status" not in body, "status 不是新建/更新接受的字段（api-contracts 第 5 条）"

    assert len(transport.requests) == 1, "队列里的那条改动推了一次"
    assert store.pending() == (), "推成功就出队"
    assert engine.status().pending_count == 0, "状态栏那个数回到 0"


async def test_a_write_schedules_the_push_without_making_the_caller_wait(store):
    """ADR-0002 的「立即推送」：写的人不等网络，但推送确实被排上了。

    ``write()`` 返回时一个请求都还没发出去——网络不是这次写的前置条件；推送排在事件循环
    那根线程上（不是新线程：t08 的连接有线程亲和）。测试用确定性入口 ``wait_for_pushes()``
    等它跑完，生产路径上没有人 await 它。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={}))
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, transport)

    engine.write("t1", changes={"title": "写周报（我改的）"})

    assert transport.requests == [], "写返回的那一刻还没碰网络"
    assert engine.status().pending_count == 1

    await engine.wait_for_pushes()

    assert len(transport.requests) == 1, "推送被排上了，只是不等它"
    assert engine.status().pending_count == 0


async def test_a_failed_push_stays_queued_until_the_clock_reaches_the_backoff(store):
    """推失败：改动留在队列里、错误与下次重试时刻都记下，状态栏那个数不归零。

    「等多久」按失败次数指数增长（2s、4s…），全部由注入的钟判定：钟没走到点一次都不许
    再发，走到了才发。没有 ``time.sleep``、没有真时钟、也没有测试管不住的线程。
    """
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    transport.enqueue(NetworkError("还是连不上"))
    transport.enqueue(httpx.Response(200, json={}))
    seed(store, task(id="t1", title="写周报"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)

    engine.write("t1", changes={"title": "写周报（我改的）"})
    await engine.wait_for_pushes()

    queued = store.pending()
    assert len(queued) == 1, "推不动就留在队列里，不许悄悄丢掉"
    assert queued[0].attempts == 1
    assert queued[0].next_retry_at == T0 + timedelta(seconds=2), "第一次失败等 2 秒"
    assert "连不上" in (queued[0].last_error or "")
    assert engine.status().pending_count == 1, "本地比服务端新，这个数就是给用户看的"
    assert titles(engine) == ["写周报（我改的）"], "推失败不许撤销用户刚做的操作"

    assert await engine.push_pending() == 0, "还没到点：一次都不许再发"
    assert len(transport.requests) == 1

    clock.advance(timedelta(seconds=2))
    assert await engine.push_pending() == 0, "到点重试了，但这次还是失败"

    queued = store.pending()
    assert len(transport.requests) == 2, "到点才重试，而且只重试一次"
    assert queued[0].attempts == 2
    assert queued[0].next_retry_at == T0 + timedelta(seconds=2 + 4), "第二次失败等 4 秒"

    clock.advance(timedelta(seconds=4))

    assert await engine.push_pending() == 1, "钟走到点，第三次才成功"
    assert len(transport.requests) == 3
    assert store.pending() == ()
    assert engine.status().pending_count == 0, "重试成功后这个数才回到 0"


def test_backoff_doubles_each_attempt_and_stops_at_the_cap():
    """退避的节奏是纯函数，直接钉住：2s、4s、8s…最多 5 分钟。

    期望值来自策略本身（写在这里的字面量），不是照代码再算一遍。
    """
    assert [backoff_delay(n).total_seconds() for n in range(5)] == [2, 4, 8, 16, 32]
    assert backoff_delay(20) == timedelta(minutes=5), "封顶，不许涨到天上去"
    assert backoff_delay(0) == timedelta(seconds=2)


def test_backoff_saturates_instead_of_overflowing_at_high_attempt_counts():
    """断网攒到四十几次失败时，退避照样算得出，而且就是那个封顶值。

    实测的来路：``all_proxy`` 指向 socks5 而本机没装 ``socksio``，每次推送都失败，队列里
    那条改动攒到 ``attempts=46``。旧的 ``base * 2**attempts`` 装不进 ``timedelta``（上限
    999999999 天），于是在**算退避**这一步抛 ``OverflowError``——把「推不上去」升级成
    「打不开 app」，而且撤掉梯子也好不了：崩在记下一次重试之前，跟网络没关系了。
    """
    assert backoff_delay(45) == timedelta(minutes=5), "溢出前的最后一次：已经是 cap"
    assert backoff_delay(46) == timedelta(minutes=5), "46 次只该封顶，不许抛 OverflowError"
    assert backoff_delay(10_000) == timedelta(minutes=5), "再大也一样：cap 是上界"


async def test_a_change_that_failed_forty_six_times_retries_instead_of_crashing(store):
    """一条攒到 ``attempts=46`` 的改动：记下失败与下次时刻，不许把 app 打崩。

    接缝是 ``push_pending()``——周期泵（``push_tick``）与手动同步每次按键走的就是这一条，
    真存储 + 真客户端 + 假传输。
    """
    transport = FakeTransport()
    for _ in range(46):
        transport.enqueue(NetworkError("连不上服务端：Using SOCKS proxy"))
    seed(store, task(id="t1", title="刷一道算法题"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)

    engine.write("t1", changes={"title": "刷一道算法题（我改的）"})
    await engine.wait_for_pushes()

    for _ in range(45):
        clock.advance(timedelta(minutes=5))
        assert await engine.push_pending() == 0, "还是推不上去"

    queued = store.pending()
    assert len(queued) == 1, "攒了 46 次失败也还在队列里，不许悄悄丢掉"
    assert queued[0].attempts == 46, f"应该正好攒到 46 次，实际 {queued[0].attempts}"

    # 关键的那一跳：已经失败过 46 次的改动**再失败一次**，旧代码在这里算 ``base * 2**46``
    # 抛 OverflowError（`record_attempt` 那行在 `except DidaError` 里，OverflowError 不是
    # DidaError，会一路穿出去）。所以这一跳才是「打不开 app」的那一步。
    clock.advance(timedelta(minutes=5))
    assert await engine.push_pending() == 0, "还是推不上去，但 app 不许崩"
    queued = store.pending()
    assert queued[0].attempts == 47, f"应该记下第 47 次，实际 {queued[0].attempts}"
    assert queued[0].next_retry_at == clock.now() + timedelta(minutes=5), "封顶，不是溢出"

    transport.enqueue(httpx.Response(200, json={}))
    clock.advance(timedelta(minutes=5))
    assert await engine.push_pending() == 1, "网络回来了就该推得出去"
    assert store.pending() == ()


async def test_completing_through_the_write_path_uses_the_complete_endpoint(store):
    """完成：本地**当场**标记完成（从今日视图里消失），推送走无请求体的 complete 端点。

    ADR-0002：完成在服务端不可逆，所以本地先动、状态栏那个数顶上；``status`` 是
    ``2`` 而不是 ``1``（api-contracts.md 第 2 条），且它不会进任何请求体（同第 5 条）。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, transport)

    engine.write("t1", kind=WriteKind.COMPLETE)

    assert titles(engine) == [], "本地当场就不再是未完成"
    assert store.task_payload("t1")["status"] == 2
    assert engine.status().pending_count == 1

    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1/complete"
    assert request.content == b"", "完成端点没有请求体"
    assert engine.status().pending_count == 0


async def test_deleting_through_the_write_path_uses_the_delete_verb(store):
    """删除：本地当场摘掉快照，推送是 ``DELETE /open/v1/project/{id}/task/{taskId}``。

    没推成功的删除整条豁免于服务端权威（t08 的 ``_exempt_fields`` 返回 ``None``），
    否则下一次刷新会让用户删掉的任务复活。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={}))
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, transport)

    engine.write("t1", kind=WriteKind.DELETE)

    assert titles(engine) == []
    assert store.task_payload("t1") is None

    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1"
    assert engine.status().pending_count == 0


async def test_one_failed_change_does_not_block_the_next_one(store):
    """队列按发生顺序走完：一条推不动，后面的照推。

    失败的那条自己留在队列里等退避（ADR-0002），不该把用户后面做的操作一起拖住。
    """
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    transport.enqueue(httpx.Response(200, json={}))
    seed(store, task(id="t1", title="写周报"), task(id="t2", title="买牛奶"))
    engine = make_engine(store, transport)

    engine.write("t1", changes={"title": "写周报（我改的）"})
    engine.write("t2", changes={"title": "买牛奶（我改的）"})
    await engine.wait_for_pushes()

    assert [str(request.url) for request in transport.requests] == [
        "https://api.dida365.com/open/v1/task/t1",
        "https://api.dida365.com/open/v1/task/t2",
    ]
    assert [change.task_id for change in store.pending()] == ["t1"], "只有失败的那条留在队列里"
    assert engine.status().pending_count == 1
    assert set(titles(engine)) == {"写周报（我改的）", "买牛奶（我改的）"}, "两条本地都生效了"


async def test_pushing_without_a_client_fails_loudly(store):
    """没接客户端的引擎不许假装推过了。"""
    engine = SyncEngine(clock=ManualClock(T0), source=store)

    with pytest.raises(RuntimeError, match="API 客户端"):
        await engine.push_pending()


DATE_IN_MARCH = "2026-03-20T09:00:00.000+0800"
"""一个合法的 ``dueDate`` 原文（API 的形状）：推出去时必须逐字节不变。"""


async def test_a_restart_still_has_the_queue_and_pushes_it(tmp_path):
    """待推送改动活在本地库里：进程结束不等于它们没了，下次启动的周期泵接着推。

    这条钉的是 ``quit_prompt`` 那句退出文案的事实依据。断网写一笔 → 关掉库（等于退出
    dida）→ 拿同一个文件重开一次：队列还在，退避到点之后 ``push_pending()`` 把它推出去。
    「推不动就留在队列里」如果只活在内存里，那句话就得反过来说。
    """
    path = tmp_path / "cache.sqlite3"
    opened = Store(path)
    down = FakeTransport()
    for _ in range(4):
        down.enqueue(httpx.ConnectError("断网"))
    seed(opened, task(id="t1", title="写周报"))
    first = make_engine(opened, down)
    first.write("t1", changes={"dueDate": DATE_IN_MARCH})
    await first.wait_for_pushes()
    assert opened.pending_count() == 1, "推失败 → 留在队列里"
    opened.close()

    reopened = Store(path)
    try:
        assert reopened.pending_count() == 1, "进程结束不等于队列没了：它在本地库那张表里"

        ok = FakeTransport(json={"id": "t1"})
        # 重启发生在几分钟之后：退避（2s × 2ⁿ，封顶 5 分钟）已经到点
        later = make_engine(reopened, ok, clock=ManualClock(T0 + timedelta(minutes=10)))
        pushed = await later.push_pending()

        assert pushed == 1, "下次启动的周期泵推得出去"
        assert reopened.pending_count() == 0
        assert ok.last_json["dueDate"] == DATE_IN_MARCH, "推出去的是原来那一笔，未知字段照旧带回"
    finally:
        reopened.close()
