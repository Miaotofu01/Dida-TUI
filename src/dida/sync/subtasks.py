"""子任务（t20）：读子任务那一行。

v2 把子任务改成**只读**（spec 的「只读的字段」：子任务只看不勾，也不能增删改），所以这一片
只剩读那一半——本地那份任务原文里的 ``items`` 数组 → 详情页的只读行（#43 从
``task_detail()`` 读同一份成品，两种读形状共用 :func:`dida.sync.view.subtask_items`）。

v1 的写路径（``toggle_subtask``：勾选时先重读该任务、再只翻这一次那一个条目）在 #58 里删掉
了，连同它的 ``SubtaskWrite`` 账目与 `TaskReader` 重读口。**为什么连结论一起删**：spec 明确
这一版不做子任务的勾选，留着它就是一段没有调用方、只有自己的测试养着的代码（正是 #58 的
S4 指出的那个形状）。将来若重开这条路，顺序仍然是那一条：``items`` 是一整个数组、更新是
「发什么就是什么」，所以写回之前必须先重读，否则会把别处刚改过的子任务一起抹掉（而且服务端
一声不吭）。
"""

from __future__ import annotations

from typing import Any, Mapping

from dida.sync.read import PayloadReader
from dida.sync.view import SubtaskItem, subtask_items


class SubtaskMixin:

    """子任务：读那一份原文里的数组（只读，spec 的「子任务只看不勾」）。"""

    def subtasks(self, task_id: str) -> tuple[SubtaskItem, ...]:
        """读：本地那份原文里的子任务数组 → 详情页的行（工单 #20）。

        读的是**任务原文**（``items`` 原样存在本地快照里），不是某个领域 dataclass：
        子任务不需要自己的表，它本来就属于这条任务这一条记录（GLOSSARY 的「子任务」）。

        只读的替身读不出原文时给空元组，与空缓存给空视图同一条口径：读路径上没有可读的
        东西就是没有，不是错误。
        """
        return subtask_items(self._payload_of(task_id), now=self._clock.now(), day_end=self._day_end)

    def _payload_of(self, task_id: str) -> Mapping[str, Any] | None:
        """本地那份任务原文；源读不出原文时当作「没有这条任务」。

        要的是**读**的能力（``PayloadReader``），不是写的能力：详情页与子任务行都只读它
        （#33 的详情读形状也从这里过），只读的替身存得下原文就该读得到。
        """
        source = self._source
        return source.task_payload(task_id) if isinstance(source, PayloadReader) else None
