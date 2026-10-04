"""日期解析器（第 6 个深模块）：纯函数。

把一行的输入解析成：标题 + 可选截止 + 可选优先级 + 可选标签 + 解析诊断，
供新建（t15）与改期（t14）共用。零依赖、无副作用、可直接单测。

公开接口：

- :func:`parse` —— ``parse(text, now, day_end="00:00") -> ParsedTask``。
  「现在」与 ``day_end`` 都是参数：本模块不看表、不读配置。
- :class:`ParsedTask` —— 标题、截止时刻、是否全天、优先级、标签、诊断。
- :class:`ParseDiagnostic` —— 一段没被识别的东西（``token`` / ``code`` / ``message``）。

语法（spec issue #1 的「日期解析器语法」表）：今天/今日、明天/明日、后天、周X、星期X、
下周X、+3d、+3天、3-15、3/15、14:00、下午3点、晚上8点。相对日期从 ``now`` 所在的
**逻辑日** 算起：凌晨 2 点（``day_end = "04:00"``）说「明天」，指的是标签为今天那个
逻辑日的下一天。

三条约定，消费方（t14 改期、t15 新建）直接依赖：

- ``due`` 一定带 ``now`` 的时区。``all_day=True``（只写日期：「明天」「+3d」「3-15」）时
  ``due`` 是那一天的 **00:00**，消费方只许按 ``due.date()`` 用它——``day_end = "04:00"``
  时 3/15 00:00 属于逻辑日 3/14，按逻辑日区间判定会把全天任务放错分区。
- 只写时刻（``all_day=False``、没有日期）时，已过去的时刻落到**下一个逻辑日**；
  写明了日期（哪怕「今天」）就照写的那天算，不顺延。
- ``diagnostics`` 空 = 没写日期，正常；非空 = 写了疑似日期/时刻但没解析出来，
  消费方**必须**提示用户，不许静默提交。

语法表没写到的地方，本工单定稿：

- 标签：``#`` 到空白、下一个 ``#`` 或句读为止。``#a#b`` 是**两个**标签；光秃秃的
  ``#`` 留在标题里（不是诊断——它本来就不是日期）；重复的标签只留一次。
- 优先级：``!高``/``!中``/``!低``，或者**档位序号** ``!1``/``!2``/``!3``（1=低、2=中、3=高）。
  数字是档位，不是 API 取值——服务端 ``priority`` 的线上编码 0/1/3/5 只在 dida/api 边界出现，
  所以 ``!3`` 解析出来是 ``5``（高），而 ``!5`` **不是**「高」，``!0`` 也**不是**清除语法
  （清除优先级是 ``p`` 键的事）：1/2/3 以外的数字一律报诊断，不留在标题里。
- 时刻只认 ``14:00`` 和带时段的 ``下午3点``。不带时段的「9点」不收：分不清上午晚上，
  而且「点」在中文里还表示条目（「第3点建议」），误判会把标题吃出洞来。它留在标题里，
  用户看得见。
- 一格里写了多个日期/时刻时取**第一个**能解析出来的；``2026-03-15`` 这种写明年的写法
  照写、不滚动（语法表没列它，但不认的话「2026-」会留在标题里）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from dida.logical_day import LogicalDay, logical_day

_MIDNIGHT = time(0, 0)
_ONE_DAY = timedelta(days=1)

_KIND_DATE = "date"
_KIND_TIME = "time"
_KIND_TAG = "tag"
_KIND_PRIORITY = "priority"

#: 相对日期的词表：值是从当前**逻辑日**起算的天数。
_RELATIVE_DAYS = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}

#: 优先级：``高/中/低`` 或**档位序号** ``1/2/3``。数字是档位（1=低、2=中、3=高），
#: 与 ``!低``/``!中``/``!高`` 一一对应，也与 ``p`` 键的循环顺序（无→低→中→高）一致。
#: 它不是 API 取值——服务端 ``priority`` 的线上编码 0/1/3/5（无/低/中/高）只出现在
#: dida/api 边界上。所以 ``!3`` → 5，而 ``!5`` 不在表里 → 诊断（``!5`` 不是「高」）。
#: 表里没有的写法（``!0``/``!4``/``!5``/``!9``…）一律报诊断，不静默当标题文本。
_PRIORITIES = {"高": 5, "中": 3, "低": 1, "1": 1, "2": 3, "3": 5}

#: 星期几的字面量 → ``date.weekday()``（周一 = 0）。
_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEKDAY_CHARS = "[一二三四五六日天]"

#: 时段词。「点」在中文里还表示「条目」，所以只认带时段的「下午3点」，不认光秃秃的「9点」。
_PERIODS = "凌晨|早上|上午|中午|下午|傍晚|晚上"

_DATE_RE = re.compile(
    "|".join(
        [
            r"(?P<relative>" + "|".join(_RELATIVE_DAYS) + r")",
            r"下周(?P<next_week>" + _WEEKDAY_CHARS + r")",
            r"(?:周|星期)(?P<weekday>" + _WEEKDAY_CHARS + r")",
            r"\+(?P<offset_days>\d+)[dD天]",
            r"(?<!\d)(?P<year>\d{4})[-/](?P<year_month>\d{1,2})[-/](?P<year_day>\d{1,2})(?![\d\-/])",
            r"(?<![\d\-/])(?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?![\d\-/])",
        ]
    )
)

_TIME_RE = re.compile(
    "|".join(
        [
            r"(?:(?P<period>" + _PERIODS + r")[ \t]*)?(?<!\d)(?P<hour>\d{1,2})[:：](?P<minute>\d{2})(?!\d)",
            r"(?P<period_cn>" + _PERIODS + r")[ \t]*(?<!\d)(?P<hour_cn>\d{1,2})点"
            r"(?:(?P<half>半)|(?P<cn_minute>\d{1,2})分)?",
        ]
    )
)

#: 标签到空白、到下一个 ``#``、到句读为止；光秃秃的 ``#`` 不是标签。
#: 收尾的句读只在它结束这个标签时（``#工作。``）跟着一起吃掉，句中的逗号留给标题。
_TAG_RE = re.compile(
    r"#(?P<tag>[^\s#，。、；：！？,.;:!?]+)(?:[，。、；：！？,.;:!?]+(?=\s|$))?"
)

#: 只有认得的档位词/数字才算「写了优先级」——``!`` 后面跟别的字就留在标题里。
_PRIORITY_RE = re.compile(r"!(?P<priority>高|中|低|\d+)")

#: 没解析出来的那一段怎么跟用户说。诊断要自成一句，UI 直接显示。
_MESSAGES = {
    _KIND_DATE: "「{token}」不是一个能认出来的日期，可以写「明天」或「3-15」",
    _KIND_TIME: "「{token}」不是一个能认出来的时刻，可以写「15:00」或「下午3点」",
    _KIND_PRIORITY: (
        "「{token}」不是一个能认出来的优先级："
        "优先级只支持「!1」/「!2」/「!3」或「!高」/「!中」/「!低」"
    ),
}


@dataclass(frozen=True)
class ParseDiagnostic:
    """一小段输入没被解析出来。"""

    token: str
    """输入里的原文，例如 ``"13-45"``（UI 可以据此高亮）。"""

    code: str
    """机器可判定的原因：``"invalid_date"`` / ``"invalid_time"`` / ``"invalid_priority"``。"""

    message: str
    """给用户看的一句话，自带 token。"""


@dataclass(frozen=True)
class ParsedTask:
    """一行输入的解析结果。"""

    title: str
    """去掉日期、时刻、优先级、标签之后剩下的标题。"""

    due: datetime | None = None
    """截止时刻（带 ``now`` 的时区）；``None`` = 这行里没有日期。"""

    all_day: bool = False
    """``True`` = 只写了日期没写时刻，按全天处理，只看 ``due.date()``。"""

    priority: int | None = None
    """服务端优先级取值：低 ``1`` / 中 ``3`` / 高 ``5``；``None`` = 没写。"""

    tags: tuple[str, ...] = ()
    """``#标签`` 解析出来的标签，按出现顺序去重。"""

    diagnostics: tuple[ParseDiagnostic, ...] = ()
    """没解析出来的部分；空 = 一切正常（没写日期也是正常的）。"""


