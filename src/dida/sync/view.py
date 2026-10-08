"""同步引擎对外的视图模型与纯函数。

引擎的读路径只暴露这里定义的类型：TUI 拿到的是**已经判断好**的东西——截止时间读作什么、
优先级标记长什么样、这一行算不算逾期、这一行算不算已完成——它自己不做判断。

两组类型：

- 输入（缓存 → 引擎）：:class:`ListSnapshot` / :class:`TaskSnapshot` / :class:`SyncState`，
  :class:`ViewSource` 是它们的只读入口，t08 的 ``Store`` 实现它。
- 输出（引擎 → TUI）：:class:`TaskItem` / :class:`CompletedItem` / :class:`CompletedSection`
  / :class:`SubtaskItem`——三种读形状（清单索引 / 某个容器的任务列表 / 单条任务的详情）
  的行在 :mod:`dida.sync.read` 里组装，这里只提供这一行的字段与它们各自的读法。

函数都是纯的：「现在」与 ``day_end`` 一律从参数进来，逻辑日判定交给
:mod:`dida.logical_day`，这里不重算任何日界。排序的规矩只有一条（:func:`by_due` →
:func:`dida.sync.rows.row_sort_key`）：逾期置顶 → 截止时间升序 → 优先级降序 → 没有截止
时间的排在有截止时间的后面 → 已完成的沉底。服务端的 ``sortOrder`` 一律不看。

**v1 那条读路径在 #58 里删掉了**：``TodayView`` 与它硬编码的三个分区（逾期 / 今日 /
收集箱无日期）、左栏的 ``ListSummary`` 徽标、以及 ``/`` 的模糊过滤（``fuzzy_match`` /
``filter_groups``）都只为那个「今日执行台」服务，spec 要求读模型重写、模糊过滤不迁移。
v2 的三种读形状在 :mod:`dida.sync.read`，没有哪一种认识「今天」这个分区。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Protocol, Sequence

from dida.logical_day import logical_day
from dida.sync.rows import completed_window_start, row_order_tail, row_sort_key

NO_DUE_TEXT = "-"
"""没有截止时间的读法；与「今天」一眼可分。

原来是 ``—``（U+2014）：rich 量它 1 格，而它的东亚宽度是**歧义**——zh_CN 的终端可能画
2 格，一旦它进了对齐列（截止时间那一列）整列就歪。换成 ASCII 的连字符：任何 locale 下
都是 1 格，形状仍然是「一道短横」。
"""

INBOX_ID = "inbox"
"""收集箱在 API 里的 projectId 别名。"""

INBOX_NAME = "收集箱"


@dataclass(frozen=True)
class ListSnapshot:
    """缓存里的一条清单（API 叫 project）：只有事实，没有判断。

    ``color`` / ``group_id`` / ``kind`` / ``permission`` 是服务端 ``Project`` 上的字段
    （``kind`` 是 ``TASK`` / ``NOTE``，``permission`` 是 ``write`` / ``read`` / ``comment``），
    清单索引页要用它们标出「装不了任务的」「改不动的」那些行（用户故事 23 / 24）。
    ``is_inbox`` 是客户端认出来的收集箱那一行——服务端的清单索引里没有它（实测）。
    """

    id: str
    name: str
    color: str | None = None
    group_id: str | None = None
    kind: str | None = None
    permission: str | None = None
    is_inbox: bool = False


@dataclass(frozen=True)
class TaskSnapshot:
    """缓存里的一条任务快照：只有事实，没有判断。"""

    id: str
    title: str
    list_id: str
    due: datetime | None = None
    """截止时刻（带时区）；``all_day=True`` 时它是个**日期标记**，按 ``.date()`` 读
    （写出去的正常形状是那一天的 UTC 午夜，#73；修好之前的历史数据可能是本地午夜）。"""

    all_day: bool = False
    priority: int = 0
    """``0`` / ``1`` / ``3`` / ``5``（无 / 低 / 中 / 高），与 API 一致。"""

    completed: bool = False

    completed_at: datetime | None = None
    """完成时刻（服务端的 ``completedTime``）；本地刚完成、服务端还没认过的那些是 ``None``。"""

    desc: str = ""
    """服务端的 ``desc``：GLOSSARY 里它是**备注**（详情页「备注」那一行画的就是它）。"""

    content: str = ""
    """服务端的 ``content``：GLOSSARY 里它是**描述**。

    ``desc`` 与 ``content`` 是两个字面不同的字段，这里不合并、也不互相兜底——详情页两行
    各画各的，改一个不会覆盖另一个。**v1 把这两个标反了**（描述当成 ``desc``），这份
    spec 纠正它：描述 = ``content``、备注 = ``desc``（``GLOSSARY.md`` 的「任务」一节，
    翻转的落点在 :func:`dida.tui.pages.detail.fields_of`）。
    """

    tags: tuple[str, ...] = ()
    """服务端的 ``tags``：标签名，按服务端给的顺序。"""

    repeat_flag: str = ""
    """服务端的 ``repeatFlag``（重复规则原文，如 ``RRULE:FREQ=WEEKLY``）。

    任务行只需要「是不是重复任务」（空串 = 不是），规则原文照旧原样留着——它只读，而且
    回写时一个字都不许动（spec 的「改期绝不触碰重复规则」）。
    """

    reminders: tuple[str, ...] = ()
    """服务端的 ``reminders``（提醒触发器原文）：行里只读「有没有提醒」，不改。"""


