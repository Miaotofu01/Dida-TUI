"""视觉地基里「看得见的那一半」：顶栏、平移、光标行、转圈、toast（工单 #51）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot，断的是外部行为——「我按了这个键，
屏幕上出现了什么」。这里不断控件树、不断内部状态；读的「里面」只有 ``selected_id``
（页面给外层的公开口子）与 ``screen_text`` / ``screen_styled_text`` 这两个既有的接缝。

这个文件里唯一读样式的地方是**渲染级**那几条：颜色到底发出什么字节，只有真跑一遍才看得见
（``Text("x", style="cyan")`` 那类写法会静默发出真彩色）。断的是 ANSI 槽位号，不是某个
主题下的具体色值。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest
from rich.cells import cell_len
from textual import events
from textual.containers import ScrollableContainer

from dida.sync.engine import DidaError
from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp, Stage
from dida.tui.keys import LAYER_DETAIL, LAYER_INDEX, LAYER_TASKS
from support import sample_frames, screen_sgr, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

SGR = re.compile(r"\x1b\[[0-9;]*m")
"""ANSI 的 SGR 序列：样式在屏幕文本里就长这样。"""


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘掉 shell 的 ``NO_COLOR``：否则 ``App`` 会挂一层 Monochrome，颜色断言全部假绿。"""
    monkeypatch.delenv("NO_COLOR", raising=False)


