"""任务列表页上的删除与顺延（工单 #40）：``d`` 先问一句，``g`` / ``G`` 顺延。

两个接缝：

- **接缝一**（内存 ``FakeBackend`` + ``run_test()`` pilot）：真按键、真看屏幕——``d`` 弹出来
  的那一句话、``y`` 与 ``n`` 各走到哪里、``g`` / ``G`` 交给引擎的是几个逻辑日。
- **接缝二**（可注入的 HTTP 传输层）：真 ``Store`` + 真 ``SyncEngine`` + 假传输，钉住删除的
  请求形状（``DELETE .../task/{taskId}``、没有请求体）与顺延「只改截止时间那一项」。

**顺延的算术不在这里**：落点由 ``sync/schedule.py`` 算，``tests/test_sync_defer.py`` 钉着它
（含 ``04:00`` 边界上凌晨两点那一条）。这一层只把 ``g`` / ``G`` 接上去，所以这里的断言是
「请求体里只多了截止时间那一项」——不是自己再把日期算一遍（那样两边一起错就一起绿）。

删除的确认文案有两条是文档级事实（``api-shapes.md`` §A6）：整份官方文档里**没有**回收站、
没有 undelete、也没有「已删除」列表，所以那一句话不许承诺任何恢复手段。
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

import pytest
from rich.cells import cell_len

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import SyncEngine, UnknownTaskError
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import DidaApp
from dida.tui.messages import UNKNOWN_DELETE_MESSAGE, delete_prompt
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def drawn_width(text: str) -> int:
    """这段文字在「歧义宽度画双格」的 CJK 终端上占几格。

    与 rich 的 ``cell_len`` 只差东亚**歧义**那一档——``tests/test_keymap.py`` 的同名守卫是
    同一笔账，这里量的是浮层文案而不是帮助正文。
    """
    return sum(
        2 if unicodedata.east_asian_width(char) in ("A", "W", "F") else cell_len(char)
        for char in text
    )


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def project(id: str = "work", name: str = "工作") -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 1}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（不必先跑一遍刷新）。"""
    store.apply_refresh(lists=[inbox(), project()], tasks=list(tasks))


def make_engine(store: Store, transport: FakeTransport, *, clock=None, day_end: str = "24:00") -> SyncEngine:
    """真存储 + 真客户端（网络走假传输）：接缝二那一条。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


def backend() -> FakeBackend:
    """一份够用的缓存：「工作」里一条有截止时间的、一条没有的。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("交水费", list_name="work", id="t2")
    return fake


async def open_work(app: DidaApp, pilot) -> None:
    """把光标从收集箱走到「工作」上，``enter`` 进层二（任务列表页）。

    不按行数走：光标走到哪一行是清单列表页自己的事（收集箱在它前面），这里只认 id。
    """
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    assert app.index_page().selected_id == "work", "光标没能走到「工作」那一行"
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ d：删任务


async def test_d_asks_once_and_only_y_deletes_the_task():
    """``d`` 弹一次 ``y/n`` 确认；``y`` 才删那一条，``n`` 一条都不删（验收标准 1、3）。

    接缝一只能断到「引擎被叫去删哪一条」：``FakeBackend`` 与 ``complete`` / ``defer`` 同一
    口径，只记录、不动缓存，所以「删完那一行没了」在接缝二上断（真 ``Store``）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        assert app.tasks_page().selected_id == "t1", "光标在第一条任务上"

        await pilot.press("d")
        await pilot.pause()
        asked = screen_text(app)

        await pilot.press("n")
        await pilot.pause()
        after_cancel = screen_text(app)

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        after_yes = screen_text(app)

    assert "删除「写周报」？" in asked, "问的是哪一条，写在最上面"
    assert "找不回来" in asked and "回收站" in asked, "确认文案必须说清删了就没有了"
    for promise in ("可恢复", "能恢复", "稍后可", "已移入回收站", "撤销"):
        assert promise not in asked, f"这句话不该出现：{promise}"
    assert "写周报" in after_cancel, "``n`` 之后什么都没发生"
    assert "删掉就找不回来了" not in after_yes, "``y`` 之后确认框自己收掉，回到任务列表页"
    assert fake.deleted == ["t1"], "只有 y 那一次真的删了"


# ------------------------------------------------------------------ g / G：顺延


async def test_g_defers_by_one_logical_day_and_G_by_a_week():
    """``g`` 顺延一个逻辑日、``G`` 顺延一周（验收标准 4、5）。

    接缝一断的是「哪一条任务、几个**逻辑日**」：落点怎么算是引擎的算术，
    ``tests/test_sync_defer.py`` 钉着它（含 ``04:00`` 边界上凌晨两点那一档）。这一页只说
    「往后几个逻辑日」，不自己算日期——它连时钟都没有。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        assert app.tasks_page().selected_id == "t1", "光标在第一条任务上"

        await pilot.press("g")
        await pilot.pause()
        await pilot.press("G")
        await pilot.pause()

    assert fake.deferred == ["t1", "t1"], "两次都落在光标那条任务上"
    assert fake.deferred_days == [1, 7], "g 是一个逻辑日、G 是一周"


# ------------------------------------------------------------------ 接缝二：删除的请求形状


