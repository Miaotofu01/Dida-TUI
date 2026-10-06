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
from dida.sync.engine import ListKind, ListRow, SyncEngine, TaskItem
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


def list_rows(engine: SyncEngine) -> list[ListRow]:
    """库里那几行**真实清单**（收集箱置顶），顺序与未完成条数照清单索引给的那一份。

    v1 的 ``view().lists`` 问的是同一句话（左栏那几行）；#58 把那条读路径删掉之后改问 v2 的
    清单索引——索引里还夹着内置视图与自定义视图那几行，所以这里按 ``ListKind.LIST`` 滤一道。
    """
    return [row for row in engine.list_index() if row.kind is ListKind.LIST]


def open_items(engine: SyncEngine) -> list[TaskItem]:
    """屏幕上「未完成」那一段的行：每个真实清单的成员，按清单索引的顺序。

    v1 的 ``view().groups`` 问的是同一句话（读路径上的成品行），只是它硬编码了三个分区。
    这些测试摆的任务都没有未来截止的，两种问法在这些断言上逐字相同。
    """
    return [item for row in list_rows(engine) for item in engine.tasks_in(row.id).items]


class PagedProjectServer:
    """**会真的分页**的假服务端（#41）。

    与 :class:`~dida.testing.FakeTransport` 的差别正是这个假服务端的全部意义：那个按队列
    回放，给什么就是什么，翻不翻页它都照给；这一个照文档办事——``offset``/``limit`` 切片，
    而**不给分页参数就按 200 的默认上限截断**（真实服务端的默认，见 notes/openapi-dida365.md
    的 ``GET /open/v1/project``）。一条不翻页的读路径因此真的会拿丢第 201 条起的东西。

    收集箱不在 ``GET /open/v1/project`` 里（ADR-0001）：索引没给过的清单，它的 data 就只回
    ``tasks``，不回 ``project``。
    """

    DEFAULT_LIMIT = 200
    """不给分页参数时服务端的默认上限（文档：给了任一参数才默认 200，这里照真实行为建模）。"""

    def __init__(self, projects: list[dict], tasks: dict[str, list[dict]] | None = None) -> None:
        self.projects = list(projects)
        self.tasks = dict(tasks or {})
        self.index_requests: list[httpx.Request] = []
        """收到过的清单索引请求，按顺序（翻页几页就有几条）。"""

        self.data_requests: list[str] = []
        """收到过的 ``.../data`` 请求的清单 id，按顺序。"""

    @property
    def index_pages(self) -> int:
        """清单索引被请求了几页。"""
        return len(self.index_requests)

    async def send(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.path == "/open/v1/project":
            self.index_requests.append(request)
            offset = int(url.params.get("offset") or 0)
            limit = int(url.params.get("limit") or self.DEFAULT_LIMIT)
            return httpx.Response(200, json=self.projects[offset : offset + limit])
        project_id = url.path.split("/")[4]
        self.data_requests.append(project_id)
        payload: dict = {"tasks": self.tasks.get(project_id, [])}
        known = next((item for item in self.projects if item["id"] == project_id), None)
        if known is not None:
            payload["project"] = known
        return httpx.Response(200, json=payload)


async def test_the_project_index_is_paged_until_a_short_page(store):
    """清单索引超过服务端一页的上限时要翻页拿全（#41 的第一条验收标准）。

    250 个清单、服务端一页 200 条：不翻页的读路径只会拿到前 200 个，第 201 个起**永远
    看不见**，而且不报错。响应是一个没有 total 的裸数组，所以「还有没有下一页」只能靠
    「这一页拿满了没有」推断。
    """
    server = PagedProjectServer(
        projects=[
            project(id=f"p{n}", name=f"清单{n}", sort_order=n + 1) for n in range(250)
        ],
        tasks={"p249": [task(id="deep", title="第 250 个清单里的任务", project_id="p249")]},
    )
    engine = make_engine(store, server)

    await engine.refresh()

    assert len(store.lists()) == 250, "第 201 个清单起不许被截断"
    assert [row.name for row in list_rows(engine)][-1] == "清单249"
    assert store.task_payload("deep") is not None, "翻页才看得见的那个清单里的任务也要在"
    assert server.index_pages == 2
    assert server.index_requests[0].url.params.get("limit") == "200"


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
        "https://api.dida365.com/open/v1/project?offset=0&limit=200",
        "https://api.dida365.com/open/v1/project/inbox/data",
        "https://api.dida365.com/open/v1/project/work/data",
    ]
    assert (report.written_lists, report.written_tasks) == (2, 1)
    assert [(row.name, row.unfinished) for row in list_rows(engine)] == [
        ("收集箱", 0),
        ("工作", 1),
    ]


