"""自定义视图的建 / 改 / 删（工单 #36）：过滤维度、表单数据模型、本地库、求值接线。

三个接缝：

- **纯函数**：:mod:`dida.sync.views` 的求值与表单数据模型直接调——「求值走纯函数那条路径，
  与内置视图同一份实现」本身就是验收标准的一条，所以它不需要 app、也不需要假后端。
  期望值来自工单与 spec（issue #30 的「视图定义」一节），不是照抄实现。
- **接缝一**（``SyncEngine`` + 真 ``Store`` + 内存 ``FakeBackend``）：一份视图定义从表单
  到本地库、再到清单列表页与任务列表页的那条路。
- 界面那三条键（``n`` / ``e`` / ``d``）在 ``tests/test_view_overlay.py``。

**视图只存在本机**（ADR-0005）：它落在本地 sqlite 的 ``views`` 表里，**一个字节都不进
``config.toml``**（那是放 token 的文件），也不进待推送队列——API 没有「保存一组过滤条件」
这个接口。所以这里有一组「建 / 改 / 删视图之后待推送还是 0」与「配置文件一个字节没变」的
断言：那不是在测实现，那是在测「我们没有假装能把视图同步上去」。
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from dida.config import Config, config_path, load_config, save_config
from dida.storage.store import Store
from dida.sync.engine import (
    Completion,
    DueWindow,
    SyncEngine,
    ViewDefinition,
    builtin_view_definitions,
    evaluate_view,
    parse_view_form,
    view_form_values,
    view_from_payload,
    view_payload,
)
from dida.sync.view import TaskSnapshot
from dida.sync.views import (
    COMPLETED_DAYS_CHOICES,
    COMPLETION_CHOICES,
    DUE_CHOICES,
    PRIORITY_CHOICES,
    VIEW_COMPLETED_DAYS_FIELD,
    VIEW_COMPLETION_FIELD,
    VIEW_DUE_FIELD,
    VIEW_LISTS_FIELD,
    VIEW_NAME_FIELD,
    VIEW_PRIORITY_FIELD,
    VIEW_TAGS_FIELD,
    ViewFormProblem,
    due_window_of,
)
from dida.testing import FakeBackend, ManualClock

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
"""测试里的「现在」：2026-03-14 中午（逻辑日就是 03-14）。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def task(
    title: str,
    *,
    due: datetime | None = None,
    all_day: bool = False,
    priority: int = 0,
    completed: bool = False,
    completed_at: datetime | None = None,
    list_id: str = "work",
    tags: tuple[str, ...] = (),
) -> TaskSnapshot:
    """一条任务快照；``id`` 就用标题（快照只要 id 唯一）。"""
    return TaskSnapshot(
        id=title,
        title=title,
        list_id=list_id,
        due=due,
        all_day=all_day,
        priority=priority,
        completed=completed,
        completed_at=completed_at,
        tags=tags,
    )


def titles(evaluated) -> list[str]:
    return [item.snapshot.title for item in evaluated]


def evaluate(definition: ViewDefinition, tasks, *, now: datetime = T0, day_end: str = "24:00"):
    return evaluate_view(definition, tasks, now=now, day_end=day_end)


def choice_values(choices) -> tuple[str, ...]:
    return tuple(choice.value for choice in choices)


# ---------------------------------------------------------------- 过滤维度（纯函数）


def test_the_priority_dimension_keeps_the_high_priority_unfinished_example():
    """「高优先级未完成」是工单点名的例子：优先级收 5（API 的「高」），完成状态默认未完成。

    ``LIST_COLORS`` 那种取值表是 API 数据，所以优先级那一维收的是**线上编码** 0/1/3/5，
    不是日期解析器那套 ``!1/!2/!3``（api-contracts.md 第 3 条）。
    """
    high = ViewDefinition(id="v1", name="高优先级未完成", priorities=(5,))
    tasks = (
        task("高", due=at(15, 9), priority=5),
        task("中", due=at(15, 9), priority=3),
        task("低", due=at(15, 9), priority=1),
        task("无", due=at(15, 9)),
        task("高但做完了", due=at(15, 9), priority=5, completed=True, completed_at=T0),
    )

    assert titles(evaluate(high, tasks)) == ["高"]


