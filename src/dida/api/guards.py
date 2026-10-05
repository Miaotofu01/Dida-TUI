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
from typing import Any, Mapping, Sequence

from dida.api.errors import DatelessRepeatError, FieldIgnoredError, InvalidDateError

#: 文档形式：``yyyy-MM-dd'T'HH:mm:ssZ``，例 ``2019-11-13T03:00:00+0000``；
#: 已完成流示例带毫秒（``2026-03-04T23:58:20.000+0000``），所以小数秒也收。
#: offset 收 ``+0800`` / ``+08:00`` / ``Z``，但**原样回写**，不重排。
_API_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)$"
)

#: 每一类请求体各自的日期字段清单。守卫按清单逐个校验，所以清单与请求体对不上时，
#: 漏掉的那个字段就会带着 ``datetime`` 直奔 json 编码器（裸 ``TypeError``），或者带着
#: 服务端会静默忽略的字符串发出去——工单 #26 就是 ``endDate`` 不在任何清单里。

#: 任务（``Task``）请求体上的日期字段。``completedTime`` 也收：服务端给的、我们原样带回的值必须是合法的。
TASK_DATE_FIELDS = ("startDate", "dueDate", "completedTime")

#: 旧名，保留兼容。
DATE_FIELDS = TASK_DATE_FIELDS

#: 子任务（``ChecklistItem``）请求体上的日期字段。字段名照样是 ``startDate``，含义不同而已；
#: ``completedTime`` 与任务上的那个一样，是服务端给的、我们要原样带回的值。
ITEM_DATE_FIELDS = ("startDate", "completedTime")

#: 已完成流（``POST /open/v1/task/completed``）窗口的两端。两端与任务日期走同一套守卫，
#: 只是清单不同：``endDate`` 不在任务字段里，曾经因此漏成裸 ``TypeError``（工单 #26）。
COMPLETED_WINDOW_DATE_FIELDS = ("startDate", "endDate")

#: 文档说 create / update **都不接受**的字段（api-contracts.md 第 5 条）。
#: 写它们等于什么都没写：服务端静默忽略，用户以为完成了，其实没有（ADR 0002）。
#: **例外只有一处**：批量更新的 ``update`` 数组（:func:`prepare_batch_body`，实测确认）。
NON_WRITABLE_FIELDS = ("status",)

#: 批量更新（``POST /open/v1/task/batch``）的 ``update`` 里每一条**只发**这三个字段。
#:
#: 端点与数组的形状是文档给的（openapi §A3 :559–562），``status`` 则是**实测确认**的
#: （spec 的实测事实第 1 条）：文档在这一节里一次都没提它。所以这张表不是照文档抄的，
#: 它记的是「我们确实这么用过、且回读确认生效」的那一份。
BATCH_UPDATE_FIELDS: tuple[str, ...] = ("id", "projectId", "status")

#: 清单（``Project``）的写请求体接受的字段（openapi-dida365.md:1184–1188 建、:1235–1239 改）。
#:
#: ``groupId`` **不在里面**：它在整份文档里只出现在 Project 的**响应**与定义上
#: （:1011 / :1052 / :1095 / :2302），没有任何一个请求体表收它。所以客户端做不到把清单
#: 放进项目组、也改不了它的归属——这是接口的能力上限，不是实现疏漏（工单 #42 的验收标准 9）。
#:
#: ``closed`` / ``permission`` / ``id`` 同理：它们是响应上的字段，写回去只会多带一笔
#: 服务端不认识的东西。
PROJECT_WRITABLE_FIELDS: tuple[str, ...] = ("name", "color", "sortOrder", "viewMode", "kind")


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


def normalize_dates(
    body: Mapping[str, Any], *, fields: Sequence[str] = TASK_DATE_FIELDS
) -> dict[str, Any]:
    """校验请求体里的日期字段；返回真正要发出去的那一份。

    ``fields`` 是**这类请求体**上的日期字段清单（任务 / 子任务 / 已完成流窗口）：
    调用方声明自己发的是哪一类，守卫就不会因为清单缺一个字段而放它过去。

    任务请求体还会覆盖子任务（``items[]``）的日期字段 —— 子任务的日期被静默丢掉
    是同一类 bug。
    """
    normalized = dict(body)
    for field in fields:
        if normalized.get(field) is not None:
            normalized[field] = api_date(normalized[field], field=field)
    items = normalized.get("items")
    if isinstance(items, list):
        normalized["items"] = [_normalize_item(index, item) for index, item in enumerate(items)]
    return normalized


