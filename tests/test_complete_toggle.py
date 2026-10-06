"""完成 ↔ 取消完成（工单 #38）：两个方向各打哪个端点、本地怎么变、按键走得到哪一步。

三个接缝各测它该测的那一半：

- **接缝二（HTTP 传输层可注入）** 钉请求形状：「取消完成打到批量更新端点且带 ``status: 0``」
  与「批量更新只发 id、projectId、status」是两条验收标准，期望值来自 spec 的实测口径
  （``已实测的 API 事实`` 第 1 条）与 openapi 的 §A3——不是从这里再算一遍。
- **引擎 + 真存储 + 真客户端**（网络钉在接缝二上）钉本地效果：取消完成之后本地立刻不再把
  它算作已完成，**即使完成时间戳还在**；一次刷新也得经得起。
- **接缝一（内存假后端 + ``run_test()`` pilot）** 钉按键那一半：任务列表页按 ``space`` 在
  两个方向之间翻，屏幕上有一句短暂的反馈；输入框有焦点时 ``space`` 只是一个空格。

``id2error`` 那一半单独说一句：批量更新的失败**塞在 200 OK 里**（openapi :567），只按状态码
分类的实现会把整批全失败报成成功——用户看到的就是一句假的「已完成」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import BatchRejectedError, DidaError
from dida.storage.store import COMPLETED_STATUS, ChangeKind, Store
from dida.sync.engine import Completion, SyncEngine, ViewDefinition
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui import messages, theme
from dida.tui.app import DidaApp
from support import screen_text

TASK_ID = "t1"
COMPLETED_TASK_ID = "t2"
PROJECT_ID = "work"
UNCOMPLETED = 0
"""未完成的 ``status``（spec：2 是完成、0 是正常、-1 是已放弃）。"""

WIDE = (100, 30)
"""三层都走得进去的终端尺寸。"""

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
COMPLETED_AT = "2026-03-14T11:00:00+0800"
"""服务端给过的完成时间戳：取消完成**不会**把它清掉（spec 的实测事实第 1 条）。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=list(tasks),
    )


def completed_task(**extra: object) -> dict:
    """一条**服务端说已完成**的任务原文（``completedTime`` 还在、``status`` 是 2）。"""
    return {
        "id": TASK_ID,
        "projectId": PROJECT_ID,
        "title": "写周报",
        "desc": "本地那份描述",
        "priority": 5,
        "status": COMPLETED_STATUS,
        "completedTime": COMPLETED_AT,
        **extra,
    }


def make_engine(store: Store, transport: FakeTransport | None = None) -> SyncEngine:
    """接上真存储与真客户端；网络钉在假传输上（接缝二）。"""
    return SyncEngine(
        clock=ManualClock(T0),
        day_end="24:00",
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def completed_titles(engine: SyncEngine) -> list[str]:
    """屏幕上「已完成」那一段里的任务（用户真正看得见的那一份）。"""
    return [item.title for item in engine.tasks_in(PROJECT_ID).completed.items]


def open_titles(engine: SyncEngine) -> list[str]:
    """屏幕上未完成那一段里的任务。"""
    return [item.title for item in engine.tasks_in(PROJECT_ID).items]


async def test_cancelling_completion_posts_to_the_batch_endpoint_with_status_zero():
    """取消完成打到批量更新端点且带 ``status: 0``（验收标准 3、9）。

    ``POST /open/v1/task/batch``，请求体是 ``{"update": [...]}``（openapi §A3：数组最多 50 条）。
    这条用法官方文档一字未提，是实测确认的（spec 的实测事实第 1 条）——所以它是一份
    **实测口径**的断言，不是从文档抄下来的。
    """
    transport = FakeTransport(json={"id2etag": {TASK_ID: "etag-1"}, "id2error": {}})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.batch_update([{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}])

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/batch"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }


async def test_the_batch_body_carries_nothing_but_id_project_id_and_status():
    """批量更新只发 id、projectId、status（验收标准 4）。

    多给的字段**不发**：批量更新是合并语义，带上标题 / 描述 / 优先级就是拿本地那一份去
    覆盖服务端——「其余字段不因这次取消完成而改变」这句话得由请求体来兑现。
    """
    transport = FakeTransport(json={"id2etag": {}, "id2error": {}})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.batch_update(
        [
            {
                "id": TASK_ID,
                "projectId": PROJECT_ID,
                "status": UNCOMPLETED,
                "title": "本地那份标题",
                "desc": "本地那份描述",
                "priority": 5,
            }
        ]
    )

    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }


