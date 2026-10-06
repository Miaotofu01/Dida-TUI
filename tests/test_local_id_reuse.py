"""本地临时清单 id 的**复用**（工单 #57）：一条陈旧记录能把用户的另一条清单删掉。

复现链（#54 的 merger 用真 ``Store`` + 引擎 + ``FakeTransport`` 探出来的，每一步都实测过）：

1. 新建「购物」→ 服务端答 ``201 No Content`` → 本地留一条**认领记录**（正确）。
2. 刷新：服务端有两条都叫「购物」（手机上的 ``p1`` + 我们建的 ``p2``）→ 识别正确地拒绝猜；
   而那一**行**按 #54 的剪枝规则被剪掉（它没有「还没到服务端的改动」）、**记录还在**
   → 那个本地 id 眼看就要空出来。
3. 新建「盐」→ 分配器**只扫行**，于是复用了 ``local-list-1`` → 一个号挂两条记录。
4. 用户删掉「盐」→ 按顺序取到**陈旧的那条**（``sentName=购物``）→ 这次删除改的是它。
5. 下一次刷新（歧义消失）→ 陈旧记录唯一对上 ``p1`` → 摘掉 ``p1`` 的本地行。
6. 推送 → ``DELETE /open/v1/project/p1`` → **手机上那条清单在服务端被销毁**，而待推送回到 0、
   界面上什么都不说。

这一层断的是外部行为（屏幕上的行、真发出去的请求、状态栏那个数），**走真 ``Store`` 接缝**
——假后端的 ``delete`` 只记录、不动缓存，在这条链上是瞎的。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import DidaError
from dida.storage.store import PendingListChange, Store
from dida.sync.engine import ListKind, SyncEngine
from dida.sync.lists import ListWire, ListWriteKind, is_addressable
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


def project(id: str, name: str, sort_order: int = 1) -> dict:
    return {"id": id, "name": name, "sortOrder": sort_order}


def serve(transport: FakeTransport, *, index: list[dict], data: list[dict] | None = None) -> None:
    """一轮刷新的响应：先清单索引，再逐个清单的 data（顺序与引擎取数顺序一致）。

    不给 ``data`` 就按索引里的每一行各回一份，再补一份收集箱的（它不在索引里）。
    """
    transport.enqueue(httpx.Response(200, json=index))
    replies = data
    if replies is None:
        replies = [{"project": row, "tasks": []} for row in index] + [{"tasks": []}]
    for payload in replies:
        transport.enqueue(httpx.Response(200, json=payload))


def rows_of(engine: SyncEngine) -> dict[str, str]:
    """清单列表页上真实清单的「id → 名字」。"""
    return {row.id: row.name for row in engine.list_index() if row.kind is ListKind.LIST}


def requests_of(transport: FakeTransport, method: str) -> list[str]:
    return [str(request.url) for request in transport.requests if request.method == method]


def seed_record(
    store: Store, *, list_id: str, kind: ListWriteKind, payload: dict, name: str
) -> None:
    """摆一条队列记录（连带那一行），用来造出「旧版本留下的坏状态」。"""
    store.enqueue_list(
        list_id=list_id,
        kind=kind,
        payload=payload,
        now=T0,
        local={"id": list_id, "name": name, "isInbox": False},
    )


# ------------------------------------------------------------------ 六步复现链


async def test_the_probe_chain_can_no_longer_delete_a_stranger_list(store):
    """#57 的六步链，一步一钉；第 6 步：**一个指向 ``p1`` 的 DELETE 都不许发出去**。"""
    clock = ManualClock(T0)
    transport = FakeTransport(status_code=201)  # 建清单：201 空 body
    engine = make_engine(store, transport, clock=clock)

    # 1. 新建「购物」→ 201 空 body → 在 local-list-1 上留一条认领记录
    shopping = engine.create_list("购物")
    await engine.wait_for_pushes()
    assert shopping == "local-list-1"
    assert engine.status().pending_count == 0, "建成功了：没有欠着服务端的东西"

    # 2. 刷新：服务端两条同名的 → 拒绝猜；那一行被剪掉、记录还在
    serve(transport, index=[project("p1", "购物"), project("p2", "购物", 2)])
    await engine.refresh()

    rows = rows_of(engine)
    assert rows["p1"] == "购物" and rows["p2"] == "购物", "两条同名的都在，一个都没认"
    assert shopping not in rows, "那一行被剪掉了（号本来要从这里空出来）"
    assert [item.list_id for item in store.pending_lists()] == [shopping], "记录还在"

    # 3. 新建「盐」：分配器必须跳过那个还挂着记录的号
    salt = engine.create_list("盐")
    await engine.wait_for_pushes()
    assert salt != shopping, "号还被那条记录占着——临时 id 的所有权跟着**记录**走"
    assert [item.list_id for item in store.pending_lists()].count(shopping) == 1, "一号码一记录"

    # 4. 用户删掉「盐」：改的必须是**盐**那一条记录
    engine.delete_list(salt)
    await engine.wait_for_pushes()
    assert "盐" not in rows_of(engine).values(), "本地先动"

    # 5. 下一次刷新：歧义消失（服务端是 p1 购物 + p3 盐）
    serve(transport, index=[project("p1", "购物"), project("p3", "盐", 2)])
    await engine.refresh()

    # 6. 推送（`r` 就是「刷新 + 推一轮」）
    await engine.push_pending()
    assert requests_of(transport, "DELETE") == [
        "https://api.dida365.com/open/v1/project/p3"
    ], "只许删「盐」；一个指向 p1 的 DELETE 都不许有"
    assert rows_of(engine)["p1"] == "购物", "手机上那条「购物」还在，本地这一行也不许被摘掉"
    assert engine.status().pending_count == 0


