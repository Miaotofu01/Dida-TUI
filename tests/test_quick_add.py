"""新建（t15）：``a`` 顶部一行输入 + 共用解析器 + 新建的写路径。

两个接缝，与仓库里其它工单一样：

- **引擎的公开入口**（``plan()`` / ``create()``）接真的 ``Store`` 与真的
  ``DidaApiClient``，网络钉在**接缝二**（``FakeTransport``）上——断言的是「引擎发了什么
  请求、本地变成了什么样」。
- **接缝一**：``DidaApp`` + ``FakeBackend``，用 Textual 的 ``Pilot`` 按键驱动——断言的是
  「屏幕文本」与「假后端收到的调用」。

钉死的规矩（工单 #15）：

- 一行里同时写标题、日期、优先级、标签，全都要真的走到请求体里；
- 新建的任务**立刻**出现在正确的分区里（乐观写：本地先生效，网络不是前置条件）；
- ``diagnostics`` 非空一律提示、绝不提交——新建的「静默建出一个没有日期的任务」在服务端
  还会被顺手清掉重复规则，是双重错误；不按 code 名单挑着报，四种 code 一视同仁；
- 新建立即推送，推不动就留在重试队列里按注入的钟退避重试。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from textual.pilot import Pilot

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import QuickAddInput, TaskPane
from support import screen_text

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


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId；快速添加的落点。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def seed(store: Store) -> None:
    """摆一份只有收集箱的缓存：新建不必先跑一遍刷新。"""
    store.apply_refresh(lists=[inbox()])


def make_engine(
    store: Store,
    *,
    transport: FakeTransport,
    clock: ManualClock | None = None,
    day_end: str = "04:00",
) -> SyncEngine:
    """接上真存储、真客户端；日界默认就是那个会咬人的 ``04:00``。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


async def test_quick_add_creates_locally_and_pushes_the_whole_line(store):
    """验收标准 #2 + #3 + #5：一行里的标题/日期/优先级/标签一起解析、一起推。

    断言的是请求形状（接缝二）与本地状态：

    - 请求发到 ``POST /open/v1/task``，体里是解析出来的四样东西（``!高`` 是 **5**，
      不是 3——API 的编码，见 api-contracts.md）；
    - ``status`` 不进请求体：文档说新建不接受它，客户端守卫会当场拒绝；
    - **本地先有**：还没等推送回来，这条任务就已经在本地缓存里了（乐观写）；
    - 推成功之后本地那条临时 id 落到服务端给的 id 上，刷新不会把它看成两条。
    """
    seed(store)
    transport = FakeTransport(json={"id": "srv-1", "projectId": "inbox", "title": "交季度报告"})
    engine = make_engine(store, transport=transport)

    parsed = engine.plan("明天下午3点交季度报告 !高 #工作")
    assert parsed.diagnostics == ()
    local_id = engine.create(
        parsed.title,
        due=parsed.due,
        all_day=parsed.all_day,
        priority=parsed.priority,
        tags=parsed.tags,
    )

    local = store.task_payload(local_id)
    assert local is not None, "本地当场就要看得见（网络不是这一屏的前置条件）"
    assert local["title"] == "交季度报告"
    assert local["projectId"] == "inbox", "快速添加落在收集箱"

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
    assert store.pending() == (), "推成功了就该出队"
    assert engine.status().pending_count == 0
    assert [item.id for item in store.tasks()] == ["srv-1"], (
        "推成功之后那条临时任务要落到服务端给的 id 上：留着两条，下一次全量刷新就会把"
        "同一条任务画两遍"
    )


async def test_a_failed_create_push_stays_in_the_retry_queue(store):
    """验收标准 #5：新建立即推送；推不动就留在重试队列里按注入的钟退避重试。

    ADR-0002：本地那条任务照旧在（还挂着临时 id）——一次网络抖动不该让用户刚写下的那条
    任务从屏幕上消失。钟走到点再推成功，才认领服务端给的 id。
    """
    seed(store)
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    clock = ManualClock(T0)
    engine = make_engine(store, transport=transport, clock=clock)

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


