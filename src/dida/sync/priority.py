"""优先级（t17 / #45）：把线上编码 ``0/1/3/5`` 推进一档（无 → 低 → 中 → 高 → 无）。

这一片只管一件事：**优先级怎么推进**。TUI 不认识优先级取值，只说「推进光标下那一条」；
两套编码（日期解析器的 ``!1/!2/!3`` 与线上的 ``0/1/3/5``）相接处只有这里一处。
"""

from __future__ import annotations

from typing import Any, Mapping

from dida.sync.view import next_priority
from dida.vocabulary import read_priority


class PriorityMixin:

    """推进一档，然后走写路径（本地当场生效、立即推送）。"""



    def cycle_priority(self, task_id: str) -> None:
        """写：把优先级推进一档（``p``）：无 → 低 → 中 → 高 → 无。

        推进的是 **API 的线上编码** ``0/1/3/5``（:func:`~dida.sync.view.next_priority`），
        不是日期解析器那套档位序号：``!3`` 是「高」，对应线上的 ``5``，而 ``!5`` 根本不是
        一种写法（见 api-contracts.md 第 3 条）。TUI 因此不认识优先级取值，只说「推进
        光标下那一条」——两套编码的相接处就在这一行。

        走 :meth:`write` 那条写路径：本地当场生效、立即推送、推不动留在队列里按注入的钟
        退避重试（验收标准 #2）。本地没有这条任务时不入队（与 :meth:`defer` 同一条口径）：
        没有「当前优先级」可推进，凭空写一个只会留下一条永远推不出去的改动。
        """
        target = self._write_target()
        payload = target.task_payload(task_id)
        if payload is None:
            return
        self.write(task_id, changes={"priority": next_priority(_priority_of(payload))})


def _priority_of(payload: Mapping[str, Any]) -> int:
    """快照里的 ``priority`` → 线上编码的整数。

    口径只有一处（:func:`dida.vocabulary.read_priority`，本地库与写路径的判据都读它）：
    缺省与脏值当作「无」（``0``），不替服务端猜档位，也不让一个字符串把 ``p`` 卡住。
    """
    return read_priority(payload.get("priority"))