async def test_the_second_identical_refresh_writes_nothing(store):
    """头条：同一份数据拉第二次，本地库零写入——「刷新」与「刷新且屏幕不闪」的区别。

    证明用的是 ``apply_refresh`` 自己给的 ``RefreshReport``（t08 的口径），不是去翻
    数据库内部：报告整份为空，就说明清单、任务、覆盖、豁免四样都没动。**剪枝（#41）也算
    在里面**：``RefreshReport`` 多出 ``pruned_lists`` / ``pruned_tasks`` 之后，这一句
    同时钉住了「没东西可剪的时候一个也不剪」。
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
    只有列清单 + 每个清单恰好一次 data，没有别的路径。列清单那一次带的是**翻页参数**
    （#41 的第一条，取全清单索引用的），逐清单的 data 一次不带任何查询串。
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
        ("GET", "https://api.dida365.com/open/v1/project?offset=0&limit=200"),
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
    assert open_items(engine) == [], "库里一条任务都没有，屏幕上也就没有行"


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


async def test_the_inbox_is_never_pruned_even_when_the_index_never_mentions_it(store):
    """收集箱不在清单索引里，所以「索引没提它」不等于「服务端没有它」（#41 的第四条）。

    ``GET /open/v1/project`` **不包含收集箱**（ADR-0001），它是默认清单。照「索引里没有的
    就剪掉」办，第一次刷新就会把收集箱从本地库里删掉：左栏少一格、随手记的任务无处可去，
    而且不报错。
    """
    server = PagedProjectServer(
        projects=[inbox(), project()],
        tasks={"inbox": [task(id="t9", project_id="inbox", title="随手记")]},
    )
    engine = make_engine(store, server)
    await engine.refresh()

    # 第二次：索引照真实服务端的样子只回工作清单，而收集箱的 data 根本没有 project 字段。
    index_only = PagedProjectServer(projects=[project()])
    engine = make_engine(store, index_only)

    report = await engine.refresh()

    assert [item.name for item in store.lists()] == ["收集箱", "工作"]
    assert [row.name for row in list_rows(engine)] == ["收集箱", "工作"]
    assert report.pruned_lists == 0


async def test_the_client_added_inbox_row_survives_a_prune_that_cannot_re_add_it(store):
    """#33 补出来的收集箱那一行，不许被 #41 的剪枝删掉——两张工单的交界处。

    实测（#33）：收集箱不在 ``GET /open/v1/project`` 的索引里，它的 data 也**没有**
    ``project`` 对象，真实 id（``inbox`` 加一截数字）只出现在它的任务上。所以那一行只能由
    客户端自己补。而 #41 的清单剪枝按「索引里没有的就删」办事：两条合起来，客户端刚补出来
    的收集箱会在同一次刷新里被删掉——左栏少一格、随手记的任务无处可去，而且不报错。

    第二趟故意让这一行**补不出来**（收集箱空了：没有 ``project`` 对象，也没有任务带着那个
    id）：这一趟的剪枝名单里根本没有它，只有「它是收集箱」这一条豁免挡得住。
    ``pruned_tasks == 1`` 说明这趟剪枝真的执行了，所以 ``pruned_lists == 0`` 与那一行的存活
    都不是「什么都没剪」的空话。
    """
    server = PagedProjectServer(
        projects=[project()],
        tasks={"inbox": [task(id="t9", project_id="inbox1234567890", title="随手记")]},
    )
    engine = make_engine(store, server)

    first = await engine.refresh()

    records = {row.id: row for row in store.list_records()}
    assert records["inbox1234567890"].is_inbox is True, "补出来的那一行要落库、要标成收集箱"
    assert first.pruned_lists == 0, "自己刚补出来的那一行不是「远端已删」"

    server.tasks["inbox"] = []
    second = await engine.refresh()

    assert "inbox1234567890" in {row.id for row in store.list_records()}, (
        "索引里没有它、这一趟也补不出它——只有 is_inbox 这条豁免挡得住剪枝"
    )
    assert second.pruned_lists == 0
    assert second.pruned_tasks == 1, "剪枝确实跑了（收集箱里那条未完成的任务没了）"
    assert engine.list_index()[0].id == "inbox1234567890"
    assert engine.list_index()[0].is_inbox is True


async def test_a_second_identical_refresh_with_the_learned_inbox_row_writes_and_prunes_nothing(
    store,
):
    """收集箱那一行也走「只写变化」：第二趟同样的数据，零写入、零剪枝（#41 + #33）。

    引擎把这一行算出来（id 取服务端那一串、名字用客户端的叫法、标成收集箱），第二趟必须
    一模一样——不然每一趟刷新都会重写它：``written_lists`` 不为 0，界面就要闪，而剪枝也会
    跟着报出一次「删了又加」。这条正是「引擎补的行」与「只写变化」的交界处。
    """
    server = PagedProjectServer(
        projects=[project()],
        tasks={"inbox": [task(id="t9", project_id="inbox1234567890", title="随手记")]},
    )
    engine = make_engine(store, server)

    first = await engine.refresh()
    second = await engine.refresh()

    assert (first.written_lists, first.pruned_lists) == (2, 0), "第一趟：工作清单 + 补出来的收集箱"
    assert (second.written_lists, second.written_tasks) == (0, 0)
    assert (second.pruned_lists, second.pruned_tasks) == (0, 0)
    assert engine.list_index()[0].id == "inbox1234567890"


async def test_a_task_deleted_remotely_disappears_from_the_local_library(store):
    """远端已经没有的任务，刷新之后不许留在本地库里（#41 的第三条验收标准）。

    落库原先只做插入与更新、从不删除，所以用户在手机上删掉的任务会永远留在本地：屏幕上
    看得见一条服务端已经不存在的任务，而且刷新多少次都在。
    """
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
        data=[data(inbox()), data(project(), [task(id="t2", title="买牛奶")])],
    )
    engine = make_engine(store, transport)
    await engine.refresh()

    report = await engine.refresh()

    assert [item.id for item in store.tasks()] == ["t2"]
    assert report.pruned_tasks == 1
    assert report.written_tasks == 0, "留下那条没变，一个字节都不写"
    assert [item.title for item in engine.tasks_in("work").items] == ["买牛奶"]


async def test_a_list_deleted_remotely_disappears_from_the_library(store):
    """远端已经没有的清单，刷新之后不许留在本地库里（#41 的第四条验收标准的前半）。

    「还在本地库里」就是「还在清单列表页上」：清单页读的是本地那份索引。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[inbox(), project()],
        data=[data(inbox()), data(project(), [task()])],
    )
    serve(transport, index=[inbox()], data=[data(inbox())])
    engine = make_engine(store, transport)
    await engine.refresh()

    report = await engine.refresh()

    assert [item.name for item in store.lists()] == ["收集箱"]
    assert [row.name for row in list_rows(engine)] == ["收集箱"]
    assert report.pruned_lists == 1


