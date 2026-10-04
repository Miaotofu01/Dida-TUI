"""优先级 ``p`` 与模糊过滤 ``/``（t17）。

三个接缝，与仓库里其它工单一样：

- **纯函数**（没有接缝，直接测）：``next_priority`` / ``fuzzy_match`` / ``filter_groups``。
- **引擎的公开入口**：``cycle_priority()`` 接真的 ``Store`` 与真的 ``DidaApiClient``，
  网络钉在**接缝二**（``FakeTransport``）上——断言的是「请求体里的 ``priority`` 是什么」。
- **接缝一**：``DidaApp`` + ``FakeBackend``，用 Textual 的 ``Pilot`` 按键驱动——断言的是
  「屏幕文本」与「假后端收到的调用」。

钉死的规矩（工单 #17 + api-contracts.md 第 3 条）：

- ``p`` 的循环是 无 → 低 → 中 → 高，走 **API 的线上编码** ``0 → 1 → 3 → 5``；
  稠密的 1/2/3 是解析器的**档位序号**，不是要发给服务端的东西；
- 优先级变化走 ``write()``：本地当场生效、立即推送、推不动进重试队列；
- ``/`` 模糊过滤当前列表、``Esc`` 清空并恢复完整列表；
- 过滤期间光标与右栏**绝不**指向被过滤掉的任务。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import (
    GroupKind,
    SyncEngine,
    TaskItem,
    TaskSnapshot,
    filter_groups,
    fuzzy_match,
    group_tasks,
    next_priority,
)
from dida.sync.view import ListSnapshot
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import DetailPane, FilterInput, TaskPane
from support import screen_text

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 0)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（这一组测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作", "sortOrder": 1}], tasks=list(tasks)
    )


def make_engine(
    store: Store,
    *,
    clock: ManualClock | None = None,
    transport: FakeTransport | None = None,
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端（网络钉在接缝二上）。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end="24:00",
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def item_of(engine: SyncEngine, task_id: str) -> TaskItem:
    """引擎的视图里那一条任务行（用户与渲染看到的同一份）。"""
    return next(item for group in engine.view().groups for item in group.items if item.task_id == task_id)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (0, 1),  # 无 → 低
        (1, 3),  # 低 → 中
        (3, 5),  # 中 → 高
        (5, 0),  # 高 → 无（循环）
    ],
)
def test_p_walks_the_wire_values_0_1_3_5(current, expected):
    """``p`` 发出去的是 ``0/1/3/5``，不是稠密的 1/2/3（api-contracts.md 第 3 条）。"""
    assert next_priority(current) == expected


async def test_p_pushes_the_wire_value_and_marks_it_with_the_existing_marks(store):
    """验收标准 #1 + #5：循环走线上编码，标记沿用既有的 ``!`` / ``~`` / ``·``。

    「当场生效」与「推给服务端」是两件事：本地那份快照在 ``cycle_priority`` 返回时就已经
    是新的（ADR-0002 的乐观写），请求体随后才发出去——两者都钉在这里。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+0800"))
    transport = FakeTransport(json=task(id="t1"))
    engine = make_engine(store, transport=transport)

    for wire, mark in ((1, "·"), (3, "~"), (5, "!"), (0, "·")):
        engine.cycle_priority("t1")

        assert item_of(engine, "t1").priority == wire, "本地当场生效，不等网络"
        assert item_of(engine, "t1").priority_mark == mark, "标记沿用 view.py 那一份映射"

        await engine.wait_for_pushes()
        assert store.pending() == (), "推成功就该出队"
        assert transport.last_json["priority"] == wire, "请求体里是线上编码"


async def test_a_failed_push_keeps_the_priority_change_in_the_retry_queue(store):
    """验收标准 #2：优先级变化立即推送；推不动就留在重试队列里（ADR-0002 的豁免）。"""
    seed(store, task(id="t1", title="写周报"))
    transport = FakeTransport()
    transport.enqueue(NetworkError("连不上"))
    engine = make_engine(store, transport=transport)

    engine.cycle_priority("t1")
    await engine.wait_for_pushes()

    assert len(transport.requests) == 1
    queued = store.pending()
    assert [change.kind for change in queued] == [ChangeKind.UPDATE]
    assert queued[0].payload == {"priority": 1}, "本地那次改动照旧生效，不被撤销"
    assert item_of(engine, "t1").priority_mark == "·", "低与无是同一个标记"
    assert engine.status().pending_count == 1


