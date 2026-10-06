"""新建任务（工单 #39）：任务列表页上按 ``n``、只填标题、落点对。

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot。断的是外部行为——「我在这一屏上按了
这个键、打了这几个字、按了回车，假后端那里多出来的是哪一条、它落在哪个清单里」。

落点有两条规矩（工单 #39 / 用户故事 35、44、45）：

- 在**清单**里建，落在**当前打开的这个清单**里（不是收集箱）；
- 在**视图**里建，落在**收集箱**（视图不是容器）；视图若隐含了日期（「今天」），新任务
  自动带上那个日期。

假后端**必须**支持按清单新建（验收标准 8）：它原来把 ``list_name=收集箱`` 写死，于是
「在 工作 里建，落在 工作」这条测试会静默断言成收集箱。这一屏的断言因此落在
``FakeBackend.created_tasks`` 上——那是「这条任务落在哪儿」的**唯一**记录处。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from dida.sync.engine import INBOX_ID, UnclaimedListError, due_window_of
from dida.testing import FakeBackend, ManualClock
from dida.tui import messages
from dida.tui.app import DidaApp
from support import screen_sgr, screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)
"""默认的「现在」：03-14 中午，逻辑日就是 03-14。"""


def backend() -> FakeBackend:
    """一份够用的缓存：两个真实清单 + 收集箱那一行 + 三个内置视图。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("收集箱", id=INBOX_ID, is_inbox=True)
    fake.add_list("工作", id="work")
    fake.add_list("生活", id="life")
    return fake


def placed(fake: FakeBackend, list_id: str) -> list[tuple[str, datetime | None, bool]]:
    """假后端记下的、落在 ``list_id`` 这个清单里的新建（标题、截止、是否全天）。"""
    return [
        (task.title, task.due, task.all_day)
        for task in fake.created_tasks
        if task.list_id == list_id
    ]


async def open_container(pilot, page, row_id: str) -> None:
    """把光标走到 ``row_id`` 那一行并进去（先一直往上，再一路往下找）。"""
    for _ in range(20):
        await pilot.press("k")
    for _ in range(60):
        if page.selected_id == row_id:
            break
        await pilot.press("j")
    else:
        raise AssertionError(f"光标没能走到 {row_id} 上，停在 {page.selected_id}")
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ n：只填标题


async def test_n_asks_for_a_title_and_nothing_else():
    """``n`` 弹出的是**只填标题**的输入框：不问截止时间，也不问优先级（用户故事 44）。

    顺带钉住「这块浮层排得下」：字段只有一格，所以底部那行提示（``Esc`` 是出口）不会被裁掉
    ——#36 实测过七格的视图表单在 30 行终端上要 30 行、而浮层只给 24 行，底部那几个字段与
    提示会被整个裁掉。这一票的表单永远只有一格，这条断言就是它的下限。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        form = screen_text(app)

    assert "标题" in form, "表单里要有一格写标题"
    assert "截止" not in form and "优先级" not in form, "这一步不问日期与优先级"
    assert "Esc 取消" in form, "底部那行提示不许被裁掉：它是「怎么退出这张表单」的唯一说明"
    assert messages.NO_TITLE_MESSAGE not in form, "刚打开时还没提交，不该报错"


async def test_enter_creates_the_task_and_leaves_the_form():
    """回车即建：建完浮层关掉、停在列表里（验收标准 1、2）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"写周报")
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created == ["写周报"], "回车就建这一条"
    assert len(fake.created_tasks) == 1
    assert "标题" not in after, "建完浮层关掉，停在任务列表页上"


async def test_the_task_lands_in_the_list_you_are_standing_in():
    """在**清单**里建，任务落在**这个清单**里，不是收集箱（验收标准 3、用户故事 45）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"写周报")
        await pilot.press("enter")
        await pilot.pause()

    assert placed(fake, "work") == [("写周报", None, False)], "落点是当前打开的这个清单"
    assert placed(fake, INBOX_ID) == [], "不是收集箱"


async def test_you_can_keep_creating_without_leaving_the_list():
    """建完停在列表里，能接着建下一条（验收标准 2）：光标还在下面那一层。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")

        for title in ("写周报", "交水费"):
            await pilot.press("n")
            await pilot.pause()
            await pilot.press(*title)
            await pilot.press("enter")
            await pilot.pause()

        still_on_tasks = app.layer
        after = screen_text(app)

    assert fake.created == ["写周报", "交水费"], "两条都建下了"
    assert still_on_tasks == "tasks", "建完还停在任务列表页上"
    assert "标题" not in after, "第二条建完浮层也关掉了"


