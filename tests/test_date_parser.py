"""日期解析器：纯函数直接测，没有任何假件。

「现在」与 ``day_end`` 都是参数——本模块不看表、不读配置，测试也不需要时钟替身。
期望值全部写成字面量（`2026-03-14` 是周六），与实现各算各的。
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import dida.date_parser
from dida.date_parser import parse

DAY = timezone(timedelta(hours=8))

#: 2026-03-14 是周六。
NOW = datetime(2026, 3, 14, 10, 0, tzinfo=DAY)


def parse_at(text: str, now: datetime = NOW, day_end: str = "00:00"):
    """默认在 2026-03-14（周六）10:00、``day_end = "00:00"``（不偏移）下解析。"""
    return parse(text, now=now, day_end=day_end)


def test_the_two_spellings_of_no_offset_agree():
    """ADR-0003 的规范形式是 ``"00:00"``；配置层会把 ``"24:00"`` 折成它，解析器两者都认。"""
    assert parse_at("明天", day_end="00:00") == parse_at("明天", day_end="24:00")
    assert parse("明天", now=NOW) == parse_at("明天")  # 省略 day_end 就是「不偏移」


def test_today_means_the_current_logical_day():
    parsed = parse_at("今天")

    assert parsed.title == ""
    assert parsed.due == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)
    assert parsed.all_day is True
    assert parsed.diagnostics == ()


def test_today_and_its_alias_agree():
    assert parse_at("今日").due == parse_at("今天").due


def test_tomorrow_and_the_day_after_are_relative_to_the_current_logical_day():
    assert parse_at("明天").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("明日").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("后天").due == datetime(2026, 3, 16, 0, 0, tzinfo=DAY)


def test_relative_days_count_from_the_logical_day_not_the_natural_day():
    """凌晨 2 点、``day_end = "04:00"`` 时，逻辑日还是 3/14。"""
    night_owl = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    assert parse_at("今天", night_owl, "04:00").due == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)
    assert parse_at("明天", night_owl, "04:00").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("后天", night_owl, "04:00").due == datetime(2026, 3, 16, 0, 0, tzinfo=DAY)


def test_the_date_is_taken_out_of_the_title():
    parsed = parse_at("明天交季度报告")

    assert parsed.title == "交季度报告"
    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)


def test_a_line_without_any_date_is_silent_not_a_diagnostic():
    parsed = parse_at("交季度报告")

    assert parsed.title == "交季度报告"
    assert parsed.due is None
    assert parsed.all_day is False
    assert parsed.priority is None
    assert parsed.tags == ()
    assert parsed.diagnostics == ()


def test_a_line_of_pure_date_has_an_empty_title():
    assert parse_at("  明天  ").title == ""


def test_a_weekday_means_the_coming_one():
    """今天是周六 3/14。"""
    assert parse_at("周一").due == datetime(2026, 3, 16, 0, 0, tzinfo=DAY)
    assert parse_at("周日").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("周天").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("周三").due == datetime(2026, 3, 18, 0, 0, tzinfo=DAY)


def test_weekday_also_accepts_the_xingqi_spelling():
    assert parse_at("星期三").due == parse_at("周三").due
    assert parse_at("星期一").due == parse_at("周一").due


def test_a_weekday_that_is_today_means_today():
    wednesday = datetime(2026, 3, 18, 9, 0, tzinfo=DAY)

    assert parse_at("周三", wednesday).due == datetime(2026, 3, 18, 0, 0, tzinfo=DAY)


def test_a_weekday_already_passed_this_week_means_the_coming_one_not_the_past():
    wednesday = datetime(2026, 3, 18, 9, 0, tzinfo=DAY)

    assert parse_at("周一", wednesday).due == datetime(2026, 3, 23, 0, 0, tzinfo=DAY)


def test_next_week_weekday_is_the_next_natural_week():
    """周三 3/18 说「下周三」= 下一个自然周（3/23–3/29）的周三 3/25。"""
    wednesday = datetime(2026, 3, 18, 9, 0, tzinfo=DAY)
    thursday = datetime(2026, 3, 19, 9, 0, tzinfo=DAY)

    assert parse_at("下周三", wednesday).due == datetime(2026, 3, 25, 0, 0, tzinfo=DAY)
    assert parse_at("下周三", thursday).due == datetime(2026, 3, 25, 0, 0, tzinfo=DAY)


def test_next_week_weekday_from_sunday_is_the_week_that_starts_the_next_day():
    """周日 3/15 所属的自然周是 3/9–3/15，下一个自然周的周三才是 3/18。"""
    sunday = datetime(2026, 3, 15, 9, 0, tzinfo=DAY)

    assert parse_at("下周三", sunday).due == datetime(2026, 3, 18, 0, 0, tzinfo=DAY)
    assert parse_at("下周日", sunday).due == datetime(2026, 3, 22, 0, 0, tzinfo=DAY)


def test_next_week_beats_plain_weekday_in_the_scan():
    """周六 3/14 所在的一周是 3/9–3/15，下一个自然周的周一是 3/16。"""
    assert parse_at("下周一").due == datetime(2026, 3, 16, 0, 0, tzinfo=DAY)
    assert parse_at("下周一").title == ""


def test_plus_days_counts_from_the_logical_day():
    assert parse_at("+3d").due == datetime(2026, 3, 17, 0, 0, tzinfo=DAY)
    assert parse_at("+3天").due == datetime(2026, 3, 17, 0, 0, tzinfo=DAY)
    assert parse_at("+0d").due == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)


def test_plus_days_counts_from_the_logical_day_not_the_natural_one():
    night_owl = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    assert parse_at("+3d", night_owl, "04:00").due == datetime(2026, 3, 17, 0, 0, tzinfo=DAY)


def test_month_day_is_this_year():
    assert parse_at("3-15").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("3/15").due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parse_at("12-31").due == datetime(2026, 12, 31, 0, 0, tzinfo=DAY)


def test_month_day_that_has_passed_rolls_to_next_year():
    assert parse_at("1-01").due == datetime(2027, 1, 1, 0, 0, tzinfo=DAY)
    assert parse_at("3/13").due == datetime(2027, 3, 13, 0, 0, tzinfo=DAY)


def test_month_day_equal_to_the_logical_day_is_today_not_next_year():
    assert parse_at("3-14").due == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)


def test_month_day_uses_the_logical_day_not_the_natural_one():
    """凌晨 2 点、``day_end = "04:00"``：逻辑日 3/14，所以 3-14 还没过去。"""
    night_owl = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    assert parse_at("3-14", night_owl, "04:00").due == datetime(2026, 3, 14, 0, 0, tzinfo=DAY)


def test_plus_days_and_month_day_come_out_of_the_title():
    assert parse_at("+3d 交季度报告").title == "交季度报告"
    assert parse_at("3-15交季度报告").title == "交季度报告"


def test_a_bare_time_that_is_still_ahead_lands_today():
    parsed = parse_at("14:00 开会")

    assert parsed.due == datetime(2026, 3, 14, 14, 0, tzinfo=DAY)
    assert parsed.all_day is False
    assert parsed.title == "开会"


def test_a_bare_time_that_has_passed_lands_on_the_next_logical_day():
    """10:00 说 9:30 只能指明天。"""
    parsed = parse_at("9:30 开会", datetime(2026, 3, 14, 10, 0, tzinfo=DAY))

    assert parsed.due == datetime(2026, 3, 15, 9, 30, tzinfo=DAY)
    assert parsed.title == "开会"


def test_a_bare_time_that_has_passed_rolls_to_the_next_logical_day_when_day_end_is_late():
    """凌晨 2:00、``day_end = "04:00"``：当前逻辑日是 3/14，1 点已经过去，下一个逻辑日是 3/15。

    「下一个逻辑日」的 1 点落在 3/16 01:00——3/15 01:00 还在当前这个逻辑日里。
    """
    night_owl = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    assert parse_at("1:00", night_owl, "04:00").due == datetime(2026, 3, 16, 1, 0, tzinfo=DAY)


def test_a_bare_time_still_ahead_inside_a_late_logical_day_stays_put():
    night_owl = datetime(2026, 3, 15, 2, 0, tzinfo=DAY)

    assert parse_at("3:00", night_owl, "04:00").due == datetime(2026, 3, 15, 3, 0, tzinfo=DAY)


def test_a_time_supplements_an_already_parsed_date():
    parsed = parse_at("明天下午3点交季度报告")

    assert parsed.due == datetime(2026, 3, 15, 15, 0, tzinfo=DAY)
    assert parsed.all_day is False
    assert parsed.title == "交季度报告"


def test_a_time_supplements_a_month_day():
    assert parse_at("3-15 14:00").due == datetime(2026, 3, 15, 14, 0, tzinfo=DAY)


def test_an_explicit_date_is_respected_even_when_the_time_has_passed():
    """「今天 15:00」写在 16:00，用户点明了今天，就不顺延。"""
    parsed = parse_at("今天15:00", datetime(2026, 3, 14, 16, 0, tzinfo=DAY))

    assert parsed.due == datetime(2026, 3, 14, 15, 0, tzinfo=DAY)


def test_chinese_hour_names_map_onto_the_24_hour_clock():
    assert parse_at("明天上午9点").due == datetime(2026, 3, 15, 9, 0, tzinfo=DAY)
    assert parse_at("明天中午12点").due == datetime(2026, 3, 15, 12, 0, tzinfo=DAY)
    assert parse_at("明天凌晨1点").due == datetime(2026, 3, 15, 1, 0, tzinfo=DAY)
    assert parse_at("明天下午3点").due == datetime(2026, 3, 15, 15, 0, tzinfo=DAY)
    assert parse_at("明天晚上8点").due == datetime(2026, 3, 15, 20, 0, tzinfo=DAY)


def test_chinese_minutes_are_honoured():
    assert parse_at("明天下午3点半").due == datetime(2026, 3, 15, 15, 30, tzinfo=DAY)
    assert parse_at("明天晚上8点15分").due == datetime(2026, 3, 15, 20, 15, tzinfo=DAY)


def test_a_plain_hour_without_a_period_is_left_visible_in_the_title():
    """语法表只认「14:00」和带时段的「下午3点」；光写「9点」分不清上午晚上，留在标题里。"""
    parsed = parse_at("明天9点开会")

    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parsed.all_day is True
    assert parsed.title == "9点开会"


def test_an_ordinal_dian_inside_chinese_text_is_not_a_time():
    """「第3点建议」是「第三点」，不是 3 点钟——「点」在中文里不止一个意思。"""
    parsed = parse_at("看一下第3点建议")

    assert parsed.due is None
    assert parsed.title == "看一下第3点建议"


def test_the_canonical_line_parses_into_title_due_priority_and_tag():
    parsed = parse_at("明天下午3点交季度报告 !高 #工作")

    assert parsed.title == "交季度报告"
    assert parsed.due == datetime(2026, 3, 15, 15, 0, tzinfo=DAY)
    assert parsed.all_day is False
    assert parsed.priority == 5
    assert parsed.tags == ("工作",)


def test_priority_words_map_onto_the_server_scale():
    assert parse_at("交报告 !高").priority == 5
    assert parse_at("交报告 !中").priority == 3
    assert parse_at("交报告 !低").priority == 1


def test_priority_digits_map_onto_the_server_scale():
    """api-contracts.md 第 3 条：档位是 0/1/3/5，中 = 3 而不是 2，``!3`` 就是中。"""
    assert parse_at("交报告 !1").priority == 1
    assert parse_at("交报告 !2").priority == 3
    assert parse_at("交报告 !3").priority == 3
    assert parse_at("交报告 !5").priority == 5
    assert parse_at("交报告 !0").priority == 0


def test_priority_is_taken_out_of_the_title():
    parsed = parse_at("交报告 !高")

    assert parsed.title == "交报告"
    assert parsed.tags == ()


def test_a_lone_bang_is_not_a_priority_attempt():
    parsed = parse_at("好! 交报告")

    assert parsed.priority is None
    assert parsed.title == "好! 交报告"
    assert parsed.diagnostics == ()


def test_tags_are_collected_in_order_and_glued_to_chinese_text():
    assert parse_at("交报告 #工作 #季度").tags == ("工作", "季度")
    assert parse_at("交报告#工作").tags == ("工作",)
    assert parse_at("交报告#工作").title == "交报告"


def test_two_hashes_together_are_two_tags():
    """``#a#b`` 是两个标签：``#`` 是分隔符，不会跑进标签名里。"""
    assert parse_at("交报告 #工作#生活").tags == ("工作", "生活")


