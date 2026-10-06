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

import asyncio
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

from dida.api.client import DidaApiClient
from dida.config import Config, ConfigError, load_config, save_config
from dida.storage.store import Store
from dida.sync.engine import (
    Completion,
    DueWindow,
    ListKind,
    SyncEngine,
    UnknownViewError,
    ViewDefinition,
    builtin_view_definitions,
    completed_section,
    evaluate_view,
    parse_view_form,
    view_form_values,
    view_from_payload,
    view_payload,
)
from dida.sync.view import ListSnapshot, TaskSnapshot
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
from dida.testing import FakeBackend, FakeTransport, ManualClock

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


def test_completed_rows_in_a_view_follow_the_same_key_as_the_real_list_completed_section():
    """视图里的已完成行与真实清单的已完成段**同一条规矩**（工单 #64 验收标准 2，用户故事 155/156）。

    这一对任务专门把差别摆出来：一条有截止时间（03-20）、优先级 0，另一条没有截止时间、
    优先级 5。真实清单的已完成段按 ``row_sort_key``（有日期的在前）排成 Z/A；视图的成员
    此前走 ``order_key`` 的已完成那一档，那一档把「有没有日期」这一位丢了，于是按优先级排成
    A/Z——同一个集合两套顺序。用户故事 155 要的是一条规矩，所以两条路都得是 Z/A。
    """
    done = ViewDefinition(id="done", name="最近完成", completion=Completion.COMPLETED)
    tasks = (
        task("Z 有日期", due=at(20, 9), completed=True, completed_at=at(14, 10)),
        task("A 无日期", priority=5, completed=True, completed_at=at(14, 15)),
    )

    real_list = [
        item.title
        for item in completed_section(
            tasks,
            [ListSnapshot(id="work", name="工作")],
            now=T0,
            day_end="24:00",
            window_hours=168,
        ).items
    ]
    from_view = titles(evaluate(done, tasks))

    assert real_list == ["Z 有日期", "A 无日期"], "有截止时间的排在没截止时间的前面"
    assert from_view == real_list, "视图里换了一套顺序——同一条规矩才对"


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
    empty_window = view_from_payload({"name": "空的区间", "due": {}})

    assert isinstance(decoded, ViewDefinition)
    assert (decoded.name, decoded.due, decoded.completion) == ("半条", None, Completion.UNFINISHED)
    assert decoded.priorities == (5,)
    assert empty_window.due is None, (
        "``{}`` 不能当成默认的 DueWindow（那是 first=0/last=0，只收今天到期的）"
    )


# ---------------------------------------------------------------- 接缝一：本地库与引擎


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def make_engine(store, *, now: datetime = T0, day_end: str = "24:00") -> SyncEngine:
    """真引擎 + 真存储，**没有客户端**：视图那条路本来就不该需要网络。"""
    return SyncEngine(clock=ManualClock(now), day_end=day_end, source=store)


class CountingViewWrites:
    """把 ``save_view`` 数一笔的本地副本（包着真 ``Store``）。

    「引擎有没有写那一行」在真存储上**看不出来**：对同一份定义再写一次，行、位置、JSON 都
    一模一样（``Store.save_view`` 是 ``INSERT OR REPLACE``）。所以这个观察只能落在一个数着
    调用的副本上——它只实现视图那一侧的五个方法，正好是 ``ViewStore`` 的公开面。
    """

    def __init__(self, inner: Store) -> None:
        self._inner = inner
        self.saves = 0
        """``save_view`` 被调用了几次。"""

    def view_definitions(self) -> tuple[ViewDefinition, ...]:
        return self._inner.view_definitions()

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        return self._inner.view_definition(view_id)

    def save_view(self, definition: ViewDefinition) -> None:
        self.saves += 1
        self._inner.save_view(definition)

    def drop_view(self, view_id: str) -> None:
        self._inner.drop_view(view_id)

    def new_view_id(self) -> str:
        return self._inner.new_view_id()


def seed(store) -> None:
    """一份够用的缓存：两个清单、四条任务（高 / 中 / 已完成 / 没日期）。"""
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作"}, {"id": "life", "name": "生活"}],
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": "交报告",
                "priority": 5,
                "status": 0,
                "dueDate": at(15, 18).isoformat(),
            },
            {
                "id": "t2",
                "projectId": "work",
                "title": "整理桌面",
                "priority": 3,
                "status": 0,
            },
            {
                "id": "t3",
                "projectId": "life",
                "title": "买牛奶",
                "priority": 5,
                "status": 0,
            },
            {
                "id": "t4",
                "projectId": "life",
                "title": "做完的",
                "status": 2,
                "completedTime": at(13, 9).isoformat(),
            },
        ],
    )


