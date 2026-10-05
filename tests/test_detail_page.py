"""任务详细页：字段列表、折行、逐字段编辑、只读段（工单 #43 的验收标准）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot，断的是
外部行为——「我在这一页按了这个键，屏幕上出现了什么」。接缝二是真引擎 + 真库 + 假传输，
用在「一次改动到底有没有立刻出去」上：那是网络这一侧的事实，替身说了不算。

不断言控件树、不断言内部状态对象、不断言渲染出来的空白。读的「里面」只有页面给外层的公开
口子 ``selected_id``（「光标停在哪个字段上」在屏幕上只能从 ``❯`` 那一列间接看出来）。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from rich.cells import cell_len

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui import messages, theme
from dida.tui.app import DidaApp
from support import screen_sgr, screen_styled_text, screen_text

SGR = re.compile(r"\x1b\[[0-9;]*m")
"""ANSI 的 SGR 序列：样式在屏幕文本里就长这样。"""


def sgr_parameters(emitted: str) -> set[int]:
    """这条屏幕文本里出现过的每一个 SGR 参数（``\x1b[36;49m`` → ``{36, 49}``）。

    不断整串：Textual 把同一格的前景与背景拼进**一条**序列，所以「是不是 ANSI 6」要按参数
    看，不按整串看（``tests/test_visual_identity.py`` 的那条教训）。
    """
    out: set[int] = set()
    for group in SGR.findall(emitted):
        out.update(int(part) for part in group[2:-1].split(";") if part)
    return out

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


def backend(*, content: str = CONTENT, desc: str = DESC, **extra: object) -> FakeBackend:
    """一份够用的缓存：一条有全部字段的任务（含只读的那三段）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(
        TITLE,
        list_name="work",
        id="t1",
        due=T0.replace(hour=18, minute=0),
        priority=5,
        content=content,
        desc=desc,
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


def has_field(text: str, label: str) -> bool:
    """屏幕上有没有 ``label`` 那个字段行（编辑态里字段列表是收起来的）。"""
    return any(line[2:].startswith(label) for line in text.splitlines())


def field_row(text: str, label: str) -> str:
    """字段列表里 ``label`` 那一行：行首那两格是光标记号，所以从第三格认起。

    「清单」这种词在顶栏的面包屑里也有（「▸ 清单列表页」），按子串找会找错行。
    """
    for line in text.splitlines():
        if line[2:].startswith(label):
            return line
    raise AssertionError(f"字段列表里没有「{label}」那一行：\n{text}")


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
    assert field_row(text, "标题").startswith(theme.CURSOR_MARK), "光标记号在标题那一行"


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
    assert field_row(bottom, "标签").startswith(theme.CURSOR_MARK), (
        f"光标走到头时不在标签那一行上：\n{bottom}"
    )
    assert field_row(text, "截止").startswith(theme.CURSOR_MARK), (
        f"退回两下之后光标不在截止那一行上：\n{text}"
    )
    assert not field_row(text, "备注").startswith(theme.CURSOR_MARK), (
        "光标不该停在折行块（描述/备注）里面"
    )


# ------------------------------------------------------------------ 折行（这一页的一半）


def wrapped_block(text: str, label: str, next_label: str) -> list[str]:
    """屏幕上从 ``label`` 那一行到 ``next_label`` 之前的那一段（折行块）。"""
    lines = text.splitlines()
    return lines[slice(*block_span(text, label, next_label))]


def block_span(text: str, label: str, next_label: str) -> tuple[int, int]:
    """那一段折行块在屏幕上是第几行到第几行（含头不含尾）。"""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line[2:].startswith(label))
    end = next(
        index for index, line in enumerate(lines) if line[2:].startswith(next_label) and index > start
    )
    return start, end


async def test_a_long_description_wraps_and_is_readable_to_the_end():
    """详细页的字段值**换行**，不截断（工单评论的渲染规则：详细页读完整的）。

    列表页那两页宁可裁断（一行一条、要能扫视），详细页存在的意义就是「来这里读完整的」。
    断法：那一段正文的每一个字都在屏上（把折行拼回去正好是原文），而且行尾没有裁断记号。
    """
    app = DidaApp(backend(content=LONG_CONTENT))

    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)

    block = wrapped_block(text, "描述", "备注")
    assert len(block) > 1, f"这一段描述没有被折行（60 列下一行放不下）：\n{text}"
    joined = "".join(part.strip() for part in block).removeprefix("描述")
    assert joined == LONG_CONTENT, f"折行之后正文变了：{joined!r}"
    assert theme.ELLIPSIS not in "".join(block), "折行不是截断：这里不该有省略号"
    for line in block:
        assert cell_len(line) <= 60, f"这一行超出了终端宽度：{line!r}"