def backend(*, slow: float = 0.0, broken: bool = False) -> FakeBackend:
    """一份够用的缓存；``slow`` / ``broken`` 用来演「同步慢」与「同步失败」。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work", group_id="g1")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18))
    fake.add_task("交水费", list_name="work", id="t2")
    fake.add_list("生活", id="life", group_id="g1")
    fake.add_list("笔记本", id="note", kind="NOTE")
    fake.set_sync_state(last_refresh_at=T0)
    if slow or broken:
        real_refresh = fake.refresh

        async def refresh() -> object:
            if slow:
                await asyncio.sleep(slow)
            if broken:
                raise DidaError("网络不可达")
            return await real_refresh()

        fake.refresh = refresh  # type: ignore[method-assign]
    return fake


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def lines(text: str) -> list[str]:
    return text.splitlines()


def line_with(text: str, needle: str) -> str:
    """屏幕上写着 ``needle`` 的那一行（没有就是测试写错了）。"""
    for line in lines(text):
        if needle in line:
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{text}")


def sgr_parameters(emitted: str) -> set[int]:
    """屏幕字节里出现过的每一个 SGR 参数（``\x1b[36;49m`` → ``{36, 49}``）。"""
    out: set[int] = set()
    for group in re.findall(r"\x1b\[([0-9;]*)m", emitted):
        out.update(int(part) for part in group.split(";") if part)
    return out


async def sample_while(action, project) -> list:
    """一边按键一边**逐帧**取一个观测量。

    ``pilot.press`` 会等到这一屏静下来（Textual 的动画跑完才算 idle），所以按完再抓屏只能
    看到落定后的样子——动效本身只有一个并发采样的人看得见。``project`` 是「这一帧上我要读
    什么」，返回的列表按时间顺序。
    """
    frames: list = []

    async def sample() -> None:
        while True:
            frames.append(project())
            await asyncio.sleep(0.01)

    sampler = asyncio.create_task(sample())
    try:
        await action()
    finally:
        sampler.cancel()
    return frames


async def enter_work(pilot, app: DidaApp) -> None:
    """走到「工作」这个清单里（光标停在它的第一条任务上）。"""
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    await pilot.press("right")
    await pilot.pause()


# ------------------------------------------------------------------ 顶栏与 chrome


async def test_the_top_bar_says_where_you_are_and_the_footer_is_gone():
    """顶栏说「你在哪」：词标 + 导航路径；**Footer 已去掉**，键位归按层的 ``h``。

    chrome 净增 0 行：底部原本是**两行**（Footer 之上还有状态栏），顶栏是拿 Footer 换来的。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

    top = lines(text)[0]
    assert "dida" in top, "顶栏要有词标"
    assert "清单列表页" in top, "启动时导航路径的第一段就是当前页"
    assert "当前这一层的键位" not in text, "Footer 已经去掉了：键位提示归按层的 ?"
    assert "手动同步" not in text, "Footer 上那一条键位提示也不该再占一行"
    assert lines(text)[-1].strip().startswith("已同步"), "底部只剩状态栏那一行"


async def test_the_breadcrumb_is_the_navigation_path_you_walked():
    """面包屑就是**导航路径**（GLOSSARY：``enter`` 压栈、``esc`` 出栈，最多三级）。

    页面只有三种，路径是走出来的：清单列表页 → 任务列表页 → 任务详细页。第三段写的是这条
    任务自己（顶层那一条正文也是它），因为「我在哪」在详细页的答案就是「在哪条任务上」。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert lines(screen_text(app))[0].count("▸") == 1, "一层路径：词标后面一段"

        for _ in range(20):  # 走到「工作」这个清单
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        await pilot.press("right")
        await pilot.pause()
        inside = lines(screen_text(app))[0]
        assert "清单列表页" in inside and "工作" in inside, "进了清单，路径上要多一段"
        assert inside.count("▸") == 2, "两段路径"

        await pilot.press("right")  # 进第一条任务的详细页
        await pilot.pause()
        detail = lines(screen_text(app))[0]
        assert detail.count("▸") == 3, "第三层：三段路径"
        assert "写周报" in detail, "最后一段就是这条任务"

        await pilot.press("left")
        await pilot.pause()
        back = lines(screen_text(app))[0]

    assert back.count("▸") == 2, "← 出栈，路径跟着退回去"
    assert "写周报" not in back


# ------------------------------------------------------------------ 一行就是一行


async def test_a_long_title_is_clipped_with_an_ellipsis_and_never_wraps():
    """60 列下一条长中文标题：**裁断**，不折行（ADR-0007 的「截断与折行按页分工」）。

    折行的三个后果都是坏的：光标字形被挤到单独一行、一行占三个屏幕行、以及
    ``scroll_cursor_into_view()`` 把「第几个 Row」当成「第几屏行」之后算错位置。
    这条量的是前两个，也顺带证明行数与行数仍是 1:1。
    """
    fake = backend()
    fake.add_task("一条非常长的任务标题用来看看这一栏在窄终端里到底会不会被截断或者折行显示", list_name="work", id="t9")
    app = DidaApp(fake)

    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        await pilot.press("j")  # 走到那条长标题上（它排在最后）
        await pilot.pause()
        text = screen_text(app)
        rows = [line for line in lines(text) if "一条非常长" in line]

    assert len(rows) == 1, f"长标题折行了——它占了 {len(rows)} 个屏幕行：\n{text}"
    assert rows[0].lstrip().startswith("❯"), f"光标字形被挤到别处去了：{rows[0]!r}"
    assert rows[0].rstrip().endswith(theme.ELLIPSIS), f"裁断要留一个省略号：{rows[0]!r}"
    assert len(rows[0]) <= 60, "这一行不许超出终端宽度"


# ------------------------------------------------------------------ 通栏细线


RULE_WIDTHS = (40, 60, 100)
"""三档宽度：窄终端、普通、宽屏。细线是按**格**铺的，所以每一档都看一眼。"""


def rule_lines(text: str) -> list[str]:
    """屏幕上那些通栏细线。

    ``…`` 也收进来：出 bug 时细线行比页面宽两格，被正文的 ``nowrap`` + ``ellipsis`` 裁掉
    并补上一个省略号——只认「整行都是细线」的话，这条测试会**看不见**那些坏行。
    """
    return [
        line
        for line in lines(text)
        if line.strip() and set(line.strip()) <= {theme.RULE, theme.ELLIPSIS}
    ]


@pytest.mark.parametrize("width", RULE_WIDTHS)
async def test_a_rule_spans_the_page_and_never_ends_in_an_ellipsis(width: int):
    """通栏细线 = **整幅页面宽、齐左、没有省略号**。

    细线不可停光标，所以它不参与光标那一列的行首空档。带上那两格之后这一行就是
    ``width + 2`` 格，正文的 ``text-wrap: nowrap`` + ``text-overflow: ellipsis`` 会裁掉一格
    再补一个 ``…``——屏幕上看到的是「缩进两格、以省略号收尾的一条线」。
    """
    app = DidaApp(backend())

    async with app.run_test(size=(width, 20)) as pilot:
        await pilot.pause()
        index = screen_text(app)
        await enter_work(pilot, app)
        tasks = screen_text(app)
        await pilot.press("right")  # 层三标题下面也有一条细线
        await pilot.pause()
        detail = screen_text(app)

    for page, text in (
        ("清单列表页", index),
        ("任务列表页", tasks),
        ("任务详细页", detail),
    ):
        found = rule_lines(text)
        assert found, f"{page}@{width} 上一条细线都没有：\n{text}"
        for line in found:
            assert not line.endswith(theme.ELLIPSIS), (
                f"{page}@{width} 的细线被裁断了——它比页面宽：{line!r}"
            )
            assert cell_len(line) == width, (
                f"{page}@{width} 的细线不是整幅页面宽（{cell_len(line)} 格）：{line!r}"
            )
            assert line == theme.RULE * width, (
                f"{page}@{width} 的细线不是齐左铺满（左边那两格是光标空档）：{line!r}"
            )


async def test_a_resized_window_re_lays_every_rule():
    """窗口宽度变了，细线要按**新的**宽度重铺（``on_resize`` → 重画）。

    细线是按格算出来的，宽度一变那个算式就变了。61 是奇数：它顺带证明这里算的是格，不是
    「半格」——也不是拿旧的宽度凑合。
    """
    app = DidaApp(backend())

    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.resize_terminal(61, 24)
        await pilot.pause()
        resized = screen_text(app)

    found = rule_lines(resized)
    assert found, f"缩放之后细线不见了：\n{resized}"
    assert all(line == theme.RULE * 61 for line in found), (
        f"细线没跟着新宽度重铺：{[cell_len(line) for line in found]}"
    )


@pytest.mark.parametrize("width", RULE_WIDTHS)
async def test_content_that_overflows_keeps_its_gutter_and_its_ellipsis(width: int):
    """会溢出的**内容**行照旧：两格行首空档 + 按格裁到页边 + 行尾一个 ``…``。

    细线不要那两格，内容行要——光标字形与标题之间的那一列不能跟着一起消失。纯 ASCII 那条
    标题是个算式：页面有 ``width`` 格，行首两格归光标，**再两格归行首那一列**（勾选框 +
    它后面那一格，工单 #37 加、#63 改成勾选框），裁断记号自己占一格，所以标题看得见的部分
    正好是前 ``width - 5`` 格。汉字那条也在（他的 locale 是 ``zh_CN.UTF-8``）：一个汉字两格，
    断在哪一格按格算，不按字符数。
    """
    ascii_title = "abcdefghij" * 12
    cjk_title = "把这一条标题写得足够长，让每一个宽度上都溢出页面——窄终端、普通终端、宽屏都得裁断，而裁断的位置要按格算不按字符数"
    fake = backend()
    fake.add_task(ascii_title, list_name="work", id="t9")
    fake.add_task(cjk_title, list_name="work", id="t10")
    app = DidaApp(fake)

    async with app.run_test(size=(width, 20)) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        await pilot.press("j")  # 走到那条纯 ASCII 的长标题上
        await pilot.pause()
        text = screen_text(app)

    ascii_row = line_with(text, "abcdefghij")
    # 行首两格是光标空档，紧跟的两格是勾选框那一列（``☐`` = 未完成，工单 #63）。
    assert ascii_row == f"❯ {theme.TODO_MARK} {ascii_title[: width - 5]}{theme.ELLIPSIS}", (
        f"@{width} 的行首空档或裁断位置变了：{ascii_row!r}"
    )
    cjk_row = line_with(text, "把这一条标题")
    assert cjk_row.endswith(theme.ELLIPSIS), f"汉字标题也要留省略号：{cjk_row!r}"
    assert cell_len(cjk_row) == width, (
        f"@{width} 这条内容行要一直画到页边（现在是 {cell_len(cjk_row)} 格）：{cjk_row!r}"
    )


# ------------------------------------------------------------------ 平移与开关


async def test_changing_layer_pans_horizontally_and_lands_aligned():
    """换层 = 横向平移（``enter`` 压栈、``esc`` 出栈是导航栈，平移是它的标准表达）。

    断的是外部行为：飞行途中，新那一页**还没对齐**（它的抬头被推到右边若干格），落定之后
    它对齐在最左边。中间那几帧就是「平移」这件事在屏幕上唯一的样子。
    """
    app = DidaApp(backend(), animations="on")

    def heading_column(text: str) -> int:
        """屏幕上**任务那一行**左边缩进了几格（找不到就是还没进屏）。

        找的是任务标题而不是容器名：容器名也在顶栏的面包屑里，而顶栏永远对齐在最左边，
        拿它当坐标只会得到 0。
        """
        for line in lines(text):
            if "写周报" in line:
                return len(line) - len(line.lstrip())
        return -1

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(20):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        offsets = await sample_while(
            lambda: pilot.press("right"), lambda: heading_column(screen_text(app))
        )
        await pilot.pause(0.3)
        settled = screen_text(app)

    assert any(offset > 0 for offset in offsets), f"没有一帧是「滑进来」的样子：{offsets}"
    assert len(set(offsets)) > 2, f"中间帧应当是一格一格滑过来的：{offsets}"
    assert heading_column(settled) == 0, "落定之后要正好对齐在最左边"


USER_SCROLL_KEYS = (
    "home",
    "end",
    "pageup",
    "pagedown",
    "ctrl+pageup",
    "ctrl+pagedown",
)
"""**用户按下去不许推动平移轨道**的那些键。

工单 #59 点名的六个横向键里，``←`` / ``→`` 从工单 #65 起归了**导航**（见
:data:`NAVIGATION_KEYS`），所以不在这份名单里；剩下这四个照样一个都不许推动轨道——实测
``page_left`` / ``page_right`` 一次挪一整屏，比 ``←`` / ``→`` 还狠。``up`` / ``down``
不在这里：它们是页面自己的光标键（与 ``j`` / ``k`` 同一条绑定），按下去本该动光标。
"""

NAVIGATION_KEYS = ("left", "right")
"""交给**导航**的那两个键（工单 #65）：``→`` 进下一层、``←`` 回上一层。

它们本来住在 Textual 的滚动键表里（``ScrollableContainer`` 的 ``scroll_left`` /
``scroll_right``）：三个导航页的绑定表在页面上把它们换成了 ``enter`` / ``back``，焦点控件
那一层就截住了。**清单列表页没有 ``left`` 这一条**（那里无处可退，用户故事 124），所以
``←`` 仍旧落到 ``scroll_left`` 上——实测它什么都不做（``VerticalScroll`` 的
``overflow-x`` 是 hidden），``tests/test_pages.py::test_left_on_the_index_page_does_nothing``
钉着这一点。
"""


async def go_to_layer(pilot, app: DidaApp, layer: int) -> None:
    """走到第 ``layer`` 层（0 就是开屏那一层）。"""
    if layer:
        await enter_work(pilot, app)
    if layer >= 2:
        await pilot.press("right")
        await pilot.pause()


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_the_user_scroll_keys_never_push_the_pan_track(layer: int):
    """``home`` ``end`` ``pageup`` ``pagedown`` ＋ ``ctrl+pageup`` / ``ctrl+pagedown``
    在三层上**什么都不做**（工单 #59）。

    平移是**程序驱动**的（``show()`` 与 ``on_resize()`` 调 ``scroll_to``），不是用户滚的：
    轨道是个真能横向滚动的容器，于是继承了 Textual 的滚动键位——一按就挪一格，那条铺满整幅的
    规则线当场少一格。``home`` 更糟：在详细页按下去视图跳到第一页的位置，而 app 仍然认为你在
    第三层——屏幕和状态彻底对不上。``page_left`` / ``page_right`` 一次挪一整屏，比谁都狠。

    ``←`` / ``→`` **不在这条里**：工单 #65 起它们是导航键（``→`` 进下一层、``←`` 回上一层），
    按下去本来就该换层——它们那半边归 :data:`NAVIGATION_KEYS` 的说明管。

    挡住它们的是 ``#stage`` 的 ``overflow-x: hidden``（``dida.tui.theme`` 那个 CSS 块里写了
    为什么）——**不是**一张「哪些键要忽略」的清单，所以同一条路也堵住了滚轮与聚焦（下面两条
    各测一半）。

    断的是外部行为：屏幕**逐字节不变**，且轨道停在原处（两层证据缺一不可——只断文本的话，
    页内自己横向挪一格也算通过）。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, layer)
        stage = app.query_one("#stage", Stage)
        parked = stage.scroll_offset.x
        assert parked == layer * WIDE[0], f"第 {layer} 层没停在该停的地方（x={parked}）"

        for key in USER_SCROLL_KEYS:
            before = screen_text(app)
            await pilot.press(key)
            await pilot.pause()

            assert stage.scroll_offset.x == parked, (
                f"第 {layer} 层按 {key}，平移轨道从 {parked} 被推到了 {stage.scroll_offset.x}"
            )
            assert screen_text(app) == before, (
                f"第 {layer} 层按 {key}，屏幕不该有任何变化：\n{screen_text(app)}"
            )


