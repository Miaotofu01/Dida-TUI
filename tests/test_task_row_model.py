"""任务行的读法（工单 #37）：一行读成什么、整份列表按什么顺序排。

**接缝一**：纯函数直接测（spec 的「纯函数直接测，不经过 UI」）——排序、截止时间读法、
优先级标记、丢弃顺序都在这一层钉死；真按键看屏幕的那一半在
``tests/test_task_rows_page.py``。

这一层不 import ``dida.tui``：行的**读法**归引擎，TUI 只画。
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from rich.cells import cell_len

from dida.config import Config
from dida.storage.store import Store
from dida.sync.completed import DEFAULT_COMPLETED_WINDOW_HOURS
from dida.sync.engine import (
    NO_DUE_TEXT,
    CompletedItem,
    ListKind,
    SyncEngine,
    TaskItem,
    completed_section,
    priority_mark,
)
from dida.sync.rows import completed_window_start, row_sort_key
from dida.sync.view import ListSnapshot, TaskSnapshot
from dida.testing import InMemorySource, ManualClock
from dida.tui import theme
from dida.tui.pages.tasks import completed_line, task_line

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def engine_with(source: InMemorySource) -> SyncEngine:
    """接上真引擎的读路径（与 TUI 拿到的是同一份成品）。"""
    return SyncEngine(clock=ManualClock(T0), day_end="24:00", source=source)


def work_source() -> InMemorySource:
    source = InMemorySource()
    source.add_list("工作", id="work")
    return source


def snapshot(
    id: str,
    title: str,
    *,
    completed_at: datetime | None,
    completed: bool = True,
    list_id: str = "work",
) -> TaskSnapshot:
    """缓存里的一条任务快照。"""
    return TaskSnapshot(
        id=id, title=title, list_id=list_id, completed=completed, completed_at=completed_at
    )


# ------------------------------------------------------------------ 排序


def test_tasks_that_are_due_at_the_same_time_come_out_by_priority():
    """截止时间相同时按优先级从高到低（spec 的排序链，用户故事 89）。

    线上编码是 ``5`` 高 / ``3`` 中 / ``1`` 低 / ``0`` 无——**数字大的排前面**，而
    ``sortOrder`` 那套「数字小的在前」在这里不成立（这正是客户端统一重排的代价）。
    """
    source = work_source()
    source.add_task("低", list_name="work", due=at(14, 18, 0), priority=1)
    source.add_task("无", list_name="work", due=at(14, 18, 0), priority=0)
    source.add_task("高", list_name="work", due=at(14, 18, 0), priority=5)
    source.add_task("中", list_name="work", due=at(14, 18, 0), priority=3)

    items = engine_with(source).tasks_in("work").items

    assert [item.title for item in items] == ["高", "中", "低", "无"]


def test_the_whole_sort_chain_holds_in_one_list():
    """一条链全都在：截止升序 → 优先级降序 → 无日期在后 → 已完成沉底（用户故事 88/89/90）。"""
    source = work_source()
    source.add_task("后天到期", list_name="work", due=at(16, 9, 0))
    source.add_task("今天到期·低", list_name="work", due=at(14, 9, 0), priority=1)
    source.add_task("今天到期·高", list_name="work", due=at(14, 9, 0), priority=5)
    source.add_task("没日期·高", list_name="work", priority=5)
    source.add_task("做完了", list_name="work", due=at(13, 9, 0), completed=True, completed_at=T0)

    task_list = engine_with(source).tasks_in("work")

    assert [item.title for item in task_list.items] == [
        "今天到期·高",
        "今天到期·低",
        "后天到期",
        "没日期·高",
    ]
    assert [row.title for row in task_list.completed.items] == ["做完了"], "已完成的沉在已完成区"


def test_a_completed_task_sinks_even_when_it_is_sorted_with_unfinished_ones():
    """已完成的一律沉到最底——**哪怕它和未完成的排在同一份名单里**（视图的成员就是混的）。

    「已完成沉底」不是「界面把两段拼起来」这一件事的副产品：读模型这一层就得成立，否则
    一个含已完成成员的视图会把做完的任务插在未完成中间。
    """
    source = work_source()
    source.add_task("没做完的", list_name="work", due=at(16, 9, 0))
    source.add_task("做完的", list_name="work", due=at(13, 9, 0), completed=True, completed_at=T0)
    source.add_view("混的", id="mix", task_ids=("t1", "t2"))

    items = engine_with(source).tasks_in("mix").items

    assert [item.title for item in items] == ["没做完的", "做完的"]


def test_the_sort_key_is_a_pure_function_of_the_row():
    """排序键就是那六位，一个不多一个不少（它同时是「无日期怎么比」的答案）。"""

    class Row:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    dated = Row(task_id="t1", title="写周报", due=at(14, 18, 0), priority=5, completed=False)
    undated = Row(task_id="t2", title="买牛奶", due=None, priority=0, completed=False)

    assert row_sort_key(dated)[:4] == (False, False, at(14, 18, 0), -5)
    assert row_sort_key(undated)[:4] == (False, True, None, 0)
    assert row_sort_key(dated) < row_sort_key(undated), "有日期的排前面"


# ------------------------------------------------------------------ 已完成窗口（7 天）


@pytest.fixture
def store(tmp_path):
    """指向临时文件的本地库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