def test_a_repeated_tag_is_kept_once():
    assert parse_at("交报告 #工作 #工作").tags == ("工作",)


def test_a_tag_stops_at_sentence_punctuation():
    parsed = parse_at("交报告 #工作。")

    assert parsed.tags == ("工作",)
    assert parsed.title == "交报告"


def test_a_hash_with_nothing_after_it_stays_in_the_title():
    """光秃秃的 ``#`` 是标点，不是标签——也没有诊断，它本来就不是日期。"""
    parsed = parse_at("交报告 #")

    assert parsed.tags == ()
    assert parsed.title == "交报告 #"
    assert parsed.diagnostics == ()


def test_a_date_that_looks_like_a_date_but_is_not_one_is_reported():
    """``13-45`` 是最典型的例子：像日期，但不是日期。"""
    parsed = parse_at("13-45 交报告")

    assert len(parsed.diagnostics) == 1
    assert parsed.diagnostics[0].token == "13-45"
    assert parsed.diagnostics[0].code == "invalid_date"
    assert "13-45" in parsed.diagnostics[0].message
    assert parsed.due is None
    assert parsed.title == "交报告"


def test_impossible_month_days_are_reported_too():
    for token in ("2-30", "3/45", "0-0", "13/1"):
        parsed = parse_at(f"{token} 交报告")

        assert [d.code for d in parsed.diagnostics] == ["invalid_date"], token
        assert parsed.diagnostics[0].token == token


