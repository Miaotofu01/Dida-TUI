"""视图求值（#35）与自定义视图的持久化 / 表单数据模型（#36）。

:func:`evaluate_view` 那一半是**纯函数**：`视图定义 + 全量任务缓存 + 当前逻辑日` → 一份排好序
的任务列表。不碰网络、不碰存储、不 import Textual。所以它可以被直接测（不需要 app，
也不需要假后端），而「内置视图与自定义视图共用同一条求值路径」这句话才有内容：

- :class:`ViewDefinition` 是一个视图的**过滤条件 + 名字**（spec 的「视图定义」一节）。
  三个内置视图就是三个写死的定义（:func:`builtin_view_definitions`），**和自定义视图走
  同一个** :func:`evaluate_view`——没有 ``if builtin`` 这种分支。用户建的视图从本地库
  读出来、组成同样的定义，就从这里接进来（#36）。
- :func:`evaluate_view` 是求值：按定义筛成员，再按客户端统一的那份顺序排好
  （:func:`order_key`）。返回的 :class:`ViewTask` 除了任务本身还带着求值才知道的东西
  （``overdue``：这条逾期了没有），TUI 因此不必自己判日期。

过滤维度**只有 ``_matches`` 一处**：截止时间（``due``）、完成状态（``completion``）、清单范围
（``lists``）、优先级（``priorities``）、标签（``tags``）、完成时间（``completed_days``）。
后四维是 #36 加的，全是加法——一个都不填时它们不筛掉任何东西，所以三个内置视图的定义一个字
都没改，成员与顺序因此一模一样。**没有第二份判据**：页面不认识这些条件，只把用户填的值交给
:func:`parse_view_form`。

## 自定义视图住哪

**本地 SQLite，不是配置文件**（ADR-0005）。``config.toml`` 是放 token 的文件，为了改一个过滤
条件去手写凭据是不对的；而 API 里根本没有「保存一组过滤条件」这个接口（服务端只有清单与任务，
``/task/filter`` 过滤的是开始时间、硬顶 200 条、没有分页），所以自定义视图**只存在这台机器上**
——手机端、网页版没有它，换台机器就没了。这句实话必须能被用户看见（浮层里写着它，
:data:`~dida.tui.messages` 那一份文案）。

于是这个模块的另一半是**写路径**：:class:`ViewStore` 是本地副本要会的那几件事（t08 的 ``Store``
与测试里的 ``InMemorySource`` 都满足它），:class:`ViewMixin` 把建 / 改 / 删挂到引擎上。它与
:mod:`dida.sync.lists` 的清单写路径**故意不一样**的只有一处：**一条改动都不入队**。清单要推给
服务端，所以要乐观入队 + 重试；视图没有服务端那一半，入队只会留下一条永远推不出去的改动，
让状态栏那个「待推送 N」一直非零（#53 / #54 就是这一类）。所以这里的写是**同步落库就完事**，
「待推送」在建 / 改 / 删视图前后都不动。

「今天」不是一段干净的截止时间区间，而是**逾期 ∪ 截止于当前逻辑日**：写成
``[今天, 明天)`` 会静默丢掉每一条逾期任务。定义里它表达成
``DueWindow(first=None, last=0)``——上界是今天、下界不设，逾期自然落进来；置顶与标红的
依据是求值给的 ``overdue``。**下界必须显式写 ``None``**：``DueWindow(last=0)`` 会留下默认
的 ``first=0``，于是每一条逾期任务被静默丢掉（#35 的 merger 实测过：三个成员变一个）。

日期判断一律走**当前逻辑日**（:func:`dida.logical_day.logical_day` 与
:func:`dida.sync.view.due_day`），不走自然日；全天任务的截止是**日期标记**（当天 00:00），
不参与逻辑日偏移——``due_day`` 里已经这么分了，这里不重写第二份日期比较。「完成时间」那一维
同理走逻辑日（完成时刻 → 逻辑日），与截止时间同一把尺子。

「现在」与 ``day_end`` 一律从参数进来（与 :mod:`dida.sync.view` 同一条规矩），这一层不读时钟。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.api.guards import all_day_date
from dida.logical_day import logical_day
from dida.sync.rows import row_order_tail
from dida.sync.view import PRIORITY_CYCLE, PRIORITY_NAMES, TaskSnapshot, due_day, is_overdue
from dida.sync.writes import is_a_change
from dida.vocabulary import (
    ANY_VALUE,
    Completion,
    DueWindow,
    ViewDefinition,
    _completed_days_of,
    _completion_of,
    view_from_payload,
    view_payload,
)

# 视图**定义**与它落本地库的那份原文住在 :mod:`dida.vocabulary`（#78）：本地库要原样存下它
# 再读回来，而本地库不许 import 这一层。求值、表单与写路径留在本模块。``_completed_days_of``
# 与 ``_completion_of`` 是「表单那一格的值 → 定义上的字段」那一步，定义读回与表单都走它们，
# 所以两处共用同一份翻译（不是各写一遍）。

__all__ = [
    "ANY_VALUE",
    "BUILTIN_VIEW_DAYS",
    "COMPLETED_DAYS_CHOICES",
    "COMPLETION_CHOICES",
    "DUE_CHOICES",
    "PRIORITY_CHOICES",
    "VIEW_COMPLETED_DAYS_FIELD",
    "VIEW_COMPLETION_FIELD",
    "VIEW_DUE_FIELD",
    "VIEW_LISTS_FIELD",
    "VIEW_NAME_FIELD",
    "VIEW_PRIORITY_FIELD",
    "VIEW_TAGS_FIELD",
    "Completion",
    "DueWindow",
    "UnknownViewError",
    "ViewChoice",
    "ViewDefinition",
    "ViewFormProblem",
    "ViewMixin",
    "ViewStore",
    "ViewTask",
    "builtin_view_definitions",
    "due_window_of",
    "evaluate_view",
    "implied_due_for",
    "is_view_edit",
    "order_key",
    "parse_view_form",
    "view_form_values",
    "view_from_payload",
    "view_payload",
]


BUILTIN_VIEW_DAYS = 7
"""「最近七天」的窗口：从当前逻辑日起的**七个**逻辑日（spec：今天算第一天）。"""


TODAY_VIEW = ViewDefinition(id="today", name="今天", due=DueWindow(first=None, last=0))
"""「今天」= 逾期 ∪ 截止于当前逻辑日（用户故事 25）。

