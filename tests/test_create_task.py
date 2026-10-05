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

from datetime import datetime, timedelta, timezone

from dida.sync.engine import INBOX_ID
from dida.testing import FakeBackend, ManualClock
from dida.tui import messages
from dida.tui.app import DidaApp
from support import screen_text

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
    """``n`` 弹出的是**只填标题**的输入框：不问截止时间，也不问优先级（用户故事 44）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_container(pilot, app.index_page(), "work")
        await pilot.press("n")
        await pilot.pause()
        form = screen_text(app)

    assert "标题" in form, "表单里要有一格写标题"
    assert "截止" not in form and "优先级" not in form, "这一步不问日期与优先级"
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
