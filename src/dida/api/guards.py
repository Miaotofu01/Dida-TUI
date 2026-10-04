"""四个本地守卫：服务端对它们一声不吭。

服务端的静默行为（见 ``Dida-TUI-notes/api-contracts.md`` 与 spec 的 traps 一节）：

1. 写不进去的日期被**静默忽略**——任务看起来建好了，日期不在；
2. 没有截止/开始时间的任务，重复规则被**静默清空**；
3. 时区字段写错，截止时间**静默位移**；
4. 更新时没带回去的字段，手机端设置的东西就被**抹掉**。

守卫只在发送前工作：拦下请求并抛 :class:`~dida.api.errors.FieldIgnoredError` 的
子类，绝不把「服务端会默默丢掉」的请求发出去。这里是纯函数，不认识 HTTP。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from dida.api.errors import DatelessRepeatError, FieldIgnoredError, InvalidDateError

#: 文档形式：``yyyy-MM-dd'T'HH:mm:ssZ``，例 ``2019-11-13T03:00:00+0000``；
#: 已完成流示例带毫秒（``2026-03-04T23:58:20.000+0000``），所以小数秒也收。
#: offset 收 ``+0800`` / ``+08:00`` / ``Z``，但**原样回写**，不重排。
_API_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)$"
)

#: 任务上的日期字段。``completedTime`` 也收：服务端给的、我们原样带回的值必须是合法的。
DATE_FIELDS = ("startDate", "dueDate", "completedTime")

#: 子任务（``ChecklistItem``）上的日期字段。字段名照样是 ``startDate``，含义不同而已。
ITEM_DATE_FIELDS = ("startDate",)

#: 文档说 create / update **都不接受**的字段（api-contracts.md 第 5 条）。
#: 写它们等于什么都没写：服务端静默忽略，用户以为完成了，其实没有（ADR 0002）。
NON_WRITABLE_FIELDS = ("status",)


def api_date(value: Any, *, field: str) -> str:
    """校验一个日期字段，返回要写进请求体的字符串。

    - 字符串**原样返回**：``+0800`` 不会变成 ``+08:00``、不会转成 UTC，毫秒也不会被抹掉。
      这正是「时区写错就静默位移」的解药——我们不重新序列化服务端给的值。
    - ``datetime`` 按它**自己的** offset 写成文档形式，不换时区：09:30+0800 就是
      ``09:30:00+0800``，不是 ``01:30:00+0000``（时刻相同，但用户的墙钟变了，服务端
      在 ``isAllDay`` / 重复规则上按墙钟理解它）。naive datetime 直接拒绝：替调用方
      猜一个时区就是静默位移。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidDateError(
                f"{field} 是 naive datetime（没有时区）；不替它猜时区，请先补上 offset",
                field=field,
            )
        return value.strftime("%Y-%m-%dT%H:%M:%S") + _fraction(value) + value.strftime("%z")
    if not isinstance(value, str):
        raise InvalidDateError(
            f"{field} 必须是文档形式的日期字符串或带时区的 datetime，收到 {type(value).__name__}",
            field=field,
        )
    if not _API_DATE_RE.match(value):
        raise InvalidDateError(
            f"{field} 不是文档要求的日期格式：{value!r}（应为 2019-11-13T03:00:00+0000）",
            field=field,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidDateError(f"{field} 不是合法日期：{value!r}", field=field) from exc
    if parsed.tzinfo is None:  # 正则已保证带 offset，这里是兜底
        raise InvalidDateError(f"{field} 缺少时区偏移：{value!r}", field=field)
    return value


def _fraction(value: datetime) -> str:
    """毫秒部分：文档形式不带它，但服务端自己的已完成流例子带（``.000``）。

    调用方给的亚秒精度照写不误——抹掉它同样是「静默位移」，只是小一点。
    """
    if not value.microsecond:
        return ""
    return f".{value.microsecond // 1000:03d}"


def normalize_dates(body: Mapping[str, Any]) -> dict[str, Any]:
    """校验请求体里的日期字段；返回真正要发出去的那一份。

    覆盖任务自己的日期字段（含 ``completedTime``：服务端给的、我们要原样带回的值）
    和子任务（``items[]``）的 ``startDate`` —— 子任务的日期被静默丢掉是同一类 bug。
    """
    normalized = dict(body)
    for field in DATE_FIELDS:
        if normalized.get(field) is not None:
            normalized[field] = api_date(normalized[field], field=field)
    items = normalized.get("items")
    if isinstance(items, list):
        normalized["items"] = [_normalize_item(index, item) for index, item in enumerate(items)]
    return normalized


def _normalize_item(index: int, item: Any) -> Any:
    if not isinstance(item, Mapping):
        return item
    checked = dict(item)
    for field in ITEM_DATE_FIELDS:
        if checked.get(field) is not None:
            checked[field] = api_date(checked[field], field=f"items[{index}].{field}")
    return checked


def guard_repeat_rule(body: Mapping[str, Any]) -> None:
    """没有日期就不写重复规则。

    服务端对这种 ``repeatFlag`` 的处理是静默清空：请求成功、规则没了。所以本地拦下，
    让调用方显式决定——要么给个日期，要么别设规则（``repeatFlag: None`` 是合法的清除）。
    """
    if body.get("repeatFlag") is None:
        return
    if body.get("dueDate") is None and body.get("startDate") is None:
        raise DatelessRepeatError(
            "任务既没有截止时间也没有开始时间，重复规则会被服务端静默清空；"
            "请先给一个日期，或把 repeatFlag 设为 None",
            field="repeatFlag",
        )


def prepare_write_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """任务写入（新建/更新）的完整守卫管线：先校验日期，再看重复规则。"""
    checked = normalize_dates(body)
    guard_repeat_rule(checked)
    return checked


def guard_writable(changes: Mapping[str, Any]) -> None:
    """拦下文档说服务端**不接受**的字段。

    ``status`` 就是那一个（api-contracts.md 第 5 条）：写它请求会成功，服务端静默忽略，
    用户以为任务完成了。完成只有一条路：``complete_task``（ADR 0002，不可逆）。
    """
    for field in NON_WRITABLE_FIELDS:
        if field in changes:
            raise FieldIgnoredError(
                f"{field} 不能通过新建/更新写入（服务端会静默忽略）；"
                "完成任务请走 complete_task",
                field=field,
            )


def merge_snapshot(
    snapshot: Mapping[str, Any] | None, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """把服务端权威的那份任务与本次改动合成请求体。

    底稿里的字段一个都不丢——包括我们不认识的（手机端设置的提醒、专注记录、
    ``kind`` 之类）。只有文档明确说「create / update 都不接受」的字段例外。
    """
    merged = {
        key: value
        for key, value in (snapshot or {}).items()
        if key not in NON_WRITABLE_FIELDS
    }
    merged.update(changes)
    return merged
