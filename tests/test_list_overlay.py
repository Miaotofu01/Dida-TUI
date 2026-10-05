"""清单的建 / 改 / 删在界面上的样子（工单 #42）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot。断的是
外部行为：「我在清单列表页上按了这个键，屏幕上 / 假后端那里看见了什么」。

那张表单是**共用的壳子**（#42 与 #36 共用，谁先落地谁搭）：字段是参数，这一页按选中行的
类型给一组字段。所以这里除了三条键位流程，还有一条**直接拿另一组字段**开那张浮层的测试——
它钉的就是「壳子不认识清单」。
"""

from __future__ import annotations

import unicodedata
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
    什么」，只能按字段名找到它自己那几行。两层的边框（浮层自己的 ``|`` 与输入框那圈
    ``+--+``）都要剥掉——它们不是内容。
    """
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if label in line), None)
    if index is None:
        raise AssertionError(f"屏幕上没有「{label}」这个字段：\n{text}")
    for following in lines[index + 1 :]:
        content = following.strip(" |")
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
        await pilot.press("enter")  # #36：``n`` 先问「清单还是视图」，默认那一档是清单
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
        await pilot.press("enter")  # 先答「清单还是视图」
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
        await pilot.press("enter")  # 先答「清单还是视图」
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
        await pilot.press("enter")  # 先答「清单还是视图」
        await pilot.pause()
        await pilot.press("enter")  # 名字那一格是空的
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


# ------------------------------------------------------------------ e：改名字与颜色


async def test_e_opens_the_form_prefilled_and_the_row_takes_the_new_name():
    """``e`` 的表单填着**当前的**名字与颜色，改完清单列表页上立刻是新名字（验收标准 2）。

    打开时那一格的值是**全选**的（Textual ``Input`` 的 ``select_on_focus``），所以直接打字
    就是换掉整个名字——像文件管理器里的重命名。要接着改就先把光标挪进去。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "work")
        await pilot.press("e")
        await pilot.pause()
        prefilled = field_value(screen_text(app), "名字")

        await pilot.press(*"renamed")  # 全选着，直接打就是整个换掉
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert prefilled == "工作", "改的是这一行，不是新建"
    assert fake.created_lists == [], "``e`` 不新建"
    assert fake.updated_lists == [("work", "renamed", None)], "没挑颜色就不动颜色"
    assert LIST_MARK in row_of(after, "renamed")
    assert "工作" not in after, "改完的清单列表页上是新名字，不是旧的"


async def test_the_colour_of_a_list_stays_when_only_the_name_changes():
    """清单上那个颜色不在客户端这一档里时，也要照原样留着并原样交回去。

    手机端挑的颜色客户端不认识（文档只有 ``#F18181`` 一个样例）；改个名字顺手把它换成
    别的颜色，与「改名把清单顺序重置成 0」是同一类静默破坏。
    """
    fake = backend()
    fake.add_list("海外", id="abroad", color="#123456")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "abroad")
        await pilot.press("e")
        await pilot.pause()
        shown = field_value(screen_text(app), "颜色")
        await pilot.press("enter")
        await pilot.pause()

    assert shown == "< #123456 >", "认不出来的颜色自成一档，原样显示"
    assert fake.updated_lists == [("abroad", "海外", "#123456")], "原样回写"


# ------------------------------------------------------------------ d：删清单


