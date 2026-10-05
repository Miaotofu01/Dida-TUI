"""清单的建 / 改 / 删在界面上的样子（工单 #42）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot。断的是
外部行为：「我在清单列表页上按了这个键，屏幕上 / 假后端那里看见了什么」。

那张表单是**共用的壳子**（#42 与 #36 共用，谁先落地谁搭）：字段是参数，这一页按选中行的
类型给一组字段。所以这里除了三条键位流程，还有一条**直接拿另一组字段**开那张浮层的测试——
它钉的就是「壳子不认识清单」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.sync.engine import LIST_COLORS
from dida.testing import FakeBackend, ManualClock
from dida.tui import messages
from dida.tui.app import DidaApp
from dida.tui.overlays import FormField, FormOption, FormOverlay
from dida.tui.pages.index import INBOX_MARK, LIST_MARK
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def backend() -> FakeBackend:
    """一份够用的缓存：两个清单、一个自建视图、收集箱（客户端自己补的那一行）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_list("生活", id="life")
    fake.add_view("我的一天", id="mine")
    return fake


def field_value(text: str, label: str) -> str:
    """表单里 ``label`` 那个字段的**控件**上写着什么。

    弹层是画在下面那一层**上面**的，所以屏幕文本里两层的字都在；要断「输入框里现在是
    什么」，只能按字段名找到它自己那几行（边框与边框线不算内容）。
    """
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if label in line), None)
    if index is None:
        raise AssertionError(f"屏幕上没有「{label}」这个字段：\n{text}")
    for following in lines[index + 1 :]:
        content = following.strip().strip("|").strip()
        if not content or set(content) <= set("+-"):
            continue
        return content
    raise AssertionError(f"「{label}」这个字段下面什么都没有：\n{text}")


def row_of(text: str, name: str) -> str:
    """屏幕上写着 ``name`` 的那一行（清单行）。"""
    for line in text.splitlines():
        if name in line and (LIST_MARK in line or INBOX_MARK in line):
            return line
    raise AssertionError(f"屏幕上没有「{name}」这一行：\n{text}")


async def move_cursor_to(pilot, page, row_id: str) -> None:
    """把光标走到指定行上：先一直往上（夹在顶上），再一路往下找。"""
    for _ in range(20):
        await pilot.press("k")
    for _ in range(60):
        if page.selected_id == row_id:
            return
        await pilot.press("j")
    raise AssertionError(f"光标没能走到 {row_id} 上，停在 {page.selected_id}")


# ------------------------------------------------------------------ n：建清单


async def test_n_asks_for_a_name_and_a_colour_then_the_new_list_is_on_the_page():
    """``n`` 开表单（名字 + 颜色），填完清单就出现在清单列表页上（验收标准 1、7）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        form = screen_text(app)

        await pilot.press(*"shopping")
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert "名字" in form and "颜色" in form, "浮层里要有名字与颜色两个字段"
    assert fake.created_lists == [("shopping", None)], "不挑颜色就是不发 color 字段"
    assert LIST_MARK in row_of(after, "shopping"), "建完立刻出现在清单列表页上"


async def test_the_colour_can_be_picked_with_the_arrow_keys():
    """颜色是同一张表单里的另一格：``tab`` 过去、``←/→`` 挑（验收标准 1）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"book")
        before = field_value(screen_text(app), "颜色")
        await pilot.press("tab")
        await pilot.press("right")
        await pilot.pause()
        picked = field_value(screen_text(app), "颜色")
        await pilot.press("enter")
        await pilot.pause()

    assert "默认" in before, "颜色那一格的起点是「默认」（不挑颜色）"
    assert picked == f"< {LIST_COLORS[0].label} >", "值表现在屏幕上"
    assert fake.created_lists == [("book", LIST_COLORS[0].value)]


async def test_escape_cancels_the_form_without_writing_anything():
    """``esc`` 关掉表单：一个字节都不写（验收标准 5 的同一条口径）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"shopping")
        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_lists == [], "取消就是取消"
    assert "shopping" not in after
    assert "工作" in row_of(after, "工作"), "浮层关掉，回到下面那一层"


async def test_a_list_without_a_name_is_not_created():
    """名字空着就什么都不建，并且如实说一句（空态不许静默）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_lists == []
    assert messages.EMPTY_LIST_NAME_MESSAGE in after


# ------------------------------------------------------------------ 浮层是共用的壳子


async def test_the_form_shell_takes_whatever_fields_the_row_needs():
    """同一张浮层换一组字段照开（#36 的接缝）：会话里没有一处假设字段是「名字 + 颜色」。

    这里给的是**视图条件**那样的一组字段（一个多选式的选择、一个文本框），壳子照样
    收下来、照样按字段名把值交回去。
    """
    app = DidaApp(backend())
    handed_back: list[dict[str, str] | None] = []

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.push_screen(
            FormOverlay(
                title="视图条件",
                fields=(
                    FormField(
                        name="scope",
                        label="清单范围",
                        options=(FormOption("work", "工作"), FormOption("life", "生活")),
                        value="life",
                    ),
                    FormField(name="keyword", label="关键词"),
                ),
            ),
            handed_back.append,
        )
        await pilot.pause()
        rendered = screen_text(app)
        await pilot.press("tab")  # 第一个字段是选择框，第二个才是文本框
        await pilot.press(*"ab")
        await pilot.press("enter")
        await pilot.pause()

    assert "清单范围" in rendered and "关键词" in rendered
    assert field_value(rendered, "清单范围") == "< 生活 >", "字段自带的当前值要显示出来"
    assert handed_back == [{"scope": "life", "keyword": "ab"}]