class StubCompletedReader:
    """只记账的已完成流读取口：把每次请求的窗口两端记下来，回放摆好的响应。"""

    def __init__(self, payload: list[dict[str, Any]] | None = None) -> None:
        self.payload = [] if payload is None else payload
        self.windows: list[tuple[Any, Any]] = []

    async def list_completed(self, *, project_ids=None, start_date=None, end_date=None):
        self.windows.append((start_date, end_date))
        return self.payload


def test_the_completed_window_defaults_to_seven_days():
    """配置项的默认值是 168 小时（spec：已完成只显示最近 7 天；原来写死 24）。"""
    assert Config().completed_window_hours == 168


def test_the_two_window_defaults_are_the_same_number():
    """两个默认值是同一个数——它们**必须**同时改，这条守卫就是为「只改了配置那一处」设的。"""
    assert Config().completed_window_hours == DEFAULT_COMPLETED_WINDOW_HOURS == 168


async def test_the_engine_asks_for_seven_days_when_nobody_says_otherwise(store):
    """引擎自己的兜底值也是 7 天——**两处都要改**，只改配置那一处的话，
    ``SyncEngine`` 直接被构造出来（测试、未来的别的组合根）时窗口还是 24 小时。"""
    reader = StubCompletedReader()
    engine = SyncEngine(clock=ManualClock(T0), source=store, client=reader)

    await engine.refresh_completed()

    assert reader.windows == [("2026-03-07T12:03:00+0800", "2026-03-14T12:03:00+0800")]


def test_the_window_cutoff_is_a_pure_function_of_now_and_the_hours():
    """窗口左端 = 现在往回 ``hours`` 小时（纯函数，直接测）。"""
    assert completed_window_start(T0, 168) == at(7, 12, 3)
    assert completed_window_start(T0, 24) == at(13, 12, 3)


def test_the_completed_section_keeps_only_the_last_seven_days():
    """更早完成的不占屏幕（用户故事：已完成只显示最近 7 天）。"""
    section = completed_section(
        [
            snapshot("t1", "六天前做完的", completed_at=T0 - timedelta(days=6)),
            snapshot("t2", "八天前做完的", completed_at=T0 - timedelta(days=8)),
            snapshot("t3", "刚刚做完的", completed_at=T0 - timedelta(minutes=1)),
        ],
        [ListSnapshot(id="work", name="工作")],
        now=T0,
        day_end="24:00",
        window_hours=168,
    )

    assert [item.title for item in section.items] == ["刚刚做完的", "六天前做完的"]


# ------------------------------------------------------------------ 已完成流：按状态过滤


def completed_payload(id: str, title: str, *, status: int, completed_time: str = "2026-03-14T12:05:00+0800") -> dict:
    """一份 ``POST /open/v1/task/completed`` 那样的任务原文。"""
    return {
        "id": id,
        "projectId": "work",
        "title": title,
        "status": status,
        "completedTime": completed_time,
    }


async def test_a_task_that_was_uncompleted_does_not_come_back_in_the_completed_stream(store):
    """取消完成之后 ``completedTime`` **不会被清空**，所以只按时间筛会把它又捞回来。

    服务端只按完成时间窗回话（那个端点没有 ``status`` 参数），「已完成」这一半只能由客户端
    拿**响应里的** ``status`` 筛掉：``2`` 是完成、``0`` 是正常、``-1`` 是已放弃。
    """
    reader = StubCompletedReader(
        [
            completed_payload("t1", "手机上做完的", status=2),
            completed_payload("t2", "取消完成过的", status=0),
            completed_payload("t3", "放弃过的", status=-1),
        ]
    )
    engine = SyncEngine(clock=ManualClock(T0), source=store, client=reader)

    report = await engine.refresh_completed()

    assert report.written_tasks == 1
    assert [snapshot.id for snapshot in store.tasks()] == ["t1"], "取消完成的那条一条都不许落库"
    assert [item.title for item in engine.view().completed.items] == ["手机上做完的"]


