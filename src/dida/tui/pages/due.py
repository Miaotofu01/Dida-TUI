"""截止时间的结构化输入：把用户敲的两个串翻成一个时刻（工单 #44）。

**为什么是结构化的**：v1 有一个自然语言日期解析器（``date_parser.py`` 的 ``plan()``），
#34 连同它一起删掉了——spec 明确 v2 不做自然语言日期输入。这一票的截止时间因此是
**两个独立的格子**：一个日期（``2026-03-15``）、一个时刻（``18:00``，可以留空＝全天）。

这一片是纯函数：没有界面、没有引擎、不发请求。它回答的只有一个问题——「用户敲进去的
这两个串，是哪一刻？」。答不上来就抛 :class:`ValueError`，由界面翻成一句人话（验收标准
「非法日期在发出前被本地拦下，请求不出门」）。**绝不猜**：``2026-02-30`` 不会被顺延成
3 月 2 号，``24:00`` 也不会被当成第二天的零点。

时刻一律**带时区**：给出去的那个 offset 是这条任务**当前**的截止时间的 offset（``reference``），
没有就用调用方给的本地时区（``tz``）。不换算、不改写——换时区就是「静默位移」那个坑
（api-shapes §D17：文档对写错时区会怎样一个字都没写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo

__all__ = ["DueInput", "due_change", "format_time_input", "parse_date", "parse_time"]

DATE_FORMAT = "%Y-%m-%d"
"""日期那一格的形状。**只收这一个**：``2026/03/15`` / ``26-3-15`` / ``明天`` 都不认。"""

TIME_FORMAT = "%H:%M"
"""时刻那一格的形状（到分钟为止；秒在任务上是一个没人设过的字段）。"""

_STRICT_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""月与日各两位，一个不多一个不少。

``datetime.strptime(text, "%Y-%m-%d")`` **收** ``2026-3-15``（实测）——只靠它，日期那一格
就同时认好几种写法。这里认死一种：编辑器回填的就是这一种，用户看着它改；而「把两种写法都
当成同一个日子」这件事在真实数据里只会在写错的时候才显形。
"""


@dataclass(frozen=True)
class DueInput:
    """编辑器里那两个格子当前的内容。

    ``day=None`` 是**清除**（把这条任务变回「没有日期」），不是「还没填」——「还没填」在
    界面上是另一件事（提交时不放行，见 ``DetailPage._finish_due_edit``）。
    """

    day: date | None
    at: time | None = None
    all_day: bool = False


def parse_date(typed: str) -> date:
    """``2026-03-15`` → 那一天；不是真实的日历日就抛 :class:`ValueError`。

    ``datetime.strptime`` 自己不收 ``2026-02-30``（会抛），而「把它顺延到 3 月 2 号」那种
    实现会静默写下一个用户没选过的日子。所以这里直接用它，不自己算月份天数——但**先用正则
    把形状认死**（``strptime`` 收 ``2026-3-15``，见 :data:`_STRICT_DATE`）。
    """
    text = typed.strip()
    if not _STRICT_DATE.match(text):
        raise ValueError(f"{text!r} 不是一个日期（写成 2026-03-15 这样）")
    try:
        return datetime.strptime(text, DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(f"{text!r} 不是一个日期（写成 2026-03-15 这样）") from exc


def parse_time(typed: str) -> time | None:
    """``18:00`` → 那一刻；留空 → ``None``（＝只有日期，也就是全天）。"""
    text = typed.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, TIME_FORMAT).time()
    except ValueError as exc:
        raise ValueError(f"{typed.strip()!r} 不是一个时刻（写成 18:00 这样）") from exc


def format_time_input(at: time | None) -> str:
    """时刻回填进编辑器时的写法（``time(9, 5)`` → ``09:05``；没有时刻就是空串）。"""
    return "" if at is None else at.strftime(TIME_FORMAT)


def due_change(entry: DueInput, *, reference: datetime | None, tz: tzinfo | None = None) -> datetime | None:
    """这两个格子 → 要写进 ``dueDate`` 的那一刻；清空日期时给 ``None``。

    - **带时刻**：那一天的那一刻，offset 取 ``reference`` 自己那个（任务当前的截止时间）；
      ``reference`` 没有或没有时区时用 ``tz``（本地时区）。
    - **全天**（时刻留空）：那一天的 ``00:00``——它是**日期标记**，读法是 ``due.date()``
      （``sync/schedule`` 的既定口径）。
    - **日期那一格清空**：``None``。清除不是「改成一个很早的时刻」。
    """
    if entry.day is None:
        return None
    zone = _zone_of(reference) or tz
    at = time(0, 0) if entry.all_day else (entry.at or time(0, 0))
    landed = datetime.combine(entry.day, at)
    return landed if zone is None else landed.replace(tzinfo=zone)


def _zone_of(reference: datetime | None) -> tzinfo | None:
    """这份参考时刻的时区；没有参考时刻、或者它自己是个 naive 的就给 ``None``。

    naive 的参考时刻不能当依据：替它猜一个时区就是静默位移（``guards.api_date`` 对同一个
    问题也是直接拒绝）。
    """
    if reference is None or reference.tzinfo is None or reference.utcoffset() is None:
        return None
    return reference.tzinfo