WHEEL_INPUTS: tuple[tuple[str, type, dict[str, bool]], ...] = (
    ("普通滚轮下", events.MouseScrollDown, {}),
    ("普通滚轮上", events.MouseScrollUp, {}),
    ("shift+滚轮下", events.MouseScrollDown, {"shift": True}),
    ("shift+滚轮上", events.MouseScrollUp, {"shift": True}),
    ("ctrl+滚轮下", events.MouseScrollDown, {"control": True}),
    ("ctrl+滚轮上", events.MouseScrollUp, {"control": True}),
    ("横向滚轮右", events.MouseScrollRight, {}),
    ("横向滚轮左", events.MouseScrollLeft, {}),
)
"""鼠标那半边：纵轴两向、加 shift / ctrl 的横向那两向、以及倾斜滚轮（横轴）两向。

``shift+滚轮`` 是最容易误触的一个（在 kitty 里滚列表时手滑按住 shift 就撞上），后果和 ``→``
一模一样：页面挪一格、通栏细线少一格，多滚几次还能把轨道推到别的页面上。
"""


async def wheel(pilot, page, event: type, **modifiers: bool) -> None:
    """把一次滚轮事件送到 ``page`` 上（指针就在那一页中间）。

    ⚠ ``_post_mouse_events`` 是 Pilot 的**私有**方法，Textual 8.2.8 没有公开的滚轮模拟口
    （``press`` / ``click`` / ``hover`` 都发不出滚轮事件）。用它而不是自己造事件对象，是因为
    它替我们算好了坐标与 ``widget``，事件走的**就是真那条路**：先到指针底下那一页，那一页横向
    滚不动，于是冒泡到轨道上。

    「事件真的送到了」不靠这个函数的返回值（它比的是「指针底下那个控件是不是我点名的那个」，
    而已知它会回 ``False``——见 ``test_..._when_the_track_is_scrollable_again`` 那条正对照）。
    """
    await pilot._post_mouse_events([event], widget=page, **modifiers)
    await pilot.pause()


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_the_mouse_wheel_never_pushes_the_pan_track(layer: int):
    """**纵轴、横轴、加不加修饰键，滚轮一律推不动轨道**（工单 #59 用户报的是键盘，这是同一半）。

    用户输入不该推动平移轨道——不管那输入是键还是滚轮。三层各来一遍八种滚轮输入，断的还是
    那两层证据：``Stage.scroll_offset.x`` 不变、屏幕逐字节不变。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, layer)
        stage = app.query_one("#stage", Stage)
        parked = stage.scroll_offset.x
        page = {0: app.index_page(), 1: app.tasks_page(), 2: app.detail_page()}[layer]

        for label, event, modifiers in WHEEL_INPUTS:
            before = screen_text(app)
            await wheel(pilot, page, event, **modifiers)

            assert stage.scroll_offset.x == parked, (
                f"第 {layer} 层{label}，平移轨道从 {parked} 被推到了 {stage.scroll_offset.x}"
            )
            assert screen_text(app) == before, (
                f"第 {layer} 层{label}，屏幕不该有任何变化：\n{screen_text(app)}"
            )


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_a_focus_change_never_pushes_the_pan_track(layer: int):
    """``tab``（Textual 的 ``focus_next``）也不许把轨道拖走（工单 #59，实测的第三条路）。

    这一条比滚轮还狠：它一次挪**整整一页**。聚焦会「把那个控件滚进可见区」（
    ``Screen.set_focus`` → ``scroll_to_center``），而 ``tab`` 聚焦的是**别层的页面**——
    于是屏幕整个换了一页，而 ``app.layer`` 还在原来那一层，与 ``home`` 那个症状一模一样。

    ``_show`` 早就用 ``focus(scroll_visible=False)`` 躲开过这一下（那次是把平移动画抹平），
    这里断的是「躲不躲都推不动」。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, layer)
        stage = app.query_one("#stage", Stage)
        parked = stage.scroll_offset.x

        for _ in range(2):  # 聚焦会一层层往后走：多按一次看下一站
            before = screen_text(app)
            await pilot.press("tab")
            await pilot.pause()

            assert stage.scroll_offset.x == parked, (
                f"第 {layer} 层按 tab，平移轨道从 {parked} 被推到了 {stage.scroll_offset.x}"
            )
            assert screen_text(app) == before, (
                f"第 {layer} 层按 tab，屏幕不该有任何变化：\n{screen_text(app)}"
            )


