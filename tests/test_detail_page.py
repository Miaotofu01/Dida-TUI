"""任务详细页：字段列表、折行、逐字段编辑、只读段（工单 #43 的验收标准）。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot，断的是
外部行为——「我在这一页按了这个键，屏幕上出现了什么」。接缝二是真引擎 + 真库 + 假传输，
用在「一次改动到底有没有立刻出去」上：那是网络这一侧的事实，替身说了不算。

不断言控件树、不断言内部状态对象、不断言渲染出来的空白。读的「里面」只有页面给外层的公开
口子 ``selected_id``（「光标停在哪个字段上」在屏幕上只能从 ``❯`` 那一列间接看出来）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
from textual import events
from textual._xterm_parser import XTermParser

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import NO_DUE_TEXT, DidaError, UnknownTaskError
from dida.sync.engine import SyncEngine

from rich.cells import cell_len

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui import messages, theme
from dida.tui.app import DidaApp
from dida.tui.keys import LAYER_DETAIL
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


def lines_with(text: str, needle: str) -> int:
    """屏幕上写着 ``needle`` 的行数（同一句话在两处出现时数得出来）。"""
    return sum(1 for line in text.splitlines() if needle in line)


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
    await pilot.press("right")
    await pilot.pause()
    await pilot.press("right")
    await pilot.pause()


# ------------------------------------------------------------------ 字段列表


async def test_enter_opens_the_detail_page_with_the_cursor_on_the_field_list():
    """任务列表页按 ``→`` 进详细页，光标落在字段列表上（验收标准 1 + 2）。

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


# ------------------------------------------------------------------ 底部那一行 + 立刻推送


async def test_the_bottom_line_always_says_saved_or_pending():
    """页面底部常驻「已保存」或「待推送（N）」（验收标准 8）。

    队列空着就是「已保存」，队列里还有改动就是「待推送（2）」——用户要能一眼看出刚才那一下
    到底出去没有（用户故事 65）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=(50, 14)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        saved = screen_text(app)
        fake.set_sync_state(last_refresh_at=T0, pending_count=2)
        app.update_status()
        await pilot.pause()
        pending = screen_text(app)

    assert messages.saved_message() in saved, f"队列空着时要写「已保存」：\n{saved}"
    assert messages.pending_message(2) in pending, f"有没推上去的改动时要写出来：\n{pending}"
    # 底部现在有**两行**贴在一起（这一页自己的一行 + app 的状态栏）。ADR-0007 实测过一次
    # 「两个 dock: bottom 挨着会吃掉汉字格」（「待推送」变成「待推 」），所以两行都要完整。
    assert lines_with(saved, messages.saved_message()) == 1, f"「已保存」只该有一处：\n{saved}"
    assert "待推送 0" in saved, f"状态栏那一份要在：\n{saved}"
    assert lines_with(pending, messages.pending_message(2)) == 1, f"这一页那一行只该有一处：\n{pending}"
    assert "待推送 2" in pending, f"状态栏那一份被吃掉了：\n{pending}"


async def test_the_bottom_line_is_still_there_when_the_page_is_long():
    """那一行是**常驻**的：正文长到要滚动时它也不跟着滚走（验收标准 8）。

    它钉在这一页自己的底边上（不是第三个 chrome 行）：正在改一段长描述的用户要能随时看见
    这一下出去没有。
    """
    app = DidaApp(backend(content=LONG_CONTENT * 3, desc=LONG_CONTENT))

    async with app.run_test(size=(50, 14)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press(*(["j"] * 6))  # 一路走到最后一个字段（正文早就超过一屏）
        await pilot.pause()
        text = screen_text(app)

    assert messages.saved_message() in text, f"滚动之后那一行不见了：\n{text}"
    assert field_row(text, "标签").startswith(theme.CURSOR_MARK), f"光标不在最后一个字段上：\n{text}"


async def test_every_field_is_pushed_the_moment_it_is_finished():
    """每改完一个字段**立刻推送**，不攒到离开这一页（验收标准 7）。

    断法：改完还在详细页上，推送**已经**发生过了（不是等 ``←`` 退回任务列表才发）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        before = fake.pushes
        await pilot.press("enter")
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"新标题")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)
        pushes = fake.pushes

    assert pushes > before, "改完一个字段没有立刻推一轮"
    assert has_field(text, "标签"), "推完还停在详细页上（没有为了推送而离开这一页）"
    assert "新标题" in field_row(text, "标题"), f"改完的标题不在屏上：\n{text}"


# ------------------------------------------------------------------ 接缝二：真引擎 + 假传输


class Unreachable:
    """假传输：每个请求都连不上（试「保存失败要说具体原因」）。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise httpx.ConnectError("连不上服务器")


class Recording:
    """假传输：把请求记下来并回一个 200（试请求体的形状）。

    「一个请求都没发出去」也要能断（#44 的非法日期那一条）：它只记，什么都不回放。
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"id": "t1"})