async def test_a_per_task_failure_inside_a_two_hundred_is_not_a_success():
    """批量更新把每个任务的失败塞在 ``200 OK`` 里（``id2error``，openapi :567）。

    整批全失败也是 200，所以「没抛异常」不等于「改成了」：只按状态码分类的实现会把
    「一条都没改成」报成成功，而用户看到的就是一句假的「已完成」——ADR-0002 要消灭的
    正是这种安静的错误。``id2error`` 里的码是文档给的那一张（``NOT_EXISTED``、
    ``DELETED``、``EXCEED_QUOTA``……），原样带给上层。
    """
    transport = FakeTransport(json={"id2etag": {}, "id2error": {TASK_ID: "NOT_EXISTED"}})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(BatchRejectedError) as caught:
        await client.batch_update(
            [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
        )

    assert isinstance(caught.value, DidaError), "UI 只认结构化错误"
    assert caught.value.errors == {TASK_ID: "NOT_EXISTED"}
    assert TASK_ID in str(caught.value) and "NOT_EXISTED" in str(caught.value)


# ---------------------------------------------------------------- 引擎：本地效果与队列


def test_cancelling_completion_leaves_it_unfinished_even_with_the_timestamp_still_there(store):
    """取消完成后本地立刻不再把它算作已完成，**即使完成时间戳还在**（验收标准 6）。

    本地判定「已完成」一律看 ``status``（spec：2 是完成、0 是正常、-1 是已放弃），不看有没有
    ``completedTime``——实测取消完成不会清掉那个时间戳，只按时间戳判会把这条又捞回已完成区。
    所以这一条同时钉两件事：屏幕上它回到未完成那一段，而**原文里时间戳一个字没动**、
    别的字段（标题、描述、优先级）也一个都没动。
    """
    seed(store, completed_task())
    engine = make_engine(store)
    assert completed_titles(engine) == ["写周报"], "摆进去的是一条已完成的任务"

    engine.uncomplete(TASK_ID)

    assert completed_titles(engine) == [], "取消完成之后本地立刻不再算它已完成"
    assert open_titles(engine) == ["写周报"], "它回到未完成那一段里"
    payload = store.task_payload(TASK_ID)
    assert payload["completedTime"] == COMPLETED_AT, "完成时间戳不许被这次取消完成清掉"
    assert payload["status"] == UNCOMPLETED
    assert (payload["title"], payload["desc"], payload["priority"]) == (
        "写周报",
        "本地那份描述",
        5,
    ), "其余字段不因这次取消完成而改变"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        (TASK_ID, ChangeKind.UNCOMPLETE)
    ], "一次取消完成只留一条 UNCOMPLETE 改动"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上（本地比服务端新）"