async def test_the_wheel_reaches_the_track_when_the_track_is_scrollable_again():
    """**正对照**：把轨道放回「用户可滚」的样子，同一个 ``shift+滚轮`` 立刻推得动它。

    上面那几条断的是「什么都没发生」，而「什么都没发生」有两种可能：真的挡住了，或者事件根本
    没送到。这条控制实验把两种可能分开——滚轮事件确实到了轨道上，挡住它的是那个「用户不可滚」
    的开关（而且是**只有**那个开关：把它拧回去，同一个事件当场成功）。
    """
    app = DidaApp(backend())

    async with app.run_test(size=(100, 60)) as pilot:  # 高一点：正对照要真的滚得动
        await pilot.pause()
        stage = app.query_one("#stage", Stage)
        page = app.index_page()

        stage.styles.overflow_x = "scroll"
        await pilot.pause()
        assert stage.allow_horizontal_scroll, "拧回 scroll 之后轨道应当又能被用户滚"

        before = stage.scroll_offset.x
        await wheel(pilot, page, events.MouseScrollDown, shift=True)
        assert stage.scroll_offset.x != before, (
            f"滚轮事件没送到轨道上（x 还是 {before}）——上面那几条测试就是空的"
        )


def scroll_bindings() -> dict[str, str]:
    """Textual **自己那张表**里，每一个滚动键绑到哪个 action（``{'left': 'scroll_left', …}``）。

    键位表不在这里抄第二遍：Textual 加一个新键或改一个 action，下面的对账立刻变红——而不是
    等用户又按一下看见页面滑走。
    """
    bound: dict[str, str] = {}
    for binding in ScrollableContainer.BINDINGS:
        for key in str(binding.key).split(","):
            bound[key.strip()] = str(binding.action)
    return bound