def real_app(tmp_path, transport) -> DidaApp:
    """接缝二：真引擎 + 真库 + 打给假服务端的真客户端。

    逐字段编辑的「立刻推送」与「保存失败说具体原因」是**网络这一侧**的事实：替身说了不算，
    所以这一条走真推送路径（这也是 ``test_app_sync.py`` 用的那一套）。

    这条任务的原文里带着**服务端给的、我们不认识的**字段（``focusSummaries``）与一份
    ``timeZone``：改期要断的「时区原样回写、陌生字段一起带回去」（#44 验收标准 7 / 8）只能
    在这种底稿上看见——底稿里本来就没有的东西，回写时当然也不会出现。
    """
    store = Store(tmp_path / "dida.sqlite3")
    store.apply_refresh(
        lists=[{"id": "work", "name": "工作", "sortOrder": 0}],
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": TITLE,
                "status": 0,
                "content": CONTENT,
                "desc": DESC,
                "dueDate": "2026-03-14T18:00:00+0800",
                "isAllDay": False,
                "timeZone": "Asia/Shanghai",
                "focusSummaries": [{"pomoCount": 1}],
            }
        ],
    )
    engine = SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )
    return DidaApp(engine)


async def test_a_change_that_cannot_be_pushed_says_why(tmp_path):
    """保存失败显示**具体错误**（验收标准 9、用户故事 81）。

    两件事一起断：这一笔**当场**就试着推出去了（还没离开详细页，更新请求已经发过一次），
    以及推不出去时那一行写的是引擎记下来的**那句话**——不是一个笼统的「保存失败」。
    """
    transport = Unreachable()
    app = real_app(tmp_path, transport)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")  # 改标题
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"新标题")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)
        method = transport.requests[0].method if transport.requests else None
        body = transport.requests[0].content if transport.requests else b""

    assert method == "POST", f"改完一个字段没有立刻推出去：{transport.requests}"
    assert "新标题".encode() in body, "推出去的请求体里没有改完的标题"
    assert messages.FIELD_SAVE_FAILED_PREFIX in text, f"没写「保存失败」：\n{text}"
    assert "连不上服务器" in text, f"保存失败没有带上具体原因：\n{text}"
    assert "新标题" in field_row(text, "标题"), f"本地那一份该照旧当场生效（乐观写）：\n{text}"


# ------------------------------------------------------------------ 只读的那几段


async def test_subtasks_reminders_and_repeat_are_read_only_but_visible():
    """子任务（含完成状态）、提醒（含触发时间）、重复都只读地看得见（验收标准 10–12）。

    只读显示不做成「一行暗字」：三种信息各自一行，子任务还带勾选状态（``☑`` / ``☐``，
    ``status == 1`` 才算完成——那是**子任务**那一对取值，与任务级那对不是一回事）。
    """
    fake = backend(
        raw={
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            "reminders": ["TRIGGER:P0DT9H0M0S"],
            "items": [
                {"id": "s1", "title": "收集数据", "status": 1},
                {"id": "s2", "title": "写结论", "status": 0},
            ],
        }
    )
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        subtasks = field_row(screen_text(app), "子任务")
        reminders = field_row(screen_text(app), "提醒")
        repeat = field_row(screen_text(app), "重复")

    assert theme.SUBTASK_DONE_MARK in subtasks and "收集数据" in subtasks, subtasks
    assert theme.SUBTASK_TODO_MARK in subtasks and "写结论" in subtasks, subtasks
    # 提醒读成人话、不摊服务端原文（#55；读法本身在 ``tests/test_reminders.py``）。
    assert "提前 9 小时" in reminders, reminders
    assert "RRULE:FREQ=DAILY;INTERVAL=1" in repeat, repeat


async def test_the_read_only_part_says_it_cannot_be_changed_here():
    """明确告知这几样在客户端里改不了（验收标准 13、用户故事 76–78）。

    光标也永远不停在它们上面：``enter`` 落不到一个改不了的东西上。
    """
    fake = backend(
        raw={
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            "reminders": ["TRIGGER:P0DT9H0M0S"],
            "items": [{"id": "s1", "title": "收集数据", "status": 1}],
        }
    )
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)
        walked = [app.detail_page().selected_id]
        for _ in range(6):
            await pilot.press("j")
            walked.append(app.detail_page().selected_id)

    assert messages.READ_ONLY_NOTE in text, f"没有一句话说清这几样改不了：\n{text}"
    assert walked == ["title", "content", "desc", "list", "due", "priority", "tags"], (
        f"光标停到了只读的行上：{walked}"
    )
    assert field_row(text, "子任务").startswith(theme.BLANK_MARK * 2), (
        f"只读的那几行不该有光标记号：{field_row(text, '子任务')!r}"
    )