@dataclass(frozen=True)
class _Mark:
    """输入里被某条语法认领的一段。"""

    start: int
    end: int
    kind: str
    value: date | time | str | int | None
    """解析结果；``None`` = 看着像日期/时刻，但解析失败。"""


def parse(text: str, now: datetime, day_end: str = "00:00") -> ParsedTask:
    """把一行输入解析成 :class:`ParsedTask`。

    相对日期从 ``now`` 所在的**逻辑日**算起（``logical_day(now, day_end)``），
    不是从自然日算起。``day_end`` 的默认值是配置的规范形式（ADR-0003 把「不偏移」
    写成 ``"00:00"``；``"24:00"`` 在配置层就折成它了，两者等价）。

    纯函数：同样的入参永远给出同样的结果。
    """
    day = logical_day(now, day_end)
    marks = _scan(text, day.label)

    dates = [m.value for m in marks if m.kind == _KIND_DATE and isinstance(m.value, date)]
    clocks = [m.value for m in marks if m.kind == _KIND_TIME and isinstance(m.value, time)]
    priorities = [m.value for m in marks if m.kind == _KIND_PRIORITY and isinstance(m.value, int)]
    due, all_day = _when(day, dates[0] if dates else None, clocks[0] if clocks else None, now)

    return ParsedTask(
        title=_title_of(text, marks),
        due=due,
        all_day=all_day,
        priority=priorities[0] if priorities else None,
        tags=tuple(dict.fromkeys(m.value for m in marks if m.kind == _KIND_TAG)),
        diagnostics=_diagnostics_of(text, marks),
    )


