"""假后端自己的契约。

#80 之后它内部装的就是**真货**（真引擎 + 真本地库 + 假传输层），#86 又把没人读的账本与
抄来的判断清掉了。所以这个文件只剩三件事：

- 它一直满足引擎接口（往 ``Engine`` 上加方法时要立刻发现）；
- 账本记的确实是**收到的调用**——记错一条，上面那一层的测试就会静默断言成另一件事
  （#39 的教训：``create`` 原来把 ``list_name=收集箱`` 写死，于是「在 工作 里建、落在
  工作」会安静地绿）；
- 「摆数据 + 委托」这条路上，新建真的落在交给它的那个清单里、标签真的读得出来。

「替身说得跟真货一样」那一类断言**不再需要**：替身不再自己说话，落点、字段与守卫都在
真引擎与真库里发生。同一件事各自的证据在 ``tests/test_engine_writes.py``（真引擎那条接缝）。
"""

from datetime import datetime, timedelta, timezone

from dida.sync.engine import INBOX_ID, Engine
from dida.testing import FakeBackend, ManualClock

T0 = datetime(2026, 3, 14, 12, 3, tzinfo=timezone(timedelta(hours=8)))


def test_fake_backend_satisfies_the_engine_interface():
    assert isinstance(FakeBackend(clock=ManualClock(T0)), Engine)


async def test_fake_backend_records_the_write_calls_later_tickets_assert_on():
    backend = FakeBackend(clock=ManualClock(T0))

    await backend.refresh()
    backend.complete("t1")
    backend.defer("t2")

    assert (backend.refreshes, backend.completed, backend.deferred) == (1, ["t1"], ["t2"])


def test_a_create_through_the_fake_lands_in_the_list_it_was_given():
    """替身把 ``list_id`` 原样递给真引擎：新建落在交给它的那个清单里（#39 的验收标准 8）。

    断的是**真读路径**（``tasks_in``）——真库写了什么、引擎读出来什么。原来这一条还兼着
    「落点别被替身写死」；替身不再自己摆落点，那个风险随 #80 一起没了，剩下的是这条接线。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_list("收集箱", id=INBOX_ID, is_inbox=True)
    backend.add_list("工作", id="work")

    task_id = backend.create("写周报", list_id="work")

    assert [task.title for task in backend.tasks_in("work").items] == ["写周报"], (
        "落点是「工作」，任务就得在「工作」里看得见"
    )
    assert [task.title for task in backend.tasks_in(INBOX_ID).items] == [], "不是收集箱"
    assert [(task.id, task.list_id) for task in backend.created_tasks] == [(task_id, "work")], (
        "账本记下的落点与真库读出来的是同一个"
    )


def test_a_create_through_the_fake_records_the_tags_on_the_task():
    """标签落到那条任务上、从读路径拿得到，账本也照旧记着（#39 顺手修的替身 bug）。

    原来它 ``created_tags.append(tuple(tags))`` 之后就不管了：请求那一侧记着、缓存里那条
    任务却没有标签，于是「新建带的标签会显示出来」这条断言无论生产代码对不对都绿。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    task_id = backend.create("写周报", list_id=INBOX_ID, tags=["周报", "工作"])

    detail = backend.task_detail(task_id)
    assert detail is not None
    assert detail.tags == ("周报", "工作"), "标签要落在那条任务上（不是只记在账本里）"
    assert backend.created_tags == [("周报", "工作")], "记下来的那一份也照旧"


def test_a_planted_pending_count_is_a_real_queue_not_an_overlay():
    """摆出来的「待推送 N」是**真队列里的 N 笔真改动**（#86）。

    原来 ``status()`` 上盖了一个摆进来的数——替身说得出真库说不出的状态。现在这个参数改成
    真的入队，所以「状态栏那个数」与「本地库算出来的那个数」永远是同一份账。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_list("工作", id="work")
    backend.add_task("写周报", list_name="work", id="t1")

    backend.set_sync_state(pending_count=2)

    assert backend.status().pending_count == 2
    assert backend.source.pending_count() == 2, "真账目算出来也是 2，不是只有 status() 这么说"
    assert len(backend.source.pending()) == 2


def test_status_reports_the_count_the_real_queue_derives():
    """没摆过的时候，那个数就是真库从队列表里算出来的那一个——没有东西在暗中顶替它。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_list("工作", id="work")
    backend.add_task("写周报", list_name="work", id="t1")
    assert backend.status().pending_count == 0

    backend.write("t1", changes={"title": "写周报（改）"})  # 真写 → 真入队

    assert backend.source.pending_count() == 1
    assert backend.status().pending_count == 1, "status() 说的就是真队列里那一笔"

