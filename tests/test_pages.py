"""三层页面的进出与画法（工单 #34 的验收标准）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径），用 Textual 的
Pilot 真的按键、真的看下一屏。断的是外部行为：「我在这一层按了这个键，下一层显示了这个」。

不断言控件树、不断言内部状态对象、不断言渲染出来的空白与颜色。唯一读的「里面」是页面
自己的 ``selected_id``——它是页面给外层的公开口子（``o`` 要知道当前是哪条任务），而
「光标停在哪一行」在屏幕上只能靠反色与行首记号间接看出来。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.messages import EMPTY_TASKS_MESSAGE, blocked_list_message
from dida.tui.pages.index import BLOCKED_MARK, BUILTIN_MARK, CUSTOM_MARK, INBOX_MARK, LIST_MARK
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)

TITLES = {
    "t1": "写周报",
    "t2": "交水费",
    "t3": "季度报告",
    "t4": "买牛奶",
    "t5": "复习 Rust 所有权",
}
"""任务 id → 标题：光标「还在原来那条上」只能从屏幕上读回来，这里给一份对照。"""


def backend() -> FakeBackend:
    """一份够用的缓存：三种行、项目组、进不去的清单、一个空清单、几条任务。

    - 收集箱那一行是**客户端补的**（服务端的清单索引里没有它），里面两条任务；
    - 两个真实清单同属项目组 ``g1``（服务端只说 ``groupId``，没有组名）；
    - ``kind`` 是 NOTE 的与没有写权限的各一个，都进不去；
    - 视图两个：内置的「今天」按日期算成员，自建的「我的一天」按求值结果。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_task("写周报", list_name="inbox1", id="t1", due=at(14, 18, 0))
    fake.add_task("交水费", list_name="inbox1", id="t2")
    fake.add_list("工作", id="work", group_id="g1")
    fake.add_task("季度报告", list_name="work", id="t3", due=at(14, 20, 0))
    fake.add_list("生活", id="life", group_id="g1")
    fake.add_task("买牛奶", list_name="life", id="t4")
    fake.add_list("学习", id="study")
    fake.add_task("复习 Rust 所有权", list_name="study", id="t5")
    fake.add_list("笔记本", id="note", kind="NOTE")
    fake.add_list("别人的清单", id="shared", permission="read")
    fake.add_list("空清单", id="empty")
    fake.add_view("我的一天", id="mine", task_ids=("t1",))
    return fake


def row_of(text: str, name: str) -> str:
    """屏幕上写着 ``name`` 的那一行。"""
    for line in text.splitlines():
        if name in line:
            return line
    raise AssertionError(f"屏幕上没有「{name}」这一行：\n{text}")


def index_rows(text: str) -> list[str]:
    """清单索引页上的那些行（收集箱与清单名出现在同一批行里）。"""
    return [line for line in text.splitlines() if any(mark in line for mark in (INBOX_MARK, LIST_MARK))]


# ------------------------------------------------------------------ 层一：启动与三种行


async def test_the_app_lands_on_the_list_index_with_the_inbox_on_top():
    """敲 ``dida`` 落在清单列表页，收集箱在最上面（验收标准 1、用户故事 8 + 11）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

    rows = index_rows(text)
    assert "收集箱" in rows[0], "收集箱要置顶——它是随手记的默认落点"
    assert INBOX_MARK in rows[0], "收集箱有自己的记号，不靠颜色"


async def test_the_three_row_kinds_are_told_apart_by_prefix_characters():
    """内置视图 / 自建视图 / 真实清单各一个前缀字符，不靠颜色单独承担（验收标准 2）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert BUILTIN_MARK in row_of(text, "今天")
    assert BUILTIN_MARK in row_of(text, "最近七天")
    assert CUSTOM_MARK in row_of(text, "我的一天")
    assert LIST_MARK in row_of(text, "工作")
    assert len({BUILTIN_MARK, CUSTOM_MARK, LIST_MARK, INBOX_MARK}) == 4, "记号本身要互不相同"


async def test_every_row_shows_its_unfinished_count():
    """每行显示未完成条数（验收标准 4、用户故事 13）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert re.search(r"收集箱\D+2\b", row_of(text, "收集箱")), "收集箱里两条未完成"
    assert re.search(r"学习\D+1\b", row_of(text, "学习")), "学习 里一条未完成"
    assert re.search(r"空清单\D+0\b", row_of(text, "空清单")), "空清单也要如实写 0"


async def test_real_lists_are_gathered_under_their_project_group_heading():
    """真实清单按项目组归拢，项目组是**不可进入**的小标题（验收标准 3、用户故事 12）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

        assert "项目组 g1" in text, "项目组要显示成小标题"
        assert "项目组 g1" in row_of(text, "项目组 g1"), "小标题自己占一行"
        assert not row_of(text, "项目组 g1").lstrip().startswith("❯"), "开头光标不在小标题上"

        # 光标走遍这一页：它一次都不该停在小标题上（小标题不是一行可进入的东西）。
        seen: list[str | None] = []
        for _ in range(12):
            await pilot.press("j")
            seen.append(app.index_page().selected_id)
        assert None not in seen, "光标停到了不可停的行上（项目组小标题）"