def _diagnostics_of(text: str, marks: list[_Mark]) -> tuple[ParseDiagnostic, ...]:
    """没解析出来的片段，按在输入里出现的顺序各报一条。

    空元组 = 这行没写日期/时刻/优先级，属于正常；有内容 = 用户写了却没人认得，
    消费方必须提示，不许静默提交。
    """
    reported: list[ParseDiagnostic] = []
    for mark in marks:
        if mark.value is not None:
            continue
        token = text[mark.start : mark.end]
        reported.append(
            ParseDiagnostic(
                token=token,
                code=f"invalid_{mark.kind}",
                message=_MESSAGES[mark.kind].format(token=token),
            )
        )
    return tuple(reported)


def _scan(text: str, label: date) -> list[_Mark]:
    """扫出所有被认领的片段，并丢掉被更大片段盖住的重叠部分。"""
    marks = [
        _Mark(match.start(), match.end(), _KIND_DATE, _date_of(match, label))
        for match in _DATE_RE.finditer(text)
    ]
    marks += [
        _Mark(match.start(), match.end(), _KIND_TIME, _time_of(match))
        for match in _TIME_RE.finditer(text)
    ]
    marks += [
        _Mark(match.start(), match.end(), _KIND_TAG, match["tag"])
        for match in _TAG_RE.finditer(text)
    ]
    marks += [
        _Mark(match.start(), match.end(), _KIND_PRIORITY, _PRIORITIES.get(match["priority"]))
        for match in _PRIORITY_RE.finditer(text)
    ]

    kept: list[_Mark] = []
    for mark in sorted(marks, key=lambda m: (m.start, -m.end)):
        if not kept or mark.start >= kept[-1].end:
            kept.append(mark)
    return kept


def _title_of(text: str, marks: list[_Mark]) -> str:
    """没被认领的部分拼起来就是标题：折叠空白、去掉首尾。"""
    pieces: list[str] = []
    cursor = 0
    for mark in marks:
        pieces.append(text[cursor : mark.start])
        cursor = mark.end
    pieces.append(text[cursor:])
    return " ".join("".join(pieces).split())


def _when(
    day: LogicalDay, target: date | None, clock: time | None, now: datetime
) -> tuple[datetime | None, bool]:
    """日期/时刻 → 截止时刻 + 是否全天。"""
    if clock is None:
        if target is None:
            return None, False
        return datetime.combine(target, _MIDNIGHT, tzinfo=now.tzinfo), True
    if target is None:
        return _time_only_moment(day, clock, now), False
    return datetime.combine(target, clock, tzinfo=now.tzinfo), False