async def test_an_empty_title_is_refused_and_the_form_stays_open():
    """空标题不许建，而且要如实说一句（引擎的 docstring：拦它的是调用方）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)
        still_open = "标题" in after
        await pilot.press("escape")
        await pilot.pause()

    assert fake.created == [], "空标题不许走到引擎"
    assert messages.NO_TITLE_MESSAGE in after
    assert still_open, "报错之后用户可以接着把标题打进去"


async def test_escape_cancels_without_writing_anything():
    """``esc`` 关掉表单：一个字节都不写（表单壳子 #42 的出口规矩）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"写周报")
        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created == []
    assert "标题" not in after
    assert "工作" in after, "浮层关掉，回到下面那一层"


# ------------------------------------------------------------------ 视图：收集箱 + 隐含日期


async def test_creating_in_a_view_lands_in_the_inbox():
    """视图不是容器：在视图里建，落在收集箱（验收标准 4、用户故事 35）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "all")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"随手记一笔")
        await pilot.press("enter")
        await pilot.pause()

    assert placed(fake, INBOX_ID) == [("随手记一笔", None, False)], "视图里建 → 收集箱"
    assert placed(fake, "work") == [] and placed(fake, "life") == [], "落不到任何真实清单里"


async def test_creating_in_today_gives_the_task_todays_date():
    """在「今天」里建：新任务自动带上**今天**那个日期（验收标准 5、用户故事 35）。

    「今天」隐含的是当前**逻辑日**（日界 24:00 时就是自然日 03-14），写法是全天任务的
    日期标记（当天 00:00）——「今天要做、没说几点」正是这样一条任务。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "today")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"今天要做")
        await pilot.press("enter")
        await pilot.pause()

    assert placed(fake, INBOX_ID) == [("今天要做", at(14, 0, 0), True)]


async def test_creating_in_a_custom_view_with_todays_window_gives_todays_date():
    """自建视图只要**条件一样**，落点与隐含日期就一样（#58 的 S1、用户故事 35）。

    这里摆的是一个用户自建的、截止窗口与内置「今天」**逐字相同**的视图（表单里的
    「今天到期（含逾期）」那一档）。内置与自定义视图走同一条求值路径，所以「在这一屏里
    写下的东西意思就是今天」这句话不该因为它是自建的而变——按 id 认内置视图的那一版会
    给 ``None``，同一屏在两种视图上两种行为，用户看不出为什么。
    """
    fake = backend()
    fake.add_view("今天到期", id="mine", due=due_window_of("today"))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "mine")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"自建的今天")
        await pilot.press("enter")
        await pilot.pause()

    assert placed(fake, INBOX_ID) == [("自建的今天", at(14, 0, 0), True)], (
        "自建视图里的新建：落收集箱，而且带上今天那个日期标记"
    )


async def test_creating_in_a_view_without_a_date_leaves_the_date_empty():
    """「最近七天」是**一段**窗口，藏不进一个日期里：在那里建就不带日期。

    它不是一个干净的日期区间（下界是今天、上界是六天后），挑其中任何一天当「隐含日期」
    都是替用户做一个他没做的决定。所以只有「今天」隐含日期。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "next7")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"以后要做")
        await pilot.press("enter")
        await pilot.pause()

    assert placed(fake, INBOX_ID) == [("以后要做", None, False)]


# ------------------------------------------------------------------ 还没同步完的清单


def test_the_message_for_a_list_that_has_not_synced_yet_says_so():
    """在还没推出去的清单里写：用户看到的那句话（#39 / #53）。

    引擎拒绝这一笔（请求体里的 ``projectId`` 服务端没见过），界面把原因原样报出来——**不许**
    说成建好了，也不许只说一句笼统的「新建失败」（用户会去查网络，而问题在那条清单还没同步
    完）。下一次刷新把那条清单认回来之后同一个按键就通了，所以这句话说的是「等同步完」，
    不是「做不到」。
    """
    shown = messages.create_failed_message(UnclaimedListError("local-list-1"))

    assert shown.startswith("没建成："), "说的是没建成，不是建好了"
    assert "还没同步完" in shown and "等它同步完再来" in shown


async def test_creating_in_a_list_that_has_not_synced_yet_is_refused_on_screen():
    """这条路径在屏幕上长什么样：拒绝 + 如实说一句，一条任务都不许多出来（#39 / #53）。

    还没推出去的清单（``local-list-…``）里建任务时，请求体里的 ``projectId`` 服务端没见过
    ——真引擎当场拒绝，假后端照它的样子拒绝（替身说了假话，这条断言就会静默变绿）。用户看到
    的是**原因**，不是一句「等一下就好」。
    """
    fake = backend()
    fake.add_list("还没推出去的清单", id="local-list-1")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "local-list-1")
        await pilot.press("n")
        await pilot.pause()
        await pilot.press(*"写周报")
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created == [] and fake.created_tasks == [], "一条都不许建"
    assert "没建成" in after and "还没同步完" in after, "屏幕上要说出原因"


# ------------------------------------------------------------------ 表单的聚焦信号


SGR = re.compile(r"\x1b\[[0-9;]*m")
"""屏幕字节里的样式序列；剥掉它剩下的才是**文本**（``test_task_rows_page.py`` 同形）。"""

ACCENT_BACKGROUND = 46
"""强调色的**底色**（ANSI 槽 6 的背景；前景是 36，见 ``theme.ACCENT``）。