# ------------------------------------------------------------------ 接缝一：p 键


def make_backend() -> FakeBackend:
    """一屏有代表性的缓存：今日三条（含一条没有截止时间的）。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    backend.add_task("交季度报告", list_name="工作", due=at(14, 9, 0), priority=5)
    backend.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    backend.add_task("买牛奶", list_name="生活")
    return backend


async def test_p_cycles_the_priority_of_the_task_under_the_cursor():
    """验收标准 #1：``p`` 推的是**光标下**那一条，不是第一条。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert backend.cycled == ["t1"], "光标在第一条上"

        await pilot.press("j")
        await pilot.press("p")
        await pilot.pause()
        assert backend.cycled == ["t1", "t2"], "j 之后推的是第二条"


async def test_p_on_an_empty_screen_does_nothing():
    """空屏上按键不该报错（与 ``x`` / ``g`` 同一条口径）。"""
    backend = FakeBackend(clock=ManualClock(T0))
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()

    assert backend.cycled == []


async def test_p_redraws_the_row_with_the_existing_mark_and_pushes_the_wire_values(store):
    """接缝一 + **真引擎**：按 ``p`` 之后屏上那一行的标记真的换了，请求体里是线上编码。

    ``FakeBackend`` 只记录调用、不动缓存，所以「标记跟着换」这一半得用真引擎 + 真存储来钉
    ——这是验收标准 #1 与 #5 在用户眼里的样子（``!`` / ``~`` / ``·`` 沿用既有的那一套）。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+0800"))
    transport = FakeTransport(json=task(id="t1"))
    app = DidaApp(make_engine(store, transport=transport))

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert "❯ · 写周报" in screen_text(app), "一开始是「无」"

        await pilot.press("p")
        await pilot.pause()
        assert "❯ · 写周报" in screen_text(app), "低与无共用同一个标记"

        await pilot.press("p")
        await pilot.pause()
        assert "❯ ~ 写周报" in screen_text(app), "低 → 中"

        await pilot.press("p")
        await pilot.pause()
        assert "❯ ! 写周报" in screen_text(app), "中 → 高"

        await app.engine.wait_for_pushes()

    assert [json.loads(request.content)["priority"] for request in transport.requests] == [1, 3, 5]


# ------------------------------------------------------------------ 纯函数：模糊过滤


@pytest.mark.parametrize(
    ("query", "title", "expected"),
    [
        ("", "写周报", True),  # 空查询 = 一行都不筛掉
        ("周", "写周报", True),
        ("写报", "写周报", True),  # 子序列：中间隔着字也算命中
        ("报写", "写周报", False),  # 顺序不对就不算
        ("周报x", "写周报", False),  # 少一个字就不算
        ("wr", "Write Report", True),  # 大小写不敏感
    ],
)
def test_fuzzy_match_is_an_ordered_subsequence(query, title, expected):
    assert fuzzy_match(query, title) is expected


def test_filter_groups_keeps_the_matching_rows_and_drops_the_empty_groups():
    """过滤后的行与标题上的条数必须一致：标题写「3 项」而屏上只有一行，就是在骗人。"""
    tasks = (
        TaskSnapshot(id="t1", title="交季度报告", list_id="work", due=at(14, 9, 0)),
        TaskSnapshot(id="t2", title="写周报", list_id="work", due=at(14, 18, 0)),
        TaskSnapshot(id="t3", title="买牛奶", list_id="home"),
    )
    groups = group_tasks(
        tasks, [ListSnapshot(id="work", name="工作")], now=T0, day_end="24:00"
    )

    filtered = filter_groups(groups, "周报")

    assert [(group.kind, group.count, [item.task_id for item in group.items]) for group in filtered] == [
        (GroupKind.TODAY, 1, ["t2"])
    ]


def test_filter_groups_with_no_match_returns_nothing_at_all():
    """一条都没命中：分区一个都不留（屏上因此是空状态，而不是空标题）。"""
    tasks = (TaskSnapshot(id="t1", title="写周报", list_id="work", due=at(14, 18, 0)),)
    groups = group_tasks(tasks, [], now=T0, day_end="24:00")

    assert filter_groups(groups, "不存在的任务") == ()


# ------------------------------------------------------------------ 接缝一：/ 过滤

FILTER_PLACEHOLDER = "过滤：打几个字"
"""过滤框的占位文案：它出现在屏幕上 = 过滤框开着。"""


async def type_text(pilot, text: str) -> None:
    """逐字把一段话打进当前焦点（Pilot 只认单个字符或键名，中文也一样）。"""
    for char in text:
        await pilot.press("space" if char == " " else char)
    await pilot.pause()


async def test_slash_filters_the_list_live_and_esc_restores_the_whole_list():
    """验收标准 #3：``/`` 边打边筛，``Esc`` 清空并恢复完整列表。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert FILTER_PLACEHOLDER not in screen_text(app), "没按 / 之前不该有过滤框"

        await pilot.press("/")
        await pilot.pause()
        assert FILTER_PLACEHOLDER in screen_text(app), "按 / 该打开过滤框"
        assert app.query_one(FilterInput).query == "", "「/」是开过滤框的键，不许漏进框里"

        await type_text(pilot, "周报")
        shown = screen_text(app)
        assert "写周报" in shown
        assert "交季度报告" not in shown, "没命中的任务要从屏上消失"
        assert "买牛奶" not in shown
        assert "── 今日 · 1 项" in shown, "标题上的条数跟着过滤走"

        await pilot.press("escape")
        await pilot.pause()
        shown = screen_text(app)
        assert FILTER_PLACEHOLDER not in shown, "Esc 收起过滤框"
        assert "── 今日 · 3 项" in shown, "Esc 恢复完整列表"
        assert "交季度报告" in shown and "买牛奶" in shown
        assert isinstance(app.focused, TaskPane), "焦点回到任务列，j/k 立刻能用"


