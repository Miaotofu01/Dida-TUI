"""完成（t11）：按 `x` → 假后端收到 complete 调用。

两条接缝：

- **接缝二（窄）**：``SyncEngine.complete()`` 的推送形状（方法、URL、空请求体）。
  完成在服务端不可逆（ADR-0002），所以「一次按键只发这一个请求」必须钉住。
- **接缝一（主，最高）**：``DidaApp`` + ``FakeBackend``，Pilot 按键 → 断言假后端收到的
  调用、屏幕文本与被渲染出来的样式。这是规范里点名的那个例子：一个测试走通「按 `x` →
  假后端收到 complete 调用」。

两个已经定死的接口形状（t05/t10 决定，本工单不改）：

- ``FakeBackend.complete(task_id)`` 是同步的，只记录调用（``backend.completed``）；
- ``Engine.complete(task_id)`` 也是同步的：乐观写 + 立即推送，网络结果不是它的前置条件。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import FLASH_SECONDS, DidaApp
from dida.tui.panes import TaskPane, task_line
from support import screen_styled_text, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store, *tasks: dict, lists: list[dict] | None = None) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=lists if lists is not None else [{"id": "work", "name": "工作", "sortOrder": 1}],
        tasks=list(tasks),
    )


def make_engine(store: Store, transport: FakeTransport) -> SyncEngine:
    """接上真存储与真客户端；网络钉在假传输上。"""
    return SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


def make_backend() -> FakeBackend:
    """两条今天的任务：光标停在第一条上。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("交季度报告", list_name="工作", id="t1", due=at(14, 9, 0))
    backend.add_task("写周报", list_name="工作", id="t2", due=at(14, 18, 0))
    return backend


SGR = re.compile(r"\x1b\[[0-9;]*m")


def row_style(app: DidaApp, title: str) -> str:
    """屏幕上某一行的标题文字被画成了什么样式（ANSI 序列）。

    样式随主题与终端变（本机 ``NO_COLOR=1``，颜色还会被换成灰阶），所以断言比的是「这一行
    和别的行长什么样」，不写死具体颜色。取标题前面那一段 SGR：它就是这一行的文字样式。
    """
    for line in screen_styled_text(app).splitlines():
        if title in line:
            prefix = SGR.findall(line[: line.index(title)])
            assert prefix, f"这一行没有样式：{line!r}"
            return prefix[-1]
    raise AssertionError(f"屏幕上没有「{title}」这一行")


# ---------------------------------------------------------------- 引擎：完成并立即推送


async def test_completing_through_the_engine_pushes_the_complete_endpoint(store):
    """``complete()`` 是「完成并立即推送」这一个动作：本地当场不再未完成，推送是这个端点。

    期望值来自 ``api-contracts.md``：``POST /open/v1/project/{listId}/task/{taskId}/complete``
    且**没有请求体**（``status`` 不是写字段，它只是引擎补在本地的那一份）。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    seed(store, {"id": "t1", "projectId": "work", "title": "写周报", "status": 0})
    engine = make_engine(store, transport)

    engine.complete("t1")

    assert [item.title for group in engine.view().groups for item in group.items] == [], (
        "本地当场就不再是未完成"
    )
    assert store.task_payload("t1")["status"] == 2, "Completed 是 2（api-contracts 第 2 条）"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        ("t1", ChangeKind.COMPLETE)
    ], "完成只有一个方向：一条 COMPLETE 改动，没有本地「取消完成」这第二条路"

    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1/complete"
    assert request.content == b"", "完成端点没有请求体"
    assert len(transport.requests) == 1, "一次完成只发这一个请求"
    assert engine.status().pending_count == 0, "推成功后那个数回到 0"


# ---------------------------------------------------------------- 接缝一：按 x


async def test_pressing_x_completes_the_task_under_the_cursor():
    """主接缝（规范点名的那个例子）：按 `x` → 假后端收到 complete 调用。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("x")
        await pilot.pause()

    assert backend.completed == ["t2"], "完成的是光标下那一条"