async def test_an_empty_read_only_section_is_not_drawn_at_all():
    """只读那几段没有内容时整段不画（工单 #20 的结论，那一页的既有行为）。

    与三个自由文本字段的区别正是这一票要的：**描述空的也要留着**（那是一扇门），只读的
    没有内容就没什么可告知的。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)

    for label in ("子任务", "提醒", "重复"):
        assert not has_field(text, label), f"没有内容的「{label}」还占着一行：\n{text}"
    assert messages.READ_ONLY_NOTE not in text, "一段只读的都没有，那句说明也不该出现"


async def test_an_empty_field_keeps_its_place_so_it_can_be_written():
    """空的描述/备注照样留在字段列表里（画一句占位符），光标停得上去、进得去。

    这是「能加描述」这件事的前提：整行不画的话，一条没有描述的任务就永远写不上描述了。
    """
    fake = backend(content="", desc="")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        text = screen_text(app)
        await pilot.press("j")  # 描述
        await pilot.press("enter")
        await pilot.press(*"补一段描述")
        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert messages.EMPTY_FIELD_TEXT in field_row(text, "描述"), f"空描述没有占位符：\n{text}"
    assert fake.writes == [("t1", {"content": "补一段描述"})], f"空字段写不进去：{fake.writes}"
    assert "补一段描述" in field_row(after, "描述"), f"写完之后那一段没画出来：\n{after}"


# ------------------------------------------------------------------ 进出与剩下几条


async def test_left_on_the_field_list_goes_back_to_the_task_you_came_from():
    """字段列表上 ``←`` 退回任务列表页，光标还原到进来时那条任务（验收标准 3 + 4）。

    从**第二条**任务进去（不是第一条），回来时任务列表页的光标还得在它上面——这是 #34 那条
    规矩在详细页这一侧的延续：``←`` 出栈，不是把用户踢回列表第一行。
    """
    fake = backend()
    fake.add_task("另一条任务", list_name="work", id="t2")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(20):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("j")  # 光标移到第二条任务上
        await pilot.press("right")  # 进它的详细页
        await pilot.pause()
        in_detail = screen_text(app)
        await pilot.press("j", "j")  # 字段列表上走两格
        await pilot.press("left")  # 回任务列表页
        await pilot.pause()
        listed = screen_text(app)
        back_on = app.tasks_page().selected_id
        await pilot.press("right")  # 再进来一次
        await pilot.pause()
        again = screen_text(app)

    assert "另一条任务" in in_detail, f"进的不是第二条任务的详细页：\n{in_detail}"
    assert "另一条任务" in listed, f"没有回到任务列表页：\n{listed}"
    assert back_on == "t2", "回来时光标不在进来时那条任务上"
    assert "另一条任务" in field_row(again, "标题"), f"再进来时进的是另一条任务：\n{again}"


async def test_esc_on_the_field_list_stays_on_the_field_list():
    """字段列表上 ``esc`` **不是**「退回」：它只结束编辑，没有编辑可结束时什么都不做。

    退回只有一个键（``←``，验收标准 3 + 4）——误按 ``esc`` 不该把人带走。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        before = screen_text(app)

        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert after == before, "字段列表上按 esc 不该退回任务列表页"
    assert has_field(after, "标题"), "还该停在字段列表上"


async def test_left_and_right_move_the_caret_inside_a_text_field_instead_of_leaving_the_layer():
    """编辑自由文本时 ``←`` / ``→`` 归输入框（在文字里移光标），不把人弹出这一层（验收标准 5）。

    两条证据一起断：按完之后**还在编辑态**（层没动），而且接着那一下退格删掉的正是光标左边
    那一格——光标真的动了，不是被页面吃掉。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")  # 光标在标题上：进编辑
        await pilot.pause()
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"abcd")
        await pilot.press("left", "left")  # 光标退到 b 与 c 之间
        await pilot.press("backspace")  # 删掉 b
        await pilot.press("right")  # 光标回到末尾
        await pilot.press(*"X")
        await pilot.pause()
        still_editing = "编辑标题" in screen_text(app)
        on_the_detail_layer = app.layer == LAYER_DETAIL
        await pilot.press("escape")  # 结束这次编辑 → 交出去
        await pilot.pause()
        text = screen_text(app)

    assert still_editing, "文本框里的 ← / → 不该把人弹出编辑态"
    assert on_the_detail_layer, "文本框里的 ← / → 不该换层"
    assert fake.writes == [("t1", {"title": "acXd"})], (
        f"← / → 没有在文字里移动光标（写出去的是 {fake.writes}）"
    )
    assert "acXd" in field_row(text, "标题"), f"改动没有生效：\n{text}"


async def test_left_and_right_also_move_the_caret_in_a_multiline_field():
    """多行框（描述 / 备注）里同一条规矩：``←`` / ``→`` 移光标，不换层（验收标准 5）。

    单行框与多行框是**两个**控件（``Input`` / ``TextArea``），绑定表各有一份，所以两半
    都要真按一遍——只断单行那一半会把「多行框里按 ← 退回上一层」漏过去。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("j")  # 标题 → 描述
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(len(CONTENT)))
        await pilot.press(*"abcd")
        await pilot.press("left", "left")
        await pilot.press("backspace")  # 删掉 b
        await pilot.press("right")
        await pilot.press(*"X")
        await pilot.pause()
        still_editing = "编辑描述" in screen_text(app)
        assert app.layer == LAYER_DETAIL, "多行框里的 ← / → 不该换层"
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert still_editing, "多行框里的 ← / → 不该把人弹出编辑态"
    assert fake.writes == [("t1", {"content": "acXd"})], (
        f"多行框里的 ← / → 没有在文字里移动光标（写出去的是 {fake.writes}）"
    )
    assert "acXd" in field_row(text, "描述"), f"改动没有生效：\n{text}"


