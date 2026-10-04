"""逻辑日：纯函数直接测。

「现在」是参数，不是假件——本模块不看表，测试也不需要时钟替身。
边界值全部写成字面量，与实现各算各的。
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from dida.logical_day import InvalidDayEnd, logical_day, parse_day_end

DAY = timezone(timedelta(hours=8))

try:
    TZ_NEW_YORK: ZoneInfo | None = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:  # pragma: no cover - 取决于本机 tzdata
    TZ_NEW_YORK = None


def test_day_end_24_00_means_the_logical_day_is_the_natural_day():
    now = datetime(2026, 3, 14, 21, 30, tzinfo=DAY)

    day = logical_day(now, "24:00")

    assert day.label == date(2026, 3, 14)
    assert day.start == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)
    assert day.end == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)


def test_day_end_00_00_behaves_like_24_00():
    now = datetime(2026, 3, 14, 2, 0, tzinfo=DAY)

    day = logical_day(now, "00:00")

    assert day.label == date(2026, 3, 14)
    assert day.start == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)
    assert day.end == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)


def test_day_end_04_00_runs_from_04_00_to_next_04_00():
    now = datetime(2026, 3, 14, 12, 0, tzinfo=DAY)

    day = logical_day(now, "04:00")

    assert day.label == date(2026, 3, 14)
    assert day.start == datetime(2026, 3, 14, 4, 0, tzinfo=DAY)
    assert day.end == datetime(2026, 3, 15, 4, 0, tzinfo=DAY)


def test_02_00_next_natural_day_still_belongs_to_the_previous_logical_day():
    now = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    day = logical_day(now, "04:00")

    assert day.label == date(2026, 3, 14)
    assert day.start == datetime(2026, 3, 14, 4, 0, tzinfo=DAY)
    assert day.end == datetime(2026, 3, 15, 4, 0, tzinfo=DAY)


def test_day_end_is_an_offset_from_midnight_at_whole_minutes():
    assert parse_day_end("04:30") == timedelta(hours=4, minutes=30)
    assert parse_day_end("23:59") == timedelta(hours=23, minutes=59)


def test_parse_day_end_normalises_24_00_and_00_00_to_the_same_zero_offset():
    assert parse_day_end("24:00") == timedelta(0)
    assert parse_day_end("00:00") == timedelta(0)


@pytest.mark.parametrize(
    "day_end",
    ["25:00", "24:01", "04:60", "04:30:30", "4:00", " 04:00", "banana", "", 2400, None],
)
def test_illegal_day_end_is_rejected(day_end):
    with pytest.raises(InvalidDayEnd):
        parse_day_end(day_end)


def test_logical_day_rejects_an_illegal_day_end_too():
    now = datetime(2026, 3, 14, 12, 0, tzinfo=DAY)

    with pytest.raises(InvalidDayEnd):
        logical_day(now, "25:00")


def test_the_logical_day_flips_exactly_at_day_end():
    one_second_before = datetime(2026, 3, 15, 3, 59, 59, tzinfo=DAY)
    exactly_on_it = datetime(2026, 3, 15, 4, 0, 0, tzinfo=DAY)

    assert logical_day(one_second_before, "04:00").label == date(2026, 3, 14)
    assert logical_day(exactly_on_it, "04:00").label == date(2026, 3, 15)


def test_minutes_of_day_end_are_honoured():
    before = datetime(2026, 3, 15, 4, 29, 59, tzinfo=DAY)
    after = datetime(2026, 3, 15, 4, 30, 0, tzinfo=DAY)

    assert logical_day(before, "04:30").label == date(2026, 3, 14)
    assert logical_day(after, "04:30").label == date(2026, 3, 15)


def test_contains_is_half_open_at_both_ends():
    day = logical_day(datetime(2026, 3, 14, 12, 0, tzinfo=DAY), "04:00")

    assert day.contains(day.start) is True
    assert day.contains(day.end - timedelta(seconds=1)) is True
    assert day.contains(day.end) is False


def test_the_next_logical_day_begins_where_this_one_ends():
    day = logical_day(datetime(2026, 3, 14, 12, 0, tzinfo=DAY), "04:00")

    assert logical_day(day.end, "04:00").label == date(2026, 3, 15)


def test_day_end_is_wall_clock_time_across_a_dst_transition():
    """区间两端都是墙上时间 04:00；春天那次跳变让这一天只有 23 小时。"""
    if TZ_NEW_YORK is None:
        pytest.skip("本机没有 tzdata")
    now = datetime(2026, 3, 8, 1, 30, tzinfo=TZ_NEW_YORK)  # 3/8 02:00 EST → EDT

    day = logical_day(now, "04:00")

    assert day.label == date(2026, 3, 7)
    assert day.start == datetime(2026, 3, 7, 4, 0, tzinfo=TZ_NEW_YORK)
    assert day.end == datetime(2026, 3, 8, 4, 0, tzinfo=TZ_NEW_YORK)
    # 同一个 tzinfo 相减算的是墙上时间（还是 24 小时），要换到 UTC 才是真实时长。
    elapsed = day.end.astimezone(timezone.utc) - day.start.astimezone(timezone.utc)
    assert elapsed == timedelta(hours=23)
    assert day.contains(now) is True