def _time_only_moment(day: LogicalDay, clock: time, now: datetime) -> datetime:
    """只给了时刻：落在当前逻辑日里；已经过去了就落到下一个逻辑日。"""
    moment = datetime.combine(_date_of_clock_in(day, clock), clock, tzinfo=now.tzinfo)
    return moment if moment >= now else moment + _ONE_DAY


def _date_of_clock_in(day: LogicalDay, clock: time) -> date:
    """当前逻辑日里，墙上时间 ``clock`` 落在哪个自然日上。"""
    return day.label if clock >= day.start.time() else day.label + _ONE_DAY


def _date_of(match: re.Match[str], label: date) -> date | None:
    """一个日期词命中时，它指的是哪一天（相对 ``label`` 这个逻辑日）。"""
    if (relative := match["relative"]) is not None:
        return label + timedelta(days=_RELATIVE_DAYS[relative])
    if (days := match["offset_days"]) is not None:
        return _shifted(label, int(days))
    if (weekday := match["next_week"]) is not None:
        return _in_the_next_natural_week(label, _WEEKDAYS[weekday])
    if (weekday := match["weekday"]) is not None:
        return _the_coming_weekday(label, _WEEKDAYS[weekday])
    if (year := match["year"]) is not None:
        return _a_real_date(int(year), int(match["year_month"]), int(match["year_day"]))
    return _this_year_or_next(label, int(match["month"]), int(match["day"]))


def _shifted(label: date, days: int) -> date | None:
    """``label`` 往后 ``days`` 天；超出 ``date`` 能表示的范围就是解析失败。"""
    try:
        return label + timedelta(days=days)
    except OverflowError:
        return None


def _this_year_or_next(label: date, month: int, day: int) -> date | None:
    """今年的 ``month``-``day``；已经过去（早于逻辑日）就取明年。"""
    for year in (label.year, label.year + 1):
        candidate = _a_real_date(year, month, day)
        if candidate is None:  # 2-30、13-45 这类不存在的日期
            return None
        if candidate >= label:
            return candidate
    return None


def _a_real_date(year: int, month: int, day: int) -> date | None:
    """日历上真有这一天吗。"""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _the_coming_weekday(label: date, weekday: int) -> date:
    """``weekday`` 的下一次；``label`` 本身就是它时取今天。"""
    return label + timedelta(days=(weekday - label.weekday()) % 7)


def _in_the_next_natural_week(label: date, weekday: int) -> date:
    """下一个自然周（周一为一周之首）里的 ``weekday``。"""
    next_monday = label - timedelta(days=label.weekday()) + timedelta(days=7)
    return next_monday + timedelta(days=weekday)


def _time_of(match: re.Match[str]) -> time | None:
    """一个时刻词命中时，它指的是几点。"""
    if (hour := match["hour"]) is not None:
        return _on_the_clock(match["period"], int(hour), int(match["minute"]))
    hour = match["hour_cn"]
    return _on_the_clock(
        match["period_cn"], int(hour), _chinese_minute(match["half"], match["cn_minute"])
    )


def _chinese_minute(half: str | None, digits: str | None) -> int:
    """「半」/「15分」→ 分钟数。"""
    if half is not None:
        return 30
    return 0 if digits is None else int(digits)


def _on_the_clock(period: str | None, hour: int, minute: int) -> time | None:
    """时段 + 小时 + 分钟 → 24 小时制时刻；不成立就是解析失败。"""
    if minute > 59:
        return None
    hour = _hour_on_the_clock(period, hour)
    return None if hour is None else time(hour, minute)


def _hour_on_the_clock(period: str | None, hour: int) -> int | None:
    """把「下午3点」「晚上12点」这样的说法折到 24 小时制。"""
    if hour > 23:
        return None
    if period is None or hour > 12:
        return hour  # 已经写了 24 小时制的小时，时段是多余的
    if hour == 12:
        return 0 if period in ("凌晨", "晚上", "傍晚") else 12
    if period in ("下午", "傍晚", "晚上"):
        return hour + 12
    if period == "中午" and hour <= 5:
        return hour + 12
    return hour