async def test_an_all_day_create_writes_the_date_marker_verbatim(store):
    """全天任务的截止是**日期标记**：照 ``due.date()`` 写成那天的 00:00，不按逻辑日区间挪。

    边界 04:00、凌晨两点（逻辑日仍是 03-14）：写「今天」的全天任务，请求体里必须是
    ``2026-03-14T00:00:00+0800`` 与 ``isAllDay: true``。把它「挪进当前逻辑日」的那种算法
    会写成 03-15 00:00——那条任务在视图里就成了明天的，从今天的屏幕上消失（与 t14 同一条
    约定，来自 t06 的解析器）。
    """
    seed(store)
    transport = FakeTransport(
        json={
            "id": "srv-2",
            "projectId": "inbox",
            "title": "还信用卡",
            "dueDate": "2026-03-14T00:00:00+0800",
            "isAllDay": True,
        }
    )
    engine = make_engine(store, transport=transport)

    parsed = engine.plan("今天 还信用卡")
    engine.create(parsed.title, due=parsed.due, all_day=parsed.all_day)
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

    新建的响应按文档就是那条建好的任务，但不拿这个赌：认领 id 是**合并**，不是替换。
    真正的服务端权威裁决在全量刷新那条路上（``apply_refresh`` 会把每一笔覆盖记进报告），
    而不是在这里悄悄少掉一个用户刚写下的日期。
    """
    seed(store)
    transport = FakeTransport(json={"id": "srv-3"})
    engine = make_engine(store, transport=transport)

    parsed = engine.plan("明天下午3点交季度报告")
    engine.create(parsed.title, due=parsed.due, all_day=parsed.all_day)
    await engine.wait_for_pushes()

    payload = store.task_payload("srv-3")
    assert payload is not None
    assert payload["dueDate"] == "2026-03-15T15:00:00+0800"
    assert payload["isAllDay"] is False
    assert payload["projectId"] == "inbox"
    assert [item.id for item in store.tasks()] == ["srv-3"]


# ------------------------------------------------------------------ 接缝一：a 顶部输入框

PLACEHOLDER = "新建：明天下午3点交季度报告 !高 #工作"
"""新建输入框的占位文案：它出现在屏幕上 = 输入框开着。"""


def make_backend() -> FakeBackend:
    """凌晨两点的屏（边界 04:00）：一条今晚截止的任务、一条没有截止时间的。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
    backend.add_task("写周报", list_name="工作", due=at(14, 23, 0))
    backend.add_task("买牛奶", list_name="生活")
    return backend


async def type_text(pilot: Pilot, text: str) -> None:
    """逐字把一段话打进当前焦点（Pilot 只认单个字符或键名，中文也一样）。"""
    for char in text:
        await pilot.press("space" if char == " " else char)
    await pilot.pause()


async def test_a_opens_the_box_on_top_and_esc_cancels_without_creating():
    """验收标准 #1：``a`` 打开**顶部**输入框、``Esc`` 取消——一条都不建。

    顶部是这一屏的规矩（规范：顶部输入框里 Enter 提交、Esc 取消），也是与 ``e`` 的区别：
    ``e`` 的改期框在底部，因为它改的是光标下那一条；新建不瞄准任何一条，所以它在最上面。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert PLACEHOLDER not in screen_text(app), "没按 a 之前不该有输入框"

        await pilot.press("a")
        await pilot.pause()
        shown = screen_text(app)
        assert PLACEHOLDER in shown, "按 a 该打开输入框"
        assert app.query_one(QuickAddInput).text == "", "「a」是开输入框的键，不许漏进输入框"
        assert "取消" in shown, "输入框开着时要把 Esc 这个出口显示出来"
        box = app.query_one(QuickAddInput)
        assert box.region.y < app.query_one("#panes").region.y, "新建框在三栏**上面**"

        await type_text(pilot, "还信用卡 a")
        assert "还信用卡 a" in screen_text(app), "打进去的字要看得见"
        assert box.text == "还信用卡 a", (
            "焦点在输入框里时 a 是普通字符：这个绑定不是 priority，"
            "否则英文标题里每写一个 a 都会把这一行清掉"
        )

        await pilot.press("escape")
        await pilot.pause()
        assert PLACEHOLDER not in screen_text(app), "Esc 该收起输入框"
        assert "还信用卡" not in screen_text(app), "取消之后输入框里那几个字不该留在屏幕上"
        assert "取消" not in screen_text(app)
        assert backend.created == [], "取消就是取消：一条都不许建"


async def test_enter_creates_the_task_and_it_shows_up_in_the_today_section():
    """验收标准 #3：新建的任务**立刻**出现在对应分区里（乐观写，网络不是前置条件）。

    「今天 18:00 交季度报告」：逻辑日是 03-14（凌晨两点、边界 04:00），所以它落在今日区、
    读作「今天 18:00」。今日区的计数从 1 涨到 2，说的就是这一条进的是今日区——原来那条
    没有截止时间的仍在它自己的「收集箱无日期」区里，没被这一笔算进今日。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, "今天 18:00 交季度报告")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.created == ["交季度报告"]
        assert backend.created_due == [at(14, 18, 0)]
        assert backend.created_all_day == [False]
        assert PLACEHOLDER not in shown, "提交成功就该收起输入框"
        assert isinstance(app.focused, TaskPane), "焦点回到任务列，j/k 立刻能用"
        assert "── 今日 · 2 项" in shown, "新建的那条当场进今日区（原来只有「写周报」一条）"
        assert shown.index("交季度报告") > shown.index("── 今日")
        assert "今天 18:00" in shown, "截止时间要读得出来"