@dataclass(frozen=True)
class SyncState:
    """缓存里的同步状态。"""

    last_refresh_at: datetime | None = None
    pending_count: int = 0


class ViewSource(Protocol):
    """读路径的数据来源：本地缓存。t08 的 ``Store`` 是生产实现，测试用内存替身。"""

    def lists(self) -> Sequence[ListSnapshot]:
        """全部清单。"""
        ...

    def tasks(self) -> Sequence[TaskSnapshot]:
        """全部任务快照，含已完成。"""
        ...

    def sync_state(self) -> SyncState:
        """同步状态。"""
        ...

    def resolve_id(self, id: str) -> str:
        """这个名字**现在是**哪个 id：认领换过名就给新的那个，否则给原样。

        认领（新建推成功）会把本地那个临时 id 换成服务端给的 id，而**已经画在屏幕上的旧 id
        不会跟着变**——界面拿着它回来时，本地那一行已经在真 id 底下了（工单 #75 / ADR-0009）。
        这一口就是那条「旧 id 继续认」的路：引擎在**调用方递 id 进来的那几个入口**上问它一次。

        只读的替身（``InMemorySource``）照实现：它的 ``create`` 直接给真 id，认领在替身里不
        存在，所以它认不出任何别名——那不是「说假话」，是它这半边没有这件事。
        """
        ...


@dataclass(frozen=True)
class TaskItem:
    """中栏一行，字段都已经是可以直接画的成品。"""

    task_id: str
    title: str
    list_id: str
    list_name: str
    priority: int
    priority_mark: str
    due: datetime | None
    all_day: bool
    due_text: str

    overdue: bool = False
    """逾期了没有（逻辑日判定，见 :func:`is_overdue`）。

    日期判断全在引擎这一层（架构规则：TUI 拿到的是已经判断好的成品），所以「标红」这件事
    的颜色由 TUI 决定、**位**由这里给：TUI 自己拿截止时间去比会写出第二份日期比较，那正是
    全天任务与 ``04:00`` 边界上会各错一次的地方。
    """

    desc: str = ""
    """**备注**，原样来自快照（TUI 不解析它；术语表：备注 = ``desc``）。"""

    content: str = ""
    """**描述**，原样来自快照（术语表：描述 = ``content``）。"""

    tags_text: str = ""
    """标签的成品读法（``#工作 #季度``，见 :func:`format_tags`）。

    没有标签就是空串——「这一行要不要画」由这个空串回答，详情栏不自己判断有没有标签。
    """

    repeat_flag: str = ""
    """服务端的 ``repeatFlag``：非空就是重复任务（行里画一个重复标记）。"""

    reminders: tuple[str, ...] = ()
    """服务端的 ``reminders``：非空就是有提醒（行里画一个提醒标记）。"""

    completed: bool = False
    """这条任务已完成。

    「已完成沉底」要在**读模型**这一层成立，不能只靠界面把两段拼起来：视图的成员是混的
    （自定义视图的过滤条件里就有完成状态这一维），一个含已完成成员的视图会把做完的任务
    插在未完成中间。判定只看 ``status``（工单 #37 / spec 的「本地判定已完成」）。
    """


