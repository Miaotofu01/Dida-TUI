"""截止时间的结构化输入：**纯函数那一半**（工单 #44）。

这一层只做一件事：把用户敲进去的两个结构化串（``2026-03-15`` 与 ``18:00``）翻成一个
带时区的时刻。它不碰界面、不碰引擎、不发请求——所以这里直接测它（``implementer-template``
的「纯函数直接测」），界面与请求形状在 ``test_detail_page.py`` / ``test_engine_writes.py``
里断。

**为什么不走自然语言**：v1 的日期解析器（``date_parser.py`` / ``plan()``）在 #34 整体删掉了，
spec 明确 v2 不做自然语言日期输入。所以这里的输入形状是被钉死的：一个 ISO 日期、一个
``HH:MM`` 时刻，各自独立校验。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from dida.tui.pages.due import (
    DueInput,
    due_change,
    format_time_input,
    parse_date,
    parse_time,
)

TZ = timezone(timedelta(hours=8))
"""固定的一份 offset：这个仓库里凡是断时刻的地方都自带时区（不读系统时区）。"""


# ------------------------------------------------------------------ 日期


def test_a_structural_date_parses_to_that_calendar_day():
    """``2026-03-15`` → 那一天。用户敲的就是日历上的一个格子，不是一句话。"""
    assert parse_date("2026-03-15") == date(2026, 3, 15)
    assert parse_date("  2026-03-15  ") == date(2026, 3, 15), "两头的空白不算内容"


@pytest.mark.parametrize(
    "typed",
    [
        "2026/03/15",  # 分隔符不对
        "2026-3-15",  # 月份没有补零（文档形式要求两位）
        "明天",  # 自然语言：v2 明确不做
        "2026-02-30",  # 日历上不存在的那一天
        "2026-13-01",  # 没有第 13 个月
        "26-03-15",  # 年份两位
        "",
        "18:00",  # 时刻敲到日期那一格上
    ],
)
def test_a_date_that_is_not_a_real_calendar_day_is_rejected(typed):
    """不合法的日期**当场**被拒——绝不猜一个「大概是想说这个」的日子出来。

    ``2026-02-30`` 最要紧：``date(2026, 2, 30)`` 抛 ``ValueError``，而一个「把 30 号往后
    顺延成 3 月 2 号」的实现会静默写下一个用户没选过的日子——那种错三天后才在手机上看见。
    """
    with pytest.raises(ValueError):
        parse_date(typed)


# ------------------------------------------------------------------ 时刻


def test_a_structural_time_parses_to_that_wall_clock_moment():
    """``18:00`` / ``9:05`` → 那一刻的墙钟（用户写的是他墙上的时间）。"""
    assert parse_time("18:00") == time(18, 0)
    assert parse_time("09:05") == time(9, 5), "小时可以只写一位"
    assert parse_time(" 23:59 ") == time(23, 59)


def test_an_empty_time_field_means_the_whole_day():
    """时刻留空 = 只有日期那一天（这就是「全天」那一档，不需要用户再按一次开关）。"""
    assert parse_time("") is None
    assert parse_time("   ") is None


@pytest.mark.parametrize("typed", ["24:00", "18:60", "六点", "18-00", "18:00:30"])
def test_a_time_that_is_not_a_wall_clock_moment_is_rejected(typed):
    """`24:00` / `18:60` 不是一天里的时刻（``18:00:30`` 的秒这一层不接受，只到分钟）。"""
    with pytest.raises(ValueError):
        parse_time(typed)


def test_the_time_field_is_shown_as_two_digits():
    """回填编辑器时时刻写成 ``HH:MM``（``9:05`` → ``09:05``），与用户敲的形状一致。"""
    assert format_time_input(time(9, 5)) == "09:05"
    assert format_time_input(time(18, 0)) == "18:00"
    assert format_time_input(None) == "", "没有时刻就是空那一格"


# ------------------------------------------------------------------ 合成一个时刻


def test_a_date_and_a_time_become_an_aware_moment_in_the_tasks_zone():
    """日期 + 时刻 → **带时区**的时刻，按任务自己那个 offset 理解墙钟。

    ``due_change`` 拿到的 ``reference`` 是这条任务**当前**的截止时间：它的 offset 就是这条
    任务在这个客户端里一直显示着的那个时区。用户的墙钟（18:00）落在这个 offset 上——不是
    被换算到 UTC，也不是被换成机器本地时区。换任何一种都是「静默位移」。
    """
    reference = datetime(2026, 3, 10, 12, 0, tzinfo=TZ)

    change = due_change(
        DueInput(day=date(2026, 3, 15), at=time(18, 0), all_day=False), reference=reference
    )

    assert change == datetime(2026, 3, 15, 18, 0, tzinfo=TZ)
    assert change.utcoffset() == timedelta(hours=8), "offset 原样，不换时区"


def test_an_all_day_change_is_the_utc_midnight_of_that_day():
    """全天 = 那一天的 **UTC 午夜**（#73）：全天标记只有这一种形状。

    它按 ``due.date()`` 被读成「今天 / 明天」，所以 00:00 是它的形状、``+0000`` 是它的口径
    ——读侧按 UTC 取日期，写成本地午夜（这里 ``reference`` 是 ``+0800``）会被读成**前一天**，
    app 里与手机端都偏一天。这个答案与参考时刻的时区、以及传进来的本地时区都无关。
    """
    change = due_change(
        DueInput(day=date(2026, 3, 15), at=None, all_day=True),
        reference=datetime(2026, 3, 10, 12, 0, tzinfo=TZ),
    )

    assert change == datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc)
    assert change is not None and change.strftime("%Y-%m-%dT%H:%M:%S%z") == (
        "2026-03-15T00:00:00+0000"
    ), "写出去的就是文档那一份形状"
    assert due_change(
        DueInput(day=date(2026, 3, 15), at=None, all_day=True), reference=None, tz=TZ
    ) == change, "任务本来没有截止时间时也一样：全天标记不认任何本地时区"


def test_a_task_with_no_date_yet_falls_back_to_the_given_zone():
    """任务还没有截止时间时，offset 只能从外面给（``reference=None``）。

    给的这一份由调用方决定（详细页给系统本地时区）：文档对 ``timeZone`` 字段「写错会怎样」
    一个字都没写（api-shapes §D17），所以这里**不做** IANA 名字 → offset 的换算——那需要
    一整个 tzdata，而换算错了正是静默位移。宁可写一个说得清的本地时区。
    """
    change = due_change(
        DueInput(day=date(2026, 3, 15), at=time(9, 30), all_day=False), reference=None, tz=TZ
    )

    assert change == datetime(2026, 3, 15, 9, 30, tzinfo=TZ)


def test_clearing_the_date_is_not_a_moment_at_all():
    """日期那一格清空 = **清除截止时间**，不是一个时刻：``due_change`` 给 ``None``。

    ``None`` 与「一个 1970 年的时刻」是两件事：前者是「没有日期」，后者会在手机上出现一条
    1970 年的任务。所以清除走这一条明确的返回，而不是让调用方去猜一个空值。
    """
    assert due_change(DueInput(day=None, at=None, all_day=False), reference=None) is None
