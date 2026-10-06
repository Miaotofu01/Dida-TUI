"""任务行的读法（工单 #37）：一行任务长什么样、整份列表按什么顺序排。

**纯函数**：这一层不读时钟、不碰网络、不 import 读模型（``TaskItem`` 在
:mod:`dida.sync.view`，那边反过来用这里的函数——所以这里只认「有这些字段的东西」，
:class:`RowFacts` 把那份形状写下来）。

三件事：

- :func:`row_sort_key` —— 整份列表的顺序。**客户端统一重排**，不看服务端的 ``sortOrder``
  （spec 的「一个已知的、故意的取舍」）：截止时间升序 → 优先级从高到低 → 没有截止时间的
  排在有截止时间的后面 → 已完成的一律沉到最底。:func:`row_order_tail` 是这条链里「已完成」
  那一位**之后**的那一段，两处只排已完成行的调用方（真实清单的已完成段、视图的已完成档）
  直接用它——同一条链，一处实现。
- :func:`completed_window_start` —— 已完成区/已完成流往回看多久（7 天）的那条左端。
- :func:`task_is_completed` —— 本地判定「已完成」只看 ``status``，不看完成时间戳。

行的**文字**读法（``format_due`` / ``priority_mark`` / ``NO_DUE_TEXT`` / ``format_tags``）留在
:mod:`dida.sync.view`：那里是 #33 落地、#35 又大改过的读模型，``read.py`` 与引擎都从那儿取，
搬一次只会白折腾一圈。行本身（标记、注解、窄屏丢弃顺序）在 :mod:`dida.tui.pages.tasks`。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

__all__ = [
    "RowFacts",
    "completed_window_start",
    "row_order_tail",
    "row_sort_key",
    "task_is_completed",
]


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


def row_order_tail(
    *, due: datetime | None, priority: int, title: str, task_id: str
) -> tuple:
    """排序链里**「已完成」那一位之后**的那一段：``(无日期, 截止时刻, 优先级降序, 标题, id)``。

    两处「同一个已完成集合要排成同一个样子」共用它：真实清单的已完成段
    （:func:`dida.sync.view.completed_section`）与视图求值的已完成档
    （:func:`dida.sync.views.order_key`）。**这一段在 #64 落地时被手抄过一遍**，而抄出来的
    那一条没有测试抓得住它漂——``tests/test_custom_views.py`` 的交叉验证只走过一对「有日期 /
    没日期」，标题、id 与优先级正负号漂了照样是绿的。所以它现在是**一处**。

    分量与理由（:func:`row_sort_key` 那段解释就住在这里，不再抄第二遍）：

    - ``due is None`` 在 ``due`` 之前：没有截止时间的排在有截止时间的后面。**这一位是必需的**
      ——直接拿 ``None`` 去和 ``datetime`` 比会抛 ``TypeError``；有了它，两个无日期的行在这一位
      相等、走到下一位时两边都是 ``None``（比较相等，不比较大小），两个有日期的行则比时刻。
    - ``-priority``：优先级从高到低（线上编码 ``0/1/3/5``，取负正好反过来）。
    - ``title`` / ``task_id``：前面的都一样时给一个**确定**的顺序（标题相同也不会每次排成
      不一样的样子）。
    """
    return (due is None, due, -int(priority), title, task_id)


def row_sort_key(item: RowFacts) -> tuple:
    """一行在列表里的位置：``(已完成, *row_order_tail(...))``。

    两段：

    - ``completed`` 单列一位、排在最前：已完成的一律沉到最底（用户故事 90 的那条链的最后
      一段）。它只在「一个列表里同时有未完成与已完成」时才分得出高低，所以只排已完成行的
      调用方省掉它——那一段的顺序由 :func:`row_order_tail` 一处提供。
    - 其余分量（无日期位、截止时刻、优先级降序、标题、id）与理由全在
      :func:`row_order_tail`：**同一个已完成集合在真实清单里与在视图里因此排成一个样子**。
    """
    return (
        bool(item.completed),
        *row_order_tail(
            due=item.due,
            priority=item.priority,
            title=item.title,
            task_id=item.task_id,
        ),
    )


def completed_window_start(now: datetime, window_hours: int) -> datetime:
    """已完成区的左端：``now`` 往回 ``window_hours`` 小时。

    纯函数（「现在」与窗口大小都从参数进来），窗口的两处用法共用它：拉取时写进请求的
    ``startDate``，以及本地已完成区的过滤（:func:`dida.sync.view.completed_section`）。
    **同一个算式**是「屏幕上只显示最近 7 天」与「请求只拉最近 7 天」不会各说各话的原因。
    """
    return now - timedelta(hours=window_hours)


def task_is_completed(status: object) -> bool:
    """这条任务的 ``status`` 是不是「已完成」——**只看状态，不看有没有完成时间戳**。

    ``2`` 是完成、``0`` 是正常、``-1`` 是已放弃（``api-contracts.md``）。认不出来的一律
    当作**没有完成**：缺失的字段是「不知道」，而这里宽松处理会把一条已经取消完成的任务
    当成完成写进本地库（它随后既在已完成区里、又不在未完成清单里，看起来像凭空消失）。

    取消完成**不会清空** ``completedTime``（实测），所以「有没有完成时间」判不出完成与否
    ——那正是这条规矩存在的原因（spec 的「已完成」两处必须写清之一）。

    ``COMPLETED_STATUS`` 从存储层取（函数内 import，与 :func:`dida.sync.push._completed_status`
    同一条路）：同一份 API 事实只留一处，而 storage 反过来 import :mod:`dida.sync.view`，
    模块级 import 会绕成环。
    """
    from dida.storage.store import COMPLETED_STATUS

    try:
        return int(status) == COMPLETED_STATUS  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
