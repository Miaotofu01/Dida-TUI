"""日界改了立刻生效（工单 #46）：配置文件 → 引擎 → 屏幕，**一条线**测穿。

工单的验收标准写得很直白——「有测试覆盖『改配置 → 逻辑日变化 → 视图跟着变』」——三个各自通过、
而接线断了的单元测试满足不了它。所以这个文件里三层都有，而且界面那一层走的是**生产真的会走的
那两条触发**（周期泵的 1 秒心跳、``r``），不是直接叫内部方法：

- 配置层：``DayEndReader``——每次都重新读，读不了给 ``None``，**从不写盘**；
- 引擎层：``SyncEngine.set_day_end`` 之后，「今天」视图、逾期判定、顺延一起按新的逻辑日重算；
- 界面层：**接缝一**（真 ``DidaApp`` + ``FakeBackend`` + Textual 的 Pilot）——改临时目录里的
  配置文件，看屏幕上写着哪个逻辑日、哪条任务还在「今天」里、光标还在不在那一行。

「现在」与「配置在哪」都在测试手里（``ManualClock`` + ``tmp_path``）。真实的
``~/.config/dida-tui/config.toml`` 一次都不读——它握着用户的 token。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from dida.config import Config, DayEndReader, load_config, save_config
from dida.storage.store import Store
from dida.sync.engine import GroupKind, SyncEngine
from dida.testing import FakeBackend, FakeTransport, InMemorySource, ManualClock
from dida.tui.app import DidaApp
from dida.tui.pages.base import CURSOR_MARK
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 2, 0)
"""凌晨两点：``24:00`` 下是 03-14，``04:00`` 下还是 03-13——两个日界在这一刻分得最开。"""


def write_day_end(path: Path, day_end: str) -> None:
    """改配置里的边界值：**只换这一个键**，其余原样（用户就是这么改的）。

    读写都走 ``dida.config`` 的公开 API，不手拼 TOML、也不碰真的 ``~/.config``。
    """
    save_config(replace(load_config(path), day_end=day_end), path)


# ------------------------------------------------------------------ 配置层：读手


def test_the_reader_gives_the_boundary_that_is_on_disk(tmp_path):
    """读手给的日界就是文件里写着的那个（规范化过的形式：``24:00`` → ``00:00``）。"""
    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00"), path)

    assert DayEndReader(path).current() == "00:00"


def test_the_reader_re_reads_after_the_file_changes(tmp_path):
    """改完文件再问一次，给的是**新的**那个——这就是「重读」这两个字。"""
    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00"), path)
    reader = DayEndReader(path)
    assert reader.current() == "00:00"

    save_config(Config(day_end="04:00"), path)

    assert reader.current() == "04:00"


def test_a_missing_config_file_is_no_value_and_is_not_created(tmp_path):
    """文件不在了就什么都不给，也**不替用户生成**一份默认配置。

    启动时 ``load_config()`` 有「缺文件就建一份默认的」这个行为（首次运行的信号），但一个
    每秒问一次的读手不能有副作用：用户把配置删了，不该由界面悄悄按默认日界继续跑。
    """
    path = tmp_path / "config.toml"

    assert DayEndReader(path).current() is None
    assert not path.exists(), "读手不写盘"


@pytest.mark.parametrize(
    "content",
    [
        'day_end = "0',  # 手改到一半的 TOML
        'day_end = "banana"',  # 合法 TOML，非法的值
        'day_end = "04:00"\nnope = 1',  # 多了一个不认识的键
    ],
)
def test_a_config_that_cannot_be_read_is_no_value_not_an_exception(tmp_path, content):
    """读不了的配置给 ``None``，不抛：一秒问一次的读手不允许把界面带走（沿用上一次那个日界）。"""
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")

    assert DayEndReader(path).current() is None


def test_a_path_that_is_not_a_readable_file_is_no_value_either(tmp_path):
    """路径上根本不是个文件（目录 / 悬空软链）：同样是「读不了」，同样不出声。"""
    path = tmp_path / "config.toml"
    path.mkdir()

    assert DayEndReader(path).current() is None


# ------------------------------------------------------------------ 引擎层：换了日界就重算


def source_with(*tasks) -> InMemorySource:
    """一份内存缓存：一个清单 + 摆进来的任务。"""
    source = InMemorySource()
    source.add_list("工作")
    for title, due in tasks:
        source.add_task(title, list_name="工作", due=due)
    return source


def test_the_engine_re_derives_the_logical_day_when_the_boundary_changes():
    """换掉日界之后，「今天」与逾期判定按**新的**逻辑日重算（工单 #46 验收标准 2）。

    算例就是工单里那句话：凌晨两点的「昨天 23:00 截止」在 ``24:00`` 下是逾期（读到「昨天
    23:00」），在 ``04:00`` 下属于今天（读到「今天 23:00」）。逾期与今日是同一份分组里的
    两个桶（GLOSSARY：逾期不属于今日），所以那一个字段的变化就是判定跟着变的证据。
    """
    engine = SyncEngine(
        clock=ManualClock(at(14, 2, 0)),
        day_end="24:00",
        source=source_with(("昨晚收尾", at(13, 23, 0))),
    )

    before = engine.view()
    assert engine.status().logical_day == date(2026, 3, 14)
    assert [group.kind for group in before.groups] == [GroupKind.OVERDUE]
    assert before.groups[0].items[0].due_text == "昨天 23:00"

    assert engine.set_day_end("04:00") is True

    after = engine.view()
    assert engine.status().logical_day == date(2026, 3, 13), "当前逻辑日被重新推导"
    assert [group.kind for group in after.groups] == [GroupKind.TODAY]
    assert after.groups[0].items[0].due_text == "今天 23:00"

    assert engine.set_day_end("04:00") is False, "日界没变就不算变过"


def test_the_today_view_is_evaluated_against_the_current_boundary():
    """「今天」是同一份求值的另一种读法（#33 的读形状）：换日界，属于下一个逻辑日的任务掉出去。"""
    source = source_with(("今天上午", at(14, 10, 0)))
    engine = SyncEngine(clock=ManualClock(at(14, 2, 0)), day_end="24:00", source=source)
    (task,) = source.tasks()

    assert [item.task_id for item in engine.tasks_in("today").items] == [task.id]

    engine.set_day_end("04:00")  # 凌晨两点：03-14 10:00 已经是「明天」了

    assert [item.task_id for item in engine.tasks_in("today").items] == []
    assert engine.tasks_in("today").container_id == "today"


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def test_deferring_lands_on_the_next_logical_day_of_the_current_boundary(store):
    """顺延读的是**当前**日界，不是引擎被造出来时那个（工单 #46）。

    落点只改截止时间，所以看库里那条任务的 ``dueDate``。同一个凌晨两点：``24:00`` 下
    「下一个逻辑日」是 03-15，把日界改成 ``04:00`` 之后是 03-14——顺延的落点是「**当前**
    逻辑日的下一个」，不是「原截止 +1 天」，所以它会往回落到用户此刻嘴里的「今天」。
    """
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作", "sortOrder": 1}],
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": "写周报",
                "status": 0,
                "dueDate": "2026-03-14T23:00:00+0800",
            }
        ],
    )
    engine = SyncEngine(clock=ManualClock(at(14, 2, 0)), day_end="24:00", source=store)

    engine.defer("t1")
    assert store.task_payload("t1")["dueDate"] == "2026-03-15T23:00:00+0800"

    engine.set_day_end("04:00")
    engine.defer("t1")

    assert store.task_payload("t1")["dueDate"] == "2026-03-14T23:00:00+0800"


