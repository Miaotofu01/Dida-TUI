"""引擎的读路径：视图模型从注入的缓存来，「现在」从注入的时钟来。"""

from datetime import date, datetime, timedelta, timezone

from dida.sync.engine import SyncEngine
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