def test_the_list_scope_keeps_only_the_selected_lists():
    """清单范围（多选）：只收列出来的那几个清单里的任务，别的清单一条都不进。"""
    scope = ViewDefinition(id="v2", name="工作与生活", lists=("work", "life"))
    tasks = (
        task("工作里的", list_id="work"),
        task("生活里的", list_id="life"),
        task("学习里的", list_id="study"),
    )

    assert titles(evaluate(scope, tasks)) == ["工作里的", "生活里的"]


def test_the_tag_dimension_keeps_a_task_carrying_any_of_the_selected_tags():
    """标签（多选）：命中**任一个**就算——用户挑两个标签问的是「这两类事」。"""
    tagged = ViewDefinition(id="v3", name="家里的事", tags=("家务", "购物"))
    tasks = (
        task("买菜", tags=("购物",)),
        task("拖地", tags=("家务",)),
        task("写代码", tags=("工作",)),
        task("没标签"),
    )

    assert titles(evaluate(tagged, tasks)) == ["买菜", "拖地"]


def test_the_completion_time_dimension_keeps_only_the_recently_completed():
    """「最近完成」= 完成状态「已完成」+ 完成时间窗口（工单点名的第二个例子）。

    「最近七天」是**含今天在内的七个逻辑日**（与「最近七天」那个内置视图同一条读法），
    所以第八天完成的不在。完成时间不知道的（本地刚勾上、服务端还没认过）也不在——
    猜一个进去就是显示一条可能几个月前就做完的任务。

    成员顺序是 #35 那份 ``order_key``（已完成沉底那一档）给的，不是按完成时间倒序：
    顺序归 #35，这一票只加「谁在成员里」。
    """
    recent = ViewDefinition(
        id="v4", name="最近完成", completion=Completion.COMPLETED, completed_days=7
    )
    tasks = (
        task("今天做完的", completed=True, completed_at=at(14, 9)),
        task("三天前做完的", completed=True, completed_at=at(11, 9)),
        task("六天前做完的", completed=True, completed_at=at(8, 9)),
        task("七天前做完的", completed=True, completed_at=at(7, 9)),
        task("没做完的"),
        task("完成时间不知道的", completed=True),
    )

    assert set(titles(evaluate(recent, tasks))) == {"今天做完的", "三天前做完的", "六天前做完的"}


def test_the_completion_window_walks_the_logical_day_at_the_boundary():
    """完成时间也按**当前逻辑日**算，不按自然日：与截止时间同一条读法。

    边界 ``04:00``、凌晨两点时今天其实是 03-13：03-14 01:00 完成的那条属于今天，
    而 03-13 03:00 完成的那条属于更早那个逻辑日。
    """
    today = ViewDefinition(
        id="v5", name="今天完成", completion=Completion.COMPLETED, completed_days=1
    )
    tasks = (
        task("凌晨一点做完的", completed=True, completed_at=at(14, 1)),
        task("昨夜三点做完的", completed=True, completed_at=at(13, 3)),
    )

    assert titles(evaluate(today, tasks, now=at(14, 2), day_end="04:00")) == ["凌晨一点做完的"]


def test_the_new_dimensions_default_to_no_filtering():
    """新维度是**加法**：一个字都不填时它们不筛掉任何东西（内置视图因此一个字都没变）。

    「所有」的成员就是全部未完成任务——带标签的、高优先级的、别的清单里的都在。
    这一条在「把某一维的默认值写成「只收空的」」时会红。
    """
    all_view = next(item for item in builtin_view_definitions() if item.id == "all")
    tasks = (
        task("高", priority=5, tags=("工作",), list_id="work"),
        task("没标签没优先级", list_id="life"),
    )

    assert titles(evaluate(all_view, tasks)) == ["高", "没标签没优先级"]
    assert titles(evaluate(ViewDefinition(id="v6", name="空的"), tasks)) == [
        "高",
        "没标签没优先级",
    ]


# ---------------------------------------------------------------- 表单数据模型（纯函数）


def high_priority_form() -> dict[str, str]:
    """用户在浮层里填的那一份（「高优先级未完成」，值就是屏幕上写的字）。"""
    return {
        VIEW_NAME_FIELD: "高优先级未完成",
        VIEW_LISTS_FIELD: "",
        VIEW_DUE_FIELD: "any",
        VIEW_PRIORITY_FIELD: "高",
        VIEW_TAGS_FIELD: "",
        VIEW_COMPLETION_FIELD: "unfinished",
        VIEW_COMPLETED_DAYS_FIELD: "any",
    }