async def test_confirming_the_delete_sends_one_delete_with_no_body(store):
    """``y`` 之后：本地当场摘掉那一条，``DELETE .../task/{taskId}`` 立刻发出去（验收标准 1）。

    端点形状照 ``api-shapes.md`` §A6：``DELETE /open/v1/project/{projectId}/task/{taskId}``
    （``openapi-dida365.md:468``）、**没有请求体**（:471–475）。
    """
    seed(
        store,
        task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+0800"),
        task(id="t2", title="交水费"),
    )
    transport = FakeTransport(json={})
    engine = make_engine(store, transport)
    app = DidaApp(engine)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        assert app.tasks_page().selected_id == "t1", "光标在第一条任务上"

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await engine.wait_for_pushes()
        after = screen_text(app)

    assert len(transport.requests) == 1, "一次删除只发一个请求"
    request = transport.requests[0]
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1"
    assert request.content == b"", "删除没有请求体（文档 :471–475）"

    assert store.task_payload("t1") is None, "本地当场摘掉那一条"
    assert store.task_payload("t2") is not None, "别的任务一个都不动"
    assert store.pending() == (), "推成功就出队"
    assert engine.status().pending_count == 0
    assert "写周报" not in after, "``y`` 之后屏幕上那一行没了"
    assert "交水费" in after, "同一页的其它任务照旧"


async def test_cancelling_the_delete_writes_nothing_and_sends_nothing(store):
    """``n`` 之后：本地快照一个字都没变，传输上一个请求都没有（验收标准 3）。

    「什么都没发生」在这里有**两个**独立证据：本地那份底稿与操作前逐字段相同、队列是空的；
    再主动推一轮，假传输依然一个请求都没收到。删除不可逆，所以这条「没做」必须是真的。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+0800"))
    transport = FakeTransport(json={})
    engine = make_engine(store, transport)
    app = DidaApp(engine)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        before = dict(store.task_payload("t1"))

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await engine.wait_for_pushes()
        after = screen_text(app)

    assert transport.requests == [], "取消就是零请求"
    assert await engine.push_pending() == 0, "队列里一条改动都没有，主动推也推不出东西"
    assert store.pending() == ()
    assert dict(store.task_payload("t1")) == before, "本地快照逐字段没变"
    assert "写周报" in after, "那一条还在屏幕上"
    assert "删掉就找不回来了" not in after, "确认框收掉了"


# ------------------------------------------------------------------ 接缝二：顺延只动截止时间


@pytest.mark.parametrize(
    ("key", "landed"),
    [
        ("g", "2026-03-15T23:00:00+0800"),
        ("G", "2026-03-21T23:00:00+0800"),
    ],
)
async def test_g_and_G_push_only_a_new_due_date_landing_on_logical_days(store, key, landed):
    """``g`` / ``G`` 的请求体里**只**改了截止时间那一项，落点是逻辑日（验收标准 4–7）。

    凌晨两点、日界 ``04:00``：机器日历日 +1 会给出 03-16，而用户作息的「明天」是 03-15
    （那个算术归 ``sync/schedule.py``，``tests/test_sync_defer.py`` 钉着它——这一层不重算，
    只把 ``g`` / ``G`` 接上去）。这里断的是按了键之后**真的发出去了**，而且请求体里除
    ``dueDate`` 以外逐字段与按键前相同：客户端不认识的 ``kind`` 也在里面——丢了它，手机端
    设的东西就没了。
    """
    transport = FakeTransport(json={})
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            priority=5,
            tags=["工作"],
            kind="TEXT",
            dueDate="2026-03-14T23:00:00+0800",
        ),
    )
    engine = make_engine(store, transport, clock=ManualClock(at(15, 2, 0)), day_end="04:00")
    app = DidaApp(engine)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        before = dict(store.task_payload("t1"))

        await pilot.press(key)
        await pilot.pause()
        await engine.wait_for_pushes()

    request = transport.last_request
    body = transport.last_json
    assert request.method == "POST", "顺延走的是更新那一条写路径，不是删除"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/t1"
    assert body["dueDate"] == landed, "落点是逻辑日，不是机器日历日加减"
    changed = {name: value for name, value in body.items() if before.get(name) != value}
    assert changed == {"dueDate": landed}, "请求体里只改了截止时间那一项"
    assert set(before) - set(body) <= {"status"}, "底稿里的字段一个都没丢"
    assert body["kind"] == "TEXT", "不认识的服务端字段原样回写"
    assert body["priority"] == 5 and body["tags"] == ["工作"]





# ------------------------------------------------------------------ 文案的宽度：浮层是 width: auto


def test_the_delete_prompt_uses_no_ambiguous_width_glyphs():
    """确认文案自己也得「说多宽就多宽」（#48 在帮助正文上立的是同一条规矩）。

    浮层是 ``width: auto``：宽度由 **rich** 量出来的最宽那行决定，而终端按**自己的**宽度表
    画。``·``（U+00B7）与 ``—``（U+2014）是东亚**歧义**宽度——rich 量 1 格、CJK 字体下终端
    可能画 2 格，多出来的那几格就把右边框挤掉。这块浮层是删除唯一的一道防线，边框歪掉会让人
    以为按键没生效。要断行就自己断，别指望终端。
    """
    assert all(unicodedata.east_asian_width(char) == "A" for char in "·—"), (
        "这两个字形不再是歧义宽度了——守卫的前提变了，重新决定还要不要拦"
    )
    for line in delete_prompt("写周报").splitlines():
        assert drawn_width(line) == cell_len(line), (
            f"这一行宽度说得不准：rich 说 {cell_len(line)} 格，CJK 终端画 {drawn_width(line)} 格"
            f"——多出来的格会把浮层的右边框挤掉：\n{line!r}"
        )


async def test_a_delete_the_engine_refuses_says_so_instead_of_pretending():
    """引擎当场拒绝（本地已经没有这条任务的底稿）时如实说「**没**删」（工单 #25 的口径）。

    删除在服务端不可逆，所以这一句必须说的是「没删成」——说成删掉了，用户就以为那条任务
    已经没了，而它还在服务端上（刷新就回来）。
    """
    fake = backend()
    fake.delete_error = UnknownTaskError("t1")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        after = screen_text(app)

    assert UNKNOWN_DELETE_MESSAGE in after
