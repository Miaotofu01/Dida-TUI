"""新建清单**成功了、但服务端没回 id** 之后怎么认领（工单 #54）。

``POST /open/v1/project`` 的响应表里两种成功都写着（``200 → Project`` / ``201 → No Content``），
所以「建好了、但不知道它的 id」是真的会走到的一条路。停在本地临时 id 上的那一行如果没有
恢复路径，之后对它的**每一次改名与删除都会打到一个服务端从没见过的 id 上**：永远推不出去，
而删掉的那条会在下一次刷新时回来（#42 的验收标准 7 因此不成立）。

这一层断的是**外部行为**：状态栏那个待推送数、清单列表页上的行（按 id 认出是哪一行）、
以及真的发出去的请求。机制本身写在 :mod:`dida.sync.lists` 的模块文档里。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import PendingListChange, Store
from dida.sync.engine import ListKind, SyncEngine
from dida.sync.lists import ListWriteKind, is_addressable
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def make_engine(store, transport, *, clock: ManualClock | None = None) -> SyncEngine:
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


def project(id: str = "p1", name: str = "购物", sort_order: int = 1) -> dict:
    """一份 ``Project``（服务端清单索引里的那一行）。"""
    return {"id": id, "name": name, "sortOrder": sort_order}


def serve(transport: FakeTransport, *, index: list[dict], data: list[dict]) -> None:
    """排好一轮刷新的响应：先清单索引，再逐个清单的 data（顺序与引擎取数顺序一致）。

    索引里没有收集箱（ADR-0001 的实测事实），所以 ``data`` 的条数 = 索引条数 + 1。
    """
    transport.enqueue(httpx.Response(200, json=index))
    for payload in data:
        transport.enqueue(httpx.Response(200, json=payload))


def rows_of(engine: SyncEngine) -> dict[str, str]:
    """清单列表页上**真实清单**的「id → 名字」——哪一行是哪一行，只有 id 说得清。"""
    return {
        row.id: row.name for row in engine.list_index() if row.kind is ListKind.LIST
    }


def urls(transport: FakeTransport) -> list[str]:
    return [str(request.url) for request in transport.requests]


def hit_a_local_id(transport: FakeTransport) -> bool:
    """这一轮里有没有请求打到本地临时 id 上（服务端没有那个清单）。"""
    return any("local-list" in url for url in urls(transport))


# ------------------------------------------------------------------ 验收 1：改名


async def test_a_rename_of_a_list_created_with_no_body_goes_out_after_the_next_refresh(store):
    """新建 → ``201`` 空 body（成功）→ 改名 → 认领之后发得出去，待推送回到 0。

    关键在于三段各自成立：建成功时**不许**显示成「有待推送」（它已经到服务端了）；
    改名那一段**不许**打到本地临时 id 上（那是永远推不出去的形状）；下一次全量刷新拿到
    服务端那行的 id 之后，那一笔改名要真的发出去，而且屏幕上只有一条。
    """
    transport = FakeTransport(status_code=201)  # 建清单：201 Created，没有响应体
    engine = make_engine(store, transport)

    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "建成功了：没有欠着服务端的东西"
    assert "购物" in rows_of(engine).values(), "本地先动：建完就看得见"

    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 1, "改名还没出去——它要等一个服务端认得的 id"
    assert not hit_a_local_id(transport), "不许把请求打到本地临时 id 上"

    # 下一轮全量刷新：服务端那行有 id 了（名字还是建出去时的那个）。
    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()

    assert not hit_a_local_id(transport)
    await engine.push_pending()

    assert engine.status().pending_count == 0, "认领之后那一笔要发得出去，不是永远重试"
    assert urls(transport)[-1].endswith("/open/v1/project/p1")
    assert transport.last_json["name"] == "买买买"
    assert rows_of(engine) == {"p1": "买买买", "inbox": "收集箱"}, "屏幕上只有一条，不是两条"


async def test_a_rename_of_a_create_that_has_not_been_sent_yet_folds_into_it(store):
    """新建**还没发出去**时改名：并进那一条新建，而不是在它后面再排一条改动。

    排在一条还没成真的新建后面的改动，只会打到一个还不存在的 id 上——这正是本票要消灭的
    那个形状。并进去之后，发出去的就是改名之后的那一份。
    """
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "买买买"})
    engine = make_engine(store, transport, clock=clock)

    transport.enqueue(httpx.ConnectError("断网了"))
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1, "新建没推出去，留在队列里"

    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1, "一条改动，不是两条"

    clock.advance(timedelta(seconds=30))
    await engine.push_pending()

    assert engine.status().pending_count == 0
    assert urls(transport)[-1].endswith("/open/v1/project"), "新建打的端点不带 id"
    assert transport.last_json == {"name": "买买买"}, "发出去的是改名之后的那一份"
    assert rows_of(engine) == {"p1": "买买买", "inbox": "收集箱"}


# ------------------------------------------------------------------ 验收 2：删除


async def test_a_list_created_with_no_body_and_then_deleted_does_not_come_back(store):
    """新建 → ``201`` 空 body → 删除 → 刷新之后**它不再出现**（#42 的验收标准 7）。

    这一格最难：服务端那行**已经存在**，而我们不知道它的 id，所以这一笔删除只能等 id 到手
    才发得出去。本地先把那一行摘掉，队列里留着「认领之后立刻删」；认领发生在下一次全量刷新
    ——那一次刷新会把服务端那行写进来，所以认领必须顺手把它再摘掉，否则屏幕上就是
    「删掉了又回来」。
    """
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    engine.delete_list(local_id)
    await engine.wait_for_pushes()

    assert "购物" not in rows_of(engine).values(), "本地先动：删掉就看不见"
    assert not hit_a_local_id(transport), "删除不许打到本地临时 id 上"

    # 服务端那行还在（我们还没能删掉它）：刷新拿回来的索引里有它。
    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()

    assert "购物" not in rows_of(engine).values(), "认领之后它也不许回来"

    await engine.push_pending()
    assert transport.last_request.method == "DELETE"
    assert urls(transport)[-1].endswith("/open/v1/project/p1"), "认领之后删除发得出去"
    assert engine.status().pending_count == 0

    serve(transport, index=[], data=[{"tasks": []}])
    await engine.refresh()
    assert "购物" not in rows_of(engine).values()


# ------------------------------------------------------------------ 验收 3：带 body 的那条路不许被弄坏


async def test_a_create_that_comes_back_with_a_body_is_identified_at_once(store):
    """新建 → ``200`` 带 body：认领照旧**立刻**发生，改名与删除照旧可用。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport)

    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "建完就认领了，没有欠着的东西"
    assert rows_of(engine) == {"p1": "购物", "inbox": "收集箱"}

    engine.update_list("p1", name="买买买")  # 认领之后这一行就叫 p1 了
    await engine.wait_for_pushes()

    assert urls(transport)[-1].endswith("/open/v1/project/p1")
    assert transport.last_json["name"] == "买买买"
    assert engine.status().pending_count == 0

    engine.delete_list("p1")
    await engine.wait_for_pushes()
    assert transport.last_request.method == "DELETE"
    assert urls(transport)[-1].endswith("/open/v1/project/p1")
    assert "买买买" not in rows_of(engine).values()
    assert local_id != "p1", "临时 id 已经在认领时让位给服务端那个了"


