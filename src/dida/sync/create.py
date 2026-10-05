"""新建（t15 / #39）：本地先造一条（临时 id），推送走 ``POST /open/v1/task``。

这一片只管一件事：**新建一条任务要发什么、本地那条长什么样**。只写用户真的写了的字段
（没写日期就不带 ``dueDate``），日期原样写回（不换时区）。新建的落点在 v1 里写死是收集箱
（``INBOX_ID``）；v2 的落点规则由 #39 改，改的就是这一行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from dida.api.guards import api_date
from dida.sync.view import INBOX_ID
from dida.sync.writes import WriteKind


class CreateMixin:

    """乐观新建：本地立刻多出一条（临时 id），服务端给的 id 由推送成功之后认领。"""



    def create(
        self,
        title: str,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：新建一条任务，落在收集箱（``a`` 快速添加，t15）。

        解析不在这一层：调用方拿 :meth:`plan` 的结果把标题、截止时间、优先级、标签交进来
        ——新建与改期共用一套语法，用户只用学一次（与 :meth:`reschedule` 同一条口径）。

        乐观写（ADR-0002）：先在本地造出这条任务（临时 id），**立即返回**；推送排到事件
        循环上走 ``POST /open/v1/task``，推不动就留在重试队列里按注入的钟退避重试。
        「新建的任务立刻出现在对应分区里」因此不依赖网络。

        没有日期是**合法**的：只写标题就是一条「今天要做、但没说几点」的任务。要挡的是
        「解析没成功还硬建」——那种任务在服务端还会被顺手清掉重复规则，是双重错误；
        拦它的是调用方：``plan()`` 的 ``diagnostics`` 非空就不该走到这里（``date_parser``
        的约定）。

        返回本地那条任务的 id；推成功之后它会落到服务端给的 id 上。
        """
        target = self._write_target()
        local_id = _local_task_id()
        target.enqueue(
            task_id=local_id,
            kind=WriteKind.CREATE,
            payload=_create_payload(
                title,
                due=due,
                all_day=all_day,
                priority=priority,
                tags=tags,
                project_id=INBOX_ID,
            ),
            now=self._clock.now(),
            list_id=INBOX_ID,
        )
        self._push_now()
        return local_id


def _local_task_id() -> str:
    """新建时的本地临时 id（服务端还没给 id，本地这条任务当场就要能被选中）。

    用 uuid 而不是计数器：计数器重启之后会从头开始，``local-1`` 会和上一次会话里那条
    还没推成功的新建撞上——两条任务共用一个 id，本地那份原文就串了。
    """
    return f"local-{uuid4().hex}"


def _create_payload(
    title: str,
    *,
    due: datetime | None,
    all_day: bool,
    priority: int | None,
    tags: Sequence[str],
    project_id: str,
) -> dict[str, Any]:
    """新建任务的请求体（也是本地那份原文）。

    只写用户真的写了的字段：没写日期就不带 ``dueDate``——凭空带一个日期字段正是
    「服务端静默忽略」的入口。日期走 :func:`dida.api.guards.api_date`，按它自己的 offset
    写成文档形式，不换时区（换时区就是静默位移那个 trap）。

    ``isAllDay`` 只跟 ``dueDate`` 一起出现：没有截止时间时它没有意义。``priority`` 同理——
    没写优先级就不写这个字段，服务端的默认值本来就是「无」。
    """
    payload: dict[str, Any] = {"title": title, "projectId": project_id}
    if due is not None:
        payload["dueDate"] = api_date(due, field="dueDate")
        payload["isAllDay"] = all_day
    if priority is not None:
        payload["priority"] = priority
    if tags:
        payload["tags"] = list(tags)
    return payload
