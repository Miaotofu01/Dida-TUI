"""新建（t15 / #39）：本地先造一条（临时 id），推送走 ``POST /open/v1/task``。

这一片只管一件事：**新建一条任务要发什么、本地那条长什么样**。只写用户真的写了的字段
（没写日期就不带 ``dueDate``），日期原样写回（不换时区）。

**落点是参数**（#39）：在清单里建就落在那个清单里，在视图里建落在收集箱（视图不是容器），
而「视图隐含的日期」由读模型给（``TaskList.implied_due``，判断写在
:func:`dida.sync.views.implied_due_for`）。这一片自己不认识视图——它只收一个 ``list_id``
和一个 ``due``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from uuid import uuid4

from dida.api.guards import api_date
from dida.sync.writes import (
    LOCAL_TASK_PREFIX,
    UnclaimedListError,
    WriteKind,
    is_addressable_task,
)


class CreateMixin:

    """乐观新建：本地立刻多出一条（临时 id），服务端给的 id 由推送成功之后认领。"""



    def create(
        self,
        title: str,
        list_id: str,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：新建一条任务，落在 ``list_id`` 那个容器里（``n``，#39）。

        **``list_id`` 是必填的**：``projectId`` 在新建上是必填字段（``openapi-dida365.md:222``），
        而「落在哪里」是调用方的判断——在清单里建就传那个清单的 id，在视图里建传收集箱
        （``INBOX_ID``，视图不是容器）。写死收集箱曾经是这里的默认值，代价是「在 工作 里
        新建」静默落进收集箱。

        解析不在这一层：调用方把标题与（视图隐含的）日期交进来——v2 的新建只填标题，
        自然语言日期那一整套已经作废（spec 的「日期解析器：整体作废」）。

        乐观写（ADR-0002）：先在本地造出这条任务（临时 id），**立即返回**；推送排到事件
        循环上走 ``POST /open/v1/task``，推不动就留在重试队列里按注入的钟退避重试。
        「新建的任务立刻出现在对应分区里」因此不依赖网络。

        没有日期是**合法**的：只写标题就是一条「今天要做、但没说几点」的任务。要挡的是
        「标题是空的还硬建」——那种任务在服务端还会被顺手清掉重复规则，是双重错误；
        拦它的是调用方（#39 的新建输入框）：空标题不该走到这里。

        落点那条清单**自己还没被认领**（id 还是 ``local-list-…``）时不入队，当场抛
        :class:`~dida.sync.writes.UnclaimedListError`：请求体里的 ``projectId`` 服务端没见过，
        推过去只会 404、退避重试、**永远出不了队**——而屏幕上那条任务看着像建好了。判据与
        写入那一侧共用一处（:func:`~dida.sync.writes.is_addressable_task`），不是这里另写一个
        前缀比较。这一条路径会自愈：那条清单被认领之后（#54 按名字认回来）再建就通了。

        返回本地那条任务的 id；推成功之后它会落到服务端给的 id 上。
        """
        target = self._write_target()
        local_id = _local_task_id()
        if not is_addressable_task(local_id, WriteKind.CREATE, project_id=list_id):
            # 只有一种可能：要落进去的那个清单还没被认领——任务那一半对新建不算数（它的 URL
            # 里没有 id）。判据仍然只有 is_addressable_task 那一处。
            raise UnclaimedListError(list_id)
        target.enqueue(
            task_id=local_id,
            kind=WriteKind.CREATE,
            payload=_create_payload(
                title,
                due=due,
                all_day=all_day,
                priority=priority,
                tags=tags,
                project_id=list_id,
            ),
            now=self._clock.now(),
            list_id=list_id,
        )
        self._push_now()
        return local_id


def _local_task_id() -> str:
    """新建时的本地临时 id（服务端还没给 id，本地这条任务当场就要能被选中）。

    用 uuid 而不是计数器：计数器重启之后会从头开始，``local-1`` 会和上一次会话里那条
    还没推成功的新建撞上——两条任务共用一个 id，本地那份原文就串了。

    前缀是 :data:`~dida.sync.writes.LOCAL_TASK_PREFIX`：它同时是「这条还没被认领」的记号，
    写路径据此拒绝往后排改动（#53）。
    """
    return f"{LOCAL_TASK_PREFIX}{uuid4().hex}"


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