# ------------------------------------------------------------------ 验收 5：不许认错


async def test_two_new_rows_with_the_same_name_are_never_guessed(store):
    """服务端那一边出现两条同名的新行：**一个都不认**，等下一次（宁可不动，也不许认错）。

    认错的表现是「用户那一行被并到别人的清单上」：屏幕上少一条、而别人的那条被改成了用户的
    名字。所以这里按 id 断：用户那行还在本地临时 id 上，服务端那两条一个字节都没被动过。
    """
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()
    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1

    # 两条都叫「购物」，而且都是我们本来不认识的：分不出哪条是自己建的。
    serve(
        transport,
        index=[project("p1", "购物"), project("p2", "购物", sort_order=2)],
        data=[
            {"project": project("p1", "购物"), "tasks": []},
            {"project": project("p2", "购物", sort_order=2), "tasks": []},
            {"tasks": []},
        ],
    )
    await engine.refresh()

    rows = rows_of(engine)
    assert rows["p1"] == "购物" and rows["p2"] == "购物", "别人的清单一个字节都不许动"
    assert rows[local_id] == "买买买", "用户那一行还在这儿，没被并到哪一条上"
    assert engine.status().pending_count == 1, "这一笔改名还在等——它推不出去，但也没丢"

    # 等下一次：分得清的那一天（另一条不在了）就认。
    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()
    await engine.push_pending()

    assert engine.status().pending_count == 0
    assert urls(transport)[-1].endswith("/open/v1/project/p1")
    assert transport.last_json["name"] == "买买买"
    assert rows_of(engine) == {"p1": "买买买", "inbox": "收集箱"}