# ------------------------------------------------------------------ 分配器：号的所有权


def test_the_allocator_does_not_hand_out_an_id_that_still_has_a_record(store):
    """行没了、记录还在时，那个号**仍然**被占着（#57 的第一条修法）。"""
    seed_record(
        store, list_id="local-list-1", kind=ListWriteKind.CREATE, payload={"name": "购物"}, name="购物"
    )
    store.drop_list("local-list-1")  # 剪枝把那一行剪掉了（记录还在）

    assert store.list_payload("local-list-1") is None
    assert store.new_local_list_id() != "local-list-1", "记录还占着号，不许再发出去"


def test_the_allocator_still_hands_out_an_id_nothing_holds_any_more(store):
    """记录也没了（认领之后出队了），那个号当然可以再用。"""
    assert store.new_local_list_id() == "local-list-1"


# ------------------------------------------------------------------ 写路径：坏状态要出声


async def test_a_local_id_whose_records_are_not_one_list_makes_the_write_path_refuse(store):
    """一个号上两条「自己的」记录 = 不变量坏了：**大声拒绝**，绝不按顺序挑一条。

    挑错正是探针里那一步：删「盐」删到了「购物」的记录上，最后删掉的是服务端的 ``p1``。
    """
    seed_record(
        store, list_id="local-list-1", kind=ListWriteKind.CREATE, payload={"name": "购物"}, name="购物"
    )
    seed_record(
        store,
        list_id="local-list-1",
        kind=ListWriteKind.AWAIT_ID,
        payload={"sentName": "购物", "knownIds": []},
        name="购物",
    )
    engine = make_engine(store, FakeTransport(status_code=201))
    before = [(item.id, item.kind) for item in store.pending_lists()]

    for action in (
        lambda: engine.delete_list("local-list-1"),
        lambda: engine.update_list("local-list-1", name="盐"),
    ):
        with pytest.raises(DidaError) as caught:
            action()
        assert "local-list-1" in str(caught.value)

    assert [(item.id, item.kind) for item in store.pending_lists()] == before, "一个字节都没动"
    assert store.list_payload("local-list-1")["name"] == "购物"