async def test_the_selected_style_covers_the_whole_wrapped_block():
    """光标那个字段的样式盖住**整个折行块**，不是只盖第一行（工单评论的实现后果 3）。

    只盖第一行的话，一个五行高的描述看起来像「只选中了半个字段」。断的是强调色那个 SGR
    参数（ANSI 6）出现在这一段的**每一行**上，而别的字段一行都没有。
    """
    app = DidaApp(backend(content=LONG_CONTENT))

    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("j")  # 光标移到描述上
        styled = screen_styled_text(app).splitlines()
        text = screen_text(app)

    start, end = block_span(text, "描述", "备注")
    assert end - start > 1, f"这一段描述没有被折行，这条断言就失去意义了：\n{text}"
    assert len(styled) == len(text.splitlines()), "两份屏幕文本的行数对不上"
    for line in styled[start:end]:
        assert 36 in sgr_parameters(line), f"折行块里有一行没有强调色：{line!r}"
    # 别的字段一行都不该带上强调色（光标只有一个）。顶栏的词标也是强调色，所以这里只查
    # 字段列表里那几行，不查整屏。
    plain = text.splitlines()
    for label in ("标题", "备注", "清单", "截止", "优先级", "标签"):
        index = next(i for i, line in enumerate(plain) if line[2:].startswith(label))
        assert 36 not in sgr_parameters(styled[index]), f"{label} 那一行不该有强调色"


@pytest.mark.parametrize("width", [30, 60, 100])
async def test_a_narrow_terminal_still_walks_field_to_field(width: int):
    """窄终端（30 列）下这一页照样成立（spec：「我要在窄终端里也能用」）。

    30 列下描述要折五六行，字段却还是七个：光标一下一个字段，屏幕上那个记号永远在标签
    那一行的开头，正文一个字都不裁。
    """
    app = DidaApp(backend(content=LONG_CONTENT))

    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        walked = [app.detail_page().selected_id]
        for _ in range(3):
            await pilot.press("j")
            walked.append(app.detail_page().selected_id)
        text = screen_text(app)

    assert walked == ["title", "content", "desc", "list"], f"@{width} 列下 j 的落点变了：{walked}"
    assert field_row(text, "清单").startswith(theme.CURSOR_MARK), f"@{width}：\n{text}"
    assert theme.ELLIPSIS not in text, f"@{width} 列下详细页还在截断：\n{text}"


async def test_the_cursor_scrolls_into_view_by_screen_lines():
    """正文比一屏高时，光标走到底要**看得见**（屏幕行算的滚动，不是行号）。

    一个字段不再是「一屏一行」：折行块让「第几个字段」与「第几屏行」分家，按行号滚就会停在
    半路——最后那几段永远不在屏上。
    """
    app = DidaApp(backend(content=LONG_CONTENT * 3, desc=LONG_CONTENT * 2))

    async with app.run_test(size=(50, 14)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        for _ in range(6):  # 一路走到最后一个字段
            await pilot.press("j")
        await pilot.pause()
        landed = app.detail_page().selected_id
        text = screen_text(app)

    assert landed == "tags"
    assert field_row(text, "标签").startswith(theme.CURSOR_MARK), (
        f"光标走到最后一个字段时它不在屏上：\n{text}"
    )


async def sample_while(action, project) -> list:
    """一边按键一边**逐帧**取一个观测量（动效只有并发采样的人看得见）。

    ``pilot.press`` 会等到这一屏静下来，所以按完再抓屏只能看到落定后的样子。
    """
    frames: list = []

    async def sample() -> None:
        while True:
            frames.append(project())
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample())
    try:
        await action()
    finally:
        sampler.cancel()
    return frames


def bar_row(app: DidaApp) -> int | None:
    """屏幕上那块**强调色实心**（装饰光标条）在第几行；它退场了就是 ``None``。"""
    for index, line in enumerate(screen_sgr(app).splitlines()):
        if 46 in sgr_parameters(line):
            return index
    return None


