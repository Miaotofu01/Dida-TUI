"""app 这一层的那几个键与状态栏：``?`` / ``o`` / ``r`` / ``q`` 与状态栏那一行。

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot。断的是「按了这个键，屏幕上/假后端
那里看得见什么」，不读控件树、不读内部状态。

状态栏那一条是个例外里的例外：用户故事 100 要的是**非零时高亮**，而「高亮了没有」在屏幕
文本上只剩样式这一条线索。所以这里断的是「哪一段文字带了样式」（与 ``test_complete`` /
``test_overdue_row`` 同一个手法：不写死颜色，只说这一段与别人不一样），外加一条纯函数
上的对账——措辞一个字都没改。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from dida.sync.engine import SyncStatus
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp, format_status, status_line
from dida.tui.escape import URL_TEMPLATE
from dida.tui.keys import LAYER_INDEX
from dida.tui.messages import delete_prompt
from support import screen_styled_text, screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
SGR = re.compile(r"\x1b\[[0-9;]*m")
"""ANSI 的 SGR 序列：样式在屏幕文本里就长这样。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def backend(*, pending: int = 0, refreshed_at: datetime | None = T0) -> FakeBackend:
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("买牛奶", list_name="work", id="t2")
    fake.set_sync_state(last_refresh_at=refreshed_at, pending_count=pending)
    return fake


def status_of(text: str) -> str:
    """屏幕上那一行状态栏。"""
    for line in text.splitlines():
        if "待推送" in line:
            return line.strip()
    raise AssertionError(f"屏幕上没有状态栏：\n{text}")


def styled_status_of(app: DidaApp) -> str:
    """状态栏那一行的**带样式**原文。"""
    for line in screen_styled_text(app).splitlines():
        if "待推送" in SGR.sub("", line):
            return line
    raise AssertionError("屏幕上没有状态栏")


# ------------------------------------------------------------------ 状态栏


def test_the_wording_still_says_sync_time_pending_count_and_logical_day():
    """措辞一个字都没改（GLOSSARY：已同步 / 待推送 / 逻辑日），改的只是非零时高亮。"""
    status = SyncStatus(
        checked_at=T0, pending_count=3, last_refresh_at=T0, logical_day=date(2026, 3, 14)
    )

    assert format_status(status) == "已同步 12:03 · 待推送 3 · 逻辑日 03-14"
    assert status_line(status).plain == format_status(status), "两份是同一句话（一处措辞）"


def test_only_the_pending_count_carries_a_style_and_only_when_it_is_not_zero():
    """待推送非零时高亮那一段；归零之后一点样式都不留（用户故事 100）。"""
    busy = status_line(
        SyncStatus(checked_at=T0, pending_count=2, last_refresh_at=T0, logical_day=date(2026, 3, 14))
    )
    idle = status_line(
        SyncStatus(checked_at=T0, pending_count=0, last_refresh_at=T0, logical_day=date(2026, 3, 14))
    )

    assert {busy.plain[span.start : span.end] for span in busy.spans if span.style} == {"待推送 2"}
    assert [span for span in idle.spans if span.style] == [], "归零了就不该再高亮"


