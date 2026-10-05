"""任务行的读法（工单 #37）：一行任务长什么样、整份列表按什么顺序排。

**纯函数**：这一层不读时钟、不碰网络、不 import 读模型（``TaskItem`` 在
:mod:`dida.sync.view`，那边反过来用这里的函数——所以这里只认「有这些字段的东西」，
:class:`RowFacts` 把那份形状写下来）。

排序是**客户端统一重排**，不看服务端的 ``sortOrder``（spec 的「一个已知的、故意的取舍」）：
截止时间升序 → 优先级从高到低 → 没有截止时间的排在有截止时间的后面 → 已完成的一律沉到最底。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

__all__ = ["RowFacts", "completed_window_start", "row_sort_key"]


class RowFacts(Protocol):
    """排一行所需的那几样事实；``TaskItem`` 满足它。

    结构类型而不是 import：:mod:`dida.sync.view` 要用这里的排序键，而 ``TaskItem`` 定义在
    那边——互相 import 会绕成一个环。**只需要这几样**也是真的：排序不该知道标题之外的东西。
    """

    task_id: str
    title: str
    due: datetime | None
    priority: int
    completed: bool


def row_sort_key(item: RowFacts) -> tuple:
    """一行在列表里的位置：``(已完成, 无日期, 截止时刻, 优先级降序, 标题, id)``。

    四段规矩各占一位，顺序就是 spec 的排序链：

    - ``completed`` 在最前：已完成的一律沉到最底（用户故事 90 的那条链的最后一段）。
    - ``due is None`` 在 ``due`` 之前：没有截止时间的排在有截止时间的后面。**这一位是必需的**
      ——直接拿 ``None`` 去和 ``datetime`` 比会抛 ``TypeError``；有了它，两个无日期的行在第
      二位相等、走到第三位时两边都是 ``None``（比较相等，不比较大小），两个有日期的行则
      在第三位比时刻。
    - ``-priority``：优先级从高到低（线上编码 ``0/1/3/5``，取负正好反过来）。
    - ``title`` / ``task_id``：前面的都一样时给一个**确定**的顺序（标题相同也不会每次排成
      不一样的样子）。
    """
    return (
        bool(item.completed),
        item.due is None,
        item.due,
        -int(item.priority),
        item.title,
        item.task_id,
    )


def completed_window_start(now: datetime, window_hours: int) -> datetime:
    """已完成区的左端：``now`` 往回 ``window_hours`` 小时。

    纯函数（「现在」与窗口大小都从参数进来），窗口的两处用法共用它：拉取时写进请求的
    ``startDate``，以及本地已完成区的过滤（:func:`dida.sync.view.completed_section`）。
    **同一个算式**是「屏幕上只显示最近 7 天」与「请求只拉最近 7 天」不会各说各话的原因。
    """
    return now - timedelta(hours=window_hours)