def test_the_store_is_where_custom_views_live(store):
    """本地副本满足 ``ViewStore``：视图那五个方法都在它身上（不是另一份替身才有）。"""
    from dida.sync.engine import ViewStore

    assert isinstance(store, ViewStore)


def test_a_created_view_lands_in_the_local_store_and_comes_back_as_a_definition(store):
    """建一个视图：本地库里多出一行，读回来的是**同一份定义**（验收标准 3）。"""
    engine = make_engine(store)

    view_id = engine.create_view(
        ViewDefinition(id="", name="高优先级未完成", priorities=(5,))
    )

    assert view_id.startswith("view-"), "本地分配的 id（没有服务端那一半要认领）"
    assert store.view_definitions() == (
        ViewDefinition(id=view_id, name="高优先级未完成", priorities=(5,)),
    )
    assert engine.view_definition(view_id) == store.view_definitions()[0]


def test_a_view_survives_closing_and_reopening_the_store(tmp_path):
    """视图定义落**本地库**：换一个连接打开同一个文件，它还在（这就是「落库」的意思）。

    这条是「视图定义落本地库，不写进配置文件」那一条验收标准的前一半；后一半
    （配置文件一个字节没变）在下面两条。
    """
    path = tmp_path / "dida.sqlite3"
    with Store(path) as first:
        make_engine(first).create_view(
            ViewDefinition(id="", name="最近完成", completion=Completion.COMPLETED, completed_days=7)
        )

    with Store(path) as second:
        definitions = second.view_definitions()

    assert [item.name for item in definitions] == ["最近完成"]
    assert definitions[0].completed_days == 7


def test_a_custom_view_is_a_row_between_the_builtins_and_the_real_lists(store):
    """建好的视图出现在清单列表页上：内置视图之后、真实清单之前，条数来自**同一次求值**。"""
    seed(store)
    engine = make_engine(store)

    view_id = engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))
    rows = {row.id: row for row in engine.list_index()}

    assert [row.id for row in engine.list_index()][:5] == [
        "inbox",
        "today",
        "next7",
        "all",
        view_id,
    ]
    assert rows[view_id].kind is ListKind.CUSTOM
    assert (rows[view_id].name, rows[view_id].unfinished) == ("高优先级未完成", 2)
    assert [item.title for item in engine.tasks_in(view_id).items] == ["交报告", "买牛奶"], (
        "enter 进去看到的是过滤后的任务：两条高优先级未完成的，顺序是求值给的"
    )


def test_the_recently_completed_view_is_built_from_the_form_and_shows_through_the_engine(store):
    """「最近完成」从表单那一份值一路走到任务列表页（验收标准 9 的那一半）。"""
    seed(store)
    engine = make_engine(store)
    parsed = parse_view_form(recent_done_form())
    assert isinstance(parsed, ViewDefinition)

    view_id = engine.create_view(parsed)

    assert [item.title for item in engine.tasks_in(view_id).items] == ["做完的"]
    assert engine.list_index()[4].unfinished == 0, "它一条未完成的都没有（那一列数的是未完成）"


def test_editing_a_view_keeps_its_place_in_the_index(store):
    """改条件不让它在清单列表页上跳到末尾（``INSERT OR REPLACE`` 会换掉 rowid 的那个坑）。"""
    engine = make_engine(store)
    first = engine.create_view(ViewDefinition(id="", name="第一个"))
    second = engine.create_view(ViewDefinition(id="", name="第二个"))

    engine.update_view(replace(engine.view_definition(first), name="改过的"))

    assert [item.name for item in store.view_definitions()] == ["改过的", "第二个"]
    assert [row.id for row in engine.list_index()][4:6] == [first, second]


def test_updating_a_view_to_what_it_already_is_skips_the_local_write(store):
    """没动过的条件不产生一次写（#66 的验收标准 3 / 用户故事 134）——引擎那一半。

    界面那一半在 ``tests/test_view_overlay.py``（它决定「叫不叫」引擎写）；``update_view`` 是
    公开的写入口，谁都可能调它，所以引擎自己也要挡。断的是外部行为：注入的那个本地副本一次
    都没被写。真的改了名字就照旧正好一次——收敛不许把真改动一起挡掉。
    """
    writes = CountingViewWrites(store)
    engine = make_engine(writes)
    view_id = engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))
    after_create = writes.saves

    engine.update_view(engine.view_definition(view_id))  # 逐字段相同

    assert writes.saves == after_create, "没动过却重写了本地那一行"
    assert engine.view_definition(view_id).name == "高优先级未完成"

    engine.update_view(replace(engine.view_definition(view_id), name="改过的"))

    assert writes.saves == after_create + 1, "真改了就正好写一次"
    assert engine.view_definition(view_id).name == "改过的"


