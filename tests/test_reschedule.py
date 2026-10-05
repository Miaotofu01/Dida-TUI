"""改期（t14）：``e`` 输入框 + 共用的日期解析器 + 只动截止时间的写路径。

两个接缝，与仓库里其它工单一样：

- **引擎的公开入口**（``plan()`` / ``reschedule()``）接真的 ``Store`` 与真的
  ``DidaApiClient``，网络钉在**接缝二**（``FakeTransport``）上——断言的是「引擎发了什么
  请求、本地变成了什么样」。
- **接缝一**：``DidaApp`` + ``FakeBackend``，用 Textual 的 ``Pilot`` 按键驱动——断言的是
  「屏幕文本」与「假后端收到的调用」。

钉死的规矩（工单 #14 + ``date_parser`` 的两条约定）：

- 只改 ``dueDate`` 与 ``isAllDay``，任务的其它字段一个不动；
- 全天任务的截止是**日期标记**：照 ``due.date()`` 写回，不按逻辑日区间挪动它
  （``day_end = "04:00"`` 时把 00:00 挪一天，会让一个「今天」的全天任务掉出今日区）；
- 只写时刻且已过去才顺延到下一个逻辑日；写明了日期（哪怕「今天」）就不顺延；
- ``diagnostics`` 非空一律提示、不提交——不按 code 名单挑着报；
- 改期走 ``write()``：本地当场生效、立即推送、推不动进重试队列。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from textual.pilot import Pilot

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine, UnknownTaskError
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import RescheduleInput, TaskPane
from support import screen_text

PLACEHOLDER = "改期：周五 14:00 / +3d"
"""改期输入框的占位文案：它出现在屏幕上 = 输入框开着。"""

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。03-14 是周六。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(15, 2, 0)
"""默认的「现在」：凌晨两点。``day_end = "04:00"`` 时它还是前一个逻辑日（03-14）。"""


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
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（改期的测试不必先跑一遍刷新）。"""
    store.apply_refresh(lists=[inbox(), project()], tasks=list(tasks))


def make_engine(
    store: Store,
    *,
    clock: ManualClock | None = None,
    transport: FakeTransport | None = None,
    day_end: str = "04:00",
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端。日界默认就是那个会咬人的 ``04:00``。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def due_of(store: Store, task_id: str = "t1") -> object:
    """本地那份快照里的截止时间（用户与推送看到的是同一份）。"""
    payload = store.task_payload(task_id)
    assert payload is not None
    return payload.get("dueDate")


async def test_reschedule_changes_only_the_due_date_and_pushes_at_once(store):
    """验收标准 #2 + #5：只改截止时间，并且走 t10 那条「立即推送」的写路径。"""
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
    engine = make_engine(store, transport=transport)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url).endswith("/open/v1/task/t1")
    body = transport.last_json
    assert body["dueDate"] == "2026-03-20T14:00:00+0800"
    assert body["isAllDay"] is False
    # 服务端权威的那份底稿照旧合并回写：改期不碰标题、优先级、标签
    assert body["title"] == "写周报"
    assert body["priority"] == 3
    assert body["tags"] == ["工作"]
    assert due_of(store) == "2026-03-20T14:00:00+0800"
    # 本地那一份也只有截止时间变了（乐观写与请求体是同一份改动）
    local = store.task_payload("t1")
    assert local is not None and (local["title"], local["priority"]) == ("写周报", 3)
    assert store.pending_count() == 0, "推成功了就该出队"


async def test_an_all_day_due_is_written_verbatim_as_a_date_marker(store):
    """契约一：``all_day=True`` 时 ``due`` 是**日期标记**，照写，不按逻辑日区间挪。

    边界 04:00、凌晨两点（逻辑日仍是 03-14）：解析器给的「今天」是 03-14 00:00。
    把它「挪进当前逻辑日」的那种算法会写成 03-15 00:00 —— 那条任务就变成**未来**的，
    既不在逾期区也不在今日区，等于从这一屏上消失。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-10T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport=transport, clock=ManualClock(at(15, 2, 0)))

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

    只发 ``dueDate`` 的话时刻会被 ``isAllDay = true`` 盖住（t22 的实测问题：省略的字段
    是不是被保留由服务端定），用户写的「14:00」就静默没了。
    """
    seed(store, task(id="t1", title="还信用卡", dueDate="2026-03-14T00:00:00+0800", isAllDay=True))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport=transport, clock=ManualClock(at(15, 2, 0)))

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert transport.last_json["isAllDay"] is False
    payload = store.task_payload("t1")
    assert payload is not None and payload["isAllDay"] is False


