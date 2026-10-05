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
from dida.sync.engine import INBOX_ID, SyncEngine
from dida.sync.writes import LOCAL_TASK_PREFIX, UnclaimedTaskError
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
        list_id=INBOX_ID,
        due=at(15, 15, 0),
        all_day=False,
        priority=5,
        tags=["工作"],
    )

    local = store.task_payload(local_id)
    assert local is not None, "本地当场就要看得见（网络不是这一屏的前置条件）"
    assert local["title"] == "交季度报告"
    assert local["projectId"] == "inbox", "落点就是交进来的那个清单"

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

    engine.create("还信用卡", list_id=INBOX_ID, due=at(14, 0, 0), all_day=True)
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

    engine.create("交季度报告", list_id=INBOX_ID, due=at(15, 15, 0), all_day=False)
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

    local_id = engine.create("买牛奶", list_id=INBOX_ID, due=at(15, 0, 0), all_day=True)
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


# ---------------------------------------------------------------- 新建的落点（#39）


async def test_a_create_can_land_in_a_named_list_not_the_inbox(store):
    """落点是参数：在「工作」里建就落在「工作」（工单 #39，用户故事 45）。

    ``projectId`` 在新建上是**必填**（``openapi-dida365.md:222``），示例发的就是一个真实
    清单 id（``:263``）；客户端本来就是透传（``api/client.py`` 的 ``create_task``）。写死
    收集箱是**仓库这一侧**的选择——就是 ``sync/create.py`` 里那一行，这一票改的正是它。
    """
    seed(store)
    transport = FakeTransport(json={"id": "srv-7", "projectId": "work", "title": "写周报"})
    engine = make_engine(store, transport)

    local_id = engine.create("写周报", list_id="work")

    assert store.task_payload(local_id)["projectId"] == "work", "本地那条也落在「工作」里"

    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task")
    assert transport.last_json == {"title": "写周报", "projectId": "work"}, (
        "没写日期就不许出现 dueDate / isAllDay（只写用户真的写了的字段）"
    )
    assert store.pending() == ()
    assert store.task_payload("srv-7")["projectId"] == "work"
    assert [item.id for item in store.tasks()] == ["srv-7"]


async def test_a_create_from_a_view_that_implies_a_date_carries_that_date(store):
    """视图隐含的日期（「今天」）：在视图里建就是「收集箱 + 今天」这一条（#39、用户故事 35）。

    ``tasks_in()`` 的读模型给的就是这一条：视图不是容器（``shows_list_name=True``），而
    「今天」隐含**当前逻辑日**那个日期（``implied_due``），写法是全天任务的日期标记。
    这一层因此不必自己认识哪个视图叫什么——它照读模型给的那一份拼请求。
    """
    seed(
        store,
        task(id="t1", title="今天要做的", dueDate="2026-03-14T18:00:00+0800", project_id="inbox"),
        lists=[inbox()],
    )
    transport = FakeTransport(
        json={
            "id": "srv-8",
            "projectId": "inbox",
            "title": "随手记一笔",
            "dueDate": "2026-03-14T00:00:00+0800",
            "isAllDay": True,
        }
    )
    engine = make_engine(store, transport)

    today = engine.tasks_in("today")
    assert today.shows_list_name is True, "视图不是容器（哪一份判断在这一处）"
    assert today.implied_due == T0.replace(hour=0, minute=0, second=0, microsecond=0)

    engine.create("随手记一笔", list_id=INBOX_ID, due=today.implied_due, all_day=True)
    await engine.wait_for_pushes()

    assert transport.last_json == {
        "title": "随手记一笔",
        "projectId": "inbox",
        "dueDate": "2026-03-14T00:00:00+0800",
        "isAllDay": True,
    }


async def test_a_create_from_a_view_without_a_date_carries_no_date(store):
    """「最近七天」是**一段**窗口，藏不进一个日期里：那里建的新任务不带日期。"""
    seed(store, lists=[inbox()])

    engine = make_engine(store, FakeTransport())

    assert engine.tasks_in("today").implied_due is not None
    assert engine.tasks_in("next7").implied_due is None, (
        "挑窗口里任何一天当「隐含日期」都是替用户做一个他没做的决定"
    )
    assert engine.tasks_in("all").implied_due is None
    assert engine.tasks_in("work").implied_due is None, "真实清单不隐含日期"


# ---------------------------------------------------------------- 新建之后接着改（#53）