def prepare_completed_window_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """已完成流窗口的守卫：``startDate`` / ``endDate`` 两端同一套规则（工单 #26）。"""
    return normalize_dates(body, fields=COMPLETED_WINDOW_DATE_FIELDS)


def prepare_batch_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """批量更新（``POST /open/v1/task/batch``）的守卫：``update`` 里每一条**只留三个字段**。

    这是 :data:`NON_WRITABLE_FIELDS` 唯一的例外，而且范围就到这里为止：``status`` 在普通
    更新端点上会被服务端静默忽略（所以 :func:`guard_writable` 继续拒绝它），但在批量更新
    的 ``update`` 数组里它是**实测确认**能生效的（spec 的实测事实第 1 条：带 ``status: 0``
    能把已完成的任务改回未完成）。放宽的是一个端点加一个数组，不是那条规矩本身。

    收窄成 :data:`BATCH_UPDATE_FIELDS` 是同一件事的另一半：批量更新是**合并语义**
    （只发改动的字段，其余服务端保留），所以就算请求体里多带了别的字段，这里也不发出去
    ——「其余字段不因这次取消完成而改变」这句话靠的就是这一行。
    """
    items = body.get("update")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise FieldIgnoredError(
            '批量更新的请求体必须是 {"update": [...]}：形状不对就一个字节都不发',
            field="update",
        )
    narrowed = [
        {field: entry[field] for field in BATCH_UPDATE_FIELDS if entry.get(field) is not None}
        for entry in items
        if isinstance(entry, Mapping)
    ]
    return {"update": narrowed}


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
    """拦下文档说服务端**不接受**的字段——**普通**写路径（新建 / 更新）上的那一道。

    ``status`` 就是那一个（api-contracts.md 第 5 条）：写它请求会成功，服务端静默忽略，
    用户以为任务完成了。两个方向都不走它：完成走没有请求体的 ``complete_task``，取消完成
    走 ``task/batch`` 的 ``update``（那是 :func:`prepare_batch_body` 的事，实测确认能生效
    ——spec 的实测事实第 1 条）。所以这条守卫的例外**只有那一个端点那一个数组**，
    规矩本身没有变：文档说服务端不接受的字段，本地一律不发。
    """
    for field in NON_WRITABLE_FIELDS:
        if field in changes:
            raise FieldIgnoredError(
                f"{field} 不能通过新建/更新写入（服务端会静默忽略）；"
                "完成请走 complete_task，取消完成请走 batch_update",
                field=field,
            )


def prepare_project_body(
    snapshot: Mapping[str, Any] | None, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """清单的写请求体：**只**带 :data:`PROJECT_WRITABLE_FIELDS` 里那些字段。

    与任务的 :func:`merge_snapshot` 是同一件事，只是字段表不同、而且是**白名单**而不是
    「原文减掉黑名单」：任务的底稿要带未知字段（手机端设的东西不能丢），清单的原文里却
    带着 ``groupId`` / ``closed`` / ``permission`` 这些请求体压根不接受的字段——照单全收
    就是把「接口不接受」的东西发出去。

    底稿里那些**不改也要原样带回**的字段（``sortOrder`` 最要紧）就在这里 echo 回去：
    文档给 ``sortOrder`` 写了 "default 0"，而省略字段到底是替换还是合并**文档没说**，
    所以「改名顺手把用户的清单顺序重置成 0」是真实可能的（api-shapes §B10）。

    ``None`` 值的改动**丢掉不写**：``color=None`` 到底是「别动」还是「清空」文档没写，
    而这两个意思在请求体里长得一样。宁可少发一个字段，也不发一个猜来的。
    """
    body = {
        field: snapshot[field]
        for field in PROJECT_WRITABLE_FIELDS
        if isinstance(snapshot, Mapping) and snapshot.get(field) is not None
    }
    body.update(
        {
            field: value
            for field, value in changes.items()
            if field in PROJECT_WRITABLE_FIELDS and value is not None
        }
    )
    return body


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