async def test_the_pan_track_is_not_user_scrollable_at_all():
    """**复发守卫（形状级）**：轨道在 Textual 自己眼里就不是个「用户能滚」的东西。

    ``allow_horizontal_scroll`` 是 Textual 每一个用户滚动入口的总闸——滚动 action
    （``action_scroll_left`` 开头就是 ``if not self.allow_horizontal_scroll: raise SkipAction``）、
    滚轮处理器（``_on_mouse_scroll_down`` 里同一个条件）、指针拖动、以及聚焦时的
    ``scroll_to_region``（不是 ``force`` 就把 x 抹成 0）。它由 ``overflow-x`` 决定。

    所以这条断言比「逐个记住哪些键要挡住」强：**任何一个入口只要还开着这个闸，它就会问**
    「用户还能滚这一轴吗」，而答案是「不能」——将来 Textual 再长出一个滚动入口也一样。
    谁把 ``#stage`` 的 ``overflow-x`` 改回 ``scroll`` / ``auto``，这条当场变红。

    程序滚动走的是另一条路（``force=True``），所以它不受这个闸的限制——那半边由
    ``test_every_layer_change_still_parks_the_track_on_that_layer`` 看着。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        stage = app.query_one("#stage", Stage)
        assert not stage.allow_horizontal_scroll, (
            "平移轨道又能被用户滚了（#stage 的 overflow-x 是不是被改回 scroll / auto 了）："
            "滚轮、tab、home / end 都会把它推走"
        )


def test_the_only_scroll_keys_this_file_leaves_alone_are_the_pages_cursor_keys():
    """``up`` / ``down`` 是**页面自己的光标键**、``←`` / ``→`` 是**导航键**，其余一个都不许漏。

    每一条守卫都得有个「谁在看这张表」的对账：Textual 哪天往族里加一个新键，这条变红，
    逼着下一个人去看它是「真的推不动」（那就加进 :data:`USER_SCROLL_KEYS`）还是「另有主人」
    （``up`` / ``down`` 与 #65 的 ``←`` / ``→`` 就是后者）。
    """
    bound = set(scroll_bindings())
    watched = set(USER_SCROLL_KEYS) | set(NAVIGATION_KEYS) | {"up", "down"}

    assert bound == watched, (
        "Textual 的滚动键表变了："
        f"新来的 {sorted(bound - watched)} 要么进 USER_SCROLL_KEYS，要么说明它为什么可以例外；"
        f"走掉的 {sorted(watched - bound)} 从那份名单里删掉。"
    )


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_up_and_down_move_the_cursor_and_do_not_push_the_track(layer: int):
    """``↑`` / ``↓`` 顶多做到 ``k`` / ``j`` 那件事，**一件都不许多**（工单 #59）。

    这两个键不能断「屏幕逐字节不变」：它们在每一页上都绑着 ``cursor_up`` / ``cursor_down``
    （与 ``k`` / ``j`` 同一条绑定），按下去本来就该动光标。它们那半边风险是**顺带**把轨道
    也推了——所以比的是「按 ``↓`` 得到的屏幕」与「按 ``j`` 得到的屏幕」：两个 app 从同一份
    缓存、同一层出发，一个键走两条路，屏幕不一样就说明 ``↓`` 多做了别的事。
    """
    async def screen_after(key: str) -> tuple[str, int]:
        app = DidaApp(backend())
        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await go_to_layer(pilot, app, layer)
            await pilot.press(key)
            await pilot.pause()
            return screen_text(app), app.query_one("#stage", Stage).scroll_offset.x

    cursor_down, after_down = await screen_after("down")
    cursor_j, after_j = await screen_after("j")
    assert cursor_down == cursor_j, f"第 {layer} 层按 down 与按 j 应当是同一件事"
    assert after_down == after_j == layer * WIDE[0], (
        f"第 {layer} 层按 down / j 都不该推动平移轨道：{after_down} / {after_j}"
    )

    cursor_up, after_up = await screen_after("up")
    cursor_k, after_k = await screen_after("k")
    assert cursor_up == cursor_k, f"第 {layer} 层按 up 与按 k 应当是同一件事"
    assert after_up == after_k == layer * WIDE[0], (
        f"第 {layer} 层按 up / k 都不该推动平移轨道：{after_up} / {after_k}"
    )


LAYER_BODY_ROW = {
    LAYER_INDEX: ("▪ 收集箱", 2),
    LAYER_TASKS: ("☐ 写周报", 2),
    LAYER_DETAIL: ("标题", 2),
}
"""每一层正文里**只有这一层有**的那一行，以及它在屏幕上该停在第几格（工单 #59）。