async def test_a_payload_without_a_status_is_not_treated_as_completed(store):
    """没有 ``status`` 就不是「已完成」——本地判定一律看状态，缺字段不是「默认完成」。

    宽松处理（缺字段也算完成）会把一条已经取消完成的任务当完成写进本地库：它随后既在
    已完成区里、又不在未完成清单里，用户看到的是「这条任务凭空消失了」。
    """
    reader = StubCompletedReader([completed_payload("t1", "状态缺失的", status=None)])  # type: ignore[arg-type]
    engine = SyncEngine(clock=ManualClock(T0), source=store, client=reader)

    await engine.refresh_completed()

    assert store.tasks() == ()


async def test_the_cap_is_read_from_the_response_not_from_what_survived_filtering(store):
    """满 200 条时不许声称这一窗拿全了——**数的是响应**，不是筛完之后剩下的。

    筛掉的那些也是这一窗里的任务：按筛完的条数判「没满」，游标就会推到 ``now``，而服务端
    已经截断的那一段从此再也不会被拉第二次。
    """
    crowd = [
        completed_payload(f"t{index:03d}", f"第 {index} 条", status=2, completed_time=f"2026-03-14T10:{index % 60:02d}:00+0800")
        for index in range(199)
    ]
    crowd.append(
        completed_payload("tx", "取消完成过的", status=0, completed_time="2026-03-14T11:30:00+0800")
    )
    reader = StubCompletedReader(crowd)
    engine = SyncEngine(clock=ManualClock(T0), source=store, client=reader)

    report = await engine.refresh_completed()

    assert len(crowd) == 200
    assert report.truncated is True, "响应是满的，就不许说这一窗拿全了"
    assert report.written_tasks == 199
    assert store.stored_sync_state().completed_cursor == at(14, 11, 30).isoformat()


def test_a_completed_timestamp_alone_does_not_make_a_task_completed(store):
    """本地判定「已完成」只看 ``status``，不看有没有完成时间戳。

    这条今天就是对的（``Store._snapshot`` 一直按 ``status`` 判）——钉住它是因为 #38 的
    「取消完成」写路径正好走在这条边上：取消完成不清 ``completedTime``，谁把判据换成
    「有没有完成时间」，一条取消完成的任务就会一直显示成做完了。
    """
    store.apply_refresh(
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": "取消完成过的",
                "status": 0,
                "completedTime": "2026-03-14T12:05:00+0800",
            }
        ]
    )

    snapshot = next(item for item in store.tasks() if item.id == "t1")

    assert snapshot.completed is False
    assert snapshot.completed_at == at(14, 12, 5), "完成时刻原样留着：那是服务端的事实，不是判据"


# ------------------------------------------------------------------ 行的读法


def one_row(**fields) -> "TaskItem":
    """一条任务的成品行（走真引擎的读路径，与 TUI 拿到的是同一份）。"""
    source = work_source()
    source.add_task(fields.pop("title", "写周报"), list_name="work", **fields)
    return engine_with(source).tasks_in("work").items[0]


def test_a_row_reads_the_priority_mark_the_title_the_due_the_tags_and_the_marks():
    """一行读成：优先级标记、标题、标签、重复标记、提醒标记、人类可读的截止时间。

    注解块**贴着行的右边缘**（截止时间因此是一列），所以这一行正好是 ``width`` 格。
    """
    item = one_row(
        due=at(14, 18, 0),
        priority=5,
        tags=("工作",),
        repeat_flag="RRULE:FREQ=WEEKLY",
        reminders=("TRIGGER:P0DT9H0M0S",),
    )

    line = task_line(item, width=60)

    assert line.plain.startswith("! 写周报")
    assert line.plain.endswith("#工作  ↻ ⚑  今天 18:00")
    assert cell_len(line.plain) == 60, "注解块贴着右边缘，所以这一行正好是 width 格"