async def test_a_task_with_an_unpushed_change_is_never_pruned(store):
    """剪枝不误删待推送改动对应的任务（#41 的第五条验收标准）。

    两类都在这里：改过还没推上去的老任务（服务端这次没给），和刚刚在本地新建、服务端
    根本还没见过的任务（临时 id）。剪掉任何一个都等于把用户刚做的操作悄悄撤销。
    """
    transport = FakeTransport()
    index = [inbox(), project()]
    serve(transport, index=index, data=[data(inbox()), data(project(), [task(id="t1")])])
    engine = make_engine(store, transport)
    await engine.refresh()

    store.enqueue(
        task_id="t1", kind=ChangeKind.UPDATE, payload={"title": "写周报（我改的）"}, now=T0
    )
    store.enqueue(
        task_id="local-new",
        kind=ChangeKind.CREATE,
        payload={"title": "随手记", "projectId": "inbox"},
        now=T0,
        list_id="inbox",
    )

    serve(transport, index=index, data=[data(inbox()), data(project(), [])])
    report = await engine.refresh()

    assert store.pending_count() == 2, "两条改动都还在队列里"
    assert store.task_payload("t1")["title"] == "写周报（我改的）"
    assert store.task_payload("local-new")["title"] == "随手记"
    assert [item.title for item in engine.tasks_in("work").items] == ["写周报（我改的）"]
    assert [item.title for item in engine.tasks_in("inbox").items] == ["随手记"]
    assert report.pruned_tasks == 0