async def test_the_cancel_write_reaches_the_batch_endpoint_and_leaves_the_queue(store):
    """一次取消完成推出去的就是那一个请求，推成功就出队（验收标准 2、3、9）。

    端到端那一条：引擎的写入口 → 队列 → 客户端 → 传输层。路径与请求体是实测口径
    （spec 的实测事实第 1 条），不是从文档抄的。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={"id2etag": {TASK_ID: "etag-1"}, "id2error": {}}))
    seed(store, completed_task())
    engine = make_engine(store, transport)

    engine.uncomplete(TASK_ID)
    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/batch"
    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }
    assert store.pending() == (), "推成功了就出队，状态栏那个数跟着落回去"


async def test_a_batch_that_failed_the_task_in_a_two_hundred_is_not_reported_as_pushed(store):
    """逐条失败藏在 ``200 OK`` 里：这一笔**没推成功**，不许当成推过了。

    取消完成是「本地先动、服务端随后到」的写（ADR-0002），所以失败的表现是那条改动还在
    队列里、状态栏那个数一直非零，而不是屏幕上悄悄变成成功。这一条同时是「整批全失败也
    是 200」那张脸的守卫：只按状态码分类的实现会在这里返回 1。
    """
    transport = FakeTransport()
    transport.enqueue(
        httpx.Response(200, json={"id2etag": {}, "id2error": {TASK_ID: "NOT_EXISTED"}})
    )
    seed(store, completed_task())
    engine = make_engine(store, transport)

    engine.uncomplete(TASK_ID)
    await engine.wait_for_pushes()

    assert await engine.push_pending() == 0, "服务端说这条没改成，就不算推成功"
    (change,) = store.pending()
    assert change.task_id == TASK_ID and change.attempts == 1
    assert "NOT_EXISTED" in (change.last_error or ""), "失败的原因要留在队列里（状态栏读得到）"
    assert engine.status().pending_count == 1


def test_a_refresh_does_not_make_a_cancelled_completion_completed_again(store):
    """取消完成的本地效果经得起一次刷新（验收标准 6 的补充）。

    判据只有 ``status``：服务端**可能仍然把 ``completedTime`` 带回来**（实测取消完成不会
    清掉那个时间戳），只看时间戳就会把刚取消完成的任务又塞回已完成区。两半都钉：

    - 服务端照做了（``status: 0``、时间戳还在）→ 屏幕上它就是未完成的；
    - 服务端还没认过这一笔（``status`` 仍然是 2）→ 本地那条待推送改动豁免于服务端权威
      （ADR-0002），这一屏不会自己变回去。
    """
    seed(store, completed_task())
    engine = make_engine(store)
    engine.uncomplete(TASK_ID)

    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=[completed_task(status=UNCOMPLETED)],
    )

    assert completed_titles(engine) == [], "时间戳还在，但它已经不是已完成了"
    assert open_titles(engine) == ["写周报"]

    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=[completed_task()],
    )

    assert completed_titles(engine) == [], "那条改动还没推上去，服务端那份不许盖回来"
    assert open_titles(engine) == ["写周报"]


# ---------------------------------------------------------------- 接缝一：按键那一半


def fake_backend(*, completed_first: bool = False) -> FakeBackend:
    """一个清单、两条任务：一条没做完的、一条做完了的（做完的沉在底下）。

    ``completed_first`` 只决定哪一条排在前面——光标默认落在第一条**可停**的行上，而两条
    都可停（已完成的行也停光标：工单 #38 要靠它把 ``space`` 送到已完成那一条上）。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id=PROJECT_ID)
    fake.add_task("写周报", list_name=PROJECT_ID, id=TASK_ID, due=at(14, 18, 0))
    fake.add_task(
        "交水费",
        list_name=PROJECT_ID,
        id=COMPLETED_TASK_ID,
        completed=True,
        completed_at=at(14, 11, 0),
    )
    return fake


async def enter_the_list(pilot, app: DidaApp) -> None:
    """从清单列表页走进「工作」这个清单（光标停在第一条任务上）。"""
    for _ in range(20):
        if app.index_page().selected_id == PROJECT_ID:
            break
        await pilot.press("j")
    assert app.index_page().selected_id == PROJECT_ID, "没能把光标挪到「工作」那一行上"
    await pilot.press("enter")
    await pilot.pause()


RECENT_VIEW_ID = "recent"
"""自建视图「最近完成」的 id（#36 验收标准里点名的那个例子）。"""