def test_a_plain_task_row_carries_no_marks_at_all():
    """没有标签、不重复、没有提醒的任务，行里一个多余的字形都不出现。"""
    line = task_line(one_row(due=at(14, 18, 0)), width=40)

    assert line.plain.startswith(". 写周报")
    assert line.plain.endswith("今天 18:00")
    assert cell_len(line.plain) == 40
    for absent in ("#", theme.REPEAT_MARK, theme.REMINDER_MARK):
        assert absent not in line.plain


def test_the_marks_are_the_ones_the_theme_owns():
    """重复与提醒的记号来自 :mod:`dida.tui.theme`（视觉常量只有一个出处）。"""
    line = task_line(one_row(due=at(14, 18, 0), repeat_flag="RRULE:FREQ=DAILY", reminders=("TRIGGER:P0DT9H0M0S",)), width=60)

    assert theme.REPEAT_MARK in line.plain
    assert theme.REMINDER_MARK in line.plain


def test_every_mark_that_goes_into_a_column_is_width_unambiguous():
    """进列的字形必须宽度无歧义：rich 量 1 格，而 Unicode 不说它是东亚歧义宽度。

    两处都会错，而且两处都已经发生过：``☰`` 是「rich 量 2 格、Unicode 说中性」（每行宽一
    格），``·``（低优先级）与 ``—``（没有截止时间）是「rich 量 1 格、Unicode 说是歧义宽度」
    （CJK 字体下终端可能画 2 格）。用户 ``LANG=zh_CN.UTF-8``——**一旦有列，歪的就是整列**。
    """
    glyphs = {
        "低优先级标记": priority_mark(1),
        "中优先级标记": priority_mark(3),
        "高优先级标记": priority_mark(5),
        "没有截止时间": NO_DUE_TEXT,
        "重复标记": theme.REPEAT_MARK,
        "提醒标记": theme.REMINDER_MARK,
    }

    for name, glyph in glyphs.items():
        assert len(glyph) == 1, f"{name} {glyph!r} 不是一个字形"
        assert cell_len(glyph) == 1, f"{name} {glyph!r} 被 rich 量成 {cell_len(glyph)} 格"
        width = unicodedata.east_asian_width(glyph)
        assert width not in ("A", "W", "F"), (
            f"{name} {glyph!r} 的东亚宽度是 {width}：rich 量它 1 格，"
            "而 zh_CN 的终端可能画 2 格——整列会歪"
        )


# ------------------------------------------------------------------ 逾期


def test_a_past_due_task_is_overdue_and_a_later_one_is_not():
    """逾期 = 有截止时间、且它属于**早于**当前逻辑日的那一天（用户故事 25 的那一位）。"""
    source = work_source()
    source.add_task("昨天就该做的", list_name="work", due=at(13, 9, 0))
    source.add_task("今天要做的", list_name="work", due=at(14, 18, 0))
    source.add_task("明天要做的", list_name="work", due=at(15, 9, 0))
    source.add_task("没日期的", list_name="work")

    items = engine_with(source).tasks_in("work").items

    assert {item.title: item.overdue for item in items} == {
        "昨天就该做的": True,
        "今天要做的": False,
        "明天要做的": False,
        "没日期的": False,
    }


def test_overdue_follows_the_logical_day_not_the_wall_clock():
    """日界配成 ``04:00`` 时，凌晨两点看到的昨天 23:00 截止**不算逾期**——它是今天的事。

    用户故事 83：夜猫子的「今天」还没过完。
    """
    source = work_source()
    source.add_task("昨晚就该做的", list_name="work", due=at(14, 23, 0))

    night_owl = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00", source=source)
    plain = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="24:00", source=source)

    assert night_owl.tasks_in("work").items[0].overdue is False
    assert plain.tasks_in("work").items[0].overdue is True, "同一个时刻，日界不同判定就不同"


def test_a_completed_task_is_never_overdue():
    """已经做完的不算逾期：它该有的样子是划掉沉底，不是标红（用户故事 25 只管未完成的）。"""
    source = work_source()
    source.add_task("昨天做完的", list_name="work", due=at(13, 9, 0), completed=True, completed_at=T0)
    source.add_view("混的", id="mix", task_ids=("t1",))

    items = engine_with(source).tasks_in("mix").items

    assert [item.overdue for item in items] == [False]