# ------------------------------------------------------------------ 界面层：改配置 → 屏幕跟着变

def stocked(fake: FakeBackend) -> FakeBackend:
    """三条任务摆进一个真实清单（「今天」是算出来的，不是摆出来的）：

    - ``early`` 03-13 09:00、``late`` 03-13 23:00：两个日界下都在「今天」里（逾期那两条）；
    - ``noon`` 03-14 10:00：``24:00`` 下是今天，``04:00`` 下是**明天**，只在一边。
    """
    fake.add_list("工作", id="work")
    fake.add_task("前晚归档", list_name="work", id="early", due=at(13, 9, 0))
    fake.add_task("昨晚收尾", list_name="work", id="late", due=at(13, 23, 0))
    fake.add_task("今天上午", list_name="work", id="noon", due=at(14, 10, 0))
    return fake


def live_app(tmp_path: Path) -> tuple[DidaApp, Path]:
    """接缝一上的 app：临时目录里的配置文件 + 内存缓存 + 手动时钟。

    启动时那个日界与之后的重读读的是**同一个文件**——生产里 bootstrap 就是这么接的
    （配置交给引擎，文件交给读手）。返回 app 与那个文件，好让测试自己动手改它。
    """
    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00"), path)
    reader = DayEndReader(path)
    fake = stocked(FakeBackend(clock=ManualClock(T0), day_end=reader.current()))
    return DidaApp(fake, day_boundary=reader.current), path