async def test_a_task_without_a_due_date_says_so_without_an_ambiguous_glyph():
    """没有截止时间那一格不用歧义宽度的字形（``—`` U+2014 换成不含糊的读法）。

    引擎给的是 ``sync.view.NO_DUE_TEXT``（``—``，东亚**歧义**宽度）：rich 量它 1 格，CJK
    字体下终端可能画 2 格。详细页按**屏幕行**算光标与滚动，一格之差就是一行之差。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(TITLE, list_name="work", id="t1", content=CONTENT, desc=DESC)
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        due = field_row(screen_text(app), "截止")

    assert theme.NO_VALUE in due, f"没有截止时间该读作「{theme.NO_VALUE}」：{due!r}"
    assert NO_DUE_TEXT not in due, f"引擎那个歧义宽度的记号漏到这一页上了：{due!r}"


async def test_a_multiline_note_keeps_its_line_breaks():
    """备注是多行文本（验收标准 6）：编辑器里的换行原样写出去、原样折行画出来。

    ``enter`` 在多行框里是换行（单行框里才是提交）——这正是键位表里 ``enter`` 的两半。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=(60, 24)) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("j", "j")  # 备注
        await pilot.press("enter")
        await pilot.press(*clear(len(DESC)))
        await pilot.press(*"第一行")
        await pilot.press("enter")  # 多行框里它是换行
        await pilot.press(*"第二行")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert fake.writes == [("t1", {"desc": "第一行\n第二行"})], f"换行没带出去：{fake.writes}"
    block = wrapped_block(text, "备注", "清单")
    assert "第一行" in block[0] and "第二行" in block[-1], f"两行没有各占一行：{block}"


# ------------------------------------------------------------------ 中文输入


COMMIT = "这是一次上屏的长句子一共十五个字"
"""一次输入法上屏的那一串：**超过四个汉字**（四个汉字是自测最容易被骗过去的长度）。"""
assert len(COMMIT) > 4


def test_a_long_cjk_commit_decodes_to_the_text_not_to_an_escape_sequence():
    """一次上屏十五个汉字：终端送上来的那段字节解出来就是原文（验收标准 14）。

    kitty 键盘协议关掉之后（``bootstrap.py`` 顶上那个开关，子进程检查在
    ``tests/test_bootstrap.py``），输入法提交的汉字就是一段普通的 UTF-8 文本，解析器逐字给出
    一个按键事件。开着那个协议时，一次上屏十几个汉字会被当成**一个**按键事件，解析器的
    32 字符阈值让它放弃匹配、把整串当按键重发——中文直接变乱码。短词没事，长句才乱。

    ⚠ 这条**不能**改成「喂一段 CSI-u 序列，断言字段拿到原文」：那是协议**开着**时的形状，
    关掉之后同一段序列解出来是空的（实测），断言会反过来。这里喂的是真终端在协议关闭时
    送的那串字节。真正的输入法是手测项（报告里写明了）。
    """
    parser = XTermParser()
    keys = [event for event in parser.feed(COMMIT) if isinstance(event, events.Key)]

    assert "".join(event.character or "" for event in keys) == COMMIT, (
        f"十四个汉字没有逐字解出来：{[event.character for event in keys]!r}"
    )


async def test_a_long_cjk_commit_lands_in_the_field_verbatim():
    """那一段原文落进字段就是原文（验收标准 14）：写出去的与屏幕上的逐字相同。

    走的是正常输入那条路（焦点在编辑器上，逐字进来），断的是字段里与屏幕上都没有转义序列
    的残渣——``[32;;`` / ``:30028u`` 这一类是乱码那一半的形状。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*COMMIT)
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert fake.writes == [("t1", {"title": COMMIT})], f"落进字段的不是原文：{fake.writes}"
    assert COMMIT in field_row(text, "标题"), f"屏幕上不是那一段原文：\n{text}"
    assert "[32;;" not in text and ":30028u" not in text, f"字段里留了转义序列：\n{text}"


async def test_a_refused_save_says_which_refusal_it_was():
    """引擎当场拒绝时说的是一句**说得出名字**的话（验收标准 9）。

    「本地已经没有这条任务的底稿」是四种失败里的一种，与断网、凭据失效、服务端拒绝要做的
    下一步完全不同（用户故事 81）。它不写成「保存失败」，写的是 ``messages`` 里那一句现成的
    话；而且这一下**确实没有改成**——屏幕上的标题原样还在。
    """
    fake = backend()
    fake.write_error = UnknownTaskError("t1")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"新标题")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert messages.UNKNOWN_TASK_MESSAGE in text, f"拒绝的原因没有说出来：\n{text}"
    assert TITLE in field_row(text, "标题"), f"没有改成的东西不该在屏上变成改成了：\n{text}"


async def test_a_save_failure_that_is_not_the_known_one_still_names_the_reason():
    """别的写失败也带上具体那一句（验收标准 9）：前缀之后是原因，不是一个句号。"""
    fake = backend()
    fake.write_error = DidaError("服务端说这个字段不行")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("enter")
        await pilot.press(*clear(len(TITLE)))
        await pilot.press(*"新标题")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert messages.field_save_failed_message("服务端说这个字段不行") in text, (
        f"保存失败没有带上具体原因：\n{text}"
    )


# ------------------------------------------------------------------ 截止时间（#44）


async def walk_to_the_due_field(pilot, app: DidaApp) -> None:
    """把光标走到「截止」那一行（标题 → 描述 → 备注 → 清单 → 截止）。

    不从标题一路 ``j`` 到别处：落点由 ``selected_id`` 认，走错了当场红——这一页的光标位置
    是 #43 的既有行为，不该由这一票的测试重新解释一遍。
    """
    for _ in range(6):
        if app.detail_page().selected_id == "due":
            return
        await pilot.press("j")
    raise AssertionError(f"光标走不到「截止」那一行：{app.detail_page().selected_id!r}")


def due_input(app: DidaApp) -> str:
    """编辑器里那两格当前的内容（日期 + 时刻）。"""
    return (
        app.detail_page().query_one("#due-date").value + " " + app.detail_page().query_one("#due-time").value
    )


async def test_the_due_field_opens_a_structured_date_then_time_editor():
    """``enter`` 落在「截止」上：进的是**结构化**编辑器，先日期、再时刻（验收标准 1）。

    断的是外部行为：按了 ``enter`` 之后屏幕上出现哪两格、光标先落在哪一格、两格里回填的是
    这条任务当前那一刻的日期与时刻（用户看着它改，而不是对着一片空白猜格式）。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)
        focused = app.focused.id if app.focused is not None else None
        prefilled = due_input(app)

    assert "2026-03-14" in prefilled, f"日期那一格没有回填当前那一天的日期：{prefilled!r}"
    assert "18:00" in prefilled, f"时刻那一格没有回填当前那一刻：{prefilled!r}"
    assert focused == "due-date", f"光标先落在日期那一格上：{focused!r}"
    assert "2026-03-14" in text, f"日期那一格没画在屏上：\n{text}"