def test_an_all_day_task_due_on_the_current_logical_day_is_overdue_only_after_it():
    """全天任务的截止是**日期标记**：当天 00:00 不该在当天一开始就被判成逾期。

    按时刻算（00:00 早于「现在」）会把今天到期的全天任务整天标红——那是「今天要做」的
    那一条，不是逾期的。
    """
    source = work_source()
    source.add_task("今天全天的", list_name="work", due=at(14, 0, 0), all_day=True)

    item = engine_with(source).tasks_in("work").items[0]

    assert item.due_text == "今天"
    assert item.overdue is False


# ------------------------------------------------------------------ 清单名：清单里不重复，视图里必须显示


def test_the_read_model_says_whether_the_container_is_a_list_or_a_view():
    """「这一行要不要写清单名」由读模型回答：清单是容器，视图不是（工单 #37 的验收标准）。"""
    source = work_source()
    source.add_task("写周报", list_name="work", due=at(14, 18, 0))
    source.add_view("我的一天", id="mine", task_ids=("t1",))
    engine = engine_with(source)

    assert engine.tasks_in("work").container_kind is ListKind.LIST
    assert engine.tasks_in("today").container_kind is ListKind.BUILTIN
    assert engine.tasks_in("mine").container_kind is ListKind.CUSTOM
    assert engine.tasks_in("已经不在的清单").container_kind is None


# ------------------------------------------------------------------ 窄终端：丢弃顺序


def crowded_row(**fields) -> TaskItem:
    """一条注解齐全的任务：标题、所属清单、标签、重复 + 提醒标记、截止时间都在。"""
    source = work_source()
    source.add_task(
        fields.pop("title", "回邮件给产品经理"),
        list_name="work",
        due=fields.pop("due", at(13, 18, 0)),
        tags=("周报",),
        repeat_flag="RRULE:FREQ=WEEKLY",
        reminders=("TRIGGER:P0DT9H0M0S",),
        **fields,
    )
    return engine_with(source).tasks_in("work").items[0]


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        # 47 格：标签放不下了（先丢它），其余都在
        (47, ". 回邮件给产品经理        工作  ↻ ⚑  昨天 18:00"),
        # 40 格：标记也丢了，只剩所属清单与截止时间
        (40, ". 回邮件给产品经理      工作  昨天 18:00"),
        # 35 格：截止时间也丢了，所属清单留着（视图里它是验收标准要求必须显示的）
        (35, ". 回邮件给产品经理             工作"),
        # 23 格：注解一个都不留，标题**一个字都不少**
        (23, ". 回邮件给产品经理"),
        # 16 格：实在放不下才截标题，按格截、带省略号
        (16, ". 回邮件给产品 …"),
    ],
)
def test_the_discard_order_is_tags_then_marks_then_due_then_the_title(width, expected):
    """宽度不够时按用户定的顺序丢：**标签 → 重复/提醒标记 → 截止时间 → 最后才截标题**。

    标题是内容，其余是注解：终端只有 30 列时，用户最需要知道的是「这条是什么」，不是
    「它什么时候到期」。所属清单名排在截止时间后面丢——视图里它是必须显示的那一样。
    """
    line = task_line(crowded_row(), width=width, show_list_name=True)

    assert line.plain == expected
    assert cell_len(line.plain) <= width, "一行不许超出它拿到的格数"


def test_a_row_that_fits_puts_its_annotations_against_the_right_edge():
    """放得下时注解块贴着右边缘：截止时间因此落在每一行的同一个位置上（一列）。"""
    line = task_line(crowded_row(), width=60, show_list_name=True)

    assert line.plain.startswith(". 回邮件给产品经理")
    assert line.plain.endswith("工作  #周报  ↻ ⚑  昨天 18:00")
    assert cell_len(line.plain) == 60


def test_the_title_keeps_its_cells_when_the_row_gets_narrow():
    """窄到只剩标题时，标题拿满它要的格——「一行只放一条任务」的另一半。"""
    line = task_line(crowded_row(), width=23, show_list_name=True)

    assert line.plain == ". 回邮件给产品经理"
    assert theme.ELLIPSIS not in line.plain


def test_a_completed_row_keeps_its_title_and_drops_its_time_when_narrow():
    """已完成的行与未完成的同一条规矩：宽度不够先丢注解（完成时刻），标题留着。"""
    item = CompletedItem(
        task_id="t1",
        title="回邮件给产品经理",
        list_name="工作",
        completed_at=at(14, 11, 0),
        completed_text="今天 11:00",
    )

    assert completed_line(item, width=24).plain == "☑ 回邮件给产品经理"
    assert completed_line(item, width=40).plain.endswith("今天 11:00")