async def test_a_refresh_that_deletes_several_remote_records_keeps_unrelated_local_changes(store):
    """一次刷新里删掉多条远端记录时，不会把未受影响的本地改动一起清掉（#41 的第六条）。

    这一趟远端删掉了一整个清单（家里的买牛奶那条就挂在那张清单下）与另一个清单里的一条
    任务；本地那条买牛奶上有一笔还没推成功的标题改动。删归删，那笔改动与它那条任务都要
    原封不动：队列里的行不能少一条、也不能被换成新的一行（那等于把用户的操作重来一遍）。
    """
    transport = FakeTransport()
    home = project(id="home", name="生活", sort_order=2)
    serve(
        transport,
        index=[inbox(), project(), home],
        data=[
            data(inbox()),
            data(project(), [task(id="t1")]),
            data(home, [task(id="t2", title="买牛奶", project_id="home")]),
        ],
    )
    engine = make_engine(store, transport)
    await engine.refresh()

    store.enqueue(
        task_id="t2", kind=ChangeKind.UPDATE, payload={"title": "买牛奶（我改的）"}, now=T0
    )
    queued = store.pending()

    serve(transport, index=[inbox(), project()], data=[data(inbox()), data(project(), [])])
    report = await engine.refresh()

    assert [item.id for item in store.lists()] == ["inbox", "work"], "只剩远端还有的清单"
    assert [item.id for item in store.tasks()] == ["t2"], "远端没有的剪掉，有本地改动的那条留住"
    assert store.pending() == queued, "队列里那一笔原封不动"
    assert store.task_payload("t2")["title"] == "买牛奶（我改的）"
    # 清单被剪掉之后，这条任务暂时刻画不出来：v2 的读形状按 container_id 取成员，而容器行
    # 已经不在了（#41 已知的后果，notes/brief.md 记着，不是这里要顺手补的洞）。留住的是本地
    # 那一份与待推送的改动——用户的操作没有被撤销；清单回来、或者那一笔推上去，它就回屏幕上。
    assert engine.tasks_in("home").items == ()
    assert report.pruned_lists == 1
    assert report.pruned_tasks == 1


async def test_a_refresh_after_a_prune_writes_and_prunes_nothing(store):
    """剪枝会收敛：剪完之后的下一趟彻底空转，报告整份为空（#41 不许破坏 ADR-0001 的只写变化）。

    这件事直接长在界面上：每一趟都写点什么（哪怕只是「再删一次已经没有的东西」）的话，
    左栏与光标每刷新一次就抖一次。
    """
    transport = FakeTransport()
    index = [inbox(), project()]
    kept = [data(inbox()), data(project(), [task(id="t2", title="买牛奶")])]
    serve(
        transport,
        index=index,
        data=[data(inbox()), data(project(), [task(id="t1"), task(id="t2", title="买牛奶")])],
    )
    serve(transport, index=index, data=kept)
    serve(transport, index=index, data=kept)
    engine = make_engine(store, transport)
    await engine.refresh()

    first = await engine.refresh()
    after_the_prune = await engine.refresh()

    assert (first.pruned_tasks, first.pruned_lists) == (1, 0)
    assert after_the_prune == RefreshReport()