async def test_d_asks_once_with_the_truth_and_only_y_deletes():
    """``d`` 一次 ``y/n`` 确认；文案如实说文档没写、且不承诺恢复（验收标准 3、4）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "work")
        await pilot.press("d")
        await pilot.pause()
        asked = screen_text(app)

        await pilot.press("n")
        await pilot.pause()
        after_cancel = screen_text(app)

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        after_yes = screen_text(app)

    assert fake.deleted_lists == ["work"], "只有 y 那一次真的删了"

    assert "删除清单「工作」" in asked, "问的是哪一条，写在最上面"
    assert "里面的任务会怎样" in asked and "文档没写" in asked, (
        "删掉一个清单时里面的任务会怎样，文档一个字都没写——确认文案必须如实说「不知道」"
    )
    assert "找不回来" in asked and "回收站" in asked, "不承诺任何恢复手段"
    assert LIST_MARK in row_of(after_cancel, "工作"), "``n`` 之后清单还在"
    assert "工作" not in after_yes, "``y`` 之后那一行没了"


# ------------------------------------------------------------------ 改不动的那几种行


async def test_the_inbox_cannot_be_renamed_or_deleted_from_here():
    """收集箱那一行是客户端补的默认落点：``e`` / ``d`` 都说清原因，不写任何东西。

    改名与删除都**不**给表单、也**不**问那一句——问一句就等于「有可能删」，而它不会。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        after_edit = screen_text(app)

        await pilot.press("d")
        await pilot.pause()
        after_delete = screen_text(app)

    assert messages.INBOX_LIST_MESSAGE in after_edit
    assert "名字" not in after_edit, "没开表单"
    assert messages.INBOX_LIST_MESSAGE in after_delete
    assert "文档没写" not in after_delete, "没问那一句"
    assert fake.updated_lists == [] and fake.deleted_lists == []


async def test_a_list_without_write_permission_says_why_it_cannot_be_changed():
    """``permission`` 不是 ``write`` 的清单改不动，说清是这一种（用户故事 24）。"""
    fake = backend()
    fake.add_list("别人的清单", id="shared", permission="read")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "shared")
        await pilot.press("e")
        await pilot.pause()
        after = screen_text(app)
        await pilot.press("d")
        await pilot.pause()

    assert "改不动" in after and "写权限" in after
    assert fake.updated_lists == [] and fake.deleted_lists == []


async def test_a_view_row_is_not_deleted_through_the_list_path():
    """视图行不归清单那三条（#36 起视图有自己的建 / 改 / 删）：``d`` 问的是**视图**那一句。

    #42 划的边界在这里换了个方向：它当初拒绝所有视图行（那时视图还没有写路径），现在
    自建视图归 #36，而清单那条删除路径**一个字都不写**——``deleted_lists`` 是空的，
    文案也不是清单那一套（清单那句说的是「里面的任务会怎样文档没写」，对视图不成立）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "mine")
        await pilot.press("d")
        await pilot.pause()
        after = screen_text(app)

    assert "删除视图「我的一天」" in after, "问的是视图那一句"
    assert "文档没写" not in after, "视图不是清单，不套用清单那套确认文案"
    assert fake.deleted_lists == [] and fake.deleted_views == [], "还没答 y，谁都不许删"


# ------------------------------------------------------------------ 表单开着时键盘归谁


async def test_the_form_keeps_the_keyboard_while_it_is_open():
    """表单开着时 ``q`` 是**打字**，不是退出；出口是 ``Esc``——焦点在输入框上也一样。

    #47 实测：浮层的键位解析**截断在最后一个浮层控件上**，app 的绑定在浮层开着时够不着。
    所以这几条必须在**浮层真的挂着、输入框真的拿到焦点**时按（下面的 ``app.focused``
    断言就是把这件事钉住）。``q`` 必须是一个字母（清单可以叫 ``quizzes``），而 ``Input``
    的绑定里没有 ``escape``，所以 Esc 照样到达浮层。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")  # 先答「清单还是视图」
        await pilot.pause()
        focused = app.focused
        await pilot.press("q")
        await pilot.pause()
        typed = field_value(screen_text(app), "名字")
        still_open = "新建清单" in screen_text(app)
        await pilot.press("escape")
        await pilot.pause()
        back = screen_text(app)

    assert focused is not None and focused.id == "field-name", "输入框拿到焦点，才谈得上「谁吃键」"
    assert typed == "q", "q 打进名字那一格——没有变成退出"
    assert still_open, "``q`` 没有把 app 带走"
    assert "工作" in row_of(back, "工作"), "Esc 是出口：回到清单列表页"