async def test_the_travelling_cursor_bar_flies_to_the_wrapped_blocks_own_line():
    """装饰光标条按**屏幕行**落位：一个字段占几行，条子就停到它那一行上。

    条子排的位置是「正文有多高」（折行之后是屏行总数，不是字段个数）。按字段个数算的话，
    它会整整差出前面那几段折行的行数——落点在描述块**内部**，用它那两个空格盖掉正文。
    这里一直盯着那块实心在第几行：飞行的最后一帧必须落在备注那一行上。
    """
    app = DidaApp(backend(content=LONG_CONTENT), animations="on")

    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("j")  # 标题 → 描述（一段折行块）
        await pilot.pause(0.3)  # 条子退场，落定
        frames = await sample_while(lambda: pilot.press("j"), lambda: bar_row(app))
        await pilot.pause(0.3)
        text = screen_text(app)

    targets = [row for row in frames if row is not None]
    assert targets, f"整段飞行里一帧都没看见那条实心：{frames}"
    noted = next(
        index
        for index, line in enumerate(text.splitlines())
        if line[2:].startswith("备注")
    )
    assert targets[-1] == noted, (
        f"条子落到了第 {targets[-1]} 行，而备注在第 {noted} 行——它停在折行块里了：\n{text}"
    )
    assert min(targets) >= noted - len(wrapped_block(text, "描述", "备注")), "起飞的落点太靠上了"
    assert field_row(text, "备注").startswith(theme.CURSOR_MARK), f"记号不在备注那一行：\n{text}"
    block = "".join(part.strip() for part in wrapped_block(text, "描述", "备注"))
    assert block.removeprefix("描述") == LONG_CONTENT, f"折行块被条子吃掉了几个字：{block!r}"


# ------------------------------------------------------------------ 逐字段编辑


def clear(length: int) -> list[str]:
    """清掉输入框里那 ``length`` 个字（没有「全选」，所以一下一下退）。"""
    return ["backspace"] * length


async def test_enter_edits_the_title_and_esc_finishes_the_edit():
    """``enter`` 进当前字段的编辑，编辑中 ``esc`` 结束并回到字段列表（验收标准 3 + 4）。

    「改动已经生效、没有取消」：``esc`` 之后屏幕上就是新标题，而不是问一句要不要保存。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")  # 光标在标题上：进编辑
        await pilot.pause()
        editing = screen_text(app)
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"新的标题")
        await pilot.press("escape")  # 结束这次编辑
        await pilot.pause()
        finished = screen_text(app)

    assert "编辑标题" in editing, f"编辑态没有说清在改哪个字段：\n{editing}"
    assert not has_field(editing, "描述"), f"编辑态还画着字段列表：\n{editing}"
    assert field_row(finished, "标题").startswith(theme.CURSOR_MARK), (
        f"``esc`` 之后光标没有回到字段列表上：\n{finished}"
    )
    assert "新的标题" in field_row(finished, "标题"), f"改动没有生效：\n{finished}"


async def test_an_empty_title_is_not_accepted_and_the_old_one_stays():
    """空标题不被接受（验收标准 6）：旧标题原样还在，下面那一行说清是哪条规矩。

    「没有取消」不等于「退不出去」：清空标题再按 ``esc`` 时这次编辑照样结束，只是那一下
    **没有改**——否则用户会卡在一个按什么都出不去的编辑态里。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")
        await pilot.press(*clear(len(TITLE)))
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert has_field(text, "标题"), f"编辑没有结束：\n{text}"
    assert TITLE in field_row(text, "标题"), f"旧标题该原样留着：\n{text}"
    assert messages.NO_TITLE_EDIT_MESSAGE in text, f"没说清为什么没改成：\n{text}"
    assert fake.writes == [], "空标题不该写出去"


async def test_description_and_note_are_edited_independently():
    """描述与备注各自可改、互不覆盖（验收标准 6）：一笔写只带一个字段。

    两笔写各带各的服务端字段名（``content`` / ``desc``）——这就是「改一个不会覆盖另一个」
    在接口上的样子；屏幕那一头是两行各显示各的。
    """
    fake = backend()
    app = DidaApp(fake)
    # 只用汉字：``pilot.press`` 会把单字符的**标点**当成键名去查（``：`` → ``colon``），
    # 那是测试驱动的脾气，不是真实输入那条路（真终端送的是 UTF-8 字节）。
    new_content = "换过的描述三件结论"
    new_desc = "换过的备注先问财务"

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("j")  # 描述
        await pilot.press("enter")
        await pilot.press(*clear(len(CONTENT)))
        await pilot.press(*new_content)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("j")  # 备注
        await pilot.press("enter")
        await pilot.press(*clear(len(DESC)))
        await pilot.press(*new_desc)
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert fake.writes == [("t1", {"content": new_content}), ("t1", {"desc": new_desc})], (
        f"两笔写没有各带各的字段：{fake.writes}"
    )
    assert new_content in field_row(text, "描述"), f"描述没改成：\n{text}"
    assert new_content not in field_row(text, "备注"), "改描述把备注盖掉了"
    assert new_desc in field_row(text, "备注"), f"备注没改成：\n{text}"
    assert new_desc not in field_row(text, "描述"), "改备注把描述盖掉了"