@dataclass(frozen=True)
class CompletedItem:
    """已完成区一行：标题、清单名、完成时刻的人类读法（都已经是成品）。"""

    task_id: str
    title: str
    list_name: str
    completed_at: datetime | None
    completed_text: str


@dataclass(frozen=True)
class CompletedSection:
    """中栏底部的已完成区：条数与行。

    折叠与否是 **TUI 的状态**（看点不看点是用户的事），所以这里没有 ``collapsed``；
    引擎只管「窗口内完成的有哪些」，行已经排好序、写好读法了。
    """

    items: tuple[CompletedItem, ...] = ()

    @property
    def count(self) -> int:
        """窗口内完成的条数（列表底部那一段有几行）。

        **屏幕上没有一处显示它**：那一段直接画行，而「已完成 N 项」那条分隔行是 spec 明确
        不要的（#64）。这个数只被测试用来断「这一段里应当有几条」。
        """
        return len(self.items)


def priority_mark(priority: int) -> str:
    """优先级标记：高 ``!``、中 ``~``、低与无 ``.``。

    三个字形都必须是宽度无歧义的（``tests/test_task_row_model.py`` 守着）。低优先级原来是
    ``·``（U+00B7，东亚**歧义**宽度）——rich 量 1 格而终端可能画 2 格，于是每一行都比终端
    实际画的宽一格。

    **列表行里已经不画它了**（#63）：任务行首那一列是勾选框 ``☐`` / ``☑``，只说「做完没有」，
    优先级只留在详细页的字段与挑选器里（排序仍然按它）。它曾经是整行的第一列。
    """
    return {5: "!", 3: "~"}.get(priority, ".")


PRIORITY_CYCLE: tuple[int, ...] = (0, 1, 3, 5)
"""四个档位的**线上编码**，按 ``p`` 键的循环顺序：无 → 低 → 中 → 高。"""


PRIORITY_NAMES: dict[int, str] = {0: "无", 1: "低", 3: "中", 5: "高"}
"""四个档位的**用户语言**（GLOSSARY：无 / 低 / 中 / 高），键是上面的线上编码。

**这是这张表唯一的家**（工单 #58 的 T3）。它原来在 ``tui/messages.py`` 与
``sync/views.py`` 各写了一份，而界面的注释还把其中一份称作「唯一一张表」——两处实现、
一句假话。它只能住在 sync 这一侧：``dida/tui/`` 只许 import ``dida.sync.engine``
（``tests/test_architecture.py`` 的允许表），而 ``sync/`` 永远不 import ``tui/``，
所以视图表单（:mod:`dida.sync.views`）与界面（经引擎的公开面）都得 import 这一份。

**次序是承重的**：``views.py`` 把「一条改动里点了哪些档」倒过来查它（``by_label``），
而界面的挑选器是按这里的插入顺序画四档的（无 → 低 → 中 → 高），所以次序照用户读的顺序写，
**不要**改成「高 → 无」。表外的取值读作「无」（与 :func:`priority_mark` 同一条口径）。
"""


def next_priority(priority: int) -> int:
    """``p`` 的下一档：无 → 低 → 中 → 高 → 无，值都是 API 的线上编码 ``0/1/3/5``。

    稠密的 ``1/2/3`` 是 v1 那套日期输入语法的档位序号（用户写的 ``!1``/``!2``/``!3``），
    不是要发给服务端的取值：``!3`` 是「高」，对应线上的 ``5``——v2 作废了那套语法，所以
    这里只剩线上编码这一套。

    认不出来的取值（服务端给了表外的数）当作「无」：``priority_mark`` 本来就把它们读作
    ``·``，从那儿往前推一档正好是「低」。
    """
    index = PRIORITY_CYCLE.index(priority) if priority in PRIORITY_CYCLE else -1
    return PRIORITY_CYCLE[(index + 1) % len(PRIORITY_CYCLE)]