async def test_one_line_carries_the_title_the_date_the_priority_and_the_tags():
    """验收标准 #2：一行里同时写四样东西，四样都要真的走到引擎。

    「明天下午3点交季度报告 !高 #工作」是规范里的例子：标题、截止时间、优先级（``!高``
    → API 的 **5**，不是 3——档位序号与 wire 值是两套编码）、标签各归各的，标题里一个
    记号都不剩。明天的任务不属于「今天」这一屏，所以它不出现在任务列里（视图只画逾期与
    今日），但那不影响它已经被建出来了。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, "明天下午3点交季度报告 !高 #工作")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.created == ["交季度报告"], "标题里不该剩下日期、优先级、标签的记号"
        assert backend.created_due == [at(15, 15, 0)]
        assert backend.created_all_day == [False]
        assert backend.created_priority == [5], "!高 是 5，不是 3（api-contracts.md）"
        assert backend.created_tags == [("工作",)]
        assert PLACEHOLDER not in shown, "提交成功就该收起输入框"
        assert "── 今日 · 1 项" in shown, "明天的任务不进今天的屏：它不该被塞进今日区"
        assert "── 收集箱无日期 · 1 项" in shown, "无日期那条仍在它自己那一区，条数没被明天这条动过"
        assert "交季度报告" not in shown, "建出来了，但它属于明天，不在这张屏上"


async def test_a_created_task_shows_the_priority_mark_it_was_written_with():
    """新建之后屏幕上看得出优先级：``!`` 是「高」的标记，由引擎按 API 取值算出来。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, "今天 20:00 交月报 !高 #工作")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert "── 今日 · 2 项" in shown
        assert "! 交月报" in shown, "高优先级在行首是 !（TUI 不自己判断，标记由引擎给）"
        assert "#工作" not in shown, "标签不是行内文字：解析器已经把它从标题里摘掉了"


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("13-45 交报告", "13-45"),  # invalid_date：日历上没有这一天
        ("25:00 交报告", "25:00"),  # invalid_time：没有 25 点
        ("!5 交报告", "!5"),  # invalid_priority：!5 不是「高」
        ("!高 !低 交报告", "!高"),  # duplicate_priority：只认最后一个（#23 加的 code）
    ],
)
async def test_any_diagnostic_is_shown_and_nothing_is_created(text, token):
    """验收标准 #4：解析不出来就明确提示、绝不静默建出一条没有日期的任务。

    不按 code 名单挑着报：四种 code 一视同仁。漏掉任何一种，都会有一条任务悄悄建在一个
    用户根本没写出来的日期上（而没有日期的任务在服务端还会被顺手清掉重复规则）——三天后
    在手机上才发现，正是「如实呈现」要消灭的那类安静错误。被拒绝时输入框留在原地，
    原文一个字不删。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, text)
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.created == [], f"「{text}」没解析干净：一条都不许建"
        assert f"「{token}」" in shown, "要指名道姓说清是哪个记号没认出来"
        assert text in shown, "输入框留在原地，用户写的原文一个字不删"
        assert PLACEHOLDER not in shown, "输入框还开着（占位文案被原文顶掉了，所以要看原文在不在）"


async def test_a_line_without_any_date_is_a_legitimate_quick_add():
    """没写日期是**合法**的：快速添加可以只写标题。

    要挡的是「解析没成功还硬建」，不是「用户就是没写日期」——这条任务落进「收集箱无日期」
    区（story 16），读作「—」，就在原来那条没有截止时间的旁边等着分诊。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, "买酱油")
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.created == ["买酱油"]
        assert backend.created_due == [None]
        assert backend.created_priority == [None]
        assert "── 收集箱无日期 · 2 项" in shown, "新建的无日期任务当场进这一区（原来只有「买牛奶」）"
        assert shown.index("买酱油") > shown.index("── 收集箱无日期")
        assert "· 买酱油  收集箱  —" in shown, "没有截止时间读作「—」，不是「今天 00:00」"


@pytest.mark.parametrize("text", ["明天 !高 #工作", "#工作", "明天"])
async def test_a_line_without_any_title_is_refused_with_a_message(text):
    """整行只有日期/优先级/标签、一个字的标题都没有：拒绝并说明白。

    任务总得有个名字——建出来是一行点不动也认不出的空行，光标停上去还不知道自己选了什么。
    这不是「解析失败」，是「这一行还没写完」。
    """
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("a")
        await type_text(pilot, text)
        await pilot.press("enter")
        await pilot.pause()
        shown = screen_text(app)

        assert backend.created == [], "没有标题就不是一条任务"
        assert "没写标题" in shown
        assert text in shown, "输入框留在原地，用户写的原文一个字不删"
