"""无接缝：人类可读截止时间、分组与排序都是纯函数，直接测。

「现在」与 ``day_end`` 一律作为参数进来，本文件里没有时钟、没有存储。
"""

from datetime import datetime, timedelta, timezone

from dida.sync.view import (
    GroupKind,
    ListSnapshot,
    TaskSnapshot,
    format_due,
    group_tasks,
    summarize_lists,
)

TZ = timezone(timedelta(hours=8))
"""测试统一用 UTC+8，避免跑到别的时区时结果漂移。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


LISTS = [ListSnapshot(id="work", name="工作"), ListSnapshot(id="life", name="生活")]


def task(id: str, title: str, *, list_id: str = "work", **rest) -> TaskSnapshot:
    """一条未完成任务快照，只有测试关心的字段不同。"""
    return TaskSnapshot(id=id, title=title, list_id=list_id, **rest)


def test_timed_due_today_reads_as_today_plus_time():
    assert format_due(at(14, 18, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "今天 18:00"


def test_all_day_due_today_never_shows_a_time():
    assert format_due(at(14), all_day=True, now=at(14, 12, 3), day_end="24:00") == "今天"


def test_timed_due_yesterday_reads_as_yesterday_plus_time():
    assert format_due(at(13, 9, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "昨天 09:00"


def test_due_three_days_ago_reads_as_days_ago():
    assert format_due(at(11, 9, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "3 天前"


def test_no_due_date_is_visually_distinct_from_due_today():
    assert format_due(None, all_day=False, now=at(14, 12, 3), day_end="24:00") == "—"
    assert format_due(at(14), all_day=True, now=at(14, 12, 3), day_end="24:00") == "今天"


def test_due_before_the_logical_day_end_reads_as_today():
    """逻辑日边界 04:00：凌晨两点看到的昨天 23:00 截止仍是「今天」。"""
    assert format_due(at(14, 23, 0), all_day=False, now=at(15, 2, 0), day_end="04:00") == "今天 23:00"


def test_due_before_the_logical_day_start_reads_as_yesterday():
    assert format_due(at(14, 3, 0), all_day=False, now=at(15, 2, 0), day_end="04:00") == "昨天 03:00"


def test_overdue_group_is_pinned_on_top_with_its_count():
    groups = group_tasks(
        [
            task("t1", "交季度报告", due=at(11, 9, 0)),
            task("t2", "还信用卡", list_id="life", due=at(13)),
            task("t3", "写周报", due=at(14, 18, 0)),
        ],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert [group.kind for group in groups] == [GroupKind.OVERDUE, GroupKind.TODAY]
    assert [group.title for group in groups] == ["逾期", "今日"]
    assert [group.count for group in groups] == [2, 1]
    assert [item.title for item in groups[0].items] == ["交季度报告", "还信用卡"]


def test_overdue_is_judged_by_the_logical_day_not_the_natural_day():
    """边界 04:00、凌晨两点：昨天 23:00 截止还没逾期，是今日。"""
    groups = group_tasks(
        [task("t1", "写周报", due=at(14, 23, 0)), task("t2", "旧账", due=at(14, 3, 0))],
        LISTS,
        now=at(15, 2, 0),
        day_end="04:00",
    )

    overdue, today = groups
    assert overdue.kind is GroupKind.OVERDUE
    assert [item.title for item in overdue.items] == ["旧账"]
    assert [item.title for item in today.items] == ["写周报"]
    assert today.items[0].due_text == "今天 23:00"


def test_completed_tasks_never_reach_the_view():
    groups = group_tasks(
        [task("t1", "写周报", due=at(14, 18, 0)), task("t2", "已做完", due=at(14, 9, 0), completed=True)],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert [item.title for group in groups for item in group.items] == ["写周报"]


def test_tasks_without_a_due_date_form_their_own_inbox_group():
    """story 16：无日期的任务是单独一区，标题按接口契约的「收集箱无日期」。"""
    groups = group_tasks(
        [task("t1", "买牛奶", list_id="life"), task("t2", "写周报", due=at(14, 18, 0))],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert [group.kind for group in groups] == [GroupKind.TODAY, GroupKind.INBOX_UNDATED]
    assert [group.title for group in groups] == ["今日", "收集箱无日期"]
    assert [group.count for group in groups] == [1, 1]
    assert [item.title for item in groups[1].items] == ["买牛奶"]
    assert [item.due_text for item in groups[1].items] == ["—"]


def test_future_tasks_are_not_part_of_today():
    groups = group_tasks(
        [task("t1", "下周再审", due=at(20, 9, 0))],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert groups == ()


def test_dated_tasks_sort_by_due_and_undated_ones_keep_their_own_group():
    groups = group_tasks(
        [
            task("t1", "买牛奶", list_id="life"),
            task("t2", "复习 Rust", due=at(14, 21, 0)),
            task("t3", "写周报", due=at(14, 18, 0)),
        ],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    today, undated = groups
    assert [item.title for item in today.items] == ["写周报", "复习 Rust"]
    assert [item.title for item in undated.items] == ["买牛奶"]


def test_the_three_groups_keep_the_order_the_interface_contract_asks_for():
    """接口契约：逾期 / 今日 / 收集箱无日期；已完成区在中栏底部，不在这三个里。"""
    groups = group_tasks(
        [
            task("t1", "交季度报告", due=at(11, 9, 0)),
            task("t2", "写周报", due=at(14, 18, 0)),
            task("t3", "买牛奶", list_id="life"),
        ],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert [group.kind for group in groups] == [
        GroupKind.OVERDUE,
        GroupKind.TODAY,
        GroupKind.INBOX_UNDATED,
    ]
    assert [group.title for group in groups] == ["逾期", "今日", "收集箱无日期"]
    assert [group.count for group in groups] == [1, 1, 1]


def test_no_due_and_due_today_land_in_two_different_groups():
    """story 23：「没有截止时间」与「今天截止」一眼可分——连区都不是同一个。"""
    groups = group_tasks(
        [
            task("t1", "修水龙头", list_id="life", due=at(14), all_day=True),
            task("t2", "买牛奶", list_id="life"),
        ],
        LISTS,
        now=at(14, 12, 3),
        day_end="24:00",
    )

    assert [(group.title, group.count) for group in groups] == [("今日", 1), ("收集箱无日期", 1)]
    assert [item.due_text for item in groups[0].items] == ["今天"]
    assert [item.due_text for item in groups[1].items] == ["—"]


def test_undated_tasks_join_the_group_whatever_list_they_live_in():
    """分诊看的是「有没有日期」，不是「在哪个清单」。

    收集箱（``"inbox"``）之外的无日期任务也在这一区：留在今日区等于骗人，丢掉等于把
    用户刚写下的一条藏起来。清单名仍然照显示，分诊时看得出来它本来属于哪儿。
    """
    groups = group_tasks(
        [
            task("t1", "买牛奶", list_id="life"),
            task("t2", "记一笔", list_id="inbox"),
        ],
        LISTS + [ListSnapshot(id="inbox", name="收集箱")],
        now=at(14, 12, 3),
        day_end="24:00",
    )

    (undated,) = groups
    assert undated.kind is GroupKind.INBOX_UNDATED
    assert [item.title for item in undated.items] == ["买牛奶", "记一笔"]
    assert [item.list_name for item in undated.items] == ["生活", "收集箱"]


def test_empty_groups_are_left_out():
    groups = group_tasks([task("t1", "写周报", due=at(14, 18, 0))], LISTS, now=at(14, 12, 3), day_end="24:00")

    assert [group.kind for group in groups] == [GroupKind.TODAY]


def test_task_rows_carry_the_list_name_and_priority_mark():
    groups = group_tasks(
        [task("t1", "交季度报告", due=at(11, 9, 0), priority=5)],
        LISTS + [ListSnapshot(id="inbox", name="收集箱")],
        now=at(14, 12, 3),
        day_end="24:00",
    )

    item = groups[0].items[0]
    assert (item.title, item.list_name, item.priority_mark, item.priority) == ("交季度报告", "工作", "!", 5)


def test_each_list_carries_its_unfinished_badge():
    summaries = summarize_lists(
        LISTS,
        [
            task("t1", "写周报"),
            task("t2", "已做完", completed=True),
            task("t3", "买牛奶", list_id="life"),
        ],
    )

    assert [(item.name, item.unfinished) for item in summaries] == [("工作", 1), ("生活", 1)]


def test_lists_keep_their_order_and_empty_ones_show_zero():
    summaries = summarize_lists(
        LISTS + [ListSnapshot(id="study", name="学习")],
        [task("t1", "买牛奶", list_id="life")],
    )

    assert [(item.name, item.unfinished) for item in summaries] == [("工作", 0), ("生活", 1), ("学习", 0)]


def test_all_day_due_is_a_date_marker_not_a_moment():
    """全天任务的截止写的是当天 00:00：日界 04:00 时它仍然属于它写的那一天。"""
    assert format_due(at(14), all_day=True, now=at(14, 12, 3), day_end="04:00") == "今天"

    groups = group_tasks(
        [task("t1", "修水龙头", list_id="life", due=at(14), all_day=True)],
        LISTS,
        now=at(14, 12, 3),
        day_end="04:00",
    )

    assert [group.kind for group in groups] == [GroupKind.TODAY]
    assert groups[0].items[0].due_text == "今天"