async def test_typing_a_date_and_a_time_reschedules_the_task_to_that_moment():
    """先选日期、再选时刻，``enter`` 提交（验收标准 1）：改的就是那一刻。

    期望值由测试直接给出（``2026-03-15 09:30`` 带 +0800）：用户墙钟上的 09:30 就是写出去的
    09:30，没有被换算到别的时区。``all_day`` 是 ``False``——有具体时刻就是「不是全天」。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-03-15")
        await pilot.press("enter")  # 日期这一格提交 → 轮到时刻
        await pilot.press(*clear(5))
        await pilot.press(*"09:30")
        await pilot.press("enter")  # 时刻这一格提交 → 这一次改动出去
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled == ["t1"], f"改期没有走到引擎：{fake.rescheduled}"
    assert fake.rescheduled_due == [T0.replace(day=15, hour=9, minute=30)], (
        f"写出去的时刻不是用户敲的那一刻：{fake.rescheduled_due}"
    )
    assert fake.rescheduled_all_day == [False], "有具体时刻就不是全天"
    # 那一格的读法是引擎给的成品（「明天 09:30」），不是把用户敲的串原样抄上去——TUI 不自己
    # 拼日期（架构规则：日期判断与读法都在引擎里）。
    assert "明天 09:30" in field_row(text, "截止"), f"改完那一格没跟着变：\n{text}"


async def test_the_all_day_switch_flips_between_a_time_and_a_date_only():
    """「全天」开关在「有具体时刻」与「只有日期」之间切换（验收标准 2）。

    两条路都走一遍：先 ``x`` 打开全天（时刻那一格空着 → 写的是那一天的 00:00，``isAllDay``
    是 ``True``），再 ``x`` 关掉它（时刻那一格回来，写的是那一刻）。开关的当前档必须画在
    屏上——一个按下去看不出状态的开关等于没有开关。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-03-15")
        await pilot.press("x")  # 打开全天
        await pilot.pause()
        toggled_on = screen_text(app)
        time_hidden = not app.detail_page().query_one("#due-time").display
        await pilot.press("enter")  # 全天：日期一提交就完事，时刻那一格不用填
        await pilot.pause()
        after_all_day = screen_text(app)
        await pilot.press("enter")  # 重新进编辑器
        await pilot.pause()
        await pilot.press("x")  # 关掉全天
        await pilot.pause()
        toggled_off = screen_text(app)
        time_shown = app.detail_page().query_one("#due-time").display

    assert fake.rescheduled_due[0] == T0.replace(day=15, hour=0, minute=0), (
        f"全天写的是那一天 00:00 这个日期标记：{fake.rescheduled_due}"
    )
    assert fake.rescheduled_all_day == [True], f"全天那一档没写出去：{fake.rescheduled_all_day}"
    # 全天任务的读法是「明天」（引擎按 ``due.date()`` 读日期标记，不画 00:00）——那一格确实
    # 跟着变了，而且没有多出一个「00:00」来。
    assert "明天" in field_row(after_all_day, "截止"), f"全天那条没落到那一格上：\n{after_all_day}"
    assert "00:00" not in field_row(after_all_day, "截止"), field_row(after_all_day, "截止")
    assert time_hidden, "全天时时刻那一格该收起来（只有日期）"
    assert time_shown, "关掉全天时时刻那一格该回来（有具体时刻）"
    assert "全天" in toggled_on, f"开关的当前档没画在屏上：\n{toggled_on}"
    assert toggled_off != toggled_on, "开关按下去屏幕上没有任何变化"