``first=None``（下界不设）是**承重**的：它就是「逾期也在里面」的那一半。写成
``DueWindow(first=0, last=0)`` 会静默丢掉每一条逾期任务。
"""

NEXT7_VIEW = ViewDefinition(
    id="next7", name="最近七天", due=DueWindow(first=0, last=BUILTIN_VIEW_DAYS - 1)
)
"""「最近七天」= 截止时间落在从今天起的七个逻辑日内（用户故事 26）。逾期的不算。"""

ALL_VIEW = ViewDefinition(id="all", name="所有")
"""「所有」= 全部未完成任务（用户故事 27）：未来截止的、没有日期的都在。"""


def builtin_view_definitions() -> tuple[ViewDefinition, ...]:
    """三个内置视图的定义，按清单索引里的顺序（今天 / 最近七天 / 所有）。"""
    return (TODAY_VIEW, NEXT7_VIEW, ALL_VIEW)


def implied_due_for(definition: ViewDefinition, *, now: datetime, day_end: str) -> datetime | None:
    """在**这个视图里**新建一条任务，它自动该带上的截止时间；没有就是 ``None``（#39 / #58）。

    用户故事 35：「如果视图隐含了日期（比如在「今天」里建），新任务自动带上那个日期」。

    **规则只有一句，而且是定义的一句话**：今天在这个视图的截止窗口里（``covers(today)``），
    而且**今天就是它最新的那一天**（``last == 0``）——这时隐含当前**逻辑日**那一个日期。
    换句话说，用户在这一屏里写下的东西，意思就是「今天」：带上去的新任务当场就在这一屏里，
    而窗口里也没有比今天更晚的日子可选（有的话，挑哪一天都是替用户做一个他没做的决定）。

    判据是 ``ViewDefinition.due``（定义），**不是这个视图的 id**：内置的那三个也是定义，
    用户自建的也是定义，两者走同一条求值路径——同一个窗口必须给同一个答案，否则同一屏
    在两种视图上会有两种行为（#58 的 S1：按 id 在三个内置定义里查的那一版就让自建的
    拿到 ``None``）。所以它是个**纯函数**：只看定义、注入的 ``now`` 与 ``day_end``，
    不读时钟、不碰存储、不认识「内置」这两个字。

    表单那五档截止条件（:data:`_DUE_CHOICE_TABLE`）逐档的结果：

    - 「今天到期（含逾期）」``DueWindow(first=None, last=0)`` → **今天的日期标记**。下界是
      ``None`` 承重地表示「逾期也在里面」（写成 ``first=0`` 会静默丢掉每一条逾期任务），
      而今天正是它最新的那一天。
    - 「不限」``None`` → 不隐含：这个视图连截止时间都不看。
    - 「最近七天」``DueWindow(first=0, last=6)`` → 不隐含：今天只是七分之一，**一段**窗口
      藏不进一个日期。
    - 「已逾期」``DueWindow(first=None, last=-1)`` → 不隐含：今天不在窗口里，带上去的新任务
      当场不在这一屏——那是给用户一个假的「落点」。
    - 「无日期」``DueWindow(dated=False, undated=True)`` → 不隐含：这一屏要的正是**没有**
      日期的任务，替它带上日期就是跟这个视图对着干。

    返回的是**全天任务的日期标记**（那个逻辑日的 UTC 午夜，``YYYY-MM-DDT00:00:00+0000``，
    承重：``due_day`` 按 UTC 认这个形状；写成本地午夜在本 app 与手机端都会偏一天，#73），
    不是「现在」——「今天要做、没说几点」正是这样一条任务。

    判断写在这里而不是 TUI 里的理由：哪一个视图隐含哪一天，是**视图定义**的语义，而定义
    只住在这一处；同时它也让「这条任务该不该带上日期」与视图求值用的是同一个逻辑日。
    """
    window = definition.due
    if window is None:
        return None
    today = logical_day(now, day_end).label
    if window.last != 0 or not window.covers(today, today=today):
        return None
    return all_day_date(today)


@dataclass(frozen=True)
class ViewTask:
    """求值结果里的一条：任务本身 + 求值才知道的那点东西。

    ``overdue`` 是「逾期」这个判断（逻辑日判定，已完成的不算），TUI 拿它标红；它不是
    TUI 自己算的——架构规则里日期判断全在引擎这一层。
    """

    snapshot: TaskSnapshot
    overdue: bool = False


def evaluate_view(
    definition: ViewDefinition,
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
) -> tuple[ViewTask, ...]:
    """求值：``定义 + 缓存 + 逻辑日`` → 排好序的成员。

    纯函数、确定性：同一份输入永远给同一个结果（排序键在 :func:`order_key`，所有分量都是
    任务自己的事实，没有随机、没有时钟、没有「和谁比过」的状态）。
    """
    today = logical_day(now, day_end).label
    members = [
        ViewTask(
            snapshot=snapshot,
            overdue=is_overdue(snapshot, today=today, day_end=day_end),
        )
        for snapshot in tasks
        if _matches(definition, snapshot, today=today, day_end=day_end)
    ]
    return tuple(
        sorted(
            members,
            key=lambda item: order_key(item.snapshot, today=today, day_end=day_end),
        )
    )


def order_key(snapshot: TaskSnapshot, *, today: date, day_end: str) -> tuple:
    """客户端统一重排的那份顺序（spec 的「排序」一节）。

    逾期置顶 → 截止时间升序 → 优先级降序 → 没有截止时间的靠后 → 已完成的沉底；
    末尾再按标题与 id 断开剩下的平局，所以结果是**确定的**（同一份输入不会两次不同）。

    **已完成那一档内部也走这条链**（工单 #64）：沉底之后仍按「有没有截止时间 → 截止时间
    升序 → 优先级降序 → 标题 → id」排，而且与真实清单已完成段**共用同一段实现**
    （:func:`dida.sync.rows.row_order_tail`，就是 ``row_sort_key`` 里「已完成」那一位之后的
    那一段）——同一个已完成集合在视图里与在清单里不会排成两个样子（用户故事 155/156）。
    **这个 ``due is None`` 位是必需的**，与那边同一条理由：直接拿 ``None`` 去比 ``datetime``
    会抛 ``TypeError``。

    四档的第一位互不相同（逾期 ``0`` / 未逾期 ``1`` / 无日期 ``2`` / 已完成 ``3``），
    而已完成那一档多一位，所以元组长度并不齐——**不会**跨档比到长度差：第一位不等就在那里
    短路了，跨档比较只发生在第一位上。

    ``due_day``（逻辑日）而不是裸的时刻，是「逾期置顶」与「截止时间升序」真正分开的地方：
    ``day_end = "04:00"`` 时当天 03:00 属于**昨天**，它逾期、要置顶，而它的时刻又比当天
    00:00 那个全天标记更晚——只按时刻升序会把它排到那条全天任务后面。
    """
    if snapshot.completed:
        return (
            3,
            *row_order_tail(
                due=snapshot.due,
                priority=snapshot.priority,
                title=snapshot.title,
                task_id=snapshot.id,
            ),
        )
    if snapshot.due is None:
        return (2, 0.0, -snapshot.priority, snapshot.title, snapshot.id)
    rank = 0 if is_overdue(snapshot, today=today, day_end=day_end) else 1
    return (
        rank,
        snapshot.due.timestamp(),
        -snapshot.priority,
        snapshot.title,
        snapshot.id,
    )


def _matches(
    definition: ViewDefinition, snapshot: TaskSnapshot, *, today: date, day_end: str
) -> bool:
    """这条任务在不在这个定义里：逐条判据，各自一句话。

    日期判据走 :func:`dida.sync.view.due_day`（它认全天任务的日期标记），所以这里不做
    第二份日期比较——两处日期比较漂移才是这类 bug 的来源。完成时间那一维同理走
    :func:`dida.logical_day.logical_day`，与截止时间同一把尺子。
    """
    if not _matches_completion(definition.completion, snapshot):
        return False
    if definition.lists and snapshot.list_id not in definition.lists:
        return False
    if definition.priorities and snapshot.priority not in definition.priorities:
        return False
    if definition.tags and not set(definition.tags) & set(snapshot.tags):
        return False
    if definition.completed_days is not None and not _completed_within(
        snapshot, days=definition.completed_days, today=today, day_end=day_end
    ):
        return False
    if definition.due is None:
        return True
    day = (
        None
        if snapshot.due is None
        else due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end)
    )
    return definition.due.covers(day, today=today)


def _completed_within(
    snapshot: TaskSnapshot, *, days: int, today: date, day_end: str
) -> bool:
    """这条任务是**最近 ``days`` 个逻辑日**（含今天）里完成的吗。

    完成时刻不知道的（本地刚勾上、服务端还没认过）**不算**：猜一个进来，用户会在这个
    「最近完成」里看到一条可能几个月前就做完了的任务——那是静默说谎。真实清单的已完成段
    （:func:`dida.sync.view.completed_section`）**不是**同一个选择（#74）：那里是用户刚刚
    做完的那一条**已经在屏幕上**、绝不能凭空消失，所以它用「现在」占位；而这里是一个按
    完成时间筛出来的集合，本来就不含刚做完的那一条（它进这个视图之前就不是成员）。
    """
    if snapshot.completed_at is None:
        return False
    day = logical_day(snapshot.completed_at, day_end).label
    return today - timedelta(days=days - 1) <= day <= today


def _matches_completion(completion: Completion, snapshot: TaskSnapshot) -> bool:
    if completion is Completion.ANY:
        return True
    return snapshot.completed if completion is Completion.COMPLETED else not snapshot.completed


# ------------------------------------------------------------------ 表单的数据模型（#36）

VIEW_NAME_FIELD = "name"
VIEW_LISTS_FIELD = "lists"
VIEW_DUE_FIELD = "due"
VIEW_PRIORITY_FIELD = "priority"
VIEW_TAGS_FIELD = "tags"
VIEW_COMPLETION_FIELD = "completion"
VIEW_COMPLETED_DAYS_FIELD = "completed_days"
"""视图表单里各格的名字（表单浮层按字段名把值交回来）。