def test_an_impossible_time_is_reported():
    for token in ("25:00", "14:75"):
        parsed = parse_at(f"交报告 {token}")

        assert [d.code for d in parsed.diagnostics] == ["invalid_time"], token
        assert parsed.diagnostics[0].token == token
        assert parsed.title == "交报告"


def test_an_unknown_priority_is_reported():
    """``!9`` 是想写优先级但没写成——照报不误，不留给标题。"""
    parsed = parse_at("交报告 !9")

    assert [d.code for d in parsed.diagnostics] == ["invalid_priority"]
    assert parsed.diagnostics[0].token == "!9"
    assert parsed.priority is None
    assert parsed.title == "交报告"


def test_every_failure_is_reported_in_the_order_it_was_written():
    parsed = parse_at("13-45 交报告 !9 25:00")

    assert [d.token for d in parsed.diagnostics] == ["13-45", "!9", "25:00"]
    assert [d.code for d in parsed.diagnostics] == [
        "invalid_date",
        "invalid_priority",
        "invalid_time",
    ]


def test_a_failed_date_does_not_take_a_parsable_time_down_with_it():
    """日期失败只报日期；时刻照常按「只给时刻」的规矩落位。"""
    parsed = parse_at("13-45 15:00", datetime(2026, 3, 14, 10, 0, tzinfo=DAY))

    assert [d.code for d in parsed.diagnostics] == ["invalid_date"]
    assert parsed.due == datetime(2026, 3, 14, 15, 0, tzinfo=DAY)