async def test_the_cursor_and_the_detail_pane_never_point_at_a_filtered_out_task():
    """验收标准 #4：过滤期间光标与右栏都只能指着**还在屏上**的那一条。

    这是最容易漏掉的一条：只把行从画面上藏起来、却不动光标底下那份行表，光标就停在一个
    用户看不见的任务上——接着按 ``x`` / ``g`` / ``p`` / ``e`` 动的全是它。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await type_text(pilot, "牛奶")

        pane = app.query_one(TaskPane)
        detail = app.query_one(DetailPane)
        shown = screen_text(app)

        assert pane.selected_task_id == "t3", "光标落在过滤后的第一行（也就是唯一那一行）"
        assert detail.task_id == "t3", "右栏跟着光标走，不许还指着上一条"
        assert "买牛奶" in shown
        assert "写周报" not in shown and "交季度报告" not in shown

        # 换一个过滤词：命中的是另一条，光标与右栏都要跟着换过去
        await pilot.press("backspace", "backspace")
        await type_text(pilot, "周报")

        assert pane.selected_task_id == "t2"
        assert detail.task_id == "t2", "右栏跟着新的过滤结果走"
        assert "交季度报告" not in screen_text(app)


async def test_the_detail_pane_follows_the_cursor():
    """右栏指着光标下那一条：这是过滤期间「不许指错」的机制本身（t18 也靠它）。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert app.query_one(DetailPane).task_id == "t1"

        await pilot.press("j")
        await pilot.pause()
        assert app.query_one(DetailPane).task_id == "t2"

        await pilot.press("k")
        await pilot.pause()
        assert app.query_one(DetailPane).task_id == "t1"


async def test_a_filter_that_matches_nothing_says_so():
    """一条都没命中时不许说「今天没有未完成的任务」——屏上明明有任务，那是在骗人。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await type_text(pilot, "zzz")
        shown = screen_text(app)

        assert "（今天没有未完成的任务）" not in shown
        assert "没有匹配" in shown
        assert app.query_one(TaskPane).selected_task_id is None
        assert app.query_one(DetailPane).task_id is None, "一条都没有，右栏也不许还指着谁"

        await pilot.press("escape")
        await pilot.pause()
        assert "买牛奶" in screen_text(app), "Esc 之后完整列表回来"