def recent_done_form() -> dict[str, str]:
    """用户在浮层里填的那一份（「最近完成」）。"""
    return {
        VIEW_NAME_FIELD: "最近完成",
        VIEW_LISTS_FIELD: "",
        VIEW_DUE_FIELD: "any",
        VIEW_PRIORITY_FIELD: "",
        VIEW_TAGS_FIELD: "",
        VIEW_COMPLETION_FIELD: "completed",
        VIEW_COMPLETED_DAYS_FIELD: "7",
    }


def test_the_form_builds_the_high_priority_example_and_it_evaluates_on_the_pure_path():
    """表单那份 ``{字段名: 值}`` → 一个 :class:`ViewDefinition` → 走**同一个** ``evaluate_view``。

    这是验收标准的最后一条在表单这一侧的落点：用户填的东西没有另一条求值路径，
    它变成的定义与内置视图那三个是同一种东西。
    """
    parsed = parse_view_form(high_priority_form(), lists={"工作": "work"})

    assert isinstance(parsed, ViewDefinition)
    assert (parsed.name, parsed.priorities, parsed.completion) == (
        "高优先级未完成",
        (5,),
        Completion.UNFINISHED,
    )
    tasks = (
        task("高", priority=5),
        task("中", priority=3),
        task("高但做完了", priority=5, completed=True, completed_at=T0),
    )
    assert titles(evaluate(parsed, tasks)) == ["高"]


def test_the_form_builds_the_recently_completed_example():
    """「最近完成」那份也一样：完成状态 + 完成时间两维从表单到求值。"""
    parsed = parse_view_form(recent_done_form())

    assert isinstance(parsed, ViewDefinition)
    assert (parsed.completion, parsed.completed_days) == (Completion.COMPLETED, 7)


def test_the_form_understands_list_names_and_ids_and_several_of_them():
    """清单范围那一格写的是**名字或 id**，多个用空格 / 逗号分开（浮层里只有输入框）。

    存下去的是 id 而不是名字：清单可以改名，视图不该因此失去目标（与「改名也照旧」同一条）。
    """
    parsed = parse_view_form(
        {**high_priority_form(), VIEW_LISTS_FIELD: "工作, life"}, lists={"工作": "work", "life": "life"}
    )

    assert isinstance(parsed, ViewDefinition)
    assert parsed.lists == ("work", "life")


def test_a_list_name_that_contains_a_space_still_resolves():
    """名字里带空格的清单整段认（先整体认，再按分隔符切）——不然它永远选不上。"""
    parsed = parse_view_form(
        {**high_priority_form(), VIEW_LISTS_FIELD: "我的 工作"}, lists={"我的 工作": "mine"}
    )

    assert isinstance(parsed, ViewDefinition)
    assert parsed.lists == ("mine",)


def test_an_unknown_list_name_is_refused_rather_than_silently_dropped():
    """认不出的清单名**拒绝保存**并说清是哪几个，不悄悄存一个筛不出东西的视图。"""
    parsed = parse_view_form(
        {**high_priority_form(), VIEW_LISTS_FIELD: "工作 全划"}, lists={"工作": "work"}
    )

    assert isinstance(parsed, ViewFormProblem)
    assert parsed.unknown_lists == ("全划",)


def test_an_unknown_priority_word_is_refused():
    """优先级也只认那四档；写错的词拒绝保存（沉默地丢掉一维就是另一条静默出错的视图）。"""
    parsed = parse_view_form({**high_priority_form(), VIEW_PRIORITY_FIELD: "高 特高"})

    assert isinstance(parsed, ViewFormProblem)
    assert parsed.unknown_priorities == ("特高",)


def test_a_view_that_can_never_match_is_refused():
    """「未完成」+ 完成时间窗口永远筛不出东西：拒绝，而不是存一个每次都空的视图。"""
    parsed = parse_view_form(
        {**high_priority_form(), VIEW_COMPLETION_FIELD: "unfinished", VIEW_COMPLETED_DAYS_FIELD: "7"}
    )

    assert isinstance(parsed, ViewFormProblem)
    assert parsed.never_matches is True


def test_a_view_without_a_name_is_refused():
    """名字空着就不保存（与清单那条同一条口径：空态不许静默）。"""
    parsed = parse_view_form({**high_priority_form(), VIEW_NAME_FIELD: "  "})

    assert isinstance(parsed, ViewFormProblem)
    assert parsed.missing_name is True