与 ``tests/test_visual_identity.py`` 那条「条子是一块强调色实心」用的是同一个参数。
"""


def sgr_parameters(emitted: str) -> set[int]:
    """屏幕字节里出现过的每一个 SGR 参数（``\\x1b[46;49m`` → ``{46, 49}``）。

    本文件自带一份，与 ``tests/test_visual_identity.py`` / ``test_task_rows_page.py`` 那几份
    同形：这几行是「读渲染字节」的取数工具，五六个测试文件各带一份是现状（``support.py``
    只放触碰合成器的那几个函数）。
    """
    out: set[int] = set()
    for group in re.findall(r"\x1b\[([0-9;]*)m", emitted):
        out.update(int(part) for part in group.split(";") if part)
    return out


def line_with(emitted: str, needle: str) -> str:
    """带样式的屏幕字节里，剥掉 SGR 之后含 ``needle`` 的那一行。"""
    for line in emitted.splitlines():
        if needle in SGR.sub("", line):
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{emitted}")


async def test_the_focused_title_field_carries_the_accent(monkeypatch: pytest.MonkeyPatch):
    """焦点在标题那一格上时，那一行带**强调底色**（SGR ``46``）——表单的聚焦信号（#39）。

    断的是 ``46``（ANSI 槽 6 的**背景**，见 ``theme.form_css`` 的「聚焦的记号从边框换成强调色
    的底色」），**不是** ``38;2;`` / ``48;2;`` 那类真彩色。理由实测过（编排者量过两种浮层
    状态，我在这一张表单上复核过）：浮层自己那层 ANSI 面已经把真彩色挡在外面，这张表单渲染
    出来的字节里**一个真彩色序列都没有**——断言 ``38;2;`` 是空的，去掉 ``Input:focus`` 那条
    覆盖它照样是 0，永远不会红。跟着那条覆盖一起消失的是 ``46``。页面里的输入框（不在浮层里）
    是另一个场合，那里才该断真彩色（#44 在详细页上量到过 24 处）。

    ⚠ ``NO_COLOR`` 必须在**构造 App 之前**摘掉（#51 实测的次序）：这个 shell 有 ``NO_COLOR=1``，
    留着它整个 App 会挂一层 Monochrome，上面这条断言就成了「测 shell 的样子」——我第一次量
    就是这么量出「一个 46 都没有」的。
    """
    monkeypatch.delenv("NO_COLOR", raising=False)  # 先摘，再构造 App

    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        focused = app.focused
        await pilot.press(*"写周报")  # 打点东西进去，那一行才有一块受样式管的文本
        await pilot.pause()
        emitted = screen_sgr(app)

    assert focused is not None and focused.id == "field-title", (
        "打开就能打字：焦点在标题那一格上"
    )
    field = line_with(emitted, "写周报")
    label = line_with(emitted, "标题")
    assert ACCENT_BACKGROUND in sgr_parameters(field), (
        "聚焦的那一格要带强调底色（表单唯一的聚焦信号）；"
        f"这一行的 SGR 参数是 {sorted(sgr_parameters(field))}"
    )
    assert ACCENT_BACKGROUND not in sgr_parameters(label), (
        "底色标的是**那一格**，不是「标题」那行字段名——否则整张表单看着都像聚焦的"
    )