async def test_a_change_queued_behind_a_create_follows_it_to_the_real_id(store):
    """推成功认领服务端 id 时，**排在它后面的那笔改动也跟着挪过去**（#53，与 #42 的清单版同形）。

    不挪的实测后果（#42 在清单那条路上量过）：那笔改动的 ``task_id`` 仍然指向 ``local-…``
    ——服务端从没见过这个 id，于是它 POST 到 ``/open/v1/task/local-…``、404、退避重试、
    **永远出不了队**：状态栏那个数一直非零，用户的编辑永远到不了服务端。
    ``UnknownTaskError`` 那条注释说的「永远推不出去的改动」就是这一类。
    """
    transport = FakeTransport(json={"id": "srv-1", "projectId": "work", "title": "写周报"})
    seed(store)
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)
    transport.enqueue(NetworkError("断网"))  # 建的那一笔先失败，停在队列里

    local_id = engine.create("写周报", list_id="work")
    await engine.wait_for_pushes()

    # 排在它后面的那笔改动。走存储层的公开入口入队，而不是 ``engine.write``：写入那一侧
    # 自己就挡着这条路（#53 的第一道，下一条测试钉它），这里要复现的是**队列里已经有一笔**
    # 指向临时 id 的改动（#42 在清单那条路上量到的形状），好让认领那一步去处理它。
    store.enqueue(
        task_id=local_id,
        kind=ChangeKind.UPDATE,
        payload={"title": "写周报（改）"},
        now=clock.now(),
        list_id="work",
    )

    assert [change.task_id for change in store.pending()] == [local_id, local_id], (
        "两笔都指向临时 id"
    )
    assert local_id.startswith(LOCAL_TASK_PREFIX), "认领之前它是本地临时 id"

    clock.advance(timedelta(seconds=2))  # 退避到点，轮到这一笔了
    await engine.push_pending()

    assert store.pending() == (), "认领之后队列该清空（那笔改动跟着挪到真 id 上了）"
    assert store.pending_count() == 0
    assert engine.status().pending_count == 0
    assert [item.id for item in store.tasks()] == ["srv-1"], "本地那一条落到服务端给的 id 上"

    # 只挪队列里的 id 还不够（#53 的第 2 件）：那一笔得**真的**打到真 id 上。请求 URL 是最
    # 外面那份证据——``local-…`` 还出现在里面，走的就是 404 那条路；而只重读队列、不挪 id
    # 的话，这里发出去的仍然是临时 id。
    urls = [str(request.url) for request in transport.requests]
    assert urls == [
        # 第一次新建：断网，失败退避（这一笔也是 POST 到集合端点——它的 URL 里没有 id）
        "https://api.dida365.com/open/v1/task",
        # 退避到点，新建推成功
        "https://api.dida365.com/open/v1/task",
        # 排在后面那笔改动：真 id，不是 local-…
        "https://api.dida365.com/open/v1/task/srv-1",
    ], "新建 POST 到集合端点；那笔改动 POST 到服务端给的真 id，不是临时 id"
    assert transport.last_json["title"] == "写周报（改）", "用户改的那一份真的发出去了"

    # 已知边界（不是本票修的那一件事，报给编排者）：本地那份的标题仍是服务端建它时回的
    # 那一个。认领是「把本地那条挪到真 id 上」，而挪过去的那一份取自**新建当时的**原文
    # 与服务端响应的合并——排在后面那笔改动的本地效果在临时 id 那一行上，没跟着走。
    # 下一次全量刷新会由服务端权威把它拉正（服务端那份**是**改过的）。修法属于 #54 说的
    # 「把改动并进那条还没成真的新建」那一类，本票不自己发明第二套。


async def test_an_edit_after_an_unclaimed_create_is_refused_not_queued(store):
    """新建还没被认领（``201`` 空 body ⇒ 认领根本没发生）时，后来的改**不许排队**（#53）。

    这是 #53 的第二半，有出处：``POST /open/v1/task`` 的响应表里 200 带 body、**201 无
    content 两条都写着**（``openapi-dida365.md`` 的 Create Task 一节），而
    ``_adopt_created`` 在没有 id 时直接提前返回——于是那一笔排在临时 id 上的改动永远停在
    ``local-…``，POST 到服务端没见过的 id 上、404、退避重试、**永远出不了队**，而用户的
    编辑永远到不了服务端。

    这条路径**会自愈**（下一次全量刷新带回真 id 那一行、剪掉临时那一行），所以诚实的回答
    是「等这一步同步完」，而不是安静地排一条永远失败的改动。完整机制归 #54；这里钉的是
    最小的那一版：拒绝 + 一个字都不入队。
    """
    transport = FakeTransport(json={"id": "srv-1", "projectId": "work", "title": "写周报"})
    seed(store)
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)
    transport.enqueue(NetworkError("断网"))

    local_id = engine.create("写周报", list_id="work")
    await engine.wait_for_pushes()

    clock.advance(timedelta(seconds=2))  # 那一笔退避到点了
    transport.enqueue(httpx.Response(201))  # 服务端只回一个空 body
    assert await engine.push_pending() == 1, "新建那一笔算推成功了"

    assert store.task_payload(local_id) is not None, "本地那条还挂着临时 id（认领没发生）"

    with pytest.raises(UnclaimedTaskError):
        engine.write(local_id, changes={"title": "写周报（改）"})

    assert [change.kind for change in store.pending()] == [], (
        "被拒绝的改动一个字都不许入队——排进去就是一条永远推不出去的改动"
    )
    assert engine.status().pending_count == 0, "待推送数回到 0"


async def test_a_completion_after_an_unclaimed_create_is_refused_too(store):
    """完成 / 删除 / 顺延走的是同一个入口，所以同一句话对它们一起成立。"""
    transport = FakeTransport(json={"id": "srv-1", "projectId": "work", "title": "写周报"})
    seed(store)
    clock = ManualClock(T0)
    engine = make_engine(store, transport, clock=clock)
    transport.enqueue(NetworkError("断网"))

    local_id = engine.create("写周报", list_id="work")
    await engine.wait_for_pushes()
    clock.advance(timedelta(seconds=2))
    transport.enqueue(httpx.Response(201))
    await engine.push_pending()

    with pytest.raises(UnclaimedTaskError):
        engine.complete(local_id)

    assert store.pending() == ()
    assert "status" not in store.task_payload(local_id), (
        "拒绝就是拒绝：本地一个字都不许写下去（新建那份原文里本来就没有 status——"
        "它只带用户写下、与守卫要求的那几个字段）"
    )


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