async def test_pressing_x_on_an_empty_screen_completes_nothing():
    """空屏上按 `x`：不崩，也不许凭空完成一条不存在的任务。"""
    backend = FakeBackend(clock=ManualClock(T0))
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert app.is_running

    assert backend.completed == []


# ---------------------------------------------------------------- 完成反馈：短暂高亮


def test_the_completed_row_carries_a_highlight_the_cursor_alone_does_not():
    """高亮是完成那一行**额外**带上的一层：不是光标本来的反色。"""
    item = make_backend().view().groups[0].items[0]

    cursor = task_line(item, selected=True)
    flashed = task_line(item, selected=True, flash=True)

    cursor_styles = {str(span.style) for span in cursor.spans}
    flashed_styles = {str(span.style) for span in flashed.spans}
    assert "bold green" in flashed_styles, "完成后这一行高亮：让用户确定这一下生效了"
    assert "reverse" in flashed_styles, "高亮盖在光标上，不是把光标顶掉"
    assert "bold green" not in cursor_styles


async def test_the_completed_row_highlights_for_a_moment_and_then_settles():
    """完成后该行短暂高亮（ADR-0002 的补偿）：按下去看得见，过后收起来。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        plain = row_style(app, "写周报")  # 光标这时在第一条上，这一行是普通行

        await pilot.press("j")  # 光标到「写周报」
        await pilot.press("x")  # 完成它：这一行亮起来
        await pilot.press("k")  # 光标挪回第一条，免得把「光标反色」当成高亮
        await pilot.pause()
        assert app.query_one(TaskPane).selected_task_id == "t1", "光标确实挪开了"

        assert row_style(app, "写周报") != plain, "完成的那一行高亮着"

        await pilot.pause(FLASH_SECONDS + 0.1)
        assert row_style(app, "写周报") == plain, "高亮是短暂的，不是常态样式"


# ------------------------------------------------- 防误按：完成不可逆的唯一补偿

NAV_KEYS = {"j", "k", "up", "down"}
"""导航键：规范键位表里的 ``j`` / ``k`` / ``↑`` / ``↓``。"""

ADJACENT_TO_NAV = {
    "j": {"h", "k", "u", "i", "m", "n"},
    "k": {"j", "l", "i", "o", "m", "n"},
    "up": set(),
    "down": set(),
    "tab": set(),
}
"""QWERTY 上与每个导航键物理挨着的键（左右 + 斜向）。

期望值来自键盘布局本身，不是照代码再算一遍：``x`` 在下面那一行，与主键区的 ``j``/``k``
隔着整行。
"""


def complete_keys() -> set[str]:
    """应用给「完成」绑定的键。"""
    return {
        key.strip()
        for binding in DidaApp.BINDINGS
        if binding.action == "complete"
        for key in binding.key.split(",")
    }


def test_the_completion_key_is_not_adjacent_to_the_navigation_keys():
    """防误按：`x` 不与导航键相邻，也不是导航键本身（ADR-0002）。"""
    keys = complete_keys()
    assert keys, "完成没有绑定任何键"

    adjacent = set().union(*(ADJACENT_TO_NAV[key] for key in NAV_KEYS))
    assert not (keys & adjacent), f"完成键挨着导航键：{sorted(keys & adjacent)}"
    assert not (keys & NAV_KEYS), f"完成键就是导航键：{sorted(keys & NAV_KEYS)}"


def test_the_completion_key_is_not_part_of_the_cursor_cluster():
    """`x` 不在栏位的光标键位组里：它不是「移动」，是「写」。"""
    pane_keys = {binding.key for binding in TaskPane.BINDINGS}

    assert {"j", "k", "up", "down"} <= pane_keys, "栏位确实收着方向键（不然这条测试没意义）"
    assert not (complete_keys() & pane_keys)


async def test_the_footer_shows_the_completion_key_with_its_label():
    """视觉上与导航键分开：`x` 在 footer 上带标签显示，j/k 这些光标键不出现。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "x 完成" in text
    assert "下移" not in text and "上移" not in text, "光标键不进 footer"
