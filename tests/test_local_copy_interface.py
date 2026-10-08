"""本地副本这条接缝的**接口本身**（工单 #85）。

在这之前声明的是 7 件、代码实际会调 11 件：多出来的那 4 件（``identify_list`` /
``amend_list_change`` / ``save_list`` / ``drop_list``）在 ``sync/`` 里从没声明过，只有真的那个
本地库实现过。后果不是「少写几行文档」，而是**第二个实现能通过入场检查**
（``isinstance(…, ListWriteTarget)`` 只查声明过的那几件），然后在三层调用深处炸掉。

这一层断的是**外部行为**：一个严格只实现「协议声明过的那几件」的替身，跑完整条清单写路径
（新建 → 推送 → 刷新认领 → 改名 → 删除）照样能跑通。声明少一件，这个替身就少一件，
红在它该红的地方——而不是在某个 ``AttributeError`` 的深处。

另一半是「这份副本会不会做某件事」：从前它是 13 处运行时探测、四种「做不到」的说法；现在它在
构造那一处定下来，做不到只有一种说法（:class:`~dida.sync.capabilities.MissingCapability`），
说清缺的是哪一件。只读的替身照旧**构造得起来**——「没有写入这件能力」是它的性质，不是错误。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import ListKind, MissingCapability, SyncEngine, ViewDefinition
from dida.sync.lists import ListWriteTarget
from dida.sync.refresh import RefreshTarget
from dida.sync.writes import WriteTarget
from dida.testing import FakeTransport, InMemorySource, ManualClock

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
    """排好一轮刷新的响应：先清单索引，再逐个清单的 data。"""
    transport.enqueue(httpx.Response(200, json=index))
    for payload in data:
        transport.enqueue(httpx.Response(200, json=payload))


def rows_of(engine: SyncEngine) -> dict[str, str]:
    """清单列表页上**真实清单**的「id → 名字」。"""
    return {row.id: row.name for row in engine.list_index() if row.kind is ListKind.LIST}


# ---------------------------------------------------------------------------
# 一个只实现「声明过的那几件」的替身
# ---------------------------------------------------------------------------


def declared_members(*protocols) -> set[str]:
    """这几个协议**声明过**的成员名（``dir`` 顺着 ``Protocol`` 的继承把 ``ViewSource`` 那几件也算上）。"""
    return {name for protocol in protocols for name in dir(protocol) if not name.startswith("_")}


def only_declared(real, *protocols):
    """一个成员表**严格等于**协议声明的那几件的替身。

    每一件都转发给真的本地库，所以行为是真的；但没声明过的成员**根本不存在**——声明少一件，
    这里就少一件。于是「代码实际会调、协议却没声明」的那些动词，一被调到就当场
    ``AttributeError``，而不是在深处炸。
    """
    members = declared_members(*protocols)
    namespace = {
        name: (
            lambda self, *args, _name=name, **kwargs: getattr(self._real, _name)(*args, **kwargs)
        )
        for name in members
    }
    namespace["__init__"] = lambda self, real: object.__setattr__(self, "_real", real)
    return type("OnlyDeclaredLocalCopy", (), namespace)(real)


# ---------------------------------------------------------------------------
# 验收 1：接口声明了代码实际会调的每一件（7 件与 11 件对齐）
# ---------------------------------------------------------------------------


async def test_a_second_implementation_that_only_declares_the_interface_runs_the_list_writes(store):
    """只按协议声明实现的那个替身，跑得完整条清单写路径（新建 / 推送 / 认领 / 改名 / 删除）。

    修之前那 4 件（``identify_list`` / ``amend_list_change`` / ``save_list`` / ``drop_list``）
    在 ``sync/`` 里从没声明过——这个替身因此没有它们，整条路会在第一次调用时红。
    """
    transport = FakeTransport(status_code=201)  # 建清单：201 Created，没有响应体（认领推迟到刷新）
    real = store
    engine = make_engine(only_declared(real, ListWriteTarget, WriteTarget, RefreshTarget), transport)

    # 新建 → 推成功但没有 id（认领记录：amend_list_change）。
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "建成功了：没有欠着服务端的东西"
    assert "购物" in rows_of(engine).values(), "本地先动：建完就看得见"

    # 还没认领时改名 → 并进那一条记录（amend_list_change）。
    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1, "改名还没出去——它要等一个服务端认得的 id"

    # 全量刷新拿到真 id → 认领（identify_list）。
    serve(
        transport,
        index=[project("p1", "购物")],
        data=[{"project": project("p1", "购物"), "tasks": []}, {"tasks": []}],
    )
    await engine.refresh()

    assert rows_of(engine).get("p1") == "买买买", "认领之后那一行叫真 id，名字是用户要的那个"

    # 改名推出去 → 把刚发出去的那一份盖回本地（save_list）。
    await engine.push_pending()

    assert engine.status().pending_count == 0, "认领之后那一笔发得出去，不是永远重试"
    assert rows_of(engine).get("p1") == "买买买"

    # 再建一条、推成功但没认领，然后删掉它 → 本地摘掉那一行（drop_list）。
    doomed = engine.create_list("临时")
    await engine.wait_for_pushes()
    engine.delete_list(doomed)

    assert "临时" not in rows_of(engine).values(), "删掉的清单当场从屏幕上消失"


def test_the_declared_interface_names_every_verb_the_write_paths_call():
    """协议声明的那几件必须**覆盖**写路径真的会调的那几件（工单 #85 的 7 件与 11 件）。

    上一条是行为上的证明（替身跑得通）；这一条把话说明白：那 4 件确实在声明里。
    """
    declared = declared_members(ListWriteTarget)

    assert {
        "identify_list",
        "amend_list_change",
        "save_list",
        "drop_list",
    } <= declared, "写路径会调的这几件必须在 ListWriteTarget 上声明过"


# ---------------------------------------------------------------------------
# 验收 2：只读替身也能通过构造
# ---------------------------------------------------------------------------


def test_a_read_only_double_constructs_and_reads():
    """只读替身（``InMemorySource`` 那种）构造得起来，读路径照常（工单 #85 新增的这一条）。

    「没有写入这件能力」是它的**性质**，不是错误：能力在构造那一处定下来，读路径读到的是
    它有的那几件。全仓库原来只有一条「替身是否合格」的断言，这一条补上另一头。
    """
    source = InMemorySource()
    source.add_task("写周报", list_name="工作")

    engine = SyncEngine(clock=ManualClock(T0), source=source)

    assert "工作" in [row.name for row in engine.list_index()]
    assert [item.title for item in engine.tasks_in("工作").items] == ["写周报"]
    assert engine.status().pending_count == 0
    assert engine.read_model() is not None
    detail = engine.task_detail("t1")
    assert detail is not None and detail.title == "写周报", "只读替身存得下原文就该读得到（#33 的详情形状）"


# ---------------------------------------------------------------------------
# 验收 3：「做不到」只有一种说法，说清缺的是哪一件
# ---------------------------------------------------------------------------


def test_a_read_only_double_refuses_every_write_with_the_same_story():
    """只读替身上每一条写路径都拒，而且拒得**一模一样**：同一个类型、说清缺本地存储。"""
    engine = SyncEngine(clock=ManualClock(T0), source=InMemorySource())

    refusals = (
        lambda: engine.write("t1", changes={"title": "写周报"}),
        lambda: engine.create("写周报", list_id="inbox1"),
        lambda: engine.delete("t1"),
        lambda: engine.complete("t1"),
        lambda: engine.uncomplete("t1"),
        lambda: engine.defer("t1"),
        lambda: engine.move_task("t1", to_list_id="work"),
        lambda: engine.reschedule("t1", due=None),
        lambda: engine.create_list("购物"),
        lambda: engine.update_list("work", name="工作"),
        lambda: engine.delete_list("work"),
    )

    for refuse in refusals:
        with pytest.raises(MissingCapability) as caught:
            refuse()
        assert "本地存储" in str(caught.value), "说清缺的是哪一件"


def test_a_copy_without_a_view_store_refuses_the_view_writes_with_the_same_story(store):
    """一个会写清单、但不存视图的副本：视图那三条写路径也拒得一模一样。

    只读替身（``InMemorySource``）**存得下视图**，所以视图这一格的拒绝要另一份副本才试得到：
    这里用「只实现声明过的那几件」的那个替身（它没有 ``ViewStore`` 那五件）。
    """
    engine = SyncEngine(
        clock=ManualClock(T0),
        source=only_declared(store, ListWriteTarget, WriteTarget, RefreshTarget),
    )

    definition = ViewDefinition(id="", name="高优先级", priorities=(5,))

    for refuse in (
        lambda: engine.create_view(definition),
        lambda: engine.update_view(definition),
        lambda: engine.delete_view("v1"),
    ):
        with pytest.raises(MissingCapability) as caught:
            refuse()
        assert "本地存储" in str(caught.value), "说清缺的是哪一件"

    assert engine.view_definition("v1") is None, "读不到视图是「没有这个视图」，不是错误"


async def test_a_write_engine_without_a_client_refuses_with_the_same_story(store):
    """没有 API 客户端时，每一条要网络的路径也拒得一模一样：同一个类型、说清缺客户端。"""
    engine = SyncEngine(clock=ManualClock(T0), source=store)

    with pytest.raises(MissingCapability) as refreshing:
        await engine.refresh()
    with pytest.raises(MissingCapability) as pushing:
        await engine.push_pending()
    with pytest.raises(MissingCapability) as tags:
        await engine.load_tags()
    with pytest.raises(MissingCapability) as completed:
        await engine.refresh_completed()

    for caught in (refreshing, pushing, tags, completed):
        assert "API 客户端" in str(caught.value), "说清缺的是哪一件"


def test_a_missing_local_copy_refuses_with_the_same_story():
    """一个本地副本都没接上时，写与刷新也是同一种说法。"""
    engine = SyncEngine(clock=ManualClock(T0))

    with pytest.raises(MissingCapability) as writing:
        engine.write("t1", changes={"title": "写周报"})

    assert "本地存储" in str(writing.value)


async def test_refresh_without_a_local_copy_refuses_with_the_same_story():
    """刷新缺本地副本时同样是那一种说法。"""
    engine = SyncEngine(clock=ManualClock(T0), source=InMemorySource())

    with pytest.raises(MissingCapability) as caught:
        await engine.refresh()

    assert "本地存储" in str(caught.value)