格数是承重的：轨道只要偏一格，整幅正文就跟着偏一格，这三个记号当场不在原来那一格上。行首
那两格是光标空档（``❯ `` 或两个空格），所以内容从第 2 格起——光标停在哪一行都不影响这个数，
``←`` 回去时光标还停在 ``⋮ 工作`` 上也不影响。``写周报`` 单独当记号不行：详细页的面包屑
里也有它，而顶栏永远齐左，量不出平移。
"""


async def test_every_layer_change_still_parks_the_track_on_that_layer():
    """``→`` / ``←`` 换层照旧把轨道**正好**停在那一页上（工单 #59 的另一半）。

    这条与上面几条是一对：修的是「用户按不动它」，不是「谁也推不动它」——``show()`` 与
    ``on_resize()`` 必须照旧能滑。两次压栈两次出栈，每一站三个独立的证据：``scroll_offset.x``
    正好是 ``层号 × 宽度``、细线照旧铺满一百格（偏一格它就只剩九十九格），以及这一页自己的
    那一行**就在第一格**。
    """
    app = DidaApp(backend())

    def check(what: str, layer: str) -> None:
        """这一站该有的三件证据：app 认为在第几层、轨道停在第几格、屏幕上对不对齐。"""
        assert app.layer == layer, f"{what}：app 认为自己在 {app.layer} 层，不是 {layer}"

        expected = (LAYER_INDEX, LAYER_TASKS, LAYER_DETAIL).index(layer) * WIDE[0]
        assert stage.scroll_offset.x == expected, (
            f"{what}：轨道 x={stage.scroll_offset.x}，应当停在 {expected}"
        )

        text = screen_text(app)
        rules = rule_lines(text)
        assert rules and all(line == theme.RULE * WIDE[0] for line in rules), (
            f"{what}：轨道没对齐在页边界上，细线不是整幅宽：{[cell_len(line) for line in rules]}"
        )
        needle, column = LAYER_BODY_ROW[layer]
        row = line_with(text, needle)
        assert row.index(needle) == column, (
            f"{what}：这一页的正文被推走了——「{needle}」在第 {row.index(needle)} 格，"
            f"应当在第 {column} 格：{row!r}"
        )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        stage = app.query_one("#stage", Stage)
        stops = []

        def parked(what: str, layer: str) -> None:
            stops.append(stage.scroll_offset.x)
            check(what, layer)

        parked("开屏", LAYER_INDEX)
        await enter_work(pilot, app)  # 0 → 1
        parked("enter 进任务列表页", LAYER_TASKS)
        await pilot.press("right")  # 1 → 2
        await pilot.pause()
        parked("enter 进任务详细页", LAYER_DETAIL)
        await pilot.press("left")  # 2 → 1
        await pilot.pause()
        parked("esc 回任务列表页", LAYER_TASKS)
        await pilot.press("left")  # 1 → 0
        await pilot.pause()
        parked("esc 回清单列表页", LAYER_INDEX)

    assert stops == [0, 100, 200, 100, 0], f"每一站的停点变了：{stops}"


async def test_a_resize_re_parks_the_track_on_the_layer_you_are_on():
    """窗口缩放之后仍然对齐：``on_resize`` 按**新的**宽度重新停一次（工单 #59 验收 4）。

    停在第二层（x=100），把窗口缩到 61 格——新的停点是 61。中途每一帧都得是「旧的停点」或
    「新的停点」：``on_resize`` 是 ``animate=False`` 的，出现中间值就是那一下变成了一次乱滑
    （动效开着时才看得出来，所以这个 app 用 ``animations="on"``）。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        stage = app.query_one("#stage", Stage)
        assert stage.scroll_offset.x == WIDE[0]

        frames = await sample_while(
            lambda: pilot.resize_terminal(61, 24), lambda: stage.scroll_offset.x
        )
        await pilot.pause()

        assert stage.scroll_offset.x == 61, f"缩放之后没停在新的页边界上：{stage.scroll_offset.x}"
        assert set(frames) <= {float(WIDE[0]), 61.0}, f"重新对齐的路上出现了中间帧：{sorted(set(frames))}"
        rules = rule_lines(screen_text(app))
        assert rules and all(line == theme.RULE * 61 for line in rules), (
            f"缩放之后细线没按新宽度铺：{[cell_len(line) for line in rules]}"
        )

    assert frames, "一帧都没采到"


async def test_animations_off_switches_layers_in_one_frame():
    """``animations=off``：换层不滑，直接到（ssh / 低能力终端就是这么用的）。"""
    app = DidaApp(backend(), animations="off")

    def task_offset(text: str) -> int | None:
        """任务那一行左边缩进了几格；它还没进屏就是 ``None``。"""
        for line in lines(text):
            if "写周报" in line:
                return len(line) - len(line.lstrip())
        return None

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(20):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        frames = await sample_while(
            lambda: pilot.press("right"), lambda: task_offset(screen_text(app))
        )
        await pilot.pause(0.2)
        settled = task_offset(screen_text(app))

    assert frames, "一帧都没采到"
    assert all(offset in (None, 0) for offset in frames), (
        f"关掉动效之后不该还有「在路上」的帧：{sorted(set(frames), key=str)}"
    )
    assert settled == 0, "换层之后直接对齐在那一页上"


async def test_the_animations_switch_is_reachable_from_the_environment(monkeypatch):
    """``DIDA_ANIM=on|off``：用户按得动这一个开关（默认 ``auto``）。

    两个 app 只差这一个环境变量，其余一模一样——一个中途有一帧没对齐（它真的在滑），
    另一个每一帧都对齐（它一步到位）。
    """
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)

    async def frames_for(mode: str) -> list:
        monkeypatch.setenv("DIDA_ANIM", mode)
        app = DidaApp(backend())

        def offset(text: str) -> int | None:
            for line in lines(text):
                if "写周报" in line:
                    return len(line) - len(line.lstrip())
            return None

        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            for _ in range(20):
                if app.index_page().selected_id == "work":
                    break
                await pilot.press("j")
            return await sample_while(
                lambda: pilot.press("right"), lambda: offset(screen_text(app))
            )

    moving = await frames_for("on")
    still = await frames_for("off")

    assert any(one and one > 0 for one in moving), f"DIDA_ANIM=on 应该真的在滑：{moving}"
    assert all(one in (None, 0) for one in still), f"DIDA_ANIM=off 不该有中间帧：{still}"