def recently_completed_view() -> FakeBackend:
    """一个自建视图「最近完成」：视图里**含已完成成员**（视图不是容器，成员由求值给）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id=PROJECT_ID)
    fake.add_task("写周报", list_name=PROJECT_ID, id=TASK_ID, due=at(14, 18, 0))
    fake.add_task(
        "交水费",
        list_name=PROJECT_ID,
        id=COMPLETED_TASK_ID,
        completed=True,
        completed_at=at(14, 11, 0),
    )
    fake.add_view(
        "最近完成",
        id=RECENT_VIEW_ID,
        completion=Completion.COMPLETED,
        completed_days=7,
    )
    return fake


async def enter_view(pilot, app: DidaApp, view_id: str) -> None:
    """从清单列表页走进一个视图（光标从收集箱往下走到那一行，再 ``enter``）。"""
    for _ in range(20):
        if app.index_page().selected_id == view_id:
            break
        await pilot.press("j")
    assert app.index_page().selected_id == view_id, f"没能把光标挪到视图 {view_id} 那一行上"
    await pilot.press("enter")
    await pilot.pause()


async def test_space_completes_the_task_under_the_cursor_and_says_so():
    """任务列表页按 ``space`` 把未完成任务标记完成，并立即推送（验收标准 1、5、8）。

    接缝一那一条完整的路：真按键 → 页面把这一下发出去 → 假后端收到 ``complete``。
    反馈那一句也在这里钉：用户按下去得有个回声（用户故事 43）。
    """
    fake = fake_backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await enter_the_list(pilot, app)
        assert app.tasks_page().selected_id == TASK_ID, "光标该停在没做完的那一条上"

        await pilot.press("space")
        await pilot.pause(0.2)
        text = screen_text(app)

    assert fake.completed == [TASK_ID], "按 space 要把这一条交给引擎（并立即推送）"
    assert fake.uncompleted == [], "没做完的那一条不该走取消完成"
    assert messages.completed_message("写周报") in text, f"按下去要有一句短暂的反馈：\n{text}"


def line_with(text: str, needle: str) -> str:
    """屏幕上写着 ``needle`` 的那一行（没有就是测试写错了）。"""
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{text}")


async def test_space_again_on_a_completed_task_turns_it_back_to_unfinished():
    """再按 ``space`` 把已完成任务改回未完成，并立即推送（验收标准 2、5、8）。

    已完成的行停在列表最底（工单 #37），而它**可以停光标**——第二个方向就是从那里送出去的
    （spec 用户故事 54：「这样我看得到它们、也才有机会对它们取消完成」）。回来之后屏幕上
    不再有那个「已完成」的前缀记号，它回到未完成那一段里。
    """
    fake = fake_backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await enter_the_list(pilot, app)
        await pilot.press("j")  # 光标落到已完成那一条上（它沉在最底）
        assert app.tasks_page().selected_id == COMPLETED_TASK_ID, "光标走不到已完成的行上"

        await pilot.press("space")
        await pilot.pause(0.2)
        text = screen_text(app)

    assert fake.uncompleted == [COMPLETED_TASK_ID], "按 space 要把取消完成交给引擎（并立即推送）"
    assert fake.completed == [], "这一条已经完成了，不该再走完成那一半"
    assert messages.uncompleted_message("交水费") in text, f"按下去要有一句短暂的反馈：\n{text}"
    assert theme.DONE_MARK not in line_with(text, "交水费"), "它不再是已完成的样子了"


async def test_space_on_a_completed_row_in_a_view_uncompletes_it_not_completes_it():
    """视图里的已完成行：``space`` 走的是**取消完成**那一半（用户故事 42，工单 #58 的 S2）。

    视图不是容器，它的成员由求值给：已完成的那条与未完成的**落在同一段里**（#36 的
    「最近完成」这类自定义视图就是按完成状态筛出来的），而真实清单的已完成那几条在
    读模型的已完成区里。所以「这一条算不算已完成」只能读**行上那一位**——按「它从哪一段
    出来」判，视图这一边就会把做完的当成没做完，``space`` 于是朝反方向写：用户想取消完成，
    发出去的却是「完成」。这一条断的就是那个方向。

    做完了的那条在视图里也**可以停光标**（工单 #38）：不然这个键永远送不到它身上。
    """
    fake = recently_completed_view()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await enter_view(pilot, app, RECENT_VIEW_ID)
        assert app.tasks_page().selected_id == COMPLETED_TASK_ID, "光标该停在视图里那一条上"

        await pilot.press("space")
        await pilot.pause(0.2)
        text = screen_text(app)

    assert fake.uncompleted == [COMPLETED_TASK_ID], "视图里的已完成行按 space 要走取消完成"
    assert fake.completed == [], "它已经完成了，不许再走完成那一半（方向反了就是写错）"
    assert messages.uncompleted_message("交水费") in text, f"按下去要有一句短暂的反馈：\n{text}"


async def test_space_on_a_finished_row_in_a_view_queues_the_uncomplete_not_a_second_complete(store):
    """同一件事在真库上：队列里落下的是一条 ``UNCOMPLETE``，状态栏那个数照着走（工单 #58 的 S2）。

    接缝二那一半——假后端只记下「哪一个方向被调了」，这一条看的是**真的写出去的是什么**：
    一次取消完成在队列里留一条 ``UNCOMPLETE``，于是「待推送 1」说的是用户的意图。写错方向
    时这里留下的是 ``COMPLETE``，屏幕上却什么都不会变（它本来就是完成的），用户永远发现不了。
    """
    seed(store, completed_task())
    store.save_view(
        ViewDefinition(
            id=RECENT_VIEW_ID, name="最近完成", completion=Completion.COMPLETED, completed_days=7
        )
    )
    engine = SyncEngine(clock=ManualClock(T0), day_end="24:00", source=store, push_on_change=False)
    app = DidaApp(engine)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_view(pilot, app, RECENT_VIEW_ID)
        await pilot.press("space")
        await pilot.pause(0.2)

    assert [change.kind for change in store.pending()] == [ChangeKind.UNCOMPLETE], (
        "从视图里取消完成落下的必须是取消完成那一笔"
    )
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上（本地比服务端新）"


async def test_space_in_a_focused_input_is_a_space_and_completes_nothing():
    """``space`` 在输入框有焦点时不影响输入：该打空格就打空格（验收标准 7、用户故事 117）。

    键绑在**任务列表页**上，不是绑在 app 上（spec 的键位表：「任务列表页 | ``space`` |
    完成 ↔ 取消完成」）。输入框有焦点时那一下属于那一格，而层二的绑定在浮层开着时根本
    够不着（浮层的绑定链截断在最后一个模态控件上，工单 #47 实测）。这里走真按键：
    在新建清单的表单里打一个带空格的名字，交上去的名字里那个空格得在。
    """
    fake = fake_backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("n")  # 层一：新建（#36 起先问一句「清单还是视图」）
        await pilot.pause()
        await pilot.press("enter")  # 答「清单」（默认那一档），才进得了清单那张表单
        await pilot.pause()
        await pilot.press(*"work report")  # 中间那一下是 space
        await pilot.press("enter")
        await pilot.pause()

    assert fake.created_lists == [("work report", None)], "输入框里的空格必须还是一个空格"
    assert (fake.completed, fake.uncompleted) == ([], []), "输入框里按 space 不许完成任何东西"


def test_space_is_bound_on_the_task_layer_only():
    """``space`` 是任务列表页那一层的键，别的层与全局那一层都没有它（验收标准 7）。

    为什么这条值得单独钉：聚焦的 ``Input`` 会把 ``space`` 当字符吃掉，**app 级绑定静默失效**
    （notes/terminal-input-evidence §3 在真 pty 里量过）。所以「输入框有焦点时它不影响输入」
    这件事有两个前提，这里钉的是后一个：它只在**任务列表页**这一层绑着——全局绑一下，
    别的页面上按空格也会去完成某条任务，而那些页面将来是会有输入框的（#39 的标题框）。
    """
    from dida.tui.keys import GLOBAL, LAYER_DETAIL, LAYER_INDEX, LAYER_TASKS, bindings_for

    def keys_of(layer: str) -> set[str]:
        return {key for binding in bindings_for(layer) for key in binding.key.split(",")}

    assert "space" in keys_of(LAYER_TASKS), "完成 / 取消完成那一行挂在任务列表页上"
    for other in (GLOBAL, LAYER_INDEX, LAYER_DETAIL):
        assert "space" not in keys_of(other), f"{other} 这一层不该绑 space"


async def test_space_on_the_list_index_page_does_nothing_at_all():
    """层一按 ``space`` 什么都不发生：它只绑在任务列表页上（验收标准 7）。

    用探针断「什么都不该发生」：种一条任务进缓存、**谁都没通知界面**，按完 key 之后
    屏幕逐字符与之前相同，而假后端一条写都没收到。
    """
    fake = fake_backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = screen_text(app)

        await pilot.press("space")
        await pilot.pause()

        after = screen_text(app)

    assert (fake.completed, fake.uncompleted) == ([], []), "层一按 space 不许完成任何东西"
    assert after == before, f"层一按 space 屏幕不该有任何变化：\n{after}"