def test_editing_a_view_that_is_no_longer_there_is_refused(store):
    """本地没有那一行时不假装改成功（与清单那条 ``UnknownListError`` 同一条口径）。"""
    engine = make_engine(store)

    with pytest.raises(UnknownViewError):
        engine.update_view(ViewDefinition(id="view-404", name="没有这个"))
    with pytest.raises(UnknownViewError):
        engine.delete_view("view-404")


def test_deleting_a_view_leaves_every_task_in_its_own_list(store):
    """删视图**不删任务**：同一份缓存上，那些任务还在各自的清单里（验收标准 8）。"""
    seed(store)
    engine = make_engine(store)
    view_id = engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))

    engine.delete_view(view_id)

    ids = [row.id for row in engine.list_index()]
    assert store.view_definitions() == ()
    assert view_id not in ids, "视图那一行没了"
    assert {"work", "life"} <= set(ids), "真实清单一条都没少"
    assert [item.title for item in engine.tasks_in("work").items] == ["交报告", "整理桌面"], (
        "工作里那两条一条都没少（清单那一份顺序：有截止的在前）"
    )
    assert [item.title for item in engine.tasks_in("life").items] == ["买牛奶"]


def test_building_and_deleting_a_view_enqueues_nothing(store):
    """视图只在本地：建 / 改 / 删前后待推送都是 0，队列里也没有一行提到它。

    这是 #53 / #54 那一类坑的反面：**绝不**把一个改动排在一个服务端从没见过的 subject 上。
    视图没有服务端那一半，所以它连队列都不该进——进去了就是一条永远推不出去的改动，
    状态栏那个「待推送 N」会一直非零地骗人。
    """
    seed(store)
    engine = make_engine(store)

    view_id = engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))
    assert engine.status().pending_count == 0
    engine.update_view(replace(engine.view_definition(view_id), name="改过的"))
    assert engine.status().pending_count == 0
    engine.delete_view(view_id)
    assert engine.status().pending_count == 0

    assert store.pending() == () and store.pending_lists() == ()
    assert all(change.task_id != view_id for change in store.pending())
    assert all(change.list_id != view_id for change in store.pending_lists())


def test_a_view_is_not_pushed_even_when_a_client_is_connected(store):
    """接上真客户端也一样：``push_pending()`` 一个请求都不发——视图从来不进队列。

    这一条比「待推送是 0」更硬：它钉的是**没有网络调用**（传输层上一条请求都没有）。
    """
    seed(store)
    transport = FakeTransport(json=[])
    engine = SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )
    engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))

    assert asyncio.run(engine.push_pending()) == 0
    assert transport.requests == [], "视图没有服务端那一半：一个请求都不该发"


# ---------------------------------------------------------------- 视图不进配置文件（ADR-0005）


def test_building_and_deleting_a_view_leaves_the_config_file_untouched(tmp_path, store):
    """``config.toml`` 是放 token 的文件：建 / 改 / 删视图一个字节都不写它（ADR-0005）。

    配置文件里那一份是**凭据**，为了改一个过滤条件去动它是不对的；所以视图落在本地库里
    （上面那条重开库的测试），配置文件连读都不用读。
    """
    config_file = tmp_path / "config.toml"
    save_config(Config(token="tok-123"), config_file)
    before = config_file.read_bytes()
    engine = make_engine(store)

    view_id = engine.create_view(ViewDefinition(id="", name="高优先级未完成", priorities=(5,)))
    engine.update_view(replace(engine.view_definition(view_id), name="改过的"))
    engine.delete_view(view_id)

    assert config_file.read_bytes() == before, "配置文件一个字节都不该因为视图而变"


def test_the_config_schema_has_nowhere_to_put_a_view():
    """``Config`` 的字段就是 ``config.toml`` 的键：里面没有、也不许有一个视图段。

    这一条是上面那条的**结构**那一半：真要往配置文件里塞视图，得先给 ``Config`` 加字段，
    那时这里就红了——而不是等某个用户发现自己的 token 文件里多了一堆过滤条件。
    """
    assert [field.name for field in fields(Config) if "view" in field.name.lower()] == []


def test_a_config_file_that_carries_a_views_section_is_refused(tmp_path):
    """手写的配置文件里出现 ``[views]`` 时报错（不认识的键），不静默忽略。"""
    path = tmp_path / "config.toml"
    path.write_text('token = "tok"\n\n[views]\nname = "高优先级"\n', encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)