async def test_clearing_the_date_turns_the_task_back_into_one_without_a_date():
    """日期那一格清空 = 清除截止时间（验收标准 3）：那一格回到「没有日期」。

    清除走的是 ``reschedule(due=None)`` 这一条明确的形状，不是一个很早的时刻——后者会在
    手机上出现一条 1970 年的任务。本地那一份也当场跟着变（乐观写）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press("enter")  # 日期那一格清空之后提交
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled_due == [None], f"清除没有走 due=None 这一条：{fake.rescheduled_due}"
    assert theme.NO_VALUE in field_row(text, "截止"), (
        f"清除之后那一格该读作「{theme.NO_VALUE}」：{field_row(text, '截止')!r}"
    )


async def test_a_date_that_is_not_a_real_day_is_refused_and_no_request_goes_out(tmp_path):
    """非法日期在**发出前**被本地拦下，请求根本不出门（验收标准 6）。

    接缝二（真引擎 + 真库 + 假传输）：「零请求」是网络这一侧的事实，替身说了不算。日期那一
    格写一个日历上不存在的日子（``2026-02-30``），提交之后：一个请求都没有，编辑器留在
    原地，屏上说出是哪一格不认。
    """
    transport = Recording()
    app = real_app(tmp_path, transport)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-02-30")
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)
        still_editing = app.detail_page().query_one("#due-date").display

    assert transport.requests == [], f"非法日期不该发出任何请求：{transport.requests}"
    assert "2026-02-30" in text, f"该说清是哪一格不认：\n{text}"
    assert still_editing, "拦下之后编辑器要留在原地，别把用户敲的东西丢掉"


async def test_an_empty_date_field_is_refused_instead_of_clearing_silently():
    """日期那一格**一个字都没敲过**时按 ``enter``：不放行，也不悄悄当成清除。

    「还没填」与「要清除」在请求体里长得一样（``dueDate: null``），而误按一次就把日期删掉
    是不可接受的（本地当场生效、立刻推送）。分界只能是「用户动过这一格没有」：这条测试
    走的是**从来没动过**那一支（一条本来就没有截止时间的任务），下面那条走「删掉里面的
    日期」那一支。
    """
    fake = backend()
    fake.add_task("没有日期的任务", list_name="work", id="t2")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("left")  # 回任务列表页
        await pilot.pause()
        await pilot.press("j")  # 光标到第二条（没有日期的那一条）
        await pilot.press("right")
        await pilot.pause()
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # 日期那一格空着，直接提交
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled == [], f"空日期不该当成清除：{fake.rescheduled_due}"
    assert "没改成" in text, f"拦下之后要说一句为什么：\n{text}"


async def test_deleting_the_date_that_was_there_is_a_clear_and_not_a_mistake():
    """把**已有的**日期删掉再提交 = 清除（与上一条成对）。

    同一个空值，两种意思：上一条是「还没填」（没动过那一格），这一条是「要清除」（用户把
    里面的日期删了）。这就是 ``_due_date_touched`` 存在的理由。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled_due == [None], f"删掉已有的日期该是清除：{fake.rescheduled_due}"
    assert theme.NO_VALUE in field_row(text, "截止"), field_row(text, "截止")


async def test_esc_in_the_due_editor_commits_the_typed_date_and_the_all_day_switch():
    """编辑中 ``esc`` 是**结束这次编辑**，改动已经生效（spec #30 用户故事 62：没有「取消」）。

    这一条正是复核量出来的那件事：打进去的日期与 ``x`` 的全天开关**一起无声消失**。断的是
    外部行为——按了 ``esc`` 之后引擎收到了那一刻，而且 ``isAllDay`` 是用户在编辑器里切到的
    那一档（开关不算进来的话，用户按出来的「全天」在这一下丢掉）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-03-15")
        await pilot.press("x")  # 全天
        await pilot.press("escape")  # 结束这次编辑 → 改动已经生效
        await pilot.pause()
        text = screen_text(app)
        landed_on = app.detail_page().selected_id

    assert fake.rescheduled == ["t1"], f"``esc`` 没有把这一刻写出去：{fake.rescheduled}"
    assert fake.rescheduled_due == [T0.replace(day=15, hour=0, minute=0)], (
        f"``esc`` 提交的不是编辑器里那一刻：{fake.rescheduled_due}"
    )
    assert fake.rescheduled_all_day == [True], (
        f"``x`` 的全天开关在 ``esc`` 上丢了：{fake.rescheduled_all_day}"
    )
    assert "明天" in field_row(text, "截止"), f"``esc`` 之后那一格没跟着变：\n{text}"
    assert landed_on == "due", f"``esc`` 之后光标该回到字段列表上：{landed_on!r}"


async def test_esc_on_the_date_step_commits_that_date_with_the_time_the_cell_shows():
    """只敲完日期就按 ``esc``：提交的是**屏幕上那两格**，不是「走到第 2 步」。

    用户看到的时刻那一格是回填的原值（``18:00``），所以提交的那一刻就是那一天 18:00——
    与他在时刻那一格上按 ``enter`` 得到的是同一件事。``esc`` 不该替用户停下来等第二步，
    也不该把整份草稿丢掉（那正是复核量到的行为）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-03-16")
        await pilot.press("escape")  # 没有走到时刻那一步，也不按 enter
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled_due == [T0.replace(day=16, hour=18, minute=0)], (
        f"``esc`` 提交的不是那两格里的那一刻：{fake.rescheduled_due}"
    )
    assert fake.rescheduled_all_day == [False], f"没切全天就不是全天：{fake.rescheduled_all_day}"
    # 那一格的读法是引擎给的成品：两天后的任务读作「2 天后」（今天 03-14，落点是 03-16）。
    assert "2 天后" in field_row(text, "截止"), (
        f"``esc`` 之后那一格没跟着变：{field_row(text, '截止')!r}"
    )