def test_the_switch_resolves_auto_off_for_remote_and_dumb_terminals():
    """``auto``：ssh / 低能力终端上不动（动效在慢链路上只是延迟）。"""
    assert theme.animations_enabled("auto", {"TERM": "xterm-kitty"}) is True
    assert theme.animations_enabled("auto", {"TERM": "xterm-kitty", "SSH_CONNECTION": "1 2 3 4"}) is False
    assert theme.animations_enabled("auto", {"TERM": "dumb"}) is False
    assert theme.animations_enabled("auto", {"TERM": "xterm-kitty", "TEXTUAL_ANIMATIONS": "none"}) is False
    assert theme.animations_enabled("on", {"TERM": "dumb"}) is True
    assert theme.animations_enabled("off", {"TERM": "xterm-kitty"}) is False


async def test_neither_the_app_nor_the_pages_shadow_textuals_animator():
    """``_animate`` 是 **Textual 自己的 animator**，谁都别拿它当自己的开关用。

    ``App.__init__`` 把 ``self._animate`` 绑成 ``BoundAnimator``，``App.animate()`` 就是调它
    （``Widget`` 那一半是第一次用时才绑的）。被一个 ``bool`` 占掉之后 ``app.animate(...)``
    抛 ``TypeError: 'bool' object is not callable``，而报错点在 ``textual/`` 里面，离现场很远。
    今天没有生产代码调它，所以这是**潜在**的——真按一下才知道。

    ``pages/base.py`` 曾经用一个 ``_motion`` 布尔开关（那是对的做法：名字没占用 ``_animate``），
    ADR-0008 四撤掉光标条时它跟着删了；这条顺手把三张页面也真按一次，不靠「以为它是干净的」。

    ⚠ 便宜的守卫查的是**类**上有没有这个名字，不能查 ``vars(app)``：``App.__init__`` 自己就
    往实例上放了 ``_animate``（绑好的 animator），所以它一直都在实例名字空间里——查那儿的
    话这条断言两边都绿，什么也证明不了。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        pages = [app.index_page(), app.tasks_page(), app.detail_page()]
        app.probe_value = 0.0
        app.animate("probe_value", 1.0, duration=0.01)  # 撞名时这里抛 TypeError
        for page in pages:
            page.animate("scroll_y", 0, duration=0.01)  # 页面那一半也要能用
        await pilot.pause(0.05)

    assert "_animate" not in DidaApp.__dict__, "开关不许在类上占用 Textual 的 animator 那个名字"
    assert callable(app._animate), "Textual 的 animator 被盖掉了"
    for page in pages:
        assert callable(page._animate), "页面上的 animator 被盖掉了"


async def test_resizing_the_window_keeps_you_on_the_page_you_were_on():
    """窗口宽度变了，舞台要重新对齐到**当前那一页**。

    三段并排停着，位置是按「第几页 × 屏宽」算的：宽度一变，那个算式就变了。不重算的话
    用户会停在两页之间的缝里——屏幕上什么都没有，看起来像 app 坏了。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        await pilot.resize_terminal(72, 24)
        await pilot.pause()
        after = screen_text(app)

    assert "写周报" in after, f"缩放之后应当还停在任务列表页：\n{after}"
    row = line_with(after, "写周报")
    assert row.lstrip().startswith("❯") or row.lstrip().startswith("交水费"), row
    assert lines(after)[0].count("▸") == 2, "顶栏还写着两段路径（没有回到清单列表页）"


# ------------------------------------------------------------------ 光标：只有 ❯ 与强调色


def cursor_row(emitted: str) -> str:
    """屏幕上带 ``❯`` 的那一行（去掉 ANSI 之后）；这一帧上没有就是空串。"""
    for line in emitted.splitlines():
        plain = SGR.sub("", line)
        if plain.startswith(theme.CURSOR_MARK):
            return plain
    return ""


def cursor_row_sgr(emitted: str) -> set[int]:
    """那一行发出去的 SGR 参数（比参数不比整串：Textual 把前景与背景并进同一条序列）。"""
    for line in emitted.splitlines():
        if SGR.sub("", line).startswith(theme.CURSOR_MARK):
            return sgr_parameters(line)
    return set()


