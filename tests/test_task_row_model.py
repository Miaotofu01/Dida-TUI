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
    DueWindow,
    Completion,
    SyncEngine,
    TaskItem,
    completed_section,
    format_due,
    next_priority,
    priority_mark,
)
from dida.sync.rows import completed_window_start, row_sort_key
from dida.sync.view import ListSnapshot, TaskSnapshot
from dida.sync.views import ViewDefinition, due_window_of, implied_due_for
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
    due: datetime | None = None,
    priority: int = 0,
) -> TaskSnapshot:
    """缓存里的一条任务快照。"""
    return TaskSnapshot(
        id=id,
        title=title,
        list_id=list_id,
        due=due,
        priority=priority,
        completed=completed,
        completed_at=completed_at,
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
    source.add_view("混的", id="mix", completion=Completion.ANY)

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
    """更早完成的不占屏幕（用户故事：已完成只显示最近 7 天）。

    留下的两条都没有截止时间、优先级也一样，顺序由标题断开（六 U+516D < 刚 U+521A）——
    **不是**完成时刻倒序（工单 #64 把那个倒序拿掉了）。
    """
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

    assert [item.title for item in section.items] == ["六天前做完的", "刚刚做完的"]


def test_the_completed_section_sorts_by_the_normal_key_not_by_completion_time():
    """已完成段按正常排序键排（工单 #64 验收标准 1）：截止时间升序，**不再**按完成时刻倒序。

    甲 是刚做完的那条、乙 更早完成——按完成时刻倒序会排成 甲/乙（旧行为）。乙 的截止
    （03-16）比 甲（03-20）早，按正常排序键应排成 乙/甲。
    """
    section = completed_section(
        [
            snapshot("t1", "甲", completed_at=at(14, 14, 0), due=at(20, 9, 0)),
            snapshot("t2", "乙", completed_at=at(14, 13, 0), due=at(16, 9, 0)),
        ],
        [ListSnapshot(id="work", name="工作")],
        now=T0,
        day_end="24:00",
        window_hours=168,
    )

    assert [item.title for item in section.items] == ["乙", "甲"]


def test_a_completed_task_without_a_due_date_sinks_below_one_that_has_a_due_date():
    """没有截止时间的排在有截止时间的后面（工单 #64 验收标准 1 的那条链）。

    无日期的「A 无日期」完成得更晚、优先级也更高——按完成时刻倒序或按优先级它都会排在最
    前。正常排序键把「有没有日期」摆在优先级之前，所以它沉到「Z 有日期」后面。
    """
    section = completed_section(
        [
            snapshot("t1", "Z 有日期", completed_at=at(14, 10, 0), due=at(20, 9, 0)),
            snapshot("t2", "A 无日期", completed_at=at(14, 15, 0), priority=5),
        ],
        [ListSnapshot(id="work", name="工作")],
        now=T0,
        day_end="24:00",
        window_hours=168,
    )

    assert [item.title for item in section.items] == ["Z 有日期", "A 无日期"]


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
    # 清单那一行由一次全量刷新写进来；这条测试只跑已完成流，所以自己先摆上它——
    # 否则那条任务没有容器行可挂，v2 的读形状按 container_id 取成员（#58 之后只有这一条路）。
    store.apply_refresh(lists=[{"id": "work", "name": "工作", "sortOrder": 1}], tasks=[])
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
    assert [item.title for item in engine.tasks_in("work").completed.items] == ["手机上做完的"]


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


def test_a_row_reads_its_checkbox_the_title_the_due_the_tags_and_the_marks():
    """一行读成：**勾选框**、标题、标签、重复标记、提醒标记、人类可读的截止时间（工单 #63）。

    注解块**贴着行的右边缘**（截止时间因此是一列），所以这一行正好是 ``width`` 格。
    这一条带的是**高优先级**（``!``）：列表行从此只表示「做完没有」，优先级不进这一列。
    """
    item = one_row(
        due=at(14, 18, 0),
        priority=5,
        tags=("工作",),
        repeat_flag="RRULE:FREQ=WEEKLY",
        reminders=("TRIGGER:P0DT9H0M0S",),
    )

    line = task_line(item, width=60)

    assert line.plain.startswith(f"{theme.CHECK_OFF} 写周报"), (
        f"未完成的行首是 {theme.CHECK_OFF}，不再是优先级标记：{line.plain!r}"
    )
    assert "!" not in line.plain, f"列表行里不该再出现高优先级字形：{line.plain!r}"
    assert line.plain.endswith("#工作  ↻ ⚑  今天 18:00")
    assert cell_len(line.plain) == 60, "注解块贴着右边缘，所以这一行正好是 width 格"


def test_the_leading_column_is_a_checkbox_and_says_nothing_about_priority():
    """行首那一列只说「做完没有」：未完成 ``☐``、已完成 ``☑``，三个优先级字形都不出现。

    优先级这个概念本身没动——详细页仍有它的字段、挑得动、排序仍按它（验收标准 3、4）；
    不再显示的只是**列表行**里那一个字形。
    """
    open_row = task_line(one_row(title="高优先级的", priority=5), width=40)
    done_row = completed_line(
        CompletedItem(
            task_id="t1",
            title="做完了的",
            list_name="工作",
            completed_at=at(14, 11, 0),
            completed_text="今天 11:00",
        ),
        width=40,
    )

    assert open_row.plain.startswith(f"{theme.CHECK_OFF} 高优先级的"), open_row.plain
    assert done_row.plain.startswith(f"{theme.CHECK_ON} 做完了的"), done_row.plain
    for glyph in (priority_mark(1), priority_mark(3), priority_mark(5)):
        assert not open_row.plain.startswith(glyph), (
            f"行首那一列还是优先级字形 {glyph!r}：{open_row.plain!r}"
        )


def test_a_plain_task_row_carries_no_marks_at_all():
    """没有标签、不重复、没有提醒的任务，行里一个多余的字形都不出现。"""
    line = task_line(one_row(due=at(14, 18, 0)), width=40)

    assert line.plain.startswith(f"{theme.CHECK_OFF} 写周报")
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

    勾选框是工单 #63 起任务行首那一列的**唯一**内容；三个优先级字形仍然列在这里，因为
    ``TaskItem.priority_mark`` 还是读模型的口子（``test_read_model.py`` 钉着它）——列表行
    不画它了，可它哪天再进某一列，宽度这一条仍然得成立。
    """
    glyphs = {
        "未完成勾选框": theme.TODO_MARK,
        "已完成勾选框": theme.DONE_MARK,
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
    source.add_view("混的", id="mix", completion=Completion.COMPLETED)

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
    source.add_view("我的一天", id="mine")
    engine = engine_with(source)

    assert engine.tasks_in("work").shows_list_name is False, "清单是容器，名字不必重复"
    assert engine.tasks_in("today").shows_list_name is True, "内置视图不是容器"
    assert engine.tasks_in("mine").shows_list_name is True, "自定义视图不是容器"
    assert engine.tasks_in("已经不在的清单").shows_list_name is False


def test_the_read_model_says_which_date_a_view_implies_for_a_new_task():
    """在哪个视图里新建会带上哪一天，也由读模型回答（工单 #39 / 用户故事 35）。

    「今天」隐含当前**逻辑日**那一个日期（03-14 中午、日界 24:00 ⇒ 逻辑日 03-14），写法是
    全天任务的日期标记（当天 00:00）。另外三个容器都不隐含：

    - 「最近七天」是**一段**窗口（七天），挑其中一天是替用户做决定；
    - 「所有」连截止时间都不看；
    - 真实清单根本不是日期视图（它那一屏里什么日期的都有）。

    期望值是测试直接给出的 ``at(14)``，不是照 ``logical_day`` 再算一遍。
    """
    source = work_source()
    engine = engine_with(source)

    assert engine.tasks_in("today").implied_due == at(14), "「今天」隐含的是那个逻辑日"
    assert engine.tasks_in("next7").implied_due is None, "七天是一段窗口，藏不进一个日期"
    assert engine.tasks_in("all").implied_due is None
    assert engine.tasks_in("work").implied_due is None, "清单不隐含日期"


def test_a_custom_view_with_the_same_due_window_implies_the_same_date():
    """隐含日期跟着视图的**定义**走，不跟着它的身份走（#58 的 S1、用户故事 35）。

    内置「今天」与一个用户自建的、截止窗口**一模一样**的视图（表单里就是「今天到期（含
    逾期）」那一档，``DueWindow(first=None, last=0)``）必须给同一个答案：内置与自定义视图
    走的是同一条求值路径，而「这个视图隐不隐含日期」是**定义**的语义、不是「它是不是内置」
    的语义。按 id 在三个内置定义里查的那一版会让自建的拿到 ``None``——同一屏里两种行为，
    用户看不出为什么。

    期望值是测试直接给出的 ``at(14)``，不是照 ``logical_day`` 再算一遍。
    """
    source = work_source()
    source.add_view("今天到期", id="mine", due=DueWindow(first=None, last=0))
    engine = engine_with(source)

    assert engine.tasks_in("today").implied_due == at(14), "内置「今天」隐含那个逻辑日"
    assert engine.tasks_in("mine").implied_due == at(14), (
        "自建视图只要窗口一样，答案就得一样（判断跟着定义走）"
    )
    assert engine.tasks_in("mine").implied_due == engine.tasks_in("today").implied_due, (
        "两种视图的答案必须逐字相同"
    )


@pytest.mark.parametrize(
    ("preset", "implies_today"),
    [
        ("any", False),  # 不限：连截止时间都不看
        ("today", True),  # 今天到期（含逾期）：今天就是它最新的那一天
        ("next7", False),  # 最近七天：今天只是七分之一，挑哪一天都是替用户做决定
        ("overdue", False),  # 已逾期：今天不在窗口里，带上去的新任务当场不在这一屏
        ("undated", False),  # 无日期：这一屏要的正是**没有**日期的任务
    ],
)
def test_every_due_preset_says_what_it_implies_for_a_new_task(preset, implies_today):
    """表单那五档截止条件**逐档**说清隐含什么（#58 的 S1 把这条规则写下来）。

    规则只有一句：**今天在这个窗口里，而且今天就是它最新的那一天**（``last == 0``）才隐含
    一个日期——隐含的是当前逻辑日那一个日期标记（当天 00:00，全天任务那个写法）。所以
    「今天到期（含逾期）」隐含今天，其余四档都不隐含，各自的理由写在上面那张表里。

    这是**纯函数**那一半：定义直接给，不经过引擎、不经过界面。
    """
    definition = ViewDefinition(
        id=f"custom-{preset}", name=f"自定义 {preset}", due=due_window_of(preset)
    )

    implied = implied_due_for(definition, now=T0, day_end="24:00")

    assert implied == (at(14) if implies_today else None)


def test_the_implied_date_follows_the_logical_day_not_the_natural_one():
    """日界 04:00 时，凌晨两点的「今天」是 03-14——隐含日期跟着逻辑日走（#39）。

    读模型给的是那条新任务该带的日期，所以它必须与视图成员用的是同一个逻辑日：写成自然日
    03-15 的话，在「今天」里新建的那条任务当场成了明天的，从这一屏上消失。
    """
    source = work_source()
    engine = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00", source=source)

    assert engine.tasks_in("today").implied_due == at(14), "凌晨两点仍是逻辑日 03-14"


def test_the_implied_date_of_a_custom_view_follows_the_logical_day_too():
    """自建的那一份也一样按逻辑日算（#58 的 S1）：同一句话不许在两种视图上分岔。

    窗口一样只是第一半——「哪一天」也得是同一个逻辑日，所以日界换到 04:00、凌晨两点进来，
    自建视图与内置「今天」仍要给同一天。
    """
    source = work_source()
    source.add_view("今天到期", id="mine", due=DueWindow(first=None, last=0))
    engine = SyncEngine(clock=ManualClock(at(15, 2, 0)), day_end="04:00", source=source)

    assert engine.tasks_in("mine").implied_due == at(14), "凌晨两点仍是逻辑日 03-14"
    assert engine.tasks_in("mine").implied_due == engine.tasks_in("today").implied_due


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
        (47, "☐ 回邮件给产品经理        工作  ↻ ⚑  昨天 18:00"),
        # 40 格：标记也丢了，只剩所属清单与截止时间
        (40, "☐ 回邮件给产品经理      工作  昨天 18:00"),
        # 35 格：截止时间也丢了，所属清单留着（视图里它是验收标准要求必须显示的）
        (35, "☐ 回邮件给产品经理             工作"),
        # 23 格：注解一个都不留，标题**一个字都不少**
        (23, "☐ 回邮件给产品经理"),
        # 16 格：实在放不下才截标题，按格截、带省略号
        (16, "☐ 回邮件给产品 …"),
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

    assert line.plain.startswith(f"{theme.CHECK_OFF} 回邮件给产品经理")
    assert line.plain.endswith("工作  #周报  ↻ ⚑  昨天 18:00")
    assert cell_len(line.plain) == 60


def test_the_title_keeps_its_cells_when_the_row_gets_narrow():
    """窄到只剩标题时，标题拿满它要的格——「一行只放一条任务」的另一半。"""
    line = task_line(crowded_row(), width=23, show_list_name=True)

    assert line.plain == f"{theme.CHECK_OFF} 回邮件给产品经理"
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


# ------------------------------------------------------------------ 重复与提醒：原文进快照


def test_the_store_carries_the_repeat_rule_and_the_reminders_into_the_snapshot(store):
    """重复规则与提醒来自**服务端原文**：快照带出「有没有」，行才画得出那两个标记。

    行里只读「有没有」（非空就是有）；规则与触发器原文整份留在 ``raw`` 里——它们只读，
    回写时一个字都不许动。
    """
    store.apply_refresh(
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": "每周交周报",
                "status": 0,
                "repeatFlag": "RRULE:FREQ=WEEKLY",
                "reminders": ["TRIGGER:P0DT9H0M0S"],
            },
            # 脏数据：reminders 不是数组。一条脏字段不该把整次刷新带崩。
            {"id": "t2", "projectId": "work", "title": "脏的", "status": 0, "reminders": "TRIGGER"},
        ]
    )

    snapshots = {item.id: item for item in store.tasks()}

    assert snapshots["t1"].repeat_flag == "RRULE:FREQ=WEEKLY"
    assert snapshots["t1"].reminders == ("TRIGGER:P0DT9H0M0S",)
    assert store.task_payload("t1")["repeatFlag"] == "RRULE:FREQ=WEEKLY", "原文整份留着"
    assert snapshots["t2"].reminders == ()


def test_an_all_day_task_due_today_stays_today_at_the_0400_boundary():
    """日界 ``04:00``、凌晨两点：**当天到期**的全天任务读作「今天」，不是「昨天」。

    全天任务的截止是日期标记（当天 00:00）：按时刻去套逻辑日偏移，它会被整天挪到前一个
    逻辑日——用户故事 83 的反面。
    """
    assert format_due(at(14, 0, 0), all_day=True, now=at(15, 2, 0), day_end="04:00") == "今天"
    assert format_due(at(14, 23, 0), all_day=False, now=at(15, 2, 0), day_end="04:00") == "今天 23:00"


# ------------------------------------------------- 纯读法：截止时间与优先级标记（#58 搬过来的）
#
# 这一节原本住在 ``tests/test_view_models.py`` 里（v1 的「视图模型」：三个分区、左栏徽标、
# ``/`` 的模糊过滤）。那张票的读路径在 #58 里删掉了，**留下的**是这几条与它无关的纯读法
# ——截止时间怎么读、优先级标记是哪几个字、``p`` 的循环顺序——所以它们搬到这里（这一层正是
# 「行读成什么」的家），那个文件跟着它钉的 v1 形状一起删掉。


def test_timed_due_today_reads_as_today_plus_time():
    assert format_due(at(14, 18, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "今天 18:00"


def test_all_day_due_today_never_shows_a_time():
    assert format_due(at(14), all_day=True, now=at(14, 12, 3), day_end="24:00") == "今天"


def test_timed_due_yesterday_reads_as_yesterday_plus_time():
    assert format_due(at(13, 9, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "昨天 09:00"


def test_due_three_days_ago_reads_as_days_ago():
    assert format_due(at(11, 9, 0), all_day=False, now=at(14, 12, 3), day_end="24:00") == "3 天前"


def test_no_due_date_is_visually_distinct_from_due_today():
    # 没有日期读作一道**宽度无歧义**的短横（工单 #37）：原来的 ``—``（U+2014）是东亚
    # 歧义宽度，进了对齐列就会歪；``今天`` 是字，两者一眼可分。
    assert format_due(None, all_day=False, now=at(14, 12, 3), day_end="24:00") == "-"
    assert format_due(at(14), all_day=True, now=at(14, 12, 3), day_end="24:00") == "今天"


def test_due_before_the_logical_day_end_reads_as_today():
    """逻辑日边界 04:00：凌晨两点看到的昨天 23:00 截止仍是「今天」。"""
    assert format_due(at(14, 23, 0), all_day=False, now=at(15, 2, 0), day_end="04:00") == "今天 23:00"


def test_due_before_the_logical_day_start_reads_as_yesterday():
    assert format_due(at(14, 3, 0), all_day=False, now=at(15, 2, 0), day_end="04:00") == "昨天 03:00"


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (0, 1),  # 无 → 低
        (1, 3),  # 低 → 中
        (3, 5),  # 中 → 高
        (5, 0),  # 高 → 无（循环）
    ],
)
def test_the_priority_cycle_walks_the_wire_values_0_1_3_5(current, expected):
    """优先级循环走的是 ``0/1/3/5``，不是稠密的 1/2/3（api-contracts.md 第 3 条）。

    原本钉在界面测试 ``test_priority_filter.py`` 里（工单 #32 搬出来的）：``p`` 那个**键**
    在 v2 里归 #45 重做，但「线上编码是哪四个值、循环顺序是什么」是 API 的事实，与界面无关。
    """
    assert next_priority(current) == expected


@pytest.mark.parametrize(
    ("wire", "mark"),
    [
        (0, "."),  # 无
        (1, "."),  # 低与无是同一个标记
        (3, "~"),  # 中
        (5, "!"),  # 高
    ],
)
def test_each_priority_wire_value_has_its_one_mark(wire, mark):
    """线上编码 → 标记是一张完整的表，不是只有「高」那一格（工单 #32 搬出来的）。

    低/无的标记从 ``·``（U+00B7）换成 ``.``（U+002E）：前者是东亚**歧义**宽度，rich 量它
    1 格而 zh_CN 的终端可能画 2 格——它站在任务行的第一列，歪的是整行（工单 #37）。
    """
    assert priority_mark(wire) == mark