def list_names(lists: Sequence[ListSnapshot]) -> dict[str, str]:
    """清单 id → 显示名；收集箱即使没有清单行也能叫出名字。"""
    names = {item.id: item.name for item in lists}
    names.setdefault(INBOX_ID, INBOX_NAME)
    return names


def format_tags(tags: Sequence[str]) -> str:
    """标签的人类读法：``#工作 #季度``；没有标签就是空串。

    ``#`` 是用户在日期解析器里写标签时用的那个记号（``交报告 #工作``），所以详情栏照它
    画——引擎把读法算好（与 ``due_text`` / ``priority_mark`` 同一个口径），TUI 只画。
    """
    return " ".join(f"#{tag}" for tag in tags)


def task_item(snapshot: TaskSnapshot, names: dict[str, str], *, now: datetime, day_end: str) -> TaskItem:
    """一条任务快照 → 一行成品。"""
    return TaskItem(
        task_id=snapshot.id,
        title=snapshot.title,
        list_id=snapshot.list_id,
        list_name=names.get(snapshot.list_id, snapshot.list_id),
        priority=snapshot.priority,
        priority_mark=priority_mark(snapshot.priority),
        due=snapshot.due,
        all_day=snapshot.all_day,
        due_text=format_due(snapshot.due, all_day=snapshot.all_day, now=now, day_end=day_end),
        overdue=is_overdue(snapshot, today=logical_day(now, day_end).label, day_end=day_end),
        desc=snapshot.desc,
        content=snapshot.content,
        tags_text=format_tags(snapshot.tags),
        repeat_flag=snapshot.repeat_flag,
        reminders=snapshot.reminders,
        completed=snapshot.completed,
    )


def due_day(due: datetime, *, all_day: bool, day_end: str) -> date:
    """这条任务的截止属于哪个逻辑日。

    有具体时刻的截止：按逻辑日偏移算（``day_end = "04:00"`` 时，昨天 23:00 属于今天）。
    全天任务的「截止」是**日期标记**（服务端写的是当天 00:00），它属于它写的那一天——
    按 00:00 这个时刻去套偏移会把它整天挪到前一个逻辑日。
    """
    return due.date() if all_day else logical_day(due, day_end).label


def is_overdue(snapshot: TaskSnapshot, *, today: date, day_end: str) -> bool:
    """这条任务逾期了没有：有截止时间、且落在当前逻辑日**之前**（用户故事 25 / 87）。

    判据是逻辑日（:func:`due_day`），不是裸的时刻比较：``day_end = "04:00"`` 时当天
    03:00 属于昨天，它逾期；而当天 00:00 那个全天标记属于今天，它不逾期。全天任务因此
    不会因为边界配在半夜就被算成逾期（那是 ``due_day`` 已经分好的事，这里不重写第二份）。

    已完成的不算逾期：一条做完的任务不该在「今天」里被标红（它压根不该在那个视图里——
    三个内置视图都只收未完成的）。
    """
    if snapshot.completed or snapshot.due is None:
        return False
    return due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end) < today


def format_due(due: datetime | None, *, all_day: bool, now: datetime, day_end: str) -> str:
    """截止时间的人类读法：「今天 18:00」「昨天 09:00」「3 天前」；没有截止时间给「—」。

    全天任务只给日词（「今天」），绝不给「今天 00:00」。哪一天用**逻辑日**判定：
    ``day_end = "04:00"`` 时，凌晨两点看到的昨天 23:00 截止读作「今天 23:00」。
    """
    if due is None:
        return NO_DUE_TEXT
    delta = (logical_day(now, day_end).label - due_day(due, all_day=all_day, day_end=day_end)).days
    if delta == 0:
        word = "今天"
    elif delta == 1:
        word = "昨天"
    elif delta == -1:
        word = "明天"
    elif delta > 1:
        word = f"{delta} 天前"
    else:
        word = f"{-delta} 天后"
    if all_day or abs(delta) > 1:
        return word
    return f"{word} {due.strftime('%H:%M')}"