async def test_a_failed_push_keeps_the_reschedule_in_the_retry_queue(store):
    """验收标准 #5：改期立即推送；推不动就留在重试队列里按注入的钟退避重试。

    ADR-0002：本地那次改期**照旧生效**——一次网络抖动不该撤销用户刚按下的那一下。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport=transport, clock=clock)

    engine.reschedule("t1", due=at(20, 14, 0), all_day=False)
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1
    assert due_of(store) == "2026-03-20T14:00:00+0800", "推失败不许撤销用户刚做的改期"
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


async def test_rescheduling_a_task_outside_the_cache_is_refused_not_queued(store):
    """缓存里没有这条任务：#25 的守卫当场拒绝（结构化错误），本地一个字都不写。

    与 ``defer()`` 的静默 no-op 不同——用户在输入框里写了日期、按了 Enter，悄悄什么都不做
    正是「如实呈现」要消灭的那种安静。TUI 那一侧把这个错误翻成一句提示（接缝一里有测试）。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-10T18:00:00+0800"))
    transport = FakeTransport(json={"id": "t2"})
    engine = make_engine(store, transport=transport)

    with pytest.raises(UnknownTaskError) as caught:
        engine.reschedule("t2", due=at(20, 14, 0), all_day=False)

    assert caught.value.task_id == "t2"
    assert store.pending() == (), "拒绝就得是拒绝：不许留一条永远推不出去的改动"
    assert transport.requests == [], "没有底稿就连请求都不该发"


# ------------------------------------------------------------------ 接缝一：e 输入框


def make_backend() -> FakeBackend:
    """凌晨两点的屏（边界 04:00）：一条有截止时间的任务、一条没有。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    backend.add_task("写周报", list_name="工作", due=at(14, 23, 0))
    backend.add_task("买牛奶", list_name="生活")
    return backend


async def type_text(pilot: Pilot, text: str) -> None:
    """逐字把一段话打进当前焦点（Pilot 只认单个字符或键名，中文也一样）。"""
    for char in text:
        await pilot.press("space" if char == " " else char)
    await pilot.pause()


async def test_e_opens_the_reschedule_box_and_esc_cancels_without_writing():
    """验收标准 #1：``e`` 打开输入框、``Esc`` 取消——一个字都不提交。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert PLACEHOLDER not in screen_text(app), "没按 e 之前不该有输入框"

        await pilot.press("e")
        await pilot.pause()
        assert PLACEHOLDER in screen_text(app), "按 e 该打开输入框"
        assert app.query_one(RescheduleInput).text == "", "「e」是开输入框的键，不许漏进输入框"
        assert "取消" in screen_text(app), "输入框开着时要把 Esc 这个出口显示出来"

        await type_text(pilot, "周五 14:00")
        assert "周五 14:00" in screen_text(app), "打进去的字要看得见"

        await pilot.press("escape")
        await pilot.pause()
        assert PLACEHOLDER not in screen_text(app), "Esc 该收起输入框"
        assert "周五 14:00" not in screen_text(app)
        assert "取消" not in screen_text(app)
        assert backend.rescheduled == [], "取消就是取消：一个字都不许写"