async def test_a_note_list_and_a_read_only_list_are_marked_and_cannot_be_entered():
    """``kind`` 是 NOTE 或没有写权限的清单有标记且进不去（验收标准 5、用户故事 23 + 24）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

        assert BLOCKED_MARK in row_of(text, "笔记本")
        assert BLOCKED_MARK in row_of(text, "别人的清单")
        assert BLOCKED_MARK not in row_of(text, "工作"), "能进的清单不该被标记"

        # 走过去按 enter：进不去，而且说清是哪一种原因。
        for _ in range(20):
            if app.index_page().selected_id == "note":
                break
            await pilot.press("j")
        assert app.index_page().selected_id == "note", "光标没能走到那条 NOTE 清单上"

        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)

    assert blocked_list_message(type("R", (), {"name": "笔记本", "project_kind": "NOTE"})) in text
    assert "收集箱" in text, "还在清单列表页上——进不去就是没进去"


async def test_the_cursor_moves_with_j_k_and_the_arrow_keys():
    """``j``/``k`` 与方向键移动光标（验收标准 6、用户故事 9）。

    断法完全走外部行为：光标移到哪一行，``enter`` 进去看到的就是哪一个容器。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press("j")  # 收集箱 → 今天
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)
        assert "写周报" in text, "进的是「今天」这个视图：今天到期的任务在里面"
        assert "交水费" not in text, "没有截止时间的不算「今天」"

        await pilot.press("escape")
        await pilot.press("down")  # 最近七天
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)

    assert "写周报" in text and "季度报告" in text, "方向键也能移动光标"


async def test_a_long_index_scrolls_instead_of_hiding_the_rest():
    """清单很多时能滚动（验收标准 6、用户故事 22）。"""
    fake = backend()
    for index in range(30):
        fake.add_list(f"清单{index:02d}", id=f"list{index:02d}")
    app = DidaApp(fake)

    async with app.run_test(size=(100, 12)) as pilot:
        await pilot.pause()
        for _ in range(50):  # 一路按到底：光标走到最后一行就夹在那里
            await pilot.press("j")
        await pilot.pause()
        text = screen_text(app)

    assert "清单29" in text, "光标走到最后一行时，那一行要滚进可见区"


# ------------------------------------------------------------------ 层二：进出与光标


async def test_enter_opens_the_container_and_esc_comes_back_to_the_row_you_came_from():
    """``enter`` 进任务列表页；``esc`` 退回，光标**还原到进来的那一行**（验收标准 7 + 8）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        for _ in range(20):  # 走到「学习」（第 3 个真实清单，norm 不是第一行）
            if app.index_page().selected_id == "study":
                break
            await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        inside = screen_text(app)
        assert "复习 Rust 所有权" in inside, "进的是「学习」"

        await pilot.press("escape")
        await pilot.pause()
        back = screen_text(app)
        assert "收集箱" in back, "回到了清单列表页"

        await pilot.press("enter")  # 再进去一次
        await pilot.pause()
        again = screen_text(app)

    assert "复习 Rust 所有权" in again, "光标还原到进来的那一行，不是被踢回第一行"


async def test_an_empty_list_says_so_instead_of_showing_a_blank_screen():
    """空清单显示明确的空态文案（验收标准 9、用户故事 53）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(30):
            if app.index_page().selected_id == "empty":
                break
            await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)

    assert EMPTY_TASKS_MESSAGE in text


async def test_enter_on_a_task_opens_the_detail_page_and_esc_comes_back():
    """``enter`` 进任务详细页，``esc`` 退回任务列表页（spec 的三层状态机）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(30):
            if app.index_page().selected_id == "study":
                break
            await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("enter")  # 光标下那条任务 → 详细页
        await pilot.pause()
        detail = screen_text(app)
        assert "复习 Rust 所有权" in detail, "详细页说的是光标那条任务"
        assert "学习" in detail, "详细页里有它属于哪个清单"

        await pilot.press("escape")
        await pilot.pause()
        back = screen_text(app)

    assert "复习 Rust 所有权" in back
    assert "学习" in back, "退回了任务列表页（标题里写着容器名）"


async def test_a_background_refresh_keeps_the_cursor_on_both_layers():
    """后台全量刷新不许把光标踢回第一行——两层都要（验收标准 10、用户故事 21 + 57）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

        await pilot.press("j", "j")  # 收集箱 → 今天 → 最近七天
        assert app.index_page().selected_id == "next7"

        fake.add_task("刷新带回来的", list_name="work", id="t9", due=at(14, 21, 0))
        app.refresh_view()  # 后台刷新落地（缓存变了，行的条数也跟着变）
        assert app.index_page().selected_id == "next7", "层一的光标不许跳"

        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("j")  # 光标移到第二条任务上
        before = app.tasks_page().selected_id
        assert before is not None

        # 再来一次刷新，而且这次**排到光标前面**：新任务截止得更早，排到最前面。
        fake.add_task("更早的一条", list_name="work", id="t10", due=at(14, 1, 0))
        app.refresh_view()
        assert app.tasks_page().selected_id == before, "层二的光标不许跳到别的任务上"
        assert before != "t10", "新来的那条不许把光标抢走"

        await pilot.press("enter")
        await pilot.pause()
        detail = screen_text(app)

    assert TITLES[before] in detail, "光标还在原来那条任务上（详细页说的是它）"