def by_due(items: Sequence[TaskItem]) -> list[TaskItem]:
    """按 spec 的排序链排：截止时间升序 → 优先级降序 → 无日期在后 → 已完成沉底。

    键在 :func:`dida.sync.rows.row_sort_key`（纯函数，直接测）：这一份与「今天」那一屏、
    某个容器的任务列表、以及视图求值用的是同一个顺序——**客户端统一重排**，服务端的
    ``sortOrder`` 一律不看（spec 的「一个已知的、故意的取舍」）。
    """
    return sorted(items, key=row_sort_key)


# ------------------------------------------------------------------ 已完成流（t12）


def _completed_row_key(snapshot: TaskSnapshot) -> tuple:
    """已完成段一行的位置：借 :func:`dida.sync.rows.row_order_tail`（不重写它）。

    只取「已完成」那一位**之后**的那一段：进到这一段里的每一行都是已完成的（见
    :func:`completed_section` 的过滤条件），那一位在这里恒定，省掉它不改变顺序。

    这一层以前还包着一个 ``_CompletedRowFacts`` 适配器，只为把快照上的 ``id`` 摆成排序键认的
    ``task_id``；#64 之后排序键那一段有了自己的名字（:func:`row_order_tail`），适配器整个
    删掉了——按名字传参，不需要一个只差一个字段名的类型。
    """
    return row_order_tail(
        due=snapshot.due,
        priority=snapshot.priority,
        title=snapshot.title,
        task_id=snapshot.id,
    )


def _completion_moment(snapshot: TaskSnapshot, now: datetime) -> datetime:
    """这一行的完成时刻：服务端的 ``completedTime``；本地刚完成、服务端还没认过的用 ``now`` 占位（#74）。

    占位只是一个「完成于此时」的读法，不会被写回存储层：服务端认下之后那一次已完成流把真正的
    ``completedTime`` 带回来，下一次读就是权威的那一份了（ADR-0002 的本地豁免只保护改动碰过的
    ``status`` 那一位，``completedTime`` 照旧服务端说了算）。
    """
    return now if snapshot.completed_at is None else snapshot.completed_at


def completed_section(
    tasks: Sequence[TaskSnapshot],
    lists: Sequence[ListSnapshot],
    *,
    now: datetime,
    day_end: str,
    window_hours: int,
) -> CompletedSection:
    """已完成区：窗口 ``[now - window_hours, …]`` 内完成的任务，按正常排序键排。

    「完成于何时」的权威是服务端的 ``completedTime``（``completed_at``）：那是 ADR-0001 里
    唯一能被服务端过滤的变化时间戳，也是这条流的窗口依据。**本地刚按了完成、服务端还没认过的
    任务照样在这里**——它 ``completed=true`` 却还没有 ``completedTime``，于是拿「现在」当占位
    时刻（工单 #74）：完成之后从屏幕上消失，无论如何都不该发生。服务端认下之后那一次已完成流
    把真正的 ``completedTime`` 写回来，占位就被换掉了（本地那条改动豁免于服务端权威，
    ADR-0002）。占位**不参与窗口过滤**（窗口只筛真的时间戳），也**不参与排序**
    （见下一段），所以它换掉前后这一行都在同一个位置。没有上界：服务端时钟快一点不该让用户
    刚做完的任务消失。

    **顺序不是完成时刻倒序**（工单 #64）：与未完成段同一套键
    （:func:`dida.sync.rows.row_sort_key`，截止升序 → 优先级降序 → 无日期在后），
    「已完成沉底」是那个键的第一个元组位——它管的是未完成段那一侧，这一段里它恒定。
    所以刚做完的一条不会因为「刚」就跳到段首，它落在它自己的位置上。

    纯函数：「现在」与窗口大小都从参数进来，这一层不读时钟（t12 的窗口由引擎按注入的
    配置给）。
    """
    window_start = completed_window_start(now, window_hours)
    names = list_names(lists)
    # 没有服务端时间戳的那条用「现在」占位，但**窗口这一关它不参加**：`completed_at is None`
    # 不是「八天前完成」，拿 `now` 去过一遍窗口过滤只会把同一个判断写两遍。
    kept = [
        snapshot
        for snapshot in tasks
        if snapshot.completed
        and (snapshot.completed_at is None or snapshot.completed_at >= window_start)
    ]
    return CompletedSection(
        items=tuple(
            CompletedItem(
                task_id=snapshot.id,
                title=snapshot.title,
                list_name=names.get(snapshot.list_id, snapshot.list_id),
                completed_at=_completion_moment(snapshot, now),
                completed_text=format_due(
                    _completion_moment(snapshot, now), all_day=False, now=now, day_end=day_end
                ),
            )
            for snapshot in sorted(kept, key=_completed_row_key)
        )
    )


