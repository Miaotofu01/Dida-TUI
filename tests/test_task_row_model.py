"""任务行的读法（工单 #37）：一行读成什么、整份列表按什么顺序排。

**接缝一**：纯函数直接测（spec 的「纯函数直接测，不经过 UI」）——排序、截止时间读法、
优先级标记、丢弃顺序都在这一层钉死；真按键看屏幕的那一半在
``tests/test_task_rows_page.py``。

这一层不 import ``dida.tui``：行的**读法**归引擎，TUI 只画。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from dida.config import Config
from dida.storage.store import Store
from dida.sync.completed import DEFAULT_COMPLETED_WINDOW_HOURS
from dida.sync.engine import SyncEngine, completed_section
from dida.sync.rows import completed_window_start, row_sort_key
from dida.sync.view import ListSnapshot, TaskSnapshot
from dida.testing import InMemorySource, ManualClock

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