async def test_the_status_bar_is_on_screen_when_there_is_nothing_pending():
    app = DidaApp(backend(pending=0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)
        styled = styled_status_of(app)

    assert status_of(text) == "已同步 12:03 · 待推送 0 · 逻辑日 03-14"
    assert style_of(styled, "待推送 0") == style_of(styled, "已同步"), (
        "没有待推送改动时，这一段与旁边一样，不该有自己的样式"
    )


async def test_the_status_bar_highlights_the_pending_count_when_it_is_not_zero():
    app = DidaApp(backend(pending=2))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        styled = styled_status_of(app)

    assert "待推送 2" in SGR.sub("", styled), "那个数还是照常写出来"
    pending = style_of(styled, "待推送 2")
    assert pending, "这个数得有自己的样式——非零时它就是该被看见的那个信号"
    assert pending != style_of(styled, "已同步"), "它必须与旁边那一段不一样"


def runs_of(line: str) -> list[tuple[str, str]]:
    """把带样式的一行拆成 ``(样式, 文字)`` 一段段——屏幕文本就是这么一段段来的。

    ``\x1b[0m`` 是「回到默认」，所以它之后的段样式是空的。颜色系统（真彩 / 16 色）、
    主题都会影响那串数字，所以下面只比「这一段与旁边一样不一样」。
    """
    out: list[tuple[str, str]] = []
    style = ""
    pos = 0
    for match in SGR.finditer(line):
        chunk = line[pos : match.start()]
        if chunk:
            out.append((style, chunk))
        style = "" if match.group() == "\x1b[0m" else match.group()
        pos = match.end()
    if line[pos:]:
        out.append((style, line[pos:]))
    return out


def style_of(line: str, text: str) -> str:
    """屏幕上 ``text`` 那一段用的样式（原样，不去解读它是哪个颜色）。"""
    for style, chunk in runs_of(line):
        if text in chunk:
            return style
    raise AssertionError(f"这一行里没有「{text}」：{line!r}")


# ------------------------------------------------------------------ ?：分层帮助


async def test_the_help_lists_the_keys_of_the_layer_you_are_on():
    """``?`` 列的是**当前这一层**的键（验收标准、用户故事 119）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        on_index = screen_text(app)

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("j")  # 收集箱 → 工作
        await pilot.press("enter")  # 进任务列表页
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        on_tasks = screen_text(app)

        await pilot.press("escape")
        await pilot.pause()
        back = screen_text(app)

    assert "进入这一行" in on_index, "清单列表页的 enter 说的是「进入这一行」"
    assert "退回清单列表页" not in on_index, "清单列表页上没有「退回清单」这件事"

    assert "任务详细页" in on_tasks, "任务列表页的 enter 说的是「任务详细页」"
    assert "退回清单列表页" in on_tasks
    assert "进入这一行" not in on_tasks, "任务列表页不该列清单列表页的键"

    assert "写周报" in back, "esc 关掉帮助，回到下面那一层（不是在层之间跳）"


# ------------------------------------------------------------------ o：浏览器逃生舱


async def test_o_hands_the_url_of_the_task_under_the_cursor_to_the_browser():
    """``o`` 在浏览器里打开当前任务（验收标准 13、用户故事 120）。"""
    opened: list[str] = []
    app = DidaApp(backend(), open_url=lambda url: (opened.append(url), True)[1])

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("j")  # 收集箱 → 工作
        await pilot.press("enter")  # 进「工作」
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    assert opened == [URL_TEMPLATE.format(project_id="work", task_id="t1")]


async def test_o_on_the_index_page_does_nothing_and_does_not_raise():
    """清单列表页上说的不是哪一条任务：``o`` 什么都不做（空屏上按键不该报错）。"""
    opened: list[str] = []
    app = DidaApp(backend(), open_url=lambda url: (opened.append(url), True)[1])

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.layer == LAYER_INDEX
        await pilot.press("o")
        await pilot.pause()

    assert opened == []


# ------------------------------------------------------------------ r：手动同步


async def test_r_asks_the_engine_for_all_three_things():
    """``r`` 在引擎**接口**上就是三件事：刷新 + 推一轮 + 拉已完成流（用户故事 53 + 50）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert (fake.refreshes, fake.pushes, fake.completed_pulls) == (1, 1, 1)


# ------------------------------------------------------------------ q：退出拦截


async def test_q_is_interrupted_once_and_says_how_many_are_pending():
    """待推送改动还在时 ``q`` 被拦一下，并且说清有几处（用户故事 101）。

    一次拦截 = 一次浮层：它必须说出那个数，并且给一个「仍然退出」的出口。``n`` / ``Esc``
    回到原样——取消必须一点痕迹都不留。
    """
    app = DidaApp(backend(pending=3))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        interrupted = screen_text(app)
        assert app.is_running, "还有没推上去的改动时，q 不该直接退"
        assert "3 处" in interrupted, "要说清有几处没推上去"
        assert "仍然退出" in interrupted, "浮层里要给出「仍然退出」这条路"

        await pilot.press("n")
        await pilot.pause()
        assert app.is_running, "取消不是退出"
        assert status_of(screen_text(app)) == "已同步 12:03 · 待推送 3 · 逻辑日 03-14"

        await pilot.press("q")
        await pilot.pause()
        assert "3 处" in screen_text(app), "再按一次还是拦"

        await pilot.press("y")
        await pilot.pause()
        assert app.is_running is False, "确认了才真的退"


async def test_q_quits_straight_away_when_nothing_is_pending():
    """没有待推送改动就照旧直接退（``q`` 即结束，spec 的单进程规矩）。"""
    app = DidaApp(backend(pending=0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

    assert app.is_running is False


async def test_the_quit_prompt_does_not_claim_the_queue_is_lost():
    """那句话不许说「就丢了」：待推送改动落在本地库里，下次启动接着补推。

    屏幕上的话必须与实现一致——``tests/test_sync_push.py`` 里那条
    ``test_a_restart_still_has_the_queue_and_pushes_it`` 就是这句话的事实依据。
    """
    app = DidaApp(backend(pending=1))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        prompt = screen_text(app)

    assert "1 处" in prompt
    assert "下次打开" in prompt, "要说清它们去哪儿了：留在本地，下次接着补推"
    assert "丢了" not in prompt, "它们没丢——本地库里有，下次启动会接着推"


# ------------------------------------------------------------------ 逃生舱的失败面（工单 #19）


async def test_a_browser_that_says_no_reports_the_url_instead_of_failing_silently():
    """开不了浏览器时必须出声，并把 URL 原样给人抄（逃生舱不能静默失败）。

    完成在服务端不可逆，``o`` 是它的补偿：按下去什么都没发生，比吵一句坏得多。
    """
    app = DidaApp(backend(), open_url=lambda url: False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        text = screen_text(app)

    assert "打不开浏览器" in text
    assert URL_TEMPLATE.format(project_id="work", task_id="t1") in text, "URL 原样给人抄"


async def test_a_browser_that_raises_is_reported_instead_of_taking_the_app_down():
    """开手当场抛（``webbrowser.Error``）也不许把整个界面带走。"""
    def boom(url: str) -> bool:
        raise RuntimeError("没有浏览器")

    app = DidaApp(backend(), open_url=boom)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        text = screen_text(app)
        still_up = app.is_running

    assert still_up, "界面还在"
    assert "打不开浏览器" in text


# ------------------------------------------------------------------ 确认文案的结论（#40 会用它）


def test_the_delete_prompt_never_implies_the_task_can_be_recovered():
    """删除确认的文案**不许**暗示还能找回来（服务端没有 undelete、没有回收站）。

    v1 有两条测试从渲染出来的屏幕上断这句话（``test_delete.py``，随三栏界面一起作废）；
    结论搬到这里：文案本身不承诺恢复手段，#40 接删除时直接用这一份。
    """
    prompt = delete_prompt("写周报")

    assert "写周报" in prompt, "要点名删的是哪一条"
    assert "找不回来" in prompt and "没有回收站" in prompt
    for promise in ("可恢复", "能恢复", "稍后可", "已移入回收站", "撤销"):
        assert promise not in prompt, f"这句话不该出现：{promise}"