它们住在这一层而不是页面里：把 ``{字段名: 值}`` 读成一份定义是**数据模型**的判断
（:func:`parse_view_form`），页面只负责把这几格画出来。清单那张表单反过来——它的字段名与
解析都在页面那一侧，因为清单的写路径没有「一份定义」这种中间物。
"""


@dataclass(frozen=True)
class ViewChoice:
    """选择框里的一档：``value`` 是交回去的东西，``label`` 是屏幕上写的字。

    与 ``dida.tui.overlays.FormOption`` 长得一样但**不是同一个类型**：这一层不许 import
    Textual（它是纯数据），页面把这几张表翻成控件那一份。与 ``sync/lists.py`` 的
    :class:`~dida.sync.lists.ListColor` 同一条口径——取值表是 API / 领域数据，住在 ``sync/``。
    """

    value: str
    label: str


_DUE_CHOICE_TABLE: tuple[tuple[str, str, DueWindow | None], ...] = (
    (ANY_VALUE, "不限", None),
    # 「今天到期」= 逾期 ∪ 截止于今天：下界显式写 ``None``（写 ``first=0`` 会静默丢掉
    # 每一条逾期任务，那是 #35 实测过的坑）。
    ("today", "今天到期（含逾期）", DueWindow(first=None, last=0)),
    ("next7", "最近七天", DueWindow(first=0, last=BUILTIN_VIEW_DAYS - 1)),
    ("overdue", "已逾期", DueWindow(first=None, last=-1)),
    ("undated", "无日期", DueWindow(dated=False, undated=True)),
)
"""「截止时间」那一格的几档：值、屏上的字、它对应的区间。

