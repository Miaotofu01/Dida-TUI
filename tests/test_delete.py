"""删除（t16）：按 `d` 先问一句，确认了才写。

两条接缝：

- **接缝一（主）**：``DidaApp`` + 真引擎（``Store`` + ``DidaApiClient`` 钉在 ``FakeTransport``
  上），Pilot 按键 → 断言屏幕文本、本地缓存与假后端收到的请求。
- **接缝二（窄）**：删除的请求形状（方法、URL、无请求体），期望值来自 ``api-contracts.md``：
  ``DELETE /open/v1/project/{projectId}/task/{taskId}``，且**没有请求体**。

这一屏最要紧的一条：**确认是唯一的防线**。滴答清单的 Open API 里没有 undelete、没有回收站、
没有「已删除」列表（``api-contracts.md`` 通篇没有这一类端点），所以：

- 取消必须**一点痕迹都不留**——没有待推送改动、本地快照照旧、一个请求都没发；
- 提示语一个字都不许暗示「还能找回来」（有测试专门扫这些词）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport, ManualClock
from dida.tui.app import DidaApp, delete_prompt
from dida.tui.panes import TaskPane

from support import screen_text

TZ = timezone(timedelta(hours=8))

T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
"""「现在」：2026-03-14 中午。下面那条任务的截止时间落在同一个逻辑日里。"""

TASK_ID = "t1"
TASK_TITLE = "交季度报告"


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store) -> None:
    """库里摆一条今天的任务（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作", "sortOrder": 1}],
        tasks=[
            {
                "id": TASK_ID,
                "projectId": "work",
                "title": TASK_TITLE,
                "status": 0,
                "dueDate": "2026-03-14T18:00:00+0800",
            }
        ],
    )


def make_app(
    store: Store, *, responses: list[httpx.Response | Exception] | None = None
) -> tuple[DidaApp, SyncEngine, FakeTransport]:
    """真存储 + 真客户端（网络钉在假传输上）+ 真引擎，交给 TUI。

    这里刻意**不用** ``FakeBackend``：那条删掉的记录必须真的从本地副本里消失，
    界面才叫「当场生效」。

    ``responses`` 按顺序排在假传输的队列**前面**，第 n 个响应配第 n 个请求；不写就是每个
    请求都回一个 200。起屏时 app 只读本地缓存、不发请求（第一屏不等网络），所以队首那个
    响应落在这条测试按下的那一次写上。
    """
    transport = FakeTransport()
    for response in responses or ():
        transport.enqueue(response)
    engine = SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )
    return DidaApp(engine), engine, transport


def titles(app: DidaApp) -> list[str]:
    """屏幕上任务列里现有的任务标题（走真引擎的视图模型）。"""
    return [item.title for group in app.engine.view().groups for item in group.items]


def titles_of(engine: SyncEngine) -> list[str]:
    """同一个视图模型，直接问引擎（没有 app 的那些测试用）。"""
    return [item.title for group in engine.view().groups for item in group.items]


class _OfflineClient:
    """一个只会断网的客户端：三个写端点都抛 ``NetworkError``，删不动。

    用它代替假传输，是因为这条测试关心的不是请求长什么样，而是**推不动**之后本地那本账。
    """

    async def delete_task(self, project_id: str, task_id: str) -> None:
        raise NetworkError("连不上")

    async def complete_task(self, project_id: str, task_id: str) -> None:
        raise NetworkError("连不上")

    async def update_task(self, project_id, task_id, changes, *, snapshot=None):  # pragma: no cover
        raise NetworkError("连不上")


async def test_pressing_d_asks_before_deleting_anything(store):
    """`d` 只问一句：屏幕上是确认提示，本地与网络都还什么都没发生。"""
    seed(store)
    app, engine, transport = make_app(store)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert titles(app) == [TASK_TITLE], "任务一开始就在屏幕上"

        await pilot.press("d")
        await pilot.pause()

        text = screen_text(app)
        assert TASK_TITLE in text, "提示里点名要删的是哪一条"
        assert "删除" in text, "这是一次删除确认"

        assert not isinstance(app.screen, TaskPane), "确认浮层压在栏位上面"
        assert store.pending() == (), "还没确认：队列里一条改动都没有"
        assert store.task_payload(TASK_ID) is not None, "任务还在本地副本里"
        assert titles(app) == [TASK_TITLE], "屏幕上这一条也还在"
        assert transport.requests == [], "一个请求都没发出去"


async def test_confirming_deletes_the_task_and_pushes_the_delete_request(store):
    """`y` 之后才是删除：本地当场消失、入队一条 DELETE，推送就是那个端点。

    请求形状的期望值来自 ``api-contracts.md``：``DELETE`` +
    ``/open/v1/project/{projectId}/task/{taskId}``，**没有请求体**。
    """
    seed(store)
    app, engine, transport = make_app(store)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()

        await pilot.press("y")
        await pilot.pause()

        assert not isinstance(app.screen, TaskPane), "删除之后浮层已经关掉"
        assert "确认删除" not in screen_text(app), "提示语也跟着收了"
        assert TASK_TITLE not in screen_text(app), "这一行当场从屏幕上消失"
        assert titles(app) == [], "本地副本里也没有它了"
        assert store.task_payload(TASK_ID) is None

        await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/work/task/t1"
    assert request.content == b"", "删除端点没有请求体"
    assert len(transport.requests) == 1, "一次删除只发这一个请求"
    assert engine.status().pending_count == 0, "推成功后那个数回到 0"


# ---------------------------------------------------------------- 取消：一点痕迹都不留