def row_of(text: str, name: str) -> str:
    """屏幕上写着 ``name`` 的那一行。"""
    for line in text.splitlines():
        if name in line:
            return line
    raise AssertionError(f"屏幕上没有「{name}」这一行：\n{text}")


async def move_cursor_to(pilot, page, row_id: str) -> None:
    """把某一页的光标走到指定行上（与 ``tests/test_pages.py`` 同一条路：只读公开的口子）。"""
    for _ in range(20):
        await pilot.press("k")
    for _ in range(60):
        if page.selected_id == row_id:
            return
        await pilot.press("j")
    raise AssertionError(f"光标没能走到 {row_id} 上，停在 {page.selected_id}")


async def open_the_today_view(pilot, app) -> None:
    """落在「今天」那一屏上（层二），光标在它的第一条任务上。"""
    await pilot.pause()
    await move_cursor_to(pilot, app.index_page(), "today")
    await pilot.press("enter")
    await pilot.pause()


async def test_editing_the_boundary_in_the_config_file_takes_effect_without_a_restart(tmp_path):
    """验收标准 1 + 2 + 5：改配置 → 逻辑日变 → 「今天」与逾期判定跟着变。

    触发走的是**生产真的会走的那一条**：周期泵那一秒一次的心跳（``bootstrap`` 把间隔交给
    app，泵每秒叫一次 ``push_tick``）。屏幕上三件事一起变：状态栏的逻辑日、逾期那条任务的
    读法（详细页的「截止」那一格：「昨天 23:00」→「今天 23:00」）、以及掉出「今天」的那一条。

    逾期**在列表页上还看不见**（ADR-0007：``TaskItem`` 上还没有 overdue 位，#37 实现、
    #52 接线），所以那条判定从详细页读——它今天就渲染 ``due_text``。
    """
    app, path = live_app(tmp_path)

    async with app.run_test(size=WIDE) as pilot:
        await open_the_today_view(pilot, app)
        await move_cursor_to(pilot, app.tasks_page(), "late")  # 昨晚收尾
        before_tasks = screen_text(app)
        await pilot.press("enter")  # 详细页：截止那一格是逾期判定的读数
        await pilot.pause()
        before_detail = screen_text(app)
        await pilot.press("escape")
        await pilot.pause()

        write_day_end(path, "04:00")
        await app.push_tick()
        await pilot.pause()
        after_tasks = screen_text(app)
        await pilot.press("enter")  # 光标还在同一条上，直接再看一次详细页
        await pilot.pause()
        after_detail = screen_text(app)

    assert "逻辑日 03-14" in before_tasks, "起步：自然日，凌晨两点已经是 03-14"
    assert "昨天 23:00" in before_detail, "读取法：此刻它是逾期的那条"
    assert "今天上午" in before_tasks

    assert "逻辑日 03-13" in after_tasks, "改完配置不重启就生效，逻辑日当场重新推导"
    assert "今天 23:00" in after_detail, "逾期判定跟着变：昨天 23:00 成了今天 23:00"
    assert "昨天 23:00" not in after_detail
    assert "今天上午" not in after_tasks, "属于下一个逻辑日的任务掉出「今天」"
    assert "前晚归档" in after_tasks, "另一个日界下本来就在今天里的任务不受影响"