# ------------------------------------------------------------------ 子任务（t20）

SUBTASK_COMPLETED_STATUS = 1
"""子任务「勾上了」的那个 ``status``（``api-contracts.md``）：Normal ``0`` / Completed ``1``。

不是任务级那一对 ``-1/0/2``：拿 ``status == 1`` 判任务完成是错的，拿 ``status == 2``
判子任务完成同样是错的。两对取值只在这里相接。

「没勾上」的 ``0`` 不再有自己的常量：写路径（勾选 / 取消勾选）在 #58 里删掉了（spec 的
「子任务只看不勾」），读这一半只认「是不是 1」，其余取值一律读作没勾上
（:func:`subtask_completed`，与 ``priority`` 同一口径）。
"""


@dataclass(frozen=True)
class SubtaskItem:
    """右栏一行子任务：字段都已经是可以直接画的成品。"""

    subtask_id: str
    title: str
    completed: bool
    due_text: str = NO_DUE_TEXT
    """子任务 ``startDate`` 的人类读法；没有日期就是 :data:`NO_DUE_TEXT`。

    没有日期**不影响这一行存在**：``startDate`` 是可选的，标题与完成状态才是子任务必有
    的两样（工单 #20 的验收标准 #1）。
    """


def subtask_items(
    payload: Mapping[str, Any] | None, *, now: datetime, day_end: str
) -> tuple[SubtaskItem, ...]:
    """一条任务的原文 → 右栏的子任务行。

    只认服务端给的那一份 ``items``（``ChecklistItem``）：``status`` 是 0/1 那一对，日期
    字段叫 ``startDate``（与任务上的 ``dueDate`` 不是同一个名字，见 ``guards``）。

    没有 ``items``、``items`` 不是数组、某一条没有可用的 ``id``——都当作「没有这一行」
    跳过而不是报错：右栏是只读的展示，一条脏数据不该让整个详情栏空掉，也不该拦住
    其它子任务的勾选（勾选要的是 ``id``）。
    """
    if not isinstance(payload, Mapping):
        return ()
    raw = payload.get("items")
    if not isinstance(raw, list):
        return ()
    rows: list[SubtaskItem] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        due = _subtask_moment(entry.get("startDate"))
        rows.append(
            SubtaskItem(
                subtask_id=entry["id"],
                title=str(entry.get("title") or ""),
                completed=subtask_completed(entry.get("status")),
                due_text=format_due(
                    due, all_day=bool(entry.get("isAllDay")), now=now, day_end=day_end
                ),
            )
        )
    return tuple(rows)


def subtask_completed(status: Any) -> bool:
    """``status`` → 「勾上了没有」。认不出来的一律当作没勾上（与 ``priority`` 同一口径）。

    子任务那一对取值是 0/1（``SUBTASK_COMPLETED_STATUS``），别拿任务级的 2 来比。
    """
    try:
        return int(status or 0) == SUBTASK_COMPLETED_STATUS
    except (TypeError, ValueError):
        return False


def _subtask_moment(value: Any) -> datetime | None:
    """子任务的 ``startDate`` → 时刻；吃不下、或者没有时区偏移就当没有日期。

    没有时区就不猜（那正是「时区写错静默位移」那个 trap），宁可这一行不显示日期。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None
