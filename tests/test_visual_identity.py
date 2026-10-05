"""视觉地基里「看得见的那一半」：顶栏、平移、会追赶的光标条、转圈、toast（工单 #51）。

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

from dida.sync.engine import DidaError
from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp
from support import screen_sgr, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

SGR = re.compile(r"\x1b\[[0-9;]*m")
"""ANSI 的 SGR 序列：样式在屏幕文本里就长这样。"""


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘掉 shell 的 ``NO_COLOR``：否则 ``App`` 会挂一层 Monochrome，颜色断言全部假绿。"""
    monkeypatch.delenv("NO_COLOR", raising=False)


def backend(*, pending: int = 0, slow: float = 0.0, broken: bool = False) -> FakeBackend:
    """一份够用的缓存；``slow`` / ``broken`` 用来演「同步慢」与「同步失败」。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work", group_id="g1")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18))
    fake.add_task("交水费", list_name="work", id="t2")
    fake.add_list("生活", id="life", group_id="g1")
    fake.add_list("笔记本", id="note", kind="NOTE")
    fake.set_sync_state(last_refresh_at=T0, pending_count=pending)
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
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ 顶栏与 chrome


async def test_the_top_bar_says_where_you_are_and_the_footer_is_gone():
    """顶栏说「你在哪」：词标 + 导航路径；**Footer 已去掉**，键位归按层的 ``?``。

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
        await pilot.press("enter")
        await pilot.pause()
        inside = lines(screen_text(app))[0]
        assert "清单列表页" in inside and "工作" in inside, "进了清单，路径上要多一段"
        assert inside.count("▸") == 2, "两段路径"

        await pilot.press("enter")  # 进第一条任务的详细页
        await pilot.pause()
        detail = lines(screen_text(app))[0]
        assert detail.count("▸") == 3, "第三层：三段路径"
        assert "写周报" in detail, "最后一段就是这条任务"

        await pilot.press("escape")
        await pilot.pause()
        back = lines(screen_text(app))[0]

    assert back.count("▸") == 2, "esc 出栈，路径跟着退回去"
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
        await pilot.press("enter")  # 层三标题下面也有一条细线
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
    标题是个算式：页面有 ``width`` 格，行首两格归光标，裁断记号自己占一格，所以标题看得见
    的部分正好是前 ``width - 3`` 格。汉字那条也在（他的 locale 是 ``zh_CN.UTF-8``）：一个
    汉字两格，断在哪一格按格算，不按字符数。
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
    assert ascii_row == f"❯ {ascii_title[: width - 3]}{theme.ELLIPSIS}", (
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
            lambda: pilot.press("enter"), lambda: heading_column(screen_text(app))
        )
        await pilot.pause(0.3)
        settled = screen_text(app)

    assert any(offset > 0 for offset in offsets), f"没有一帧是「滑进来」的样子：{offsets}"
    assert len(set(offsets)) > 2, f"中间帧应当是一格一格滑过来的：{offsets}"
    assert heading_column(settled) == 0, "落定之后要正好对齐在最左边"


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
            lambda: pilot.press("enter"), lambda: task_offset(screen_text(app))
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
                lambda: pilot.press("enter"), lambda: offset(screen_text(app))
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

    ``pages/base.py`` 的 ``_motion`` 是对的做法（那里第 130 行起的文档就写着这个坑）；这条
    顺手把三张页面也真按一次，不靠「以为它是干净的」。

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


# ------------------------------------------------------------------ 会追赶的光标条


async def test_the_cursor_marker_survives_the_travelling_bar():
    """光标条是**装饰**：它飞过去、到站退场，``❯`` 与标题一个字都不能少。

    原型里这条没有 ``on_complete``，条子就永远停在落点，用自己那两个空格盖住 ``❯``——
    用户报的是「上下键选中的行会消失」。这条测试盯的就是那个报告。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        before = line_with(screen_text(app), "写周报")
        await pilot.press("j")
        instant = line_with(screen_text(app), "交水费")
        await pilot.pause(0.4)  # 远超过 120ms：条子早就该退场了
        after = line_with(screen_text(app), "交水费")

    assert before.startswith("❯"), "进清单时光标在第一条任务上"
    assert instant.startswith("❯"), "数据选中必须**立刻**到位，不能等装饰条"
    assert after.startswith("❯"), "条子飞过之后，光标记号还得在"
    assert "交水费" in after, "条子不许盖住标题"
    assert after.lstrip().startswith("❯ 交水费"), "条子退场之后那一行与平时一模一样"


async def test_the_travelling_bar_is_a_short_lived_accent_block():
    """飞行途中屏幕上多一块**强调色实心**（SGR 46 = 青底），到站之后它必须消失。

    「必须消失」就是那个 ``on_complete``：原型里漏了它，条子会永远停在落点。
    """
    app = DidaApp(backend(), animations="on")

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        frames = await sample_while(
            lambda: pilot.press("j"),
            lambda: 46 in sgr_parameters(screen_sgr(app)),
        )
        await pilot.pause(0.2)
        landed = sgr_parameters(screen_sgr(app))

    assert any(frames), f"飞行途中该有一块强调色实心的条子在动：{frames}"
    assert 46 not in landed, "到站之后条子要退场：屏幕上不再多任何东西"


# ------------------------------------------------------------------ 同步转圈


async def test_a_fast_sync_never_shows_a_spinner():
    """快同步什么都不显示——本地缓存那一屏不该有个东西一直在转。"""
    app = DidaApp(backend(pending=1))

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
    app = DidaApp(backend(pending=1))

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
