"""子任务勾选（t20）：右栏显示、**写前重读**、只合并这一次改动。

两个接缝，与仓库里其它工单一样：

- **接缝二（传输）**：真 ``Store`` + 真 ``DidaApiClient`` + ``FakeTransport``——断言
  「重读了没有、发出去的请求体里到底有什么」。这是本工单最要紧的一条：``items`` 是
  一整个数组，不重读就写等于把别处改过的子任务一起抹掉。
- **接缝一**：``DidaApp`` + ``FakeBackend`` + Textual 的 ``Pilot``——断言屏幕文本与
  假后端收到的调用。

钉死的规矩（工单 #20 + ``api-contracts.md``）：

- 子任务状态是**另一对**取值：Normal ``0`` / Completed ``1``，不是任务级的 ``-1/0/2``；
- 子任务的日期字段叫 ``startDate``；**没有日期的子任务照样显示**（标题 + 完成状态）；
- 写回前先重读该任务，只翻这一次那一个子任务，别处的修改原样留着；
- 重读发现任务在别处被改过时，服务端那一份说了算，而且用户必须看得见；
- 回写不丢服务端给的未知字段（``merge_snapshot`` 的底稿）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import NO_DUE_TEXT, SubtaskItem, SyncEngine, UnknownTaskError
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import (
    SUBTASK_ELSEWHERE_MESSAGE,
    SUBTASK_GONE_MESSAGE,
    SUBTASK_READ_FAILED_MESSAGE,
    UNKNOWN_SUBTASK_MESSAGE,
    DidaApp,
)
from dida.tui.panes import SubtaskPane, TaskPane
from support import screen_text

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(15, 2, 0)
"""默认的「现在」：凌晨两点。``day_end = "04:00"`` 时它还属于逻辑日 03-14。"""


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "交季度报告", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def item(id: str, title: str, status: int = 0, **extra: object) -> dict:
    """一份 ``ChecklistItem`` 原文：状态是 0/1 那一对，日期字段叫 ``startDate``。"""
    return {"id": id, "title": title, "status": status, **extra}


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（本工单的测试不必先跑一遍刷新）。"""
    store.apply_refresh(lists=[inbox()], tasks=list(tasks))