async def test_an_index_that_never_advances_is_a_structured_error_not_a_hang(store):
    """一页拿满就一直问下一页：服务端要是压根不认 ``offset``，得报错，不能死循环。

    代理不认查询串时就是这个样子：每一页都把第一页原样再给一遍。客户端不能一直问下去
    （挂死、内存一路上涨，屏幕上一句解释都没有）。清单 id 在索引里不会重复，所以「这一页
    的 id 见过了」就是它没有前进的证据。
    """

    class StuckIndexServer(PagedProjectServer):
        """分页参数被无视的服务端：每一页都给同一份。

        问到第 6 页还不停就当场炸：真挂死的话这个测试会一直转下去，那不是一个好的红灯。
        """

        async def send(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/open/v1/project":
                self.index_requests.append(request)
                assert self.index_pages <= 5, "索引问了 5 页还没停：翻页没有前进"
                return httpx.Response(200, json=self.projects[: self.DEFAULT_LIMIT])
            return await super().send(request)

    server = StuckIndexServer(
        projects=[project(id=f"p{n}", name=f"清单{n}", sort_order=n + 1) for n in range(200)]
    )
    engine = make_engine(store, server)

    with pytest.raises(MalformedResponseError):
        await engine.refresh()

    assert store.lists() == ()


async def test_a_failed_index_page_writes_and_prunes_nothing(store):
    """索引翻到一半失败：整份刷新失败，本地库一动不动——**尤其是一行都不剪**（#41）。

    没翻完的索引是一份**残缺**的清单集合。它要是落了库、或者照它剪了枝，第 201 个清单起
    连同里面的任务就会被当成「远端已经删掉了」而消失，而且不报错。所以翻页的每一页失败都
    照旧往上抛：取数没取全就绝不落库（ADR-0001 的全有全无）。
    """
    transport = FakeTransport()
    index = [inbox(), project()]
    serve(transport, index=index, data=[data(inbox()), data(project(), [task()])])
    engine = make_engine(store, transport)
    await engine.refresh()
    cached = (store.lists(), store.tasks())

    # 第一页拿满 200 条（于是「可能还有下一页」），要第二页时服务端 500。
    transport.enqueue(
        httpx.Response(
            200, json=[project(id=f"p{n}", name=f"清单{n}", sort_order=n + 1) for n in range(200)]
        )
    )
    transport.enqueue(httpx.Response(500, json={"error": "boom"}))

    with pytest.raises(ServerRejectionError):
        await engine.refresh()

    assert (store.lists(), store.tasks()) == cached, "没取全的索引连一行都不许落，更不许照它剪"


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

    assert [item.title for item in engine.tasks_in("work").items] == ["写周报（我改的）"]
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
    assert [item.title for item in engine.tasks_in("work").items] == ["写周报（我改的）"]
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
        "https://api.dida365.com/open/v1/project?offset=0&limit=200",
        "https://api.dida365.com/open/v1/project/work/data",
        "https://api.dida365.com/open/v1/project/inbox/data",
    ]
    assert (report.written_lists, report.written_tasks) == (2, 1)
    assert [(row.name, row.unfinished) for row in list_rows(engine)] == [
        ("收集箱", 1),
        ("工作", 0),
    ]


async def test_refresh_learns_the_inbox_id_from_the_payload_and_adds_that_row_itself(store):
    """收集箱那一行由客户端补，id 是**服务端返回**的那一串（#33，实测事实 #2）。

    实测：``GET /open/v1/project/inbox/data`` 的响应里没有 ``project`` 对象，收集箱的真实 id
    只在它的任务上（``projectId`` 形如 ``inbox`` 加数字）。所以「客户端补一行」只能这么做：
    从这批任务里认出那个 id，用它当行 id——字面量 ``"inbox"`` 不是身份，拿它归类一条都对不上。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[project()],
        data=[
            data(project(), []),
            {"tasks": [task(id="t9", project_id="inbox1234567890", title="随手记")]},
        ],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    rows = engine.list_index()
    assert rows[0].is_inbox is True, "收集箱置顶"
    assert rows[0].id == "inbox1234567890"
    assert rows[0].name == "收集箱"
    assert rows[0].unfinished == 1
    assert "inbox" not in {row.id for row in rows}
    assert [item.title for item in engine.tasks_in("inbox1234567890").items] == ["随手记"]
    assert {row.id: row for row in store.list_records()}["inbox1234567890"].is_inbox is True, (
        "这一行要落库：下次启动（还没刷新时）也认得出收集箱"
    )


async def test_a_missing_project_id_is_filled_with_the_server_id_not_the_literal(store):
    """服务端漏写 ``projectId`` 时，补的是**服务端返回的那个 id**，不是请求侧别名。

    同一批任务里就有那个 id（收集箱的真实 id 只在任务上），用它补，这条任务才归得进收集箱。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[project()],
        data=[
            data(project(), []),
            {
                "tasks": [
                    task(id="t9", project_id="inbox1234567890", title="随手记"),
                    {"id": "t10", "title": "服务端漏了 projectId", "status": 0},
                ]
            },
        ],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    assert store.task_payload("t10")["projectId"] == "inbox1234567890"
    assert {item.title for item in engine.tasks_in("inbox1234567890").items} == {
        "随手记",
        "服务端漏了 projectId",
    }