async def test_esc_with_a_date_the_calendar_does_not_have_writes_nothing_and_stays(tmp_path):
    """``esc`` 撞上认不出来的日期：**一个字都不写**，编辑器也不收起（工单 #58 的 S3）。

    「没有取消」不等于「什么都吞下去」：``2026-02-30`` 不是一个日历日，于是这一下既不写
    垃圾（一个请求都不出门），也不是悄悄什么都没做——下面那一行说清是哪一格不认，编辑器
    留在原地让用户改。旧行为是**悄悄收起**：用户以为改动生效了，其实什么都没发生。

    接缝二（真引擎 + 真库 + 假传输）：「一个请求都没有」是网络这一侧的事实，替身说了不算。
    """
    transport = Recording()
    app = real_app(tmp_path, transport)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press(*"2026-02-30")
        await pilot.press("escape")  # 结束这次编辑 → 这一格不认，于是什么都不写
        await pilot.pause()
        text = screen_text(app)
        still_editing = app.detail_page().query_one("#due-date").display

    assert transport.requests == [], f"认不出来的日期不该发出任何请求：{transport.requests}"
    assert "2026-02-30" in text, f"该说清是哪一格不认：\n{text}"
    assert still_editing, "拦下之后编辑器要留在原地，别把用户敲的东西丢掉"


async def test_esc_on_an_empty_untouched_date_ends_the_edit_and_says_why():
    """日期那一格从来没被碰过就按 ``esc``：**结束这次编辑**，并说出为什么一个字都没写。

    「没有取消」不等于「必须写一笔」：用户什么都没改过，于是没有改动可生效。编辑器照规矩
    收起、回到字段列表（编辑器里 ``esc`` 的唯一含义就是这个），而下面那一行说明为什么——
    不能把这一下当成**清除**：「还没填」与「要清除」在请求体里是同一个空值，而清除要求
    用户真的动过那一格（``_due_date_touched``）。旧行为是悄悄收起：既没写、也没说。
    """
    fake = backend()
    fake.add_task("没有日期的任务", list_name="work", id="t2")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await pilot.press("left")  # 回任务列表页
        await pilot.pause()
        await pilot.press("j")  # 光标到第二条（没有日期的那一条）
        await pilot.press("right")
        await pilot.pause()
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")  # 两格都是空的，而且谁都没碰过
        await pilot.pause()
        text = screen_text(app)
        back_on_the_fields = has_field(text, "截止")

    assert fake.rescheduled == [], f"没碰过的空日期不该被当成清除：{fake.rescheduled_due}"
    assert "没改成" in text, f"一个字都没写就要说一句为什么：\n{text}"
    assert back_on_the_fields, f"``esc`` 该结束这次编辑、回到字段列表：\n{text}"