async def test_moving_the_cursor_never_blanks_a_row_and_never_draws_a_bar():
    """上下移动光标时那一行**不许整行消失**（工单 #62）。

    真 app + ``animations="on"``：默认 ``auto`` 在测试环境里把动效关掉，就复现不出来。
    两个断言都是外部的——那两条任务的标题**每一帧都在**屏幕上（装饰条还在时，它压在旧行、
    新行上会把那一整行擦掉，ADR-0008 四），而且**每一帧**都不出现强调色实心块
    （SGR 46 = 青底，就是那条装饰条本身）。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        frames = await sample_frames(app, lambda: pilot.press("j"), screen_sgr)
        await pilot.pause(0.3)
        selected = app.tasks_page().selected_id

    assert frames, "一帧都没采到"
    assert selected == "t2", "这一次按键没落到下一条任务上——采样之后的第一次 press 是已知的坑"
    blanked = [
        index
        for index, frame in enumerate(frames)
        if "写周报" not in SGR.sub("", frame) or "交水费" not in SGR.sub("", frame)
    ]
    assert not blanked, f"这些帧上有一整行被擦掉了（装饰条压在那一行上）：{blanked[:5]}"
    barred = [index for index, frame in enumerate(frames) if 46 in sgr_parameters(frame)]
    assert not barred, f"这些帧上出现了强调色实心的装饰光标条：{barred[:5]}"


async def test_the_cursor_row_is_the_mark_and_the_accent_at_once_and_then_still():
    """光标行的反馈只有 ``❯`` 与强调色，而且**一次到位、之后不动**（工单 #62 / ADR-0008 四）。

    逐帧读那一行：从「写周报」换成「交水费」只发生一次，换过去的那一帧就已经带强调色
    （ANSI 36），之后每一帧都还是它。装饰条还在时，中途会有几帧那一行整个不见——条子正压在
    它上面。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        before = cursor_row(screen_sgr(app))
        frames = await sample_frames(app, lambda: pilot.press("j"), screen_sgr)
        await pilot.pause(0.3)

    rows = [cursor_row(frame) for frame in frames]
    assert "写周报" in before, f"按键之前光标不在第一条任务上：{before!r}"
    assert any("交水费" in row for row in rows), f"光标行一直没走到第二条任务上：{rows}"
    first = next(index for index, row in enumerate(rows) if "交水费" in row)
    assert all("写周报" in row for row in rows[:first]), f"换过去之前光标行不是第一条：{rows[:first]}"
    assert all("交水费" in row for row in rows[first:]), (
        f"光标行换过去之后又空了或又动了——那就是装饰条盖住了它：{rows[first:]}"
    )
    accents = [36 in cursor_row_sgr(frame) for frame in frames[first:]]
    assert all(accents), f"光标行换过去之后有几帧没有强调色：{accents}"


async def test_the_cursor_row_is_the_same_whether_animations_are_on_or_off(monkeypatch):
    """``DIDA_ANIM=on|off`` 只影响换层：光标行的画法**完全一样**（工单 #62）。

    两个 app 只差这一个环境变量（照 #51 那条开关测试的做法），都在任务列表页把光标从第一条
    移到第二条；屏幕上带 ``❯`` 的那一行必须逐字相同——光标自己不再有任何装饰动效。
    """

    async def row_after_move(mode: str) -> str:
        monkeypatch.setenv("DIDA_ANIM", mode)
        app = DidaApp(backend())

        async with app.run_test(size=WIDE) as pilot:
            await pilot.pause()
            await enter_work(pilot, app)
            await pilot.press("j")
            await pilot.pause(0.3)
            return cursor_row(screen_sgr(app))

    moving = await row_after_move("on")
    still = await row_after_move("off")

    assert "交水费" in moving and "交水费" in still, (moving, still)
    assert moving == still, f"DIDA_ANIM=off 与 on 下的光标行不一样：{still!r} != {moving!r}"


# ------------------------------------------------------------------ 同步转圈


async def test_a_fast_sync_never_shows_a_spinner():
    """快同步什么都不显示——本地缓存那一屏不该有个东西一直在转。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        seen = []
        await pilot.press("r")
        for _ in range(6):
            await pilot.pause(0.05)
            seen.append(screen_text(app))
        await pilot.pause(0.2)

    assert not any(theme.SPINNER_FRAMES[i] in text for text in seen for i in range(len(theme.SPINNER_FRAMES))), (
        "快同步里出现了转圈"
    )


async def test_a_slow_sync_shows_a_spinner_only_after_the_threshold():
    """超过阈值才转圈：300ms 之前屏幕上没有它，之后有。"""
    app = DidaApp(backend(slow=1.2))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.12)
        early = screen_text(app)
        await pilot.pause(0.5)
        late = screen_text(app)
        await pilot.pause(0.9)  # 同步跑完了

    def spins(text: str) -> bool:
        return any(frame in text for frame in theme.SPINNER_FRAMES)

    assert not spins(early), "阈值之前不该出现转圈"
    assert spins(late), "超过阈值之后要看得见它在转"


# ------------------------------------------------------------------ toast


async def test_a_manual_sync_that_works_says_so_with_a_native_toast():
    """完成 = 原生 toast（``App.notify()``），不是改状态栏那一行字符串。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.2)
        text = screen_text(app)

    assert "同步完成" in text, f"完成没有以 toast 说出来：\n{text}"


async def test_a_failed_sync_says_so_with_a_native_toast_and_keeps_the_status_line():
    """失败也是 toast（用户故事 43 要的「短暂的视觉反馈」）；状态栏那一份**照留**。

    两份不重复：状态栏是留在屏幕上的记录（刷新失败时它写的正是那句话），toast 是「刚刚那
    一下」的信号。断网时用户按了 ``r`` 却什么都不发生，比吵一句坏得多。
    """
    quiet = DidaApp(backend(broken=True))
    async with quiet.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.2)
        without_toast = screen_text(quiet)

    app = DidaApp(backend(broken=True))
    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.2)
        with_toast = screen_text(app)

    record = lines(without_toast)[-1].strip()
    assert record.startswith("同步失败"), f"状态栏留着那句话：{record!r}"
    assert any(
        "同步失败" in line and line.strip() != record for line in lines(with_toast)
    ), f"toast 里也该说一遍：\n{with_toast}"


async def test_toasts_are_painted_in_ansi_slots_like_everything_else():
    """toast 的颜色也要覆盖：Textual 自带的规则用 ``$success`` / ``$text-success``（真彩色）。"""
    app = DidaApp(backend(broken=True))

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause(0.2)
        emitted = screen_sgr(app)

    assert "38;2;" not in emitted, f"toast 把真彩色带回来了：\n{emitted[:400]}"
    assert "48;2;" not in emitted, f"toast 的底色是真彩色：\n{emitted[:400]}"