async def test_a_missing_project_id_is_not_filled_with_the_literal_inbox(store):
    """整批都没有可认的 id 时，缺失的 ``projectId`` 就**空着**——绝不写字面量 ``inbox``。

    写下去的那个字面量与收集箱的真实 id 一条都对不上（左栏徽标会是 0、清单名也对不上），
    而且会让深链的兜底分支永远不可达。#33 明说要去掉的就是这个猜。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[project()],
        data=[
            data(project(), []),
            {"tasks": [{"id": "t10", "title": "不知道在哪个清单", "status": 0}]},
        ],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    assert "projectId" not in (store.task_payload("t10") or {})
    assert store.tasks()[0].list_id == ""


async def test_the_inbox_row_from_a_project_object_is_marked_as_the_inbox(store):
    """收集箱的 data 里带 ``project`` 对象时照它写一行，并**标成收集箱**。

    ``Project`` 定义里没有「我是收集箱」这种字段（api-contracts.md 的字段表里没有），所以
    「这一行是收集箱」只有客户端知道——它刚用别名把这个容器取回来。标了它，清单索引才把它
    置顶、认得出它（用户故事 11）。
    """
    transport = FakeTransport()
    serve(
        transport,
        index=[project()],
        data=[
            data(project(), []),
            data(
                project(id="inbox1234567890", name="收集箱"),
                [task(id="t9", project_id="inbox1234567890", title="随手记")],
            ),
        ],
    )
    engine = make_engine(store, transport)

    await engine.refresh()

    records = {row.id: row for row in store.list_records()}
    assert records["inbox1234567890"].is_inbox is True
    assert engine.list_index()[0].id == "inbox1234567890"


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

    assert [item.list_name for item in open_items(engine)] == ["工作"]
    assert store.task_payload("t1")["projectId"] == "work"


async def test_offline_still_reads_the_cache_and_queues_writes(store):
    """断网：刷新如实失败，但缓存照读、写照样本地生效并排队。

    这是「本地副本」这条设计的全部意义（ADR-0002 / 用户故事 62）：网络不是这一屏的前置
    条件。结论原本钉在 ``test_sync_session.py`` 里，靠着起屏按 ``r``、再看状态栏那个数；
    搬过来的是**引擎那一半**——失败的刷新不许动缓存，写不许因为推不出去就撤销。

    界面上那句「同步失败：……」（以及「凭据失效，请重新粘贴 token」那一支）归 #34 的状态栏
    工单，不在这里。
    """
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    seed = task(id="t1", title="写周报", project_id="work")
    store.apply_refresh(lists=[inbox(), project()], tasks=[seed])
    engine = make_engine(store, transport)

    with pytest.raises(NetworkError):
        await engine.refresh()

    assert [item.title for item in engine.tasks_in("work").items] == ["写周报"], (
        "缓存里的任务还在，断网不改变这一屏"
    )

    engine.cycle_priority("t1")
    await engine.wait_for_pushes()

    assert store.task_payload("t1")["priority"] == 1, "断网也照样能改任务（乐观写：本地先动）"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        ("t1", ChangeKind.UPDATE)
    ], "推不出去就留在队列里"
    assert engine.status().pending_count == 1, "这个数就是给用户看的「还没上去」"