async def test_enter_reschedules_the_task_under_the_cursor_and_closes_the_box():
    """验收标准 #1 + #2：``Enter`` 提交，「今天」= 当前**逻辑日**（凌晨两点还是 03-14）。

    用户在逻辑日 03-14 的凌晨两点写「今天 20:00」：那是 03-14 20:00，不是 03-15——
    按自然日算或者「过去了就往后挪」都会把它推到明天，用户看到的就变成了另一个日子。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await type_text(pilot, "今天 20:00")
        await pilot.press("enter")
        await pilot.pause()

        assert backend.rescheduled == ["t1"], "改的是光标下那一条"
        assert backend.rescheduled_due == [at(14, 20, 0)]
        assert backend.rescheduled_all_day == [False]
        assert PLACEHOLDER not in screen_text(app), "提交成功就该收起输入框"
        assert isinstance(app.focused, TaskPane), "焦点回到任务列，j/k 立刻能用"


@pytest.mark.parametrize(
    ("text", "due", "all_day"),
    [
        ("今天", at(14, 0, 0), True),
        ("明天", at(15, 0, 0), True),
        ("+3d", at(17, 0, 0), True),
        ("3-15", at(15, 0, 0), True),
        ("周五 14:00", at(20, 14, 0), False),
        ("14:00", at(15, 14, 0), False),
        ("今天 01:00", at(14, 1, 0), False),
    ],
)
async def test_the_reschedule_box_understands_the_parser_syntax_table(text, due, all_day):
    """验收标准 #3：改期与新建共用同一套解析器——语法表里的每一行都真的通到写路径。

    期望值来自 spec issue #1 的「日期解析器语法」表，不是照实现再算一遍。逻辑日是 03-14
    （凌晨两点、边界 ``04:00``），所以：今天 = 03-14、明天 = 03-15、``+3d`` = 03-17、
    ``3-15`` = 03-15、周五 = 03-20。两条边界规则也在这里钉住：

    - 只写时刻且**已经过去** → 落到下一个逻辑日（``14:00`` 在凌晨两点早过了 → 03-15 14:00）；
    - **写明了日期就不滚**（``今天 01:00`` 照写 03-14 01:00，哪怕它已经过去）。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("e")
        await type_text(pilot, text)
        await pilot.press("enter")
        await pilot.pause()

        assert backend.rescheduled == ["t1"], f"「{text}」该提交给光标下那条任务"
        assert backend.rescheduled_due == [due], f"「{text}」的落点"
        assert backend.rescheduled_all_day == [all_day], f"「{text}」是不是全天"


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("13-45 交报告", "13-45"),  # invalid_date：日历上没有这一天
        ("25:00 交报告", "25:00"),  # invalid_time：没有 25 点
        ("!5 交报告", "!5"),  # invalid_priority：!5 不是「高」
        ("!高 !低 交报告", "!高"),  # duplicate_priority：只认最后一个（#23 加的 code）
    ],
)
async def test_any_diagnostic_is_shown_and_nothing_is_submitted(text, token):
    """验收标准 #4：解析不出来就明确提示、绝不静默提交——**所有 code 一视同仁**。

    这里不按 code 名单挑着报：``duplicate_priority``（#23 补的）与 ``invalid_date`` 一样，
    都得让用户看见、都不能提交。漏掉任何一种，都会有一次改动悄悄落在一个用户根本
    没写的日期上——三天后在手机上才发现，正是「如实呈现」要消灭的那类安静错误。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("e")
        await type_text(pilot, text)
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.rescheduled == [], f"「{text}」没解析干净：一个字都不许提交"
        assert f"「{token}」" in shown, "要指名道姓说清是哪个记号没认出来"
        assert text in shown, "输入框留在原地，用户写的原文一个字不删"


async def test_a_reschedule_without_any_date_is_refused_with_a_message():
    """一个日期都没写：拒绝并说明白。新建可以没有日期，改期不行——那等于什么都没改。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("e")
        await type_text(pilot, "随便写点什么")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.rescheduled == [], "没写日期就不是一次改期"
        assert "没写日期" in shown
        assert "随便写点什么" in shown, "输入框留在原地，用户写的原文一个字不删"


async def test_the_box_reports_an_engine_refusal_instead_of_crashing():
    """引擎当场拒绝（本地已经没有这条任务的底稿，#25）时：如实显示，不崩、不猜。

    光标下的任务来自缓存视图，所以正常路径上走不到这里；但输入框可以一直开着，
    这期间一次刷新（t09/t21）就可能把那条任务刷掉——那一下必须**说出来**，
    而不是抛一个 traceback，或者假装改成了。
    """
    backend = make_backend()
    backend.reschedule_error = UnknownTaskError("t1")
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("e")
        await type_text(pilot, "明天")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.rescheduled == ["t1"], "调用发出去了，是引擎拒的"
        assert "没有改成" in shown
        assert "明天" in shown, "输入框留在原地，原文一个字不删"


async def test_the_reschedule_targets_the_task_the_cursor_was_on_when_e_was_pressed():
    """改的是**按下 e 那一刻**光标下那条：输入框拿到焦点之后，j/k 是文本，不再移动光标。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("j")  # 光标移到第二条
        await pilot.press("e")
        await type_text(pilot, "j 明天")  # 这个 j 该进输入框，不该动光标
        await pilot.press("enter")
        await pilot.pause()

        assert backend.rescheduled == ["t2"], "改的是按下 e 时那条，不是按 Enter 时那条"
        assert backend.rescheduled_due == [at(15, 0, 0)]
        # 输入框里的那两个字符一个都没漏出去当成光标移动：改的仍是 t2。
        # （提交后重画会把光标放回第一行——与 ``g``/``x`` 之后同一套行为，见 TaskPane.render_groups）


async def test_e_does_nothing_when_there_is_no_task_under_the_cursor():
    """空屏上按 e：什么都不做，也不报错（与 ``x`` / ``g`` 同一条口径）。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        assert backend.rescheduled == []
        assert PLACEHOLDER not in screen_text(app), "没有任务可改，就不该打开输入框"
