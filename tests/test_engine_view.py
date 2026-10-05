"""引擎的读路径：视图模型从注入的缓存来，「现在」从注入的时钟来。"""

from datetime import date, datetime, timedelta, timezone

import pytest

from dida.storage.store import Store
from dida.sync.engine import NO_DUE_TEXT, SyncEngine
from dida.sync.view import GroupKind, SyncState, TodayView
from dida.testing import InMemorySource, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_source() -> InMemorySource:
    source = InMemorySource()
    source.add_list("工作")
    source.add_list("生活")
    source.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    source.add_task("买牛奶", list_name="生活")
    source.add_task("交季度报告", list_name="工作", due=at(11, 9, 0), priority=5)
    source.state = SyncState(last_refresh_at=at(14, 12, 0), pending_count=2)
    return source


def test_view_reads_the_cache_the_test_controls():
    engine = SyncEngine(clock=ManualClock(at(14, 12, 3)), day_end="24:00", source=make_source())

    view = engine.view()

    assert [(item.name, item.unfinished) for item in view.lists] == [("工作", 2), ("生活", 1)]
    assert [group.kind for group in view.groups] == [
        GroupKind.OVERDUE,
        GroupKind.TODAY,
        GroupKind.INBOX_UNDATED,
    ]
    assert [group.count for group in view.groups] == [1, 1, 1]
    assert view.groups[0].items[0].title == "交季度报告"


def test_view_without_a_cache_is_empty_not_an_error():
    engine = SyncEngine(clock=ManualClock(at(14, 12, 3)))

    assert engine.view() == TodayView(lists=(), groups=())


def test_status_carries_the_logical_day_the_refresh_time_and_the_pending_count():
    engine = SyncEngine(clock=ManualClock(at(14, 12, 3)), day_end="24:00", source=make_source())

    status = engine.status()

    assert status.checked_at == at(14, 12, 3)
    assert status.logical_day == date(2026, 3, 14)
    assert status.last_refresh_at == at(14, 12, 0)
    assert status.pending_count == 2


def test_status_logical_day_follows_the_injected_day_end():
    """边界 04:00、凌晨两点：状态栏该显示前一天。"""
    engine = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00")

    assert engine.status().logical_day == date(2026, 3, 14)


def test_view_groups_a_last_night_due_date_into_today_at_two_in_the_morning():
    source = InMemorySource()
    source.add_list("工作")
    source.add_task("写周报", list_name="工作", due=at(14, 23, 0))
    engine = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00", source=source)

    (today,) = engine.view().groups

    assert today.kind is GroupKind.TODAY
    assert today.items[0].due_text == "今天 23:00"


# ------------------------------------------------------- 子任务：只读那两半（#43）


def item(id: str, title: str, status: int = 0, **extra: object) -> dict:
    """一份 ``ChecklistItem`` 原文：子任务状态是 ``0/1`` 那一对，日期字段叫 ``startDate``。"""
    return {"id": id, "title": title, "status": status, **extra}


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里。"""
    store.apply_refresh(lists=[{"id": "work", "name": "工作", "sortOrder": 1}], tasks=list(tasks))


def test_subtasks_show_title_and_completion_state_even_without_a_due_date(store):
    """``subtasks()`` 每一行都有标题与完成状态，**没有日期的照样在**（工单 #32 搬出来的）。

    v2 的子任务是只读显示（spec 用户故事 76），所以这一条比 v1 更要紧：行是右栏唯一能给的
    东西，缺了日期就不显示的话，大多数子任务会整行消失。没有日期读作 ``NO_DUE_TEXT``，
    不是「不该出现」。
    """
    seed(
        store,
        {
            "id": "t1",
            "projectId": "work",
            "title": "交季度报告",
            "status": 0,
            "items": [
                item("i1", "收集数据", 1),  # 完成了，而且没有日期
                item("i2", "画图表", 0),  # 没完成，也没有日期
                item("i3", "写结论", 0, startDate="2026-03-15T18:00:00+0800"),
            ],
        },
    )
    engine = SyncEngine(clock=ManualClock(at(14, 12, 3)), day_end="24:00", source=store)

    subtasks = engine.subtasks("t1")

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
    seed(store, {"id": "t1", "projectId": "work", "title": "交季度报告", "status": 0})
    engine = SyncEngine(clock=ManualClock(at(14, 12, 3)), day_end="24:00", source=store)

    assert engine.subtasks("t1") == ()
