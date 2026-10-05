"""时间写：顺延（t13 / #40）与改期（t14 / #44）——把任务的截止时间挪到某一天。

这一片只管一件事：**截止时间怎么挪**。落点一律按逻辑日算（注入的 ``day_end``），而且
**只动 ``dueDate``**：不改优先级、不碰重复规则、不凭空补一个日期。

v1 还有一条 ``plan()``（把一行自然语言解析成日期，新建与改期共用）：v2 整体作废日期解析器
（#34 删的，spec：「不做自然语言日期输入」），改期改走 #44 的结构化选择器——所以这里只剩
「已经知道改到哪一刻」之后的写路径。

日期一律走 :func:`dida.api.guards.api_date` 原样写成文档形式，不换时区：换时区就是「静默
位移」那个坑。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from dida.api.guards import api_date
from dida.logical_day import LogicalDay, logical_day


class ScheduleMixin:

    """顺延与改期：两条写路径都只改截止时间，落点都按逻辑日算。

    TUI 不重算任何日界——它只说「往后一个逻辑日」或「改成这一刻」。"""



    def defer(self, task_id: str, *, days: int = 1) -> None:
        """写：顺延到下一个逻辑日（``g``）；``days=7`` 是下周同一天（``G``）。t13 实现。

        落点由 :func:`dida.logical_day.logical_day` 决定，TUI 不重算任何日界：先找出目标
        逻辑日（``[start, end)`` 的 ``end`` 就是下一个逻辑日），再把这条任务**原来的墙钟
        时刻**放进那个逻辑日。所以边界配成 ``04:00`` 时，凌晨两点的「明天」是用户作息里的
        明天，而不是机器日历日 +1——顺延过的任务醒来时仍然读作「今日」，不会被判成逾期。

        只改 ``dueDate`` 一个字段（GLOSSARY 的「顺延」），而且走 :meth:`write` 那条写路径：
        本地当场生效、立即推送、推不动就留在队列里按注入的钟退避重试。
        """
        target = self._write_target()
        payload = target.task_payload(task_id)
        if payload is None:  # 本地没有这条任务，就没有「当前的截止时间」可挪
            return
        changes = _defer_changes(payload, now=self._clock.now(), day_end=self._day_end, days=days)
        if changes is None:  # 没有截止时间可挪：无日期的任务不凭空长出一个日期来
            return
        self.write(task_id, changes=changes)

    def reschedule(self, task_id: str, *, due: datetime, all_day: bool) -> None:
        """写：改期——把截止时间换成给定那一刻，只动 ``dueDate`` 与 ``isAllDay``。

        「改成哪一天」由调用方（#44 的结构化选择器）定；这里只把结果交给 :meth:`write`：
        本地当场生效、立即推送、推不动留在队列里按注入的钟退避重试。

        **截止时间原样写回，不做任何逻辑日加减。** ``all_day=True`` 时 ``due`` 是那一天的
        00:00，是个**日期标记**：按 ``[start, end)`` 去把它挪进「当前逻辑日」，会让一个
        「今天」的全天任务整天掉出今日区（``due_day()`` 对全天任务只看 ``due.date()``）。
        """
        self.write(
            task_id,
            changes={"dueDate": api_date(due, field="dueDate"), "isAllDay": all_day},
        )


def _defer_changes(
    payload: Mapping[str, Any], *, now: datetime, day_end: str, days: int
) -> dict[str, Any] | None:
    """顺延要写进 ``write(changes=)`` 的那一份字段：只动 ``dueDate``。

    落点是**逻辑日**而不是自然日：目标逻辑日由 :func:`_logical_day_after` 逐步问纯函数得来，
    再把这条任务原来的墙钟时刻放进去。没有合用的截止时间时返回 ``None``——顺延不改其它
    字段，也绝不凭空补一个日期（服务端对没日期的任务是另一套静默行为）。
    """
    raw = payload.get("dueDate")
    if not isinstance(raw, str):
        return None
    subject = _parse_due(raw)
    if subject is None or subject.tzinfo is None:
        # 脏日期或没有时区的日期：当作挪不动。替它猜一个时区就是「静默位移」那个 trap。
        return None
    target = _logical_day_after(logical_day(now, day_end), day_end, days)
    landed = datetime.combine(target.label, subject.timetz())
    if not payload.get("isAllDay") and landed < target.start:
        # 墙钟时刻比日界早（如 ``04:00`` 边界上的 02:00）：那个时刻按逻辑日属于**前一天**，
        # 落在目标逻辑日之外，顺延就等于没顺延。往后挪一天，让落点真的在 ``[start, end)`` 里
        # ——夜猫子的「明天凌晨两点」是后天 02:00，不是今天的深夜。
        #
        # 全天任务不走这一步：它的截止是**日期标记**（看 ``due.date()``），00:00 只是标记
        # 的形状；套偏移会把它整天推到再下一天。
        landed += timedelta(days=1)
    return {"dueDate": api_date(landed, field="dueDate")}


def _parse_due(value: str) -> datetime | None:
    """解析服务端的日期字符串；吃不下就当作没有截止时间（与存储层同一口径）。"""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _logical_day_after(day: LogicalDay, day_end: str, days: int) -> LogicalDay:
    """往后数 ``days`` 个逻辑日（``g`` 是 1 天、``G`` 是 7 天）。

    每一步都问纯函数：``[start, end)`` 的 ``end`` 恰好是下一个逻辑日。不拿自然日加加减减
    ——跨 DST 时一个逻辑日的墙钟长度不是 24 小时，加天数会悄悄挪走用户的墙钟时刻。
    """
    for _ in range(days):
        day = logical_day(day.end, day_end)
    return day
