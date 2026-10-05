"""假后端自己的契约。

它是接缝一的地基，t12/t16/t17/t20 都要在上面按键，所以这里钉住两件事：
它一直满足引擎接口（t11/t13 往 ``Engine`` 上加方法时要立刻发现），
以及写操作真的被记下来了——**包括记下了什么**：一条记错的写会让上面那一层的测试静默断言
成另一件事（#39 的教训：``create`` 原来把 ``list_name=收集箱`` 写死，于是「在 工作 里建，
落在 工作」会安静地绿）。
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
    """按清单新建（工单 #39 的验收标准 8）：假后端认得落点，并且**照它**摆进缓存。

    三条一起断：落点记在 ``created_tasks`` 上、缓存里那条任务的 ``list_id`` 是它、
    那个清单的容器里真的看得见这条任务。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_list("收集箱", id=INBOX_ID, is_inbox=True)
    backend.add_list("工作", id="work")

    task_id = backend.create("写周报", list_id="work")

    created = [task for task in backend.created_tasks if task.id == task_id]
    assert len(backend.created_tasks) == 1, "新建记在那一条记录里"
    assert [(task.title, task.list_id) for task in created] == [("写周报", "work")]
    assert [task.title for task in backend.tasks_in("work").items] == ["写周报"], (
        "落点是「工作」，任务就得在「工作」里看得见"
    )
    assert [task.title for task in backend.tasks_in(INBOX_ID).items] == [], "不是收集箱"


def test_a_create_through_the_fake_records_the_tags_on_the_task():
    """标签记下来也要**落到那条任务上**（#39 顺手修的替身 bug）。

    原来它 ``created_tags.append(tuple(tags))`` 之后就不管了：请求那一侧记着、缓存里那条
    任务却没有标签，于是「新建带的标签会显示出来」这条断言无论生产代码对不对都绿。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    task_id = backend.create("写周报", list_id=INBOX_ID, tags=["周报", "工作"])

    payload = backend.source.task_payload(task_id)
    assert payload is not None
    assert payload["tags"] == ["周报", "工作"], "标签要摆进这条任务的原文里"
    assert backend.created_tags == [("周报", "工作")], "记下来的那一份也照旧"