async def test_esc_after_deleting_the_date_that_was_there_is_a_clear():
    """把已有的日期删掉再按 ``esc`` = 清除（与「没碰过」那一条成对）。

    用户做过「删掉那个日期」这个明确动作，所以 ``esc`` 交出去的就是清除这一笔
    （``dueDate: null``）——与他在时刻那一格上按 ``enter`` 得到的是同一笔。``all_day``
    跟着写 ``False``：不留「全天、但没有日期」这个自相矛盾的一格。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))  # 把里面那个日期删掉
        await pilot.press("escape")  # 结束这次编辑 → 清除这一笔出去
        await pilot.pause()
        text = screen_text(app)

    assert fake.rescheduled_due == [None], f"删掉已有的日期再按 esc 该是清除：{fake.rescheduled_due}"
    assert fake.rescheduled_all_day == [False], f"清除不留「全天、但没有日期」：{fake.rescheduled_all_day}"
    assert theme.NO_VALUE in field_row(text, "截止"), field_row(text, "截止")


async def test_the_due_editor_takes_the_zone_from_the_injected_clock_not_the_machine():
    """任务还没有截止时间时，用户敲的墙钟按**注入的钟**那个时区理解（工单 #58 的 T1）。

    README 与架构文档的硬规则：「现在」只能来自注入的时钟，业务代码不许调 ``datetime.now()``
    ——这一页曾经用它查本地时区，于是写出去的那一刻跟着**跑测试的机器**走。替身这只钟故意
    摆在 -05:00（与这台机器的 +08:00 不同）：页面要是还去读真实时钟，那一刻会带 +0800。

    只有「本来没有截止时间」的任务才需要这个时区——有截止时间时那一刻自己带着 offset
    （``due.due_change`` 的 ``reference``），所以这条任务的日期不能有。
    """
    else_where = timezone(timedelta(hours=-5))
    fake = FakeBackend(clock=ManualClock(datetime(2026, 3, 14, 12, 3, tzinfo=else_where)))
    fake.add_list("工作", id="work")
    fake.add_task("没有日期的任务", list_name="work", id="t2")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*"2026-03-20")
        await pilot.press("enter")  # 日期这一格 → 轮到时刻
        await pilot.pause()
        await pilot.press(*"08:15")
        await pilot.press("enter")  # 时刻这一格 → 这一笔出去
        await pilot.pause()

    assert fake.rescheduled_due == [datetime(2026, 3, 20, 8, 15, tzinfo=else_where)], (
        f"写出去的那一刻没跟着注入的钟走：{fake.rescheduled_due}"
    )


async def test_esc_with_an_incomplete_time_writes_nothing_and_stays():
    """``esc`` 撞上认不出来的时刻：与认不出来的日期同一套（草稿第二种状态的另一半）。

    ``18`` 不是一个 ``HH:MM``，而用户可能正打到一半。这一下既不该替他把 ``18`` 猜成 18:00
    （「绝不猜」是这一页的既定口径），也不该悄悄收起——编辑器留在原地，下面那一行说清是
    哪一格不认。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # 日期这一格提交 → 轮到时刻
        await pilot.pause()
        await pilot.press(*clear(5))  # 清掉回填的 18:00
        await pilot.press(*"18")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)
        still_editing = app.detail_page().query_one("#due-time").display

    assert fake.rescheduled == [], f"认不出来的时刻不该写出去：{fake.rescheduled_due}"
    assert "不是一个时刻" in text, f"该说清是时刻那一格不认：\n{text}"
    assert still_editing, "拦下之后编辑器要留在原地，别把用户敲的东西丢掉"


async def test_the_due_editor_writes_out_an_explicit_null_shape(tmp_path):
    """清空日期那一笔请求体的形状：显式 ``dueDate: null``（验收标准 3 + 9，接缝二）。

    与 ``test_engine_writes`` 里那条形状测试是同一个事实的两个入口：那一条从引擎进，这一条
    从**界面**进（用户清了那一格），两条都要求请求体里写着显式的 null——「省略这个字段」
    在文档没说的省略语义下可能是「别动它」，那用户按了清除却什么都没发生。
    """
    transport = Recording()
    app = real_app(tmp_path, transport)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*clear(10))
        await pilot.press("enter")
        await pilot.pause()

    assert len(transport.requests) == 1, f"清除该只发一笔：{transport.requests}"
    body = json.loads(transport.requests[0].content)
    assert body["dueDate"] is None, body
    assert body["isAllDay"] is False, body
    assert body["timeZone"] == "Asia/Shanghai", "时区字段原样回写（验收标准 7）"
    assert body["focusSummaries"] == [{"pomoCount": 1}], "服务端给的陌生字段一起回去（验收标准 8）"


async def test_the_due_editor_emits_no_truecolor():
    """截止时间编辑器里**一格真彩色都没有**（工单 #44；ADR-0007 的第一条决定）。

    这是 #43 的 merger 在真 pty 里手工数出来、而当时**没有任何测试守着**的那件事：新挂一个
    Textual 组件（``Input`` / ``TextArea`` / ``Select``……）默认从 ``$surface`` / ``$boost`` /
    ``$input-cursor-*`` 那几个主题变量上色，那些值在 ``textual-dark`` 下是**真彩色**
    （``#1E1E1E`` 这种）。漏覆盖的表现是真终端收到 ``38;2;`` / ``48;2;``，而
    **屏幕上看不出异常**——只有读 SGR 才发现。

    所以这一条断的是**渲染字节**（``screen_sgr``：拿一个 ``color_system="truecolor"`` 的控制台
    去渲染当前这一屏），覆盖的是编辑器**整屏**：那两格、提示行、以及页面上其余一切。
    断言按**参数**看，不比整串——Textual 把前景与背景拼进同一条序列（``\\x1b[36;49m``）。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to_the_due_field(pilot, app)
        await pilot.press("enter")
        await pilot.pause()
        editing = screen_sgr(app)
        await pilot.press("x")  # 连「全天」那一档也渲染一遍
        await pilot.pause()
        toggled = screen_sgr(app)

    for label, emitted in (("日期/时刻两格", editing), ("全天那一档", toggled)):
        parameters = sgr_parameters(emitted)
        assert 38 not in {p for p in parameters if p >= 38 and p <= 48}, (
            f"{label}漏了真彩色前景色（38;2;…）：{sorted(parameters)}"
        )
        assert "38;2;" not in emitted, f"{label}里有真彩色前景色：{emitted[:200]!r}"
        assert "48;2;" not in emitted, f"{label}里有真彩色背景色：{emitted[:200]!r}"