def test_a_failed_date_never_leaks_into_the_title_while_being_reported():
    parsed = parse_at("13-45")

    assert parsed.title == ""
    assert len(parsed.diagnostics) == 1


def test_a_written_year_is_honoured_as_written():
    """语法表没列带年份的写法，但它必须被认出来——否则「2026-」会留在标题里。"""
    parsed = parse_at("2026-03-15 交报告")

    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parsed.title == "交报告"


def test_a_written_year_is_not_rolled_forward_even_when_it_has_passed():
    parsed = parse_at("2020-01-01 交报告")

    assert parsed.due == datetime(2020, 1, 1, 0, 0, tzinfo=DAY)
    assert parsed.diagnostics == ()


def test_an_absurd_offset_is_reported_instead_of_crashing():
    parsed = parse_at("+99999999d 交报告")

    assert [d.code for d in parsed.diagnostics] == ["invalid_date"]
    assert parsed.due is None


def test_an_empty_line_parses_into_an_empty_task():
    for text in ("", "   ", "\t"):
        parsed = parse_at(text)

        assert parsed.title == ""
        assert parsed.due is None
        assert parsed.priority is None
        assert parsed.tags == ()
        assert parsed.diagnostics == ()


def test_a_line_of_only_tags_and_priority_has_an_empty_title():
    parsed = parse_at("#工作 !高")

    assert parsed.title == ""
    assert parsed.priority == 5
    assert parsed.tags == ("工作",)


def test_due_carries_the_timezone_of_now():
    utc = timezone.utc

    parsed = parse("明天", now=datetime(2026, 3, 14, 10, 0, tzinfo=utc))

    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=utc)


def test_a_naive_now_stays_naive_instead_of_inventing_a_timezone():
    parsed = parse("明天", now=datetime(2026, 3, 14, 10, 0))

    assert parsed.due == datetime(2026, 3, 15, 0, 0)


def test_the_moment_is_mandatory_so_nobody_can_silently_read_the_clock():
    with pytest.raises(TypeError):
        parse("明天")


def test_the_parser_never_looks_at_the_clock_or_the_calendar():
    """纯函数：「现在」只能从参数进来。AST 守着这条，别让 ``datetime.now()`` 溜回来。"""
    clock_reads = {
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "date.today",
        "time.time",
        "time.monotonic",
    }
    tree = ast.parse(Path(dida.date_parser.__file__).read_text(encoding="utf-8"))
    called = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}

    assert not called & clock_reads


def test_a_date_inside_a_tag_is_just_a_tag():
    parsed = parse_at("#明天 交报告")

    assert parsed.tags == ("明天",)
    assert parsed.due is None
    assert parsed.title == "交报告"


def test_a_tag_can_touch_a_date():
    parsed = parse_at("明天#工作")

    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parsed.tags == ("工作",)
    assert parsed.title == ""


def test_the_first_date_wins_when_a_line_writes_two():
    parsed = parse_at("明天 3-20 交报告")

    assert parsed.due == datetime(2026, 3, 15, 0, 0, tzinfo=DAY)
    assert parsed.title == "交报告"


def test_a_weekday_combines_with_a_time():
    """周六 3/14 的下一个自然周是 3/16–3/22，它的周三就是 3/18。"""
    parsed = parse_at("下周三下午3点 评审")

    assert parsed.due == datetime(2026, 3, 18, 15, 0, tzinfo=DAY)
    assert parsed.all_day is False
    assert parsed.title == "评审"