@pytest.mark.parametrize("cancel_key", ["n", "escape"])
async def test_cancelling_leaves_no_queue_entry_and_no_local_change(store, cancel_key):
    """取消（``n`` / ``Esc``）：队列里没有改动、本地快照照旧、一个请求都没发。

    这一条是**断言出来的，不是假设的**：确认是删除唯一的防线，而取消的那条路要是悄悄
    入了队，用户看到的就是「我明明没删，任务却没了」——而且是永久没了。
    """
    seed(store)
    app, engine, transport = make_app(store)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert "确认删除" in screen_text(app), "前置条件：确认浮层确实开着"

        await pilot.press(cancel_key)
        await pilot.pause()

        assert not isinstance(app.screen, TaskPane), "浮层关掉了"
        assert "确认删除" not in screen_text(app), "提示语也跟着收了"
        assert store.pending() == (), "没有待推送改动"
        assert store.task_payload(TASK_ID) is not None, "本地快照照旧"
        assert titles(app) == [TASK_TITLE], "屏幕上这一条还在"
        assert TASK_TITLE in screen_text(app), "而且真的画出来了"
        assert transport.requests == [], "一个请求都没发"
        assert engine.status().pending_count == 0


async def test_pressing_d_on_an_empty_screen_asks_nothing(store):
    """空屏上按 `d`：不崩，也不弹一个没有对象可删的确认。"""
    app, engine, transport = make_app(store)  # 库里一条任务都没有

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()

        assert app.is_running
        assert "确认删除" not in screen_text(app), "没有对象可删，就别问"
        assert store.pending() == ()
        assert transport.requests == []


# ---------------------------------------------------------------- 引擎这一层：一次删除一个请求


async def test_deleting_through_the_engine_enqueues_one_delete_change(store):
    """``engine.delete(task_id)``：本地当场摘掉、队列里**只有**一条 DELETE 改动。

    这是 TUI 与写路径之间的那根线：删除没有第二个方向（没有「撤销删除」的改动类型），
    所以队列里那一条删掉之后就该是空的。
    """
    seed(store)
    app, engine, transport = make_app(store)

    engine.delete(TASK_ID)

    assert store.task_payload(TASK_ID) is None, "本地当场摘掉"
    assert titles(app) == [], "视图里也没有了"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        (TASK_ID, ChangeKind.DELETE)
    ], "队列里正好一条 DELETE，没有别的"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上"

    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "推成功后回到 0"
    assert transport.last_request.method == "DELETE"
    assert len(transport.requests) == 1, "一次删除一个请求"


# ---------------------------------------------------------------- 卡在队列里也不会复活


async def test_a_delete_that_cannot_be_pushed_does_not_come_back_on_the_next_refresh(store):
    """推不动的删除留在队列里；紧接着的一次全量刷新**不许**把这条任务放回来。

    没推成功的删除整条豁免于服务端权威（t08 的 ``_exempt_fields`` 返回 ``None``）。
    没有这条豁免，用户按了确认、看着它消失了，下一次刷新它又回来了——而服务端那边其实
    还留着，用户会以为删除失败了。
    """
    seed(store)
    engine = SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=_OfflineClient(),
    )

    engine.delete(TASK_ID)
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 1, "推不动，留在队列里等退避"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        (TASK_ID, ChangeKind.DELETE)
    ]

    # 服务端那边这条任务还在（删除没推成功），全量刷新会把它拉回来。
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作", "sortOrder": 1}],
        tasks=[
            {
                "id": TASK_ID,
                "projectId": "work",
                "title": TASK_TITLE,
                "status": 0,
                "dueDate": "2026-03-14T18:00:00+0800",
            }
        ],
    )

    assert store.task_payload(TASK_ID) is None, "删掉的任务不许刷新一下又回来"
    assert titles_of(engine) == []


async def test_a_delete_that_could_not_be_pushed_still_leaves_the_screen_empty(store):
    """网络断了：任务照样从屏幕上消失（乐观写），只是状态栏那个数还非零。

    删除在界面上**没有第二条路**——没有「删除失败，点这里恢复」。所以推不动的时候，
    唯一诚实的呈现就是「它不在了」+「待推送 1」；这条测试钉住前半句。
    """
    seed(store)
    # 这一个断网落在这次删除上（起屏只读本地缓存，不发请求）。
    app, engine, transport = make_app(store, responses=[NetworkError("连不上")])

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await engine.wait_for_pushes()

        assert TASK_TITLE not in screen_text(app), "推不动也当场消失：不看网络"
        assert titles(app) == []
        assert store.task_payload(TASK_ID) is None
        assert engine.status().pending_count == 1, "状态栏那个数还顶着，等退避重试"

    assert transport.last_request.method == "DELETE"


# ---------------------------------------------------------------- 提示语的措辞


FORBIDDEN_IN_PROMPT = ["恢复", "撤销", "撤回"]
"""提示语里不许出现的词：它们都在暗示「还能拿回来」，而 API 里没有那条路。

名单只收**同义的退路暗示**：提示语里没有「回收站」这个词位（它说的是「没有回收站」），
所以「回收站」本身不进名单——这条测试管的是措辞，不是这几个字本身。
"""


def test_the_prompt_names_the_task_and_says_it_cannot_be_undone():
    """提示语点名要删哪一条，并明说删了就没有了。"""
    prompt = delete_prompt("交季度报告")

    assert "交季度报告" in prompt, "点名是哪一条：光标按错了要能当场看出来"
    assert "找不回来" in prompt, "明说不可挽回"
    assert "没有回收站" in prompt, "说清服务端那边也没有退路"


def test_the_prompt_never_implies_the_task_can_be_recovered():
    """一个字都不许暗示「还能找回来」：确认是唯一的防线。"""
    prompt = delete_prompt("交季度报告")

    implied = [word for word in FORBIDDEN_IN_PROMPT if word in prompt]
    assert not implied, f"提示语暗示可恢复：{implied}"
