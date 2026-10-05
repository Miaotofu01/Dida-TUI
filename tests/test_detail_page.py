"""任务详细页：字段列表、折行、逐字段编辑、只读段（工单 #43 的验收标准）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot，断的是
外部行为——「我在这一页按了这个键，屏幕上出现了什么」。接缝二是真引擎 + 真库 + 假传输，
用在「一次改动到底有没有立刻出去」上：那是网络这一侧的事实，替身说了不算。

不断言控件树、不断言内部状态对象、不断言渲染出来的空白。读的「里面」只有页面给外层的公开
口子 ``selected_id``（「光标停在哪个字段上」在屏幕上只能从 ``❯`` 那一列间接看出来）。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui import messages, theme
from dida.tui.app import DidaApp
from support import screen_sgr, screen_styled_text, screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
NARROW = (30, 20)
"""spec 有一条「我要在窄终端里也能用」：30 列是下限。"""

T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

CONTENT = "记得附上上周的对比数据"
DESC = "先问一下财务再发"
"""描述 = ``content``、备注 = ``desc``（GLOSSARY）：两个独立字段，改一个不覆盖另一个。"""

TITLE = "交季度报告"

LONG_CONTENT = (
    "这一段是要读完整的那种正文：上周的对比数据、这一周的三个结论、以及下个季度要盯的两件事，"
    "全都得在这一个字段里说完。"
)
"""一段会折好几行的描述：详细页的字段行**不是**一屏一行（工单评论的实现后果）。"""


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘掉 shell 的 ``NO_COLOR``：否则 ``App`` 会挂一层 Monochrome，样式断言全部假绿。"""
    monkeypatch.delenv("NO_COLOR", raising=False)


def backend(**extra: object) -> FakeBackend:
    """一份够用的缓存：一条有全部字段的任务（含只读的那三段）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(
        TITLE,
        list_name="work",
        id="t1",
        due=T0.replace(hour=18, minute=0),
        priority=5,
        content=CONTENT,
        desc=DESC,
        tags=("工作", "季度"),
        raw=extra.get("raw"),  # type: ignore[arg-type]
    )
    return fake


def row_with(text: str, needle: str) -> str:
    """屏幕上写着 ``needle`` 的那一行（没有就是测试写错了）。"""
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{text}")


async def enter_detail(pilot, app: DidaApp) -> None:
    """走进「工作」清单的第一条任务的详细页。"""
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ 字段列表


async def test_enter_opens_the_detail_page_with_the_cursor_on_the_field_list():
    """任务列表页按 ``enter`` 进详细页，光标落在字段列表上（验收标准 1 + 2）。

    七个字段一个不少，而且光标**已经**停在第一个字段上（``j``/``k``/``enter`` 立刻能用）。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)
        cursor_on = app.detail_page().selected_id

    for label in ("标题", "描述", "备注", "清单", "截止", "优先级", "标签"):
        assert label in text, f"字段列表里没有「{label}」：\n{text}"
    assert cursor_on == "title", "光标落在字段列表的第一个字段上"
    assert row_with(text, "标题").lstrip().startswith(theme.CURSOR_MARK), "光标记号在标题那一行"


async def test_描述_shows_content_and_备注_shows_desc():
    """描述 = ``content``、备注 = ``desc``（GLOSSARY，v1 标反了；工单 #43 翻过来）。

    两个字段各画各的：一个字段里的字**不许**出现在另一个字段那一行上。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)

    described = row_with(text, "描述")
    noted = row_with(text, "备注")
    assert CONTENT in described, f"描述那一行画的不是 ``content``：{described!r}"
    assert CONTENT not in noted, f"描述的内容跑到备注那一行上去了：{noted!r}"
    assert DESC in noted, f"备注那一行画的不是 ``desc``：{noted!r}"
    assert DESC not in described, f"备注的内容跑到描述那一行上去了：{described!r}"


async def test_j_and_k_move_the_cursor_field_to_field_over_a_wrapped_block():
    """``j``/``k`` 一次跳**一个字段**，永远不停在折行块内部（验收标准 3）。

    描述长到占好几屏行，而字段只有七个：六下 ``j`` 从标题走到标签，一下都不能落在描述那块
    折行里的第二行上。光标**停在哪一行**在屏幕上只能从 ``❯`` 那一列读出来，所以两头都断：
    页面给外层的口子（``selected_id``）与屏幕上的记号。
    """
    app = DidaApp(backend(content=LONG_CONTENT))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        walked = [app.detail_page().selected_id]
        for _ in range(6):
            await pilot.press("j")
            walked.append(app.detail_page().selected_id)
        bottom = screen_text(app)
        await pilot.press("k", "k")
        back = app.detail_page().selected_id
        text = screen_text(app)

    assert walked == ["title", "content", "desc", "list", "due", "priority", "tags"], (
        f"j 的落点不是「一个字段一行」：{walked}"
    )
    assert back == "due", "k 也要一次退一个字段"
    assert row_with(bottom, "标签").lstrip().startswith(theme.CURSOR_MARK), (
        f"光标走到头时不在标签那一行上：\n{bottom}"
    )
    assert row_with(text, "截止").lstrip().startswith(theme.CURSOR_MARK), (
        f"退回两下之后光标不在截止那一行上：\n{text}"
    )
    assert not row_with(text, "备注").lstrip().startswith(theme.CURSOR_MARK), (
        "光标不该停在折行块（描述/备注）里面"
    )
