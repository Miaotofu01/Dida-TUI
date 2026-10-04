"""逻辑日（第 5 个深模块）：纯函数。t04 填充。

一次「还没睡」的周期所对应的日期；结束时刻可配置，默认 24:00。
「现在」从参数进来，本模块自己不看表——零依赖、无副作用、可直接单测。

公开接口：

- :func:`logical_day` —— ``logical_day(now, day_end) -> LogicalDay``：``now`` 落在哪个
  逻辑日，连同该逻辑日的区间（``start`` 含、``end`` 不含）与日期标签。
- :func:`parse_day_end` —— ``day_end`` 字符串（``"HH:MM"``）→ 相对午夜的偏移量；
  ``"24:00"`` 与 ``"00:00"`` 归一到同一个表示（零偏移 = 自然日）。
- :class:`LogicalDay` —— 逻辑日的值对象。
- :class:`InvalidDayEnd` —— 非法 ``day_end``（``ValueError`` 子类）。

两个约定，下游工单（t05 渲染标签、t13 顺延）直接依赖：

- 区间是 ``[start, end)``，所以 ``logical_day(day.end, day_end)`` 恰好是**下一个逻辑日**。
- 边界按**墙上时间**判定：``day_end = "04:00"`` 的日子两端都是本地 04:00；跨 DST 时
  真实时长不是 24 小时。重复或缺失的墙上时间按 ``fold=0``（第一次出现）锚定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo as tzinfo_type

_ONE_DAY = timedelta(days=1)
_DAY_END_RE = re.compile(r"([0-9]{2}):([0-9]{2})")


class InvalidDayEnd(ValueError):
    """``day_end`` 不是合法的一天结束时刻。"""


@dataclass(frozen=True)
class LogicalDay:
    """一个逻辑日：日期标签 + 区间 ``[start, end)``。"""

    label: date
    """逻辑日的日期标签。"""

    start: datetime
    """逻辑日开始的时刻（含）。"""

    end: datetime
    """下一个逻辑日开始的时刻（不含）。"""

    def contains(self, moment: datetime) -> bool:
        """``moment`` 是否落在本逻辑日里：``start`` 含、``end`` 不含。"""
        return self.start <= moment < self.end


def parse_day_end(day_end: str) -> timedelta:
    """``day_end`` → 相对午夜的偏移量，``0 <= 偏移 < 24 小时``。

    只认 ``"HH:MM"``（两位小时、两位分钟，整分钟），范围 ``"00:00"``–``"24:00"``；
    其余一律 :class:`InvalidDayEnd`。``"24:00"`` 与 ``"00:00"`` 是同一个东西：
    零偏移，逻辑日等于自然日。
    """
    if not isinstance(day_end, str):
        raise InvalidDayEnd(f'day_end 要是 "HH:MM" 文本，收到 {day_end!r}')
    match = _DAY_END_RE.fullmatch(day_end)
    if match is None:
        raise InvalidDayEnd(f'day_end 要是 "HH:MM"（整分钟），收到 {day_end!r}')
    hour, minute = int(match[1]), int(match[2])
    offset = timedelta(hours=hour, minutes=minute)
    if minute > 59 or hour > 24 or offset > _ONE_DAY:
        raise InvalidDayEnd(f'day_end 要落在 "00:00"–"24:00" 之间，收到 {day_end!r}')
    return timedelta(0) if offset == _ONE_DAY else offset


def logical_day(now: datetime, day_end: str) -> LogicalDay:
    """``now`` 落在哪个逻辑日：区间 + 日期标签。

    **不看表**：``now`` 是参数。结果的 ``tzinfo`` 与 ``now`` 一致。
    """
    offset = parse_day_end(day_end)
    label = now.date()
    if now < _day_start(label, offset, now.tzinfo):
        label -= timedelta(days=1)
    return _bounds(label, offset, now.tzinfo)


def _day_start(label: date, offset: timedelta, tzinfo: tzinfo_type) -> datetime:
    """逻辑日 ``label`` 的开始时刻：``label`` 那天的「午夜 + 偏移」墙上时间。"""
    minutes = offset // timedelta(minutes=1)
    return datetime.combine(label, time(minutes // 60, minutes % 60), tzinfo=tzinfo)


def _bounds(label: date, offset: timedelta, tzinfo: tzinfo_type) -> LogicalDay:
    """``label`` 这一天的逻辑日区间：``[start, end)``。"""
    return LogicalDay(
        label=label,
        start=_day_start(label, offset, tzinfo),
        end=_day_start(label + timedelta(days=1), offset, tzinfo),
    )
