"""时间写：顺延（t13 / #40）与改期（t14 / #44）——把任务的截止时间挪到某一天。

这一片只管一件事：**截止时间怎么挪**。落点一律按逻辑日算（注入的 ``day_end``），而且
**只动 ``dueDate``**：不改优先级、不碰重复规则、不凭空补一个日期。

v1 还有一条 ``plan()``（把一行自然语言解析成日期，新建与改期共用）：v2 整体作废日期解析器
（#34 删的，spec：「不做自然语言日期输入」），改期改走 #44 的结构化选择器——所以这里只剩
「已经知道改到哪一刻」之后的写路径。

带时刻的日期一律走 :func:`dida.api.guards.api_date` 原样写成文档形式，不换时区：换时区就是
「静默位移」那个坑。**全天**的日期不是一刻，写的是那一天的 UTC 午夜（#73）——两种形状都在
:func:`dida.api.guards.due_wire_fields` / :func:`dida.api.guards.all_day_date` 那一处。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from dida.api.guards import all_day_date, api_date, due_wire_fields
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

        **全天任务**的落点也是逻辑日，但形状归 UTC（#73）：写的是目标日那一天的 UTC 午夜，
        这条任务原来那一份的时刻与偏移都不参与。带时刻的任务照旧把原来的墙钟时刻放进目标
        逻辑日。
        """
        target = self._write_target()
        payload = target.task_payload(task_id)
        if payload is None:  # 本地没有这条任务，就没有「当前的截止时间」可挪
            return
        changes = _defer_changes(payload, now=self._clock.now(), day_end=self._day_end, days=days)
        if changes is None:  # 没有截止时间可挪：无日期的任务不凭空长出一个日期来
            return
        self.write(task_id, changes=changes)

    def reschedule(self, task_id: str, *, due: datetime | None, all_day: bool = False) -> None:
        """写：改期——把截止时间换成给定那一刻，只动 ``dueDate`` 与 ``isAllDay``。

        「改成哪一天」由调用方（#44 的结构化选择器）定；这里只把结果交给 :meth:`write`：
        本地当场生效、立即推送、推不动留在队列里按注入的钟退避重试。

        **截止时间原样写回，不做任何逻辑日加减。** ``all_day=True`` 时 ``due`` 是那一天的
        00:00，是个**日期标记**：按 ``[start, end)`` 去把它挪进「当前逻辑日」，会让一个
        「今天」的全天任务整天掉出今日区（``due_day()`` 对全天任务只看 ``due.date()``）。

        ``due=None`` 是**清除**（工单 #44 的「把任务变回没有日期」）：写显式的
        ``dueDate: null``，而不是省略这个字段。理由见 :func:`_date_changes`。

        ``all_day`` 默认 ``False``：只想清掉日期或者只想挪到某一天的调用方不必重复写它，而
        「全天」永远是**显式**写出去的一笔（省略它的话，一条原来是全天的任务会带着
        ``isAllDay = true`` 收到一个带时刻的 ``dueDate``，用户写的 14:00 就静默没了）。

        ``all_day=True`` 时 ``due`` 只贡献**日历日**（按它自己的墙钟）：写出去的是那一天的
        UTC 午夜，它携带的时刻与偏移都不写出去（#73）。所以这条路上调用方的时区在「全天」
        那一档上不再被需要——带时刻那一档照旧用它（``api_date`` 不换时区）。
        """
        self.write(task_id, changes=_date_changes(due=due, all_day=all_day))


def _date_changes(*, due: datetime | None, all_day: bool) -> dict[str, Any]:
    """改期要写进 ``write(changes=)`` 的那一份字段：只动 ``dueDate`` 与 ``isAllDay``。

    **清除**（``due=None``）写的是显式的 ``None``，不是「不发这个字段」。api-shapes §A2
    记着省略字段是替换还是合并**文档没说**（openapi-dida365.md:309–333 一个字都没提），
    所以「不发 dueDate」在两种语义下意思不同，其中一种是「别动它」——那用户按了清除却什么
    都没发生。显式 null 是唯一一个把「清空」说出来的形状。

    ``isAllDay`` 一起写出去：清除之后不该留下「全天、但没有日期」这个组合（读法上它是
    自相矛盾的一格），所以界面那一侧清除时写 ``False``；这里照样把调用方给的那一档写下去，
    不替他改主意。

    这一对的形状归写边界那一处守卫（:func:`dida.api.guards.due_wire_fields`）：带时刻的
    原样按自己的 offset 写、不换时区；全天的写成那一天的 **UTC 午夜**（#73）。
    """
    return due_wire_fields(due, all_day=all_day)


def _defer_changes(
    payload: Mapping[str, Any], *, now: datetime, day_end: str, days: int
) -> dict[str, Any] | None:
    """顺延要写进 ``write(changes=)`` 的那一份字段：只动 ``dueDate``。

    落点是**逻辑日**而不是自然日：目标逻辑日由 :func:`_logical_day_after` 逐步问纯函数得来，
    再把这条任务原来的墙钟时刻放进去。没有合用的截止时间时返回 ``None``——顺延不改其它
    字段，也绝不凭空补一个日期（服务端对没日期的任务是另一套静默行为）。

    **全天那条路上原来的墙钟时刻不参与**：全天是日期标记（``due.date()``），归一成目标日的
    UTC 午夜（#73）。老数据里那种「本地午夜 + 本地偏移」的标记也照这条口径回到 UTC——否则
    它顺延之后还是旧形状，读侧按 UTC 取日期仍旧偏一天。
    """
    raw = payload.get("dueDate")
    if not isinstance(raw, str):
        return None
    subject = _parse_due(raw)
    if subject is None or subject.tzinfo is None:
        # 脏日期或没有时区的日期：当作挪不动。替它猜一个时区就是「静默位移」那个 trap。
        return None
    target = _logical_day_after(logical_day(now, day_end), day_end, days)
    if payload.get("isAllDay"):
        return {"dueDate": api_date(all_day_date(target.label), field="dueDate")}
    landed = datetime.combine(target.label, subject.timetz())
    if landed < target.start:
        # 墙钟时刻比日界早（如 ``04:00`` 边界上的 02:00）：那个时刻按逻辑日属于**前一天**，
        # 落在目标逻辑日之外，顺延就等于没顺延。往后挪一天，让落点真的在 ``[start, end)`` 里
        # ——夜猫子的「明天凌晨两点」是后天 02:00，不是今天的深夜。
        #
        # 全天任务不走这一步：它的截止是**日期标记**（看 ``due.date()``），00:00 只是标记
        # 的形状；上面那一条已经把它整个换掉了。
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