几个**相对逻辑日的说法**，不是一个自由输入的范围：用户问的是「今天到期」「最近七天」
这种话，而区间两端都由这一层解释（写错一位就是一个静默筛错东西的视图）。
"""

DUE_CHOICES: tuple[ViewChoice, ...] = tuple(
    ViewChoice(value, label) for value, label, _ in _DUE_CHOICE_TABLE
)

PRIORITY_CHOICES: tuple[ViewChoice, ...] = tuple(
    ViewChoice(str(value), PRIORITY_NAMES[value]) for value in (5, 3, 1, 0)
)
"""优先级那几档：**高 → 中 → 低 → 无**，值是线上编码 ``5/3/1/0``。

四档的中文写法不在这一页：表本体是 :data:`dida.sync.view.PRIORITY_NAMES`（一处，
工单 #58 的 T3）。这里只决定**表单里的次序**（筛选条件从高往低读最自然），所以显式写出
那四个值，不拿表的插入顺序当屏上顺序——表的次序是给界面的挑选器用的（无 → 低 → 中 → 高）。"""

COMPLETION_CHOICES: tuple[ViewChoice, ...] = (
    ViewChoice(Completion.UNFINISHED.value, "未完成"),
    ViewChoice(Completion.COMPLETED.value, "已完成"),
    ViewChoice(ANY_VALUE, "不限"),
)

COMPLETED_DAYS_CHOICES: tuple[ViewChoice, ...] = (
    ViewChoice(ANY_VALUE, "不限"),
    ViewChoice("7", "最近七天"),
    ViewChoice("30", "最近三十天"),
)
"""「完成时间」那一格的几档：完成的**逻辑日**落在最近几个逻辑日内（含今天）。"""


def due_window_of(value: str) -> DueWindow | None:
    """「截止时间」那一档的值 → 它对应的区间；``ANY_VALUE`` 与认不出的值都是「不限」。"""
    return next((window for name, _, window in _DUE_CHOICE_TABLE if name == value), None)


def view_form_values(
    definition: ViewDefinition, *, names: Mapping[str, str] | None = None
) -> dict[str, str]:
    """一份定义 → 表单上该显示的那一格一格的值（``e`` 打开时填的就是这一份）。

    清单范围显示**名字**（用户认的是名字，id 是给服务端的；``names`` 是 id → 名单），
    优先级显示「高 低」，完成时间显示「最近七天」——存下去的仍然是 id 与线上编码。
    认不出来的东西**照原样显示**（清单被删了就把 id 写出来，改个名字不该顺手把它丢掉）。
    """
    lookup = names or {}
    return {
        VIEW_NAME_FIELD: definition.name,
        VIEW_LISTS_FIELD: " ".join(lookup.get(item, item) for item in definition.lists),
        VIEW_DUE_FIELD: next(
            (
                value
                for value, _, window in _DUE_CHOICE_TABLE
                if window is not None and window == definition.due
            ),
            ANY_VALUE,
        ),
        VIEW_PRIORITY_FIELD: " ".join(
            # 认不出来的档照原样显示成数字（手改过的库）：静默丢掉一维就是静默筛错东西，
            # 而写成数字之后用户一按确认就会被拒、并且被告知是哪一个词。
            PRIORITY_NAMES.get(item, str(item)) for item in definition.priorities
        ),
        VIEW_TAGS_FIELD: " ".join(definition.tags),
        VIEW_COMPLETION_FIELD: definition.completion.value,
        VIEW_COMPLETED_DAYS_FIELD: (
            ANY_VALUE
            if definition.completed_days is None
            else str(definition.completed_days)
        ),
    }


@dataclass(frozen=True)
class ViewFormProblem:
    """那张表单填不下去的几处**结构化**的理由（句子在 :mod:`dida.tui.messages` 里拼）。

    这一层不 import Textual、也不该拼给用户看的句子（那些是产品文案，住在 TUI 那一侧），
    所以它只说清楚「哪一处、哪几个词」。
    """

    missing_name: bool = False
    """名字那一格是空的。"""

    unknown_lists: tuple[str, ...] = ()
    """清单范围里认不出来的那几个词（不是任何一个清单的名字或 id）。"""

    unknown_priorities: tuple[str, ...] = ()
    """优先级里认不出来的那几个词。"""

    never_matches: bool = False
    """这个组合**永远筛不出任务**：完成状态「未完成」+ 完成时间窗口。"""


def parse_view_form(
    values: Mapping[str, str],
    *,
    view_id: str = "",
    lists: Mapping[str, str] | None = None,
) -> ViewDefinition | ViewFormProblem:
    """表单那一份 ``{字段名: 值}`` → 一份 :class:`ViewDefinition`（或填不下去的几处理由）。

    ``lists`` 是清单范围那一格认得的词 → id（名字与 id 都在里面，由页面从清单索引里攒）。
    认不出来的清单名 / 优先级词**拒绝保存**并指明是哪几个：沉默地存下一个筛不出东西的视图，
    用户要过一阵子才会发现，而且发现时不知道是自己填错了还是客户端没做。

    ``view_id`` 是保存到哪一行（新建时留空，由本地副本分配一个）。纯函数：不读时钟、
    不碰存储、不 import Textual。
    """
    name = (values.get(VIEW_NAME_FIELD) or "").strip()
    if not name:
        return ViewFormProblem(missing_name=True)
    known = lists or {}
    scope, unknown_lists = _scope_ids(values.get(VIEW_LISTS_FIELD) or "", known)
    priorities, unknown_priorities = _priority_values(values.get(VIEW_PRIORITY_FIELD) or "")
    if unknown_lists or unknown_priorities:
        return ViewFormProblem(
            unknown_lists=unknown_lists, unknown_priorities=unknown_priorities
        )
    completion = _completion_of(values.get(VIEW_COMPLETION_FIELD))
    completed_days = _completed_days_of(values.get(VIEW_COMPLETED_DAYS_FIELD))
    if completion is Completion.UNFINISHED and completed_days is not None:
        # 未完成的任务没有完成时间可筛：这个视图从建出来的那一刻起就是空的。拒绝它，
        # 而不是让用户对着一个永远空着的视图猜自己哪里填错了。
        return ViewFormProblem(never_matches=True)
    return ViewDefinition(
        id=view_id,
        name=name,
        due=due_window_of(values.get(VIEW_DUE_FIELD) or ANY_VALUE),
        completion=completion,
        lists=scope,
        priorities=priorities,
        tags=_tokens(values.get(VIEW_TAGS_FIELD) or ""),
        completed_days=completed_days,
    )


def _tokens(text: str) -> tuple[str, ...]:
    """一格文本 → 几个词：空格、逗号、顿号都算分隔符（用户按哪种习惯写都认）。

    **代价写在这里**：名字里带空格的标签（``read later``）没法在这一格里表达——它会被切成
    两个词。这一层不猜「哪一段是名字的哪一半」（猜错就是静默筛错东西），而多选控件是
    #45 那张票的挑选型字段；在那之前，这一格认的是单词与逗号分隔的写法。
    """
    return tuple(token for token in re.split(r"[,，、\s]+", text.strip()) if token)


def _scope_ids(
    text: str, known: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """清单范围那一格 → ``(认出来的 id, 认不出来的词)``。

    先拿**整段**认一次（清单名里可以有空格：「我的 工作」），再按分隔符逐个认；名字与 id
    都能写（id 认不出来就没法用了，而名字是用户唯一记得住的东西）。大小写不敏感是对 ASCII
    名字的照顾——中文名字本来就没有这一档。
    """
    stripped = text.strip()
    if not stripped:
        return (), ()
    folded = {key.casefold(): value for key, value in known.items()}

    def look(token: str) -> str | None:
        return known.get(token) or folded.get(token.casefold())

    whole = look(stripped)
    if whole is not None:
        return (whole,), ()
    found: list[str] = []
    unknown: list[str] = []
    for token in _tokens(stripped):
        view_id = look(token)
        if view_id is None:
            unknown.append(token)
        elif view_id not in found:
            found.append(view_id)
    return tuple(found), tuple(unknown)


def _priority_values(text: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """优先级那一格 → ``(认出来的档, 认不出来的词)``；中文写法与线上编码都认。"""
    by_label = {label: value for value, label in PRIORITY_NAMES.items()}
    found: list[int] = []
    unknown: list[str] = []
    for token in _tokens(text):
        value = by_label.get(token)
        if value is None and token.isdigit() and int(token) in PRIORITY_CYCLE:
            value = int(token)
        if value is None:
            unknown.append(token)
        elif value not in found:
            found.append(value)
    return tuple(found), tuple(unknown)


# ------------------------------------------------------------------ 写路径（只在本地）


class UnknownViewError(DidaError):
    """这个视图改不动 / 删不掉：本地没有这一行（工单 #36）。

    与清单的 :class:`~dida.sync.lists.UnknownListError` 同一条口径：本地没有这一行的原文，
    就没法改它——两行同名视图正是这么来的。视图不推服务端，所以这里的后果只是「没写成」。
    """

    def __init__(self, view_id: str) -> None:
        super().__init__(f"本地库里没有视图 {view_id}：这一笔改动没有落点")
        self.view_id = view_id
        """请求写入的那个视图 id，UI 可以直接显示出来。"""


@runtime_checkable
class ViewStore(Protocol):
    """本地副本在视图路径上要会的那几件事；``Store`` 与 ``InMemorySource`` 都满足它。

    它是 :class:`~dida.sync.read.ViewReader` 的**超集**（读那几条同名同形），但不是它的子类：
    :mod:`dida.sync.read` 反过来 import 本模块，写成继承就是一条循环 import。

    与清单那个 :class:`~dida.sync.lists.ListWriteTarget` 的分别在于**没有队列**：视图只在本地，
    所以这里没有 ``enqueue`` / ``pending`` / ``resolve`` / 认领服务端 id 那一套。
    """

    def view_definitions(self) -> Sequence[ViewDefinition]:
        """本地库里那些自定义视图的定义，按用户自己的顺序。"""
        ...

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """一个视图的定义；本地没有就是 ``None``。"""
        ...

    def save_view(self, definition: ViewDefinition) -> None:
        """写下一行视图（新建与改都是覆盖式地写）；行的位置照旧不动。"""
        ...

    def drop_view(self, view_id: str) -> None:
        """本地摘掉一行视图（**只动这一行**，一条任务都不碰）。"""
        ...

    def new_view_id(self) -> str:
        """一个还没被占用的本地视图 id。"""
        ...


def is_view_edit(current: ViewDefinition, definition: ViewDefinition) -> bool:
    """这份表单交回来的定义与本地那一行比，是不是一次真改动——**判据只此一处**（工单 #66）。

    比的是**整份定义的值**：:class:`ViewDefinition` 是 frozen dataclass，逐字段相等，所以六个
    维度一维都不用在这里重数一遍。在界面那一侧逐格重比一份是不行的——加一维（#36 就是这么
    加的）就会漏掉一处，而漏掉的那一处正是「改了那一维却不写」。

    比较本身交给 :func:`dida.sync.writes.is_a_change`（工单 #79 立的**那一个**判据本体）：
    两份定义各自读成落库的那份原文（:func:`view_payload`），逐位比。这一处只做「值 → 原文」
    的翻译，不自己再比一遍——所以「改视图」与「改字段」「改清单」用的是同一条口径。

    引擎在 :meth:`ViewMixin.update_view` 里问它，决定**写不写**，并把答案回报给调用方
    （#79：签名从 ``-> None`` 变成 ``-> bool``）。界面**不再**问第二遍——它已经按回报值走
    下一步了。

    本地没有那一行时**不归它管**：那是 :meth:`ViewMixin.update_view` 的 ``UnknownViewError``
    ——「不存在」与「没改」是两件事，混成一个判断会让前者静默变成后者。
    """
    return is_a_change(view_payload(current), view_payload(definition))


class ViewMixin:
    """自定义视图的建 / 改 / 删：**只写本地库**，一条改动都不入队（工单 #36）。

    方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上（要用 ``self._source``）。

    为什么没有乐观入队 + 重试那一套：那是给「要推给服务端」的改动用的（ADR-0002）。
    视图没有服务端那一半——API 里没有「保存一组过滤条件」这个接口——所以入队只会留下一条
    永远推不出去的改动，让状态栏那个「待推送 N」一直非零地骗人（#53 / #54 就是这一类：
    改动排在一个服务端从没见过的 id 后面）。这里的写因此是同步的：落库就完事，
    ``status().pending_count`` 在建 / 改 / 删视图前后一个数都不动。
    """

    def create_view(self, definition: ViewDefinition) -> str:
        """新建一个视图，返回本地那一行的 id。

        传进来的 ``definition.id`` **被忽略**（列表页上那一份值来自表单，它没有 id）：
        身份由本地副本分配——它才知道哪些 id 已经被占了。
        """
        target = self._view_target()
        view_id = target.new_view_id()
        target.save_view(replace(definition, id=view_id))
        return view_id

    def update_view(self, definition: ViewDefinition) -> bool:
        """改一个视图的条件与名字：那一行原地换掉（位置不动，建完再改不会跳到末尾）。

        **回报这次到底改了没有**（工单 #79）：交回来的那份与本地那一行**逐字段相同**时什么都
        不写、回 ``False``（#66 的验收标准 3 / 用户故事 134）——判据在 :func:`is_view_edit`，
        这里不另写一遍。``esc`` 从 #66 起是「保存并退出」，所以「开了表单又没改」这条路真的
        会走到；为一次没发生的改动重写本地那一行，就是「一次写」发生了。真改了回 ``True``。
        """
        target = self._view_target()
        current = target.view_definition(definition.id)
        if current is None:
            raise UnknownViewError(definition.id)
        if not is_view_edit(current, definition):
            return False
        target.save_view(definition)
        return True

    def delete_view(self, view_id: str) -> None:
        """删一个视图：**只摘掉这一行**。

        视图是一组过滤条件，不是容器：它「里面」的任务本来就在各自的清单里，所以这里一条
        任务都不动（验收标准「删视图不删任务」）。
        """
        target = self._view_target()
        if target.view_definition(view_id) is None:
            raise UnknownViewError(view_id)
        target.drop_view(view_id)

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """读：一个自定义视图的定义（``e`` 打开表单时要拿它填当前值）。

        源上没有这个能力（只读替身、降级模式）就是「没有这个视图」，不是错误——与读路径上
        那几个 ``isinstance`` 门同一条口径。
        """
        source = self._source
        if not isinstance(source, ViewStore):
            return None
        return source.view_definition(view_id)

    def _view_target(self) -> ViewStore:
        """视图写路径要写的那个本地副本。没接上就大声报错——绝不假装写成功了。"""
        if not isinstance(self._source, ViewStore):
            raise RuntimeError(
                "视图的写路径需要本地存储：SyncEngine(source=Store(...))；"
                "视图只存在本地库里，没有服务端那一半"
            )
        return self._source