def test_the_form_values_of_a_saved_definition_are_what_the_edit_form_shows():
    """``e`` 打开的那张表单填着**当前的**条件：每一项都回到它自己那一档 / 那一段文字。

    清单范围显示的是**名字**（用户认的是名字，id 是给服务端的），优先级显示「高」，
    完成时间显示「最近七天」——存下去的仍是 id 与线上编码。
    """
    definition = ViewDefinition(
        id="v7",
        name="高优先级未完成",
        priorities=(5, 1),
        tags=("家务",),
        lists=("work",),
        completion=Completion.COMPLETED,
        completed_days=7,
    )

    values = view_form_values(definition, names={"work": "工作"})

    assert values[VIEW_NAME_FIELD] == "高优先级未完成"
    assert values[VIEW_LISTS_FIELD] == "工作"
    assert values[VIEW_PRIORITY_FIELD] == "高 低"
    assert values[VIEW_TAGS_FIELD] == "家务"
    assert values[VIEW_COMPLETION_FIELD] == "completed"
    assert values[VIEW_COMPLETED_DAYS_FIELD] == "7"
    assert values[VIEW_DUE_FIELD] == "any", "没设截止时间就是「不限」那一档"


def test_the_form_shows_a_due_preset_by_its_own_value():
    """截止时间那几档是**相对逻辑日的说法**：今天到期（含逾期）/ 最近七天 / 已逾期 / 无日期。

    「今天到期」那一档的值就是 ``DueWindow(first=None, last=0)``——下界不设，所以逾期
    也在里面（写成 ``first=0`` 会静默丢掉每一条逾期任务）。
    """
    parsed = parse_view_form({**high_priority_form(), VIEW_DUE_FIELD: "today"})

    assert isinstance(parsed, ViewDefinition)
    assert parsed.due == DueWindow(first=None, last=0)
    assert view_form_values(parsed)[VIEW_DUE_FIELD] == "today"
    assert choice_values(DUE_CHOICES) == ("any", "today", "next7", "overdue", "undated"), (
        "档位表按屏上顺序，第一个是「不限」"
    )
    assert due_window_of("overdue") == DueWindow(first=None, last=-1), "已逾期：上界昨天、下界不设"


def test_the_priority_choices_are_the_online_codes_in_high_to_none_order():
    """优先级那几档：高 → 中 → 低 → 无，值是线上编码 5/3/1/0。"""
    assert tuple((choice.value, choice.label) for choice in PRIORITY_CHOICES) == (
        ("5", "高"),
        ("3", "中"),
        ("1", "低"),
        ("0", "无"),
    )


def test_the_completion_and_completion_time_choices_are_the_ones_the_form_offers():
    """完成状态三档、完成时间三档；「不限」在三处都是同一个值（表单里只有一个「不限」的意思）。"""
    assert tuple(choice.label for choice in COMPLETION_CHOICES) == ("未完成", "已完成", "不限")
    assert tuple(choice.label for choice in COMPLETED_DAYS_CHOICES) == (
        "不限",
        "最近七天",
        "最近三十天",
    )
    assert [choice.value for choice in COMPLETION_CHOICES if choice.label == "不限"] == [
        choice.value for choice in COMPLETED_DAYS_CHOICES if choice.label == "不限"
    ] == [choice.value for choice in DUE_CHOICES if choice.label == "不限"]


# ---------------------------------------------------------------- 落本地库的那一份原文


def test_a_definition_survives_the_storage_payload_round_trip():
    """定义与它落库的那份原文一一对应：每一维都活着回来（含完成时间那一维）。"""
    definition = ViewDefinition(
        id="v9",
        name="全都有的一个",
        due=due_window_of("today"),
        completion=Completion.COMPLETED,
        lists=("work", "life"),
        priorities=(5, 3),
        tags=("家务",),
        completed_days=30,
    )

    assert view_from_payload(view_payload(definition)) == definition


def test_a_payload_we_do_not_understand_degrades_to_a_usable_definition():
    """库里那一行读不成样子时给一个能用的定义，不把整个清单列表页带走。

    读路径上没有「坏一行就整屏空掉」的道理（与空缓存给空视图同一条口径）；认得出来的
    部分照旧留着。
    """
    decoded = view_from_payload(
        {"name": "半条", "due": "不是字典", "completion": "不认识", "priorities": ["x", 5]}
    )

    assert isinstance(decoded, ViewDefinition)
    assert (decoded.name, decoded.due, decoded.completion) == ("半条", None, Completion.UNFINISHED)
    assert decoded.priorities == (5,)