async def test_an_older_list_with_the_same_name_does_not_confuse_the_match(store):
    """本来就有一张同名的清单：认领要认得**新出现的那一条**，不许把老的当成自己的。"""
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()
    assert rows_of(engine) == {"p1": "购物", "inbox": "收集箱"}

    local_id = engine.create_list("购物")  # 又建一张同名的
    await engine.wait_for_pushes()
    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()

    serve(
        transport,
        index=[project("p1", "购物"), project("p2", "购物", sort_order=2)],
        data=[
            {"project": project("p1", "购物"), "tasks": []},
            {"project": project("p2", "购物", sort_order=2), "tasks": []},
            {"tasks": []},
        ],
    )
    await engine.refresh()
    await engine.push_pending()

    rows = rows_of(engine)
    assert rows["p1"] == "购物", "老的那条是别人（用户本来就有的），不许被改名"
    assert rows["p2"] == "买买买", "认的是新出现的那一条"
    assert urls(transport)[-1].endswith("/open/v1/project/p2")
    assert engine.status().pending_count == 0


# ------------------------------------------------------------------ 机制本身：一条没有 id 的改动不可寻址


def test_a_change_addressed_to_an_id_the_server_has_never_seen_cannot_be_pushed():
    """本地临时 id 是**不可寻址**的：改 / 删打上去只会得到一个 404，然后永远重试。

    新建是例外——它打的端点不带 id（``POST /open/v1/project``），所以它照样发得出去。
    """

    def change(kind: ListWriteKind, list_id: str) -> PendingListChange:
        return PendingListChange(
            id=1, list_id=list_id, kind=kind, payload={}, created_at=T0
        )

    assert is_addressable(change(ListWriteKind.CREATE, "local-list-1")), "新建不带 id"
    assert not is_addressable(change(ListWriteKind.UPDATE, "local-list-1"))
    assert not is_addressable(change(ListWriteKind.DELETE, "local-list-1"))
    assert not is_addressable(change(ListWriteKind.AWAIT_ID, "local-list-1"))
    assert is_addressable(change(ListWriteKind.UPDATE, "p1"))
    assert is_addressable(change(ListWriteKind.DELETE, "p1"))


async def test_a_delete_parked_behind_an_unsent_create_follows_it_to_the_real_id(store):
    """新建还没发出去就被删掉：新建照发（它可能已经到过服务端，只是回执没回来），删除排在它后面。

    这一格是「不可寻址」那条规矩的另一半：删除排在一条还没有 id 的新建后面，所以它现在发不
    出去；而新建成功（``200`` 带 body）把两笔一起挪到真 id 上之后，删除要**删对地方**——
    不是删一个本地临时 id，也不是把那一行写回来。
    """
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport, clock=clock)

    transport.enqueue(httpx.ConnectError("断网了"))
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    engine.delete_list(local_id)
    await engine.wait_for_pushes()

    assert "购物" not in rows_of(engine).values(), "本地先动：删掉就看不见"
    assert engine.status().pending_count == 2, "新建与删除都还没出去"

    clock.advance(timedelta(seconds=30))
    await engine.push_pending()

    assert engine.status().pending_count == 0
    assert transport.requests[-2].method == "POST", "先把它建出来"
    assert urls(transport)[-2].endswith("/open/v1/project")
    assert transport.last_request.method == "DELETE"
    assert urls(transport)[-1].endswith("/open/v1/project/p1"), "再删——用服务端给的 id"
    assert "购物" not in rows_of(engine).values(), "认领不许把删掉的那一行写回来"


async def test_a_delete_of_a_list_whose_queue_record_is_gone_still_finds_it_by_its_name(store):
    """队列记录不全的旧状态（本地有临时行、没有那条记录）：删除照样有恢复路径。

    认领记录是#54 才有的东西，所以「临时行 + 没有记录」这种状态在真实库里可能存在。这一格
    用**本地那一行现在的名字**当钥匙去对：唯一才认，认不出来就等下一次。
    """
    store.save_list({"id": "local-list-9", "name": "购物", "isInbox": False})
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    engine.delete_list("local-list-9")
    await engine.wait_for_pushes()
    assert "购物" not in rows_of(engine).values()

    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()
    await engine.push_pending()

    assert transport.last_request.method == "DELETE"
    assert urls(transport)[-1].endswith("/open/v1/project/p1")
    assert engine.status().pending_count == 0
    assert "购物" not in rows_of(engine).values()


async def test_a_rename_that_ends_up_matching_the_server_is_not_sent_again(store):
    """改了一圈又改回原名：认领之后那一笔直接出队，不再发一个什么都不改的请求。"""
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()
    engine.update_list(local_id, name="购物")  # 又改回去了
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1

    before = len(transport.requests)
    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()
    await engine.push_pending()

    assert engine.status().pending_count == 0
    assert not any(
        request.method == "POST" and str(request.url).endswith("/open/v1/project/p1")
        for request in transport.requests[before:]
    ), "服务端那一份就是用户要的那一份：不该再发一次改名"
    assert rows_of(engine) == {"p1": "购物", "inbox": "收集箱"}
