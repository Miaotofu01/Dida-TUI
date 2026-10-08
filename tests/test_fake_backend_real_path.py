"""假后端内部装的是真货（工单 #80 第一步）。

第 08 条收深分两步落地，这是第一步：``FakeBackend`` 的**内部实现**换成真引擎 +
真本地库（``Store(":memory:")``）+ 假传输层，对外那套记录字段照旧——所以既有那 23 个
引用 ``FakeBackend`` 的测试文件一行都不用改。

这个新文件证明「换的确实是真货」，而不是把第二份实现换个名字：

- :attr:`FakeBackend.source` 是**真的** ``Store``（内存 SQLite），不是内存替身；
- 一次写走的是**真写路径**：真引擎解析 id → 真库入队并乐观落库 → 真客户端把请求发到
  **假传输层**，推成功后出队。旧替身自己抄的那一份写路径**一个请求都不发**，所以
  「请求出现在传输层上」与「真库里那一行变了」这两条它都断不出来；
- 面层（TUI）按键那一半同样落在真写路径上：``→`` 进详细页、改一个字段、``esc``，
  请求照样出现在传输层上、真库里那条任务也真的改了。

更硬的那一档（认领换 id 之后编辑得动，工单 #75 那一类）走的是
``tests/test_local_id_after_claim.py``：真 ``DidaApp`` + 真 ``SyncEngine`` + 真 ``Store``
+ 假传输——#80 之后它与这里用的是**同一条接缝**。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.storage.store import Store
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)
TITLE = "交报告"


def backend() -> FakeBackend:
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(TITLE, list_name="work", id="t1")
    return fake


def test_the_local_replica_is_a_real_store():
    """本地副本是**真的**那个：内存 SQLite，不是第二份实现。"""
    fake = backend()

    assert isinstance(fake.source, Store), "替身的本地副本必须是真的 Store"
    assert isinstance(fake.transport, object)
    assert [task.title for task in fake.source.tasks()] == [TITLE], "数据摆在真库里"


async def test_a_write_goes_out_on_the_transport_and_lands_in_real_sqlite():
    """一次写走真写路径：传输层上出现请求、真库里那一行真的变了、队列推空。"""
    fake = backend()

    fake.write("t1", changes={"title": "交报告（改）"})
    pushed = await fake.push_pending()

    assert pushed == 1, "真引擎把这一笔推出去了"
    assert [request.url.path for request in fake.transport.requests] == ["/open/v1/task/t1"], (
        "请求出现在假传输层上（抄一份写路径的替身不会发请求）"
    )
    assert fake.source.task_payload("t1")["title"] == "交报告（改）", "真库里那一行真的改了"
    assert fake.source.pending() == (), "推成功就出队（真队列的记账）"


async def test_a_tui_field_edit_runs_on_the_real_write_path():
    """面层那一条：详细页改标题 → 真写路径 → 请求出现在传输层上、真库那一行变了。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(20):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        assert app.index_page().selected_id == "work"
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()

        await pilot.press("enter")  # 进标题编辑
        await pilot.press(*(["backspace"] * len(TITLE)))
        await pilot.press(*"新标题")
        await pilot.press("escape")
        await pilot.pause()
        shown = screen_text(app)

    assert "新标题" in shown, "本地当场生效（乐观写）"
    assert any(
        request.url.path == "/open/v1/task/t1" for request in fake.transport.requests
    ), "这一笔真的走完了真写路径，请求发到了假传输层上"
    assert fake.source.task_payload("t1")["title"] == "新标题", "真库里那一行真的改了"
