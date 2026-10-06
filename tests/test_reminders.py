"""提醒那一格：``TRIGGER:<ISO-8601 时长>`` 读成人话（工单 #55）。

这一格是**只读**的（详细页不编辑提醒），所以它唯一的职责就是说明白——而它原来把服务端原文
直接摊给用户看（``TRIGGER:P0DT9H0M0S``）。

**接缝一**：纯函数 :func:`dida.tui.pages.detail.reminder_text` 直接测；页面那几条走真
``DidaApp`` + ``FakeBackend`` + Pilot，断屏幕上出现了什么。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.pages.detail import reminder_text
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)
NARROW = (30, 20)
"""窄终端：人话比原文长，这一格要折行折得对（工单 #55 的第 4 条）。"""


def backend(*reminders: str) -> FakeBackend:
    """一条带提醒的任务（提醒是服务端原文那种字符串）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(
        "交季度报告",
        list_name="work",
        id="t1",
        due=T0.replace(hour=18, minute=0),
        content="记得附上上周的对比数据",
        raw={"reminders": list(reminders)} if reminders else None,
    )
    return fake


def field_row(text: str, label: str) -> str:
    """字段列表里 ``label`` 那一行（行首那两格是光标记号，所以从第三格认起）。"""
    for line in text.splitlines():
        if line[2:].startswith(label):
            return line
    raise AssertionError(f"字段列表里没有「{label}」那一行：\n{text}")


def compact(text: str) -> str:
    """去掉所有空白的一串：折行会吃掉断点处的空格，比「一个字都没少」时要按字比。"""
    return "".join(text.split())


def reminder_block(text: str) -> list[str]:
    """屏幕上「提醒」那一格占的每一行（它可能折行）。"""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line[2:].startswith("提醒"))
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line[2:].startswith(("重复", "以上只读")) or line[:2] in ("❯ ", "  ") and line[2:6] in (
            "重复",
            "以上只读",
        ):
            break
        if not line.strip():
            break
        block.append(line)
    return block


async def enter_detail(pilot, app: DidaApp) -> None:
    """走进「工作」清单第一条任务的详细页。"""
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    await pilot.press("right")
    await pilot.pause()
    await pilot.press("right")
    await pilot.pause()


# ------------------------------------------------------------------ 纯函数：人话


def test_a_positive_duration_reads_as_hours_before_the_due_time():
    """``TRIGGER:P0DT9H0M0S`` → 「提前 9 小时」（工单 #55 给的两个已知例子的第一个）。

    零的那些段不念出来：``0 天 9 小时 0 分 0 秒`` 读成「9 小时」。
    """
    assert reminder_text("TRIGGER:P0DT9H0M0S") == "提前 9 小时"


def test_a_zero_duration_reads_as_on_time():
    """``TRIGGER:PT0S`` → 「准时」（第二个已知例子：0 时长就是到期那一刻）。"""
    assert reminder_text("TRIGGER:PT0S") == "准时"


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        ("TRIGGER:P1DT2H", "提前 1 天 2 小时"),
        ("TRIGGER:PT1H30M", "提前 1 小时 30 分钟"),
        ("TRIGGER:PT30M", "提前 30 分钟"),
        ("TRIGGER:PT45S", "提前 45 秒"),
        ("TRIGGER:P2D", "提前 2 天"),
        ("TRIGGER:P1W", "提前 7 天"),
        ("TRIGGER:PT2H0M30S", "提前 2 小时 30 秒"),
        ("TRIGGER:P0DT9H0M0S", "提前 9 小时"),
    ],
)
def test_days_hours_minutes_and_seconds_are_spelled_out_not_collapsed(trigger, expected):
    """一天以上的时长读成「1 天 2 小时」，不是「26 小时」（工单 #55 的第 3 条）。

    周按 7 天算（ISO-8601 里 ``W`` 是唯一的日期段写法，换算没有歧义）。
    """
    assert reminder_text(trigger) == expected


def test_a_negative_duration_does_not_claim_a_direction():
    """负时长**不猜方向**（工单 #55 的第 1 条）：只说「相对截止时间 N」。

    服务端的文档对负数一个字都没有（``openapi-dida365.md`` 只给了两个正时长的例子），而
    iCalendar 的 ``TRIGGER`` 里正负号是反过来的（RFC 5545 §3.8.6.3：正时长在之后、负时长
    在之前）——TickTick 的格式看起来抄的就是它，可 TickTick 自己没这么说。猜错就是把
    「提前 30 分钟」显示成「延后 30 分钟」，所以这里宁可说得含糊。
    """
    assert reminder_text("TRIGGER:-PT30M") == "相对截止时间 30 分钟"
    assert reminder_text("TRIGGER:-P1DT2H") == "相对截止时间 1 天 2 小时"
    assert reminder_text("TRIGGER:-PT0S") == "准时", "零就是零，符号不改变「到期那一刻」"


@pytest.mark.parametrize(
    "raw",
    [
        "TRIGGER:P1Y",
        "TRIGGER:P1M",
        "TRIGGER:P1Y2M3D",
        "TRIGGER:PT0.5H",
        "TRIGGER:PT",
        "TRIGGER:P",
        "TRIGGER:",
        "TRIGGER:2026-03-14T09:00:00+0800",
        "TRIGGER;VALUE=DATE-TIME:19980101T050000Z",
        "TRIGGER;RELATED=END:PT5M",
        "TRIGGER:garbage",
        "PT30M",
        "",
        "P0DT9H0M0S",
    ],
)
def test_something_that_is_not_a_duration_comes_back_verbatim(raw):
    """解析不了就**原样**回去（工单 #55 的第 2 条）：一个字都不许丢、也不许改。

    年与月没有固定长度（``P1M`` 是 28–31 天）、小数秒要四舍五入、绝对时刻与带参数的形状
    不是这一格的事——它们全部原样显示。安静地少显示一个提醒，比显示得难看严重得多。
    """
    assert reminder_text(raw) == raw


# ------------------------------------------------------------------ 详细页那一格


async def test_the_detail_page_shows_the_reminder_in_words():
    """详细页的提醒格显示人话，不再出现 ``TRIGGER:`` 前缀（工单 #55 的验收 1）。

    文档给的那两个例子一起上：一个正时长、一个零时长。
    """
    app = DidaApp(backend("TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S"))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)

    assert "提前 9 小时" in field_row(text, "提醒"), f"提醒那一格不是人话：\n{text}"
    assert "准时" in field_row(text, "提醒"), f"零时长该读作准时：\n{text}"
    assert "TRIGGER:" not in text, f"服务端编码漏到屏幕上了：\n{text}"


async def test_a_reminder_that_cannot_be_read_is_shown_verbatim_and_never_dropped():
    """解析不了的提醒**照原样**显示，而且不连累别的提醒（工单 #55 的验收 2）。

    「不许丢」这条测试的写法：摆两条提醒，一条是读不出来的（``P1Y2M3D``：年月没有固定长度），
    一条是读得出来的。断言**两条都在屏幕上**——原文那条逐字出现，人话那条也照旧读出来。
    安静地少显示一条比显示得难看严重得多，所以这里断的是「一条都不许少」。
    """
    raw = "TRIGGER:P1Y2M3D"
    app = DidaApp(backend(raw, "TRIGGER:P0DT9H0M0S"))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)
        block = compact(" ".join(reminder_block(text)))

    assert compact(raw) in block, f"读不出来的那条被吞掉了：{block!r}"
    assert compact("提前 9 小时") in block, f"另一条提醒被连累了：{block!r}"


@pytest.mark.parametrize("width", [30, 60])
async def test_several_long_reminders_wrap_and_do_not_break_the_cell(width: int):
    """多条提醒在窄终端下折行，而不是被裁掉（工单 #55 的第 4 条）。

    人话比原文长：两条「提前 1 天 2 小时 30 分钟」「相对截止时间 45 分钟」在 30 列下要占好几
    行。断的是每一段都还在（折行不是截断），而且折行块下面的字段照样能走——那一格高了几行，
    光标与滚动的屏幕行账要跟着对。
    """
    app = DidaApp(backend("TRIGGER:P1DT2H30M", "TRIGGER:-PT45M"))

    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        for _ in range(6):
            await pilot.press("j")  # 一路走到最后一个字段
        await pilot.pause()
        text = screen_text(app)

    block = compact("".join(reminder_block(text)))
    assert compact("提前 1 天 2 小时 30 分钟") in block, f"@{width} 第一条提醒没读全：{block!r}"
    assert compact("相对截止时间 45 分钟") in block, f"@{width} 第二条提醒没读全：{block!r}"
    assert field_row(text, "标签").startswith("❯"), f"@{width} 光标没有走到最后一个字段上：\n{text}"