async def test_ctrl_c_still_quits_from_the_form_in_every_state():
    """表单开着时 ``Ctrl+C`` **照旧退出**，三种状态都一样（写下来的决定，见浮层文档）。

    Textual 把 ``ctrl+c`` 绑给 ``screen.copy_text``，而它在绑定链里更靠前：**没选中东西时
    它 SkipAction**，有选中就复制——不压过它，同一个键就会看「用户有没有选中文字」行事
    （#47 的 merger 实测过这条链）。所以这里**三种状态都按一遍**：

    1. 输入框空着、没有选中（按 ``n`` 刚打开）；
    2. 选择框上有焦点（没有文本框参与）；
    3. 输入框里**有选中**（按 ``e`` 打开时那一格是全选的，``select_on_focus``）。

    有待推送改动时退出先拦一句，所以「屏幕上出现了那句确认」就是「它真的走到了 app 的退出」
    ——不是在断言绑定表。
    """
    fake = backend()
    fake.set_sync_state(pending_count=1)
    app = DidaApp(fake)
    asked: list[str] = []

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press("n")  # 1. 空输入框，没有选中
        await pilot.pause()
        await pilot.press("enter")  # 先答「清单还是视图」（#36），才进得了清单那张表单
        await pilot.pause()
        empty = app.focused
        await pilot.press("ctrl+c")
        await pilot.pause()
        asked.append(screen_text(app))

        await pilot.press("n")  # 取消退出，回到表单；2. 挪到选择框上
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        on_choice = app.focused
        await pilot.press("ctrl+c")
        await pilot.pause()
        asked.append(screen_text(app))

        await pilot.press("n")
        await pilot.pause()
        await pilot.press("escape")  # 关掉表单
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "work")
        await pilot.press("e")  # 3. 输入框里那整个名字是选中的
        await pilot.pause()
        selected = app.focused
        selection = getattr(selected, "selection", None)
        await pilot.press("ctrl+c")
        await pilot.pause()
        asked.append(screen_text(app))

    assert empty is not None and empty.id == "field-name", "1. 空输入框"
    assert on_choice is not None and on_choice.id == "field-color", "2. 选择框"
    assert selection is not None and selection.start != selection.end, (
        "3. 这一格真的有选中的文字（select_on_focus），才谈得上「复制还是退出」"
    )
    assert all("仍然退出" in text for text in asked), "三种状态下 Ctrl+C 都是退出"
    assert all(messages.quit_prompt(1).splitlines()[0] in text for text in asked)


# ------------------------------------------------------------------ 表单上的字也得宽度老实


def test_the_form_text_uses_no_ambiguous_width_glyphs():
    """表单上那几行字里不许出现东亚**歧义**宽度的字形（#48 在帮助正文上立的同一条规矩）。

    ``·``（分隔符）、``←``/``→``（方向键）都是 rich 量 1 格、CJK 字体下终端可能画 2 格的
    字形：落进一行要对齐或要撑出一块宽度的地方，整块布局就歪。表单里没有对齐列，但同一条
    规矩在这里一样成立——底部那行提示与字段名就是这块浮层的宽度来源。
    """
    from dida.tui.overlays import FORM_HINT
    from dida.tui.pages.index import DEFAULT_COLOR_OPTION, list_form_fields

    texts = [
        FORM_HINT,
        *(field.label for field in list_form_fields()),
        DEFAULT_COLOR_OPTION.label,
        *(color.label for color in LIST_COLORS),
    ]
    # 这条守卫自己也有人守：它拦的正是 ``·`` 与方向箭头这种字形（前提变了要重新决定）。
    assert all(unicodedata.east_asian_width(char) == "A" for char in "·←→"), (
        "这几个字形不再是歧义宽度了——守卫的前提变了，重新决定还要不要拦"
    )
    offenders = [
        (text, char)
        for text in texts
        for char in text
        if unicodedata.east_asian_width(char) == "A"
    ]
    assert offenders == [], f"表单上出现了歧义宽度的字形：{offenders}"