async def test_the_index_page_counts_are_computed_from_the_new_boundary(tmp_path):
    """层一的「今天」条数也是同一个逻辑日算的（``list_index`` 那条读形状）：3 → 2。

    不跟着变的读形状就是漏了一处：`tasks_in` 对了而索引还写着 3，用户会以为界面在骗他。
    """
    app, path = live_app(tmp_path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = screen_text(app)

        write_day_end(path, "04:00")
        await app.push_tick()
        await pilot.pause()
        after = screen_text(app)

    assert row_of(before, "今天").split()[-1] == "3"
    assert row_of(after, "今天").split()[-1] == "2"


async def test_the_cursor_stays_on_its_task_across_the_recompute(tmp_path):
    """验收标准 3：重算不许把人踢回第一行。

    光标先落在「昨晚收尾」上——它**不是**第一行（「前晚归档」在它前面），而且它在重算前后
    都还在「今天」里，所以这一条断的是「光标认回了那一行」，不是「恰好还停在第一行」。
    """
    app, path = live_app(tmp_path)

    async with app.run_test(size=WIDE) as pilot:
        await open_the_today_view(pilot, app)
        await pilot.press("j")
        assert app.tasks_page().selected_id == "late", "先站到第二条上"

        write_day_end(path, "04:00")
        await app.push_tick()
        await pilot.pause()
        text = screen_text(app)
        on_tasks = app.tasks_page().selected_id
        await pilot.press("escape")
        await pilot.pause()
        on_index = app.index_page().selected_id

    assert on_tasks == "late", "重算之后光标还在那条任务上"
    assert CURSOR_MARK in row_of(text, "昨晚收尾"), "而且屏幕上那一行还是光标行"
    assert on_index == "today", "层一的光标也没被这次重算弄丢"


async def test_r_re_reads_the_config_too(tmp_path):
    """``r`` 是用户嘴里那句「现在再看一眼」，配置也算在内。

    这是兜底那一条：周期泵的间隔是组合根给的策略，**可以不挂**（``push_tick_seconds=None``
    就是构造 app 的默认）。没有它，「日界立刻生效」会悄悄绑死在「重试泵开着」上。
    """
    app, path = live_app(tmp_path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        write_day_end(path, "04:00")

        await pilot.press("r")
        await pilot.pause()
        text = screen_text(app)

    assert "逻辑日 03-13" in text


async def test_a_config_that_cannot_be_read_leaves_the_boundary_alone(tmp_path):
    """读不了的配置：沿用上一次那个日界，不崩、也不悄悄按默认值来（一秒问一次的东西不许炸）。

    改好之后照常生效——「读不了」不等于「坏了要重启」。
    """
    app, path = live_app(tmp_path)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        path.write_text('day_end = "banana"', encoding="utf-8")
        await app.push_tick()
        await pilot.pause()
        broken = screen_text(app)

        save_config(Config(day_end="04:00"), path)
        await app.push_tick()
        await pilot.pause()
        fixed = screen_text(app)

    assert "逻辑日 03-14" in broken, "读不了就沿用 03-14"
    assert "逻辑日 03-13" in fixed, "改好了就照常生效"


async def test_the_composition_root_follows_the_config_file_it_was_given(tmp_path):
    """组合根真的把「哪个文件」接上了：``build_app(config_file=…)`` 之后改那个文件就生效。

    这一条盯的是**接线本身**——启动那次读配置与之后每次重读必须是同一个文件。少了它，
    「三个单元测试各自通过而接线断了」照样是绿的。
    """
    from dida.bootstrap import build_app

    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00", refresh_on_start=False), path)
    app = build_app(
        clock=ManualClock(T0),
        config=load_config(path),
        config_file=path,
        transport=FakeTransport(),
        db_path=tmp_path / "cache.sqlite3",
    )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = screen_text(app)

        write_day_end(path, "04:00")
        await app.push_tick()
        await pilot.pause()
        after = screen_text(app)

    assert "逻辑日 03-14" in before
    assert "逻辑日 03-13" in after


def test_the_command_hands_the_app_the_config_file_it_just_read(monkeypatch, tmp_path):
    """``dida`` 装出来的 app 跟着**它刚读过的那一份**配置走（生产那条路，工单 #46）。

    ``main()`` 里那一行接线（``config_file=config_path()``）没有别的东西守着：忘了它，
    「日界立刻生效」在生产里就是静默关掉的，而其他测试照样全绿。`HOME` 指到 tmp，配置与库
    都落在那里；`DidaApp.run` 被换掉——真的 `run()` 会进 alt-screen 并阻塞到用户按 `q`，
    这里换掉的是框架边界，不是被测的那一段接线。钟按接缝一摆到凌晨两点（`main()` 没有注入
    `Clock` 的口子，所以直接钉 :class:`SystemClock`）。
    """
    from dida.bootstrap import main
    from dida.clock import SystemClock

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(SystemClock, "now", lambda self: T0)
    config_file = tmp_path / ".config" / "dida-tui" / "config.toml"
    save_config(Config(token="tok-探针", day_end="24:00", refresh_on_start=False), config_file)

    composed: list[DidaApp] = []
    monkeypatch.setattr(DidaApp, "run", lambda self: composed.append(self))
    main()
    (app,) = composed

    write_day_end(config_file, "04:00")

    assert app.reload_day_boundary() is True, "改完文件，重读要真的拿到新值"
    assert app.engine.status().logical_day == date(2026, 3, 13), "凌晨两点 + 04:00 是前一天"
    assert app.reload_day_boundary() is False, "没变就不算变过"