async def test_identification_never_acts_on_a_local_id_whose_records_are_not_one_list(store):
    """旧库留下的坏状态：那条陈旧记录也不许拿服务端的行去办任何事（宁可不动）。"""
    seed_record(
        store,
        list_id="local-list-1",
        kind=ListWriteKind.DELETE,
        payload={"sentName": "购物", "knownIds": []},
        name="购物",
    )
    seed_record(
        store, list_id="local-list-1", kind=ListWriteKind.CREATE, payload={"name": "盐"}, name="盐"
    )
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    serve(transport, index=[project("p1", "购物")])
    await engine.refresh()
    await engine.push_pending()

    assert requests_of(transport, "DELETE") == [], "一个 DELETE 都不许发出去"
    assert rows_of(engine)["p1"] == "购物", "p1 的本地行不许被摘掉"


# ------------------------------------------------------------------ 两种分类都必须显式


def test_every_list_write_kind_is_classified_where_it_is_read():
    """加一种记录 / 一种端点时，那两处分类必须**显式**写下来（#57 的检查 8）。

    默默按默认值走的下场：新记录被当成「还没到服务端的改动」（于是剪枝替它留住一行、
    状态栏多一个数），或者被当成「可以发」（于是打到一个服务端没见过的 id 上）。
    默认值是**保守的那一侧**（算改动、要真 id），这张表则逼着加成员的人当场决定。
    """

    assert {kind: (kind.counts_as_pending, kind.holds_its_row) for kind in ListWriteKind} == {
        ListWriteKind.CREATE: (True, True),
        ListWriteKind.UPDATE: (True, True),
        ListWriteKind.DELETE: (True, True),
        ListWriteKind.AWAIT_ID: (False, False),
    }
    assert {
        wire: wire.addresses_an_id for wire in ListWire
    } == {
        ListWire.CREATE_PROJECT: False,  # POST /open/v1/project：URL 里没有 id
        ListWire.UPDATE_PROJECT: True,
        ListWire.DELETE_PROJECT: True,
        ListWire.NONE: False,  # 认领记录，从来不发
    }


def test_a_change_is_addressable_only_when_its_id_is_one_the_server_has_seen():
    """「发得出去吗」只有一处判断，而且读的是词表（#57 的检查 8 与检查 10）。"""

    def change(kind: ListWriteKind, list_id: str) -> PendingListChange:
        return PendingListChange(id=1, list_id=list_id, kind=kind, payload={}, created_at=T0)

    assert is_addressable(change(ListWriteKind.CREATE, "local-list-1")), "新建不带 id"
    assert is_addressable(change(ListWriteKind.UPDATE, "p1"))
    assert is_addressable(change(ListWriteKind.DELETE, "p1"))
    assert not is_addressable(change(ListWriteKind.UPDATE, "local-list-1"))
    assert not is_addressable(change(ListWriteKind.DELETE, "local-list-1"))
    assert not is_addressable(change(ListWriteKind.AWAIT_ID, "local-list-1"))


async def test_a_stale_delete_record_beside_a_later_rename_is_refused_too(store):
    """同族的另一条（#57）：陈旧**删除**记录 + 被复用的号，会把后来的改名变成对**错误真 id**
    的 UPDATE——服务端上另一条清单被改名，同样没有撤销。"""
    seed_record(
        store,
        list_id="local-list-1",
        kind=ListWriteKind.DELETE,
        payload={"sentName": "购物", "knownIds": []},
        name="购物",
    )
    seed_record(
        store,
        list_id="local-list-1",
        kind=ListWriteKind.UPDATE,
        payload={"name": "盐"},
        name="盐",
    )
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    with pytest.raises(DidaError):
        engine.update_list("local-list-1", name="糖")

    serve(transport, index=[project("p1", "购物")])
    await engine.refresh()
    await engine.push_pending()

    assert [url for url in requests_of(transport, "POST") if "/open/v1/project/" in url] == [], (
        "一个指向 p1 的改名都不许发出去"
    )
    assert rows_of(engine)["p1"] == "购物", "别人的清单一个字都不许动"