def make_engine(
    store: Store,
    *,
    clock: ManualClock | None = None,
    transport: FakeTransport | None = None,
    day_end: str = "04:00",
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


# ---------------------------------------------------------------- 读：右栏要显示的东西


def test_subtasks_show_title_and_completion_state_even_without_a_due_date(store):
    """验收标准 #1：标题与完成状态都要显示，**没有截止时间的子任务照样显示**。"""
    seed(
        store,
        task(
            items=[
                item("i1", "收集数据", 1),  # 完成了，而且没有日期
                item("i2", "画图表", 0),  # 没完成，也没有日期
                item("i3", "写结论", 0, startDate="2026-03-15T18:00:00+0800"),
            ]
        ),
    )

    subtasks = make_engine(store).subtasks("t1")

    assert [(row.title, row.completed) for row in subtasks] == [
        ("收集数据", True),
        ("画图表", False),
        ("写结论", False),
    ]
    assert subtasks[0].due_text == NO_DUE_TEXT, "没有日期的子任务不是「不该出现」，是「读作没有日期」"
    assert subtasks[1].due_text == NO_DUE_TEXT
    assert subtasks[2].due_text != NO_DUE_TEXT, "有日期的子任务照样读出人类可读的截止时间"


def test_a_task_without_subtasks_reads_as_no_rows(store):
    """没有 ``items`` 的任务不是错误：右栏就是没有子任务可显示。"""
    seed(store, task(id="t1"))

    assert make_engine(store).subtasks("t1") == ()


# ---------------------------------------------------------------- 接缝一：右栏渲染


async def test_detail_pane_lists_subtask_titles_and_state():
    """验收标准 #1（接缝一）：右栏能看到每个子任务的标题与完成状态。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    backend.add_task("交季度报告", list_name="工作", id="t1")
    backend.set_subtasks(
        "t1",
        SubtaskItem(subtask_id="i1", title="收集数据", completed=True),
        SubtaskItem(subtask_id="i2", title="画图表", completed=False),
    )
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "收集数据" in text
    assert "画图表" in text
    assert "☑ 收集数据" in text, "完成了的子任务要看得出来"
    assert "☐ 画图表" in text, "没完成的子任务要看得出来"


# ---------------------------------------------------------------- 接缝二：写前重读


def server_task(title: str = "交季度报告", **extra: object) -> dict:
    """服务端那份任务原文（重读 ``GET .../task/{taskId}`` 回来的那一份）。"""
    return task(id="t1", title=title, **extra)


def enqueue_read(transport: FakeTransport, payload: dict) -> None:
    """让下一次 GET 回这一份（POST 走传输的默认响应）。"""
    transport.enqueue(httpx.Response(200, json=payload))


async def test_toggling_a_subtask_rereads_then_writes_only_that_change(store):
    """验收标准 #3：**先重读、后写回**，请求体里只有这一次改动，别处的修改原样留着。

    ``items`` 是一整个数组：拿本地那份旧数组直接写回去，别处刚勾上的 ``i2`` 就被抹掉了，
    而且服务端一声不吭。这条测试就是那个场景——服务端那份在「我们读到它」与「我们写回」
    之间被别处改过。
    """
    seed(store, server_task(items=[item("i1", "收集数据"), item("i2", "画图表")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(
        transport,
        server_task(
            items=[item("i1", "收集数据"), item("i2", "画图表", 1)],
            unknownField="手机端设的提醒",
        ),
    )
    engine = make_engine(store, transport=transport)

    report = await engine.toggle_subtask("t1", "i1")
    await engine.wait_for_pushes()

    assert [request.method for request in transport.requests] == ["GET", "POST"], "顺序是先读后写"
    assert transport.requests[0].url.path == "/open/v1/project/work/task/t1"
    assert transport.requests[1].url.path == "/open/v1/task/t1"
    body = transport.last_json
    assert [(entry["id"], entry["status"]) for entry in body["items"]] == [
        ("i1", 1),
        ("i2", 1),
    ], "只翻 i1；别处勾上的 i2 必须留着"
    assert body["unknownField"] == "手机端设的提醒", "服务端给的未知字段一个不丢（验收标准 #5）"
    assert report.written is True
    assert [(row.subtask_id, row.completed) for row in report.items] == [
        ("i1", True),
        ("i2", True),
    ]


async def test_the_reread_adopts_the_server_copy_and_says_the_task_changed_elsewhere(store):
    """验收标准 #4：重读发现任务在别处被改过时，服务端那一份说了算，而且用户看得见。"""
    seed(store, server_task(title="交季度报告", items=[item("i1", "收集数据")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(
        transport,
        server_task(title="交季度报告（手机改过）", items=[item("i1", "收集数据")]),
    )
    engine = make_engine(store, transport=transport)

    report = await engine.toggle_subtask("t1", "i1")
    await engine.wait_for_pushes()

    assert report.changed_elsewhere is True, "别处改过就得报出来——覆盖必须被看见（ADR-0002）"
    payload = store.task_payload("t1")
    assert payload is not None
    assert payload["title"] == "交季度报告（手机改过）", "服务端权威落地，不是只在报告里提一句"


async def test_a_reread_that_matches_what_we_already_had_is_not_a_change_elsewhere(store):
    """别处没动过就别喊狼来了：屏幕上的提示只有在真有覆盖时才出现。"""
    seed(store, server_task(items=[item("i1", "收集数据")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(transport, server_task(items=[item("i1", "收集数据")]))
    engine = make_engine(store, transport=transport)

    report = await engine.toggle_subtask("t1", "i1")
    await engine.wait_for_pushes()

    assert report.changed_elsewhere is False


async def test_a_failed_push_stays_in_the_retry_queue(store):
    """验收标准 #2：勾选后立即推送；推不动就留在队列里按注入的钟退避重试。"""
    seed(store, server_task(items=[item("i1", "收集数据")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(transport, server_task(items=[item("i1", "收集数据")]))
    transport.enqueue(httpx.Response(500))
    engine = make_engine(store, transport=transport)

    await engine.toggle_subtask("t1", "i1")
    await engine.wait_for_pushes()

    pending = store.pending()
    assert [change.kind for change in pending] == [ChangeKind.UPDATE]
    assert pending[0].next_retry_at is not None, "失败之后要排下一次重试"
    payload = store.task_payload("t1")
    assert payload is not None
    assert payload["items"][0]["status"] == 1, "本地当场生效：用户的操作不因一次网络抖动被撤销"


async def test_a_subtask_the_server_no_longer_has_is_not_written_back(store):
    """重读回来的那一份里已经没有这个子任务：以服务端为准，什么都不写回去。"""
    seed(store, server_task(items=[item("i1", "收集数据"), item("i2", "画图表")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(transport, server_task(items=[item("i2", "画图表")]))
    engine = make_engine(store, transport=transport)

    report = await engine.toggle_subtask("t1", "i1")

    assert report.written is False
    assert [request.method for request in transport.requests] == ["GET"], "没有这个子任务就不发写请求"
    assert [(row.subtask_id, row.completed) for row in report.items] == [("i2", False)]


async def test_our_own_pending_tick_survives_the_next_reread(store):
    """本地还有没推成功的勾选时，重读不许把它撤销（ADR-0002 的豁免，逐字段）。

    断网时连勾两个子任务是常见事：第一次推送失败留在队列里，第二次重读回来的服务端那一份
    里第一个子任务**还是没勾**。以服务端为底稿直接写，用户刚勾的那个就被悄悄撤销了——
    正是 ADR-0002 要挡的「一次失败推送加上一次刷新，用户的操作没了」。
    """
    seed(store, server_task(items=[item("i1", "收集数据"), item("i2", "画图表")]))
    transport = FakeTransport(json={"id": "t1"})
    enqueue_read(transport, server_task(items=[item("i1", "收集数据"), item("i2", "画图表")]))
    transport.enqueue(httpx.Response(500))  # 第一次推送失败
    engine = make_engine(store, transport=transport)
    await engine.toggle_subtask("t1", "i1")
    await engine.wait_for_pushes()
    assert store.pending_count() == 1

    enqueue_read(transport, server_task(items=[item("i1", "收集数据"), item("i2", "画图表")]))
    await engine.toggle_subtask("t1", "i2")
    await engine.wait_for_pushes()

    body = transport.last_json
    assert [(entry["id"], entry["status"]) for entry in body["items"]] == [
        ("i1", 1),
        ("i2", 1),
    ], "i1 是我们自己还没推成功的勾选，重读不许撤销它"


# ---------------------------------------------------------------- 接缝一：勾选


def subtask_backend() -> FakeBackend:
    """一条带两个子任务的任务：右栏渲染与勾选测试的屏。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    backend.add_task("交季度报告", list_name="工作", id="t1")
    backend.set_subtasks(
        "t1",
        SubtaskItem(subtask_id="i1", title="收集数据", completed=False),
        SubtaskItem(subtask_id="i2", title="画图表", completed=False),
    )
    return backend


async def test_s_focuses_the_subtask_list_and_t_ticks_the_one_under_the_cursor():
    """验收标准 #2（接缝一）：按下去就交给引擎勾选，屏幕上的状态跟着变。"""
    backend = subtask_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.query_one(SubtaskPane).has_focus, "s 把焦点交给右栏那份子任务列表"
        await pilot.press("t")
        await pilot.pause()
        text = screen_text(app)

    assert backend.toggled_subtasks == [("t1", "i1")]
    assert "☑ 收集数据" in text, "勾上的那一个当场就是勾上的样子"
    assert "☐ 画图表" in text, "别的子任务不受影响"


async def test_j_moves_the_subtask_cursor_before_ticking():
    """光标动的是子任务，不是任务列：``j`` 之后 ``t`` 勾的是第二个。"""
    backend = subtask_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s", "j", "t")
        await pilot.pause()

    assert backend.toggled_subtasks == [("t1", "i2")]


async def test_escape_hands_the_focus_back_to_the_task_list():
    backend = subtask_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one(TaskPane).has_focus, "Esc 把焦点还给任务列"


async def test_s_does_nothing_when_the_task_under_the_cursor_has_no_subtasks():
    """没有子任务可勾时按键什么都不做——空屏上按键不许报错（与 x/g/e 同一条口径）。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    backend.add_task("买牛奶", list_name="生活", id="t9")
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        assert app.query_one(TaskPane).has_focus, "没有子任务可勾，焦点就不动"


async def test_the_server_overwrite_is_visible_when_the_task_changed_elsewhere():
    """验收标准 #4（接缝一）：重读发现别处改过时，用户**在屏幕上看得见**。"""
    backend = subtask_backend()
    backend.subtask_changed_elsewhere = True
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s", "t")
        await pilot.pause()
        text = screen_text(app)

    assert SUBTASK_ELSEWHERE_MESSAGE in text


async def test_a_subtask_the_server_no_longer_has_says_so():
    backend = subtask_backend()
    backend.subtask_written = False
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s", "t")
        await pilot.pause()
        text = screen_text(app)

    assert SUBTASK_GONE_MESSAGE in text


async def test_an_unknown_task_is_reported_instead_of_crashing():
    """引擎当场拒绝（#25 的 ``UnknownTaskError``）时如实说一句，不崩。"""
    backend = subtask_backend()
    backend.subtask_error = UnknownTaskError("t1")
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s", "t")
        await pilot.pause()
        text = screen_text(app)

    assert UNKNOWN_SUBTASK_MESSAGE in text


async def test_a_reread_that_fails_is_reported_instead_of_crashing():
    """重读是一次网络调用：断了的时候要落地成一句看得见的话，不能把界面带走。

    引擎的失败一律是结构化错误（``DidaError``），TUI 这一层只负责如实说出来——推送失败
    走重试队列，而重读失败根本没有「写回」可言，所以这一句必须说出来。
    """
    backend = subtask_backend()
    backend.subtask_error = NetworkError("断了")
    app = DidaApp(backend)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("s", "t")
        await pilot.pause()
        text = screen_text(app)

    assert SUBTASK_READ_FAILED_MESSAGE in text
