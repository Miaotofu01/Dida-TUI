"""自定义视图在界面上的样子（工单 #36）：``n`` 先问一句、``e`` 改条件、``d`` 删掉。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（内存缓存 + 真引擎的读路径）+ Pilot。断的是
外部行为：「我在清单列表页上按了这个键，屏幕上 / 假后端那里看见了什么」。

那张表单是 #42 搭的**共用壳子**（字段是参数），这一组不重测壳子自己的键位（那些在
``tests/test_list_overlay.py``），只测视图这一侧的字段集与三条键的落点。

「视图只存在本机」是这一屏最要紧的一句实话（ADR-0005：API 没有「保存一组过滤条件」这个
接口），所以它有一条自己的断言；「删视图不删任务」也在这里再断一次——用户按 ``y`` 的那一
刻，屏幕上与缓存里都得看得出来任务一条都没少。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui import messages
from dida.tui.app import DidaApp
from dida.tui.pages.index import BUILTIN_MARK, CUSTOM_MARK, LIST_MARK
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够（与 #42 那张浮层测试同一个）。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def backend() -> FakeBackend:
    """一份够用的缓存：两个清单、四条任务（高 / 中 / 高 / 已完成）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_list("生活", id="life")
    fake.add_task("交报告", list_name="work", due=at(15, 18), priority=5)
    fake.add_task("整理桌面", list_name="work", priority=3)
    fake.add_task("买牛奶", list_name="life", priority=5)
    fake.add_task("做完的", list_name="life", completed=True, completed_at=at(13, 9))
    return fake




def field_value(text: str, label: str) -> str:
    """表单里 ``label`` 那个字段的**控件**上写着什么（与 #42 那张测试同一个口子）。

    只在**浮层的框里**找（框里每一行都以 ``|`` 起头）。浮层是画在下面那一层**上面**的，
    而下面那一层上也可能写着同名的字——视图叫「高优先级未完成」时，清单列表页那一行里
    就有「优先级」三个字，按整屏找会先撞上它。
    """
    lines = [line for line in text.splitlines() if line.strip().startswith("|")]
    # 字段名**独占一行**（框里那一行去掉边框就是它）：按整行相等找，不按子串找——
    # 视图叫「高优先级未完成」时，名字那一格的**值**里就有「优先级」三个字。
    index = next((i for i, line in enumerate(lines) if line.strip(" |") == label), None)
    if index is None:
        raise AssertionError(f"屏幕上没有「{label}」这个字段：\n{text}")
    for following in lines[index + 1 :]:
        content = following.strip(" |")
        if not content or set(content) <= set("+-"):
            continue
        return content
    raise AssertionError(f"「{label}」这个字段下面什么都没有：\n{text}")


def row_of(text: str, name: str) -> str:
    """屏幕上写着 ``name`` 的那一行（清单 / 视图行）。"""
    for line in text.splitlines():
        if name in line and any(mark in line for mark in (LIST_MARK, CUSTOM_MARK, BUILTIN_MARK)):
            return line
    raise AssertionError(f"屏幕上没有「{name}」这一行：\n{text}")


async def move_cursor_to(pilot, page, name: str) -> None:
    """把光标走到**名字**是 ``name`` 的那一行上（先一直往上夹在顶上，再一路往下找）。

    按名字找而不是按 id：id 是本地库分配的（``view-1`` 这种），测试不该把它抄一遍。
    """
    for _ in range(20):
        await pilot.press("k")
    for _ in range(60):
        row = page.row(page.selected_id or "")
        if row is not None and row.name == name:
            return
        await pilot.press("j")
    raise AssertionError(f"光标没能走到「{name}」上，停在 {page.selected_id}")


async def open_view_form(pilot) -> None:
    """``n`` → 选「视图」→ 条件表单开着。"""
    await pilot.press("n")
    await pilot.pause()
    await pilot.press("right")  # 第一档是「清单」，右方向键换到「视图」
    await pilot.press("enter")
    await pilot.pause()


async def new_high_priority_view(pilot, app: DidaApp) -> str:
    """建一个「高优先级未完成」，返回它的名字。"""
    await open_view_form(pilot)
    await pilot.press(*"高优先级未完成")
    await pilot.press("tab")  # 清单范围（留空 = 不限）
    await pilot.press("tab")  # 截止时间（不限）
    await pilot.press("tab")  # 优先级
    await pilot.press("5")
    await pilot.press("enter")
    await pilot.pause()
    return "高优先级未完成"


# ------------------------------------------------------------------ n：先问「清单还是视图」


async def test_n_asks_whether_it_is_a_list_or_a_view_first():
    """``n`` 先问一句「清单还是视图」（验收标准 1）——这两种东西后面完全是两回事。

    问完才开表单，而且两张表单的字段不一样：清单是名字 + 颜色，视图是名字 + 条件。

    两张表单分两次会话看：编辑态从 #66 起没有「取消」（ADR-0008 二），``Esc`` 是「交回这一
    份」，所以没法把一张表单原地收回去、再在同一段会话里开另一张。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        asked = screen_text(app)

        await pilot.press("right")
        await pilot.press("enter")
        await pilot.pause()
        view_form = screen_text(app)

    app = DidaApp(backend())
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")  # 默认那一档就是「清单」
        await pilot.pause()
        list_form = screen_text(app)

    assert "清单" in asked and "视图" in asked, "两个选项都写在屏幕上"
    assert "清单范围" in view_form and "颜色" not in view_form, "选视图给的是条件表单"
    assert "颜色" in list_form and "清单范围" not in list_form, "选清单照旧是 #42 那张表单"


async def test_escape_on_the_question_takes_the_default_choice():
    """在「清单还是视图」那一句上按 ``esc``：交回**第一档**（清单），接着开清单表单（#66）。

    这一问也是同一张 ``FormOverlay``（``index.py`` 的 ``new_kind_fields``），所以它没有
    「取消」这道门（ADR-0008 二）：``Esc`` 与 ``Enter`` 一样交回当前那一档，而当前那一档的
    起点就是「清单」，于是 ``Esc`` 走的是「建清单」那条路。到这一步一个字节都还没写——真正
    的写入要等清单表单那一份值交回来（名字空着还会被 ``EMPTY_LIST_NAME_MESSAGE`` 挡下）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_lists == [] and fake.created_views == [], "还没填名字，谁都还没建"
    assert "名字" in after and "颜色" in after, "Esc 交回默认那一档（清单），清单表单开着"
    assert "清单范围" not in after, "开出来的不是视图那张表单"


async def test_choosing_list_still_builds_a_list():
    """选了「清单」那条路照旧能用（#42 的验收标准不许被这一票弄坏）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*"shopping")
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_lists == [("shopping", None)]
    assert LIST_MARK in row_of(after, "shopping")


# ------------------------------------------------------------------ 浮层里的实话


async def test_the_view_form_says_the_view_lives_only_on_this_machine():
    """浮层里明确告知视图只存在本机（验收标准 4）。

    这是 API 的能力上限，不是实现疏漏：没有「保存一组过滤条件」这个接口，所以手机端、
    网页版上不会有它，换台机器也没了。说不清这一句，用户会以为换个设备还能看见。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        form = screen_text(app)

    # 按**行**断，不按整屏拼：那句话要在一行里排得下（浮层 74 格宽），拼上理由会折行，
    # 而折行处夹着边框——屏幕上就再也读不出一整句了。
    assert messages.VIEW_LOCAL_ONLY in form, "「只存在本机」那句话要写在浮层里，而且在一行里读得完"
    assert "没有保存一组过滤条件" in form, "说清理由：是接口里没有这一条，不是客户端没做"


# ------------------------------------------------------------------ n → 视图：建


async def test_the_view_form_takes_the_conditions_and_the_view_lands_on_the_page():
    """填完条件，视图就出现在清单列表页上（验收标准 2、5）。

    断的是屏幕：自建视图那个前缀 + 名字 + 条数（条数来自求值，两条高优先级未完成）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        name = await new_high_priority_view(pilot, app)
        after = screen_text(app)

    row = row_of(after, name)
    assert CUSTOM_MARK in row, "自建视图有自己的前缀（内置视图是另一个）"
    assert "2" in row, "高优先级未完成有两条（索引上的条数来自同一次求值）"
    assert fake.created_views[0].priorities == (5,)
    assert fake.created_views[0].completion.value == "unfinished"


async def test_entering_the_new_view_shows_the_filtered_tasks():
    """``enter`` 进去看到过滤后的任务（验收标准 5）：只有高优先级未完成的那两条。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        name = await new_high_priority_view(pilot, app)
        await move_cursor_to(pilot, app.index_page(), name)
        await pilot.press("enter")
        await pilot.pause()
        inside = screen_text(app)

    assert "交报告" in inside and "买牛奶" in inside
    assert "整理桌面" not in inside, "中优先级的不在"
    assert "做完的" not in inside, "已完成的不在"


async def test_the_recently_completed_example_can_be_built_from_the_form():
    """「最近完成」也能建出来：完成状态「已完成」+ 完成时间「最近七天」（验收标准 9）。

    这两维在表单上是**两格**（完成状态 / 完成时间），不是一个合并的枚举——工单的 AC 说的
    就是「按完成状态与完成时间筛」。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        await pilot.press(*"zuijinwancheng")
        await pilot.press("tab")  # 清单范围
        await pilot.press("tab")  # 截止时间
        await pilot.press("tab")  # 优先级
        await pilot.press("tab")  # 标签
        await pilot.press("tab")  # 完成状态
        await pilot.press("right")  # 未完成 → 已完成
        await pilot.press("tab")  # 完成时间
        await pilot.press("right")  # 不限 → 最近七天
        await pilot.press("enter")
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "zuijinwancheng")
        await pilot.press("enter")
        await pilot.pause()
        inside = screen_text(app)

    assert fake.created_views[0].completion.value == "completed"
    assert fake.created_views[0].completed_days == 7
    assert "做完的" in inside
    assert "交报告" not in inside and "买牛奶" not in inside, "没做完的不在"


async def test_an_unknown_list_name_is_reported_instead_of_saving_an_empty_view():
    """清单范围里写了一个不存在的清单：**拒绝保存**并说清是哪一个，不存一个筛不出东西的视图。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        await pilot.press(*"gongzuo")
        await pilot.press("tab")
        await pilot.press(*"quanhua")  # 清单范围：没有这个清单
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_views == [], "没保存"
    assert "quanhua" in after, "说清是哪一个词认不出来"
    assert "清单范围" in after, "表单还开着，用户可以接着改"


async def test_escape_saves_the_view_form_just_like_enter():
    """``esc`` 在视图表单上也是保存：填的那一份落进本地库、索引上多一行（#66 / ADR-0008 二）。

    视图只在本地（ADR-0005：API 没有「保存一组过滤条件」这个接口），所以「写出去」在这一屏
    就是清单列表页上多出那一行自建视图。这一条原来是
    ``test_escape_cancels_the_view_form_without_writing_anything``。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        await pilot.press(*"buzhu")
        await pilot.press("escape")
        await pilot.pause()
        after = screen_text(app)

    assert [view.name for view in fake.created_views] == ["buzhu"], "Esc 交回那一份，视图建下了"
    assert CUSTOM_MARK in row_of(after, "buzhu"), "清单列表页上多了一行自建视图"
    assert "清单范围" not in after, "表单关掉了"


# ------------------------------------------------------------------ e：改条件，立刻生效


async def test_e_opens_the_conditions_prefilled_and_the_change_takes_effect_at_once():
    """``e`` 的表单填着**当前的**条件；改完清单列表页上立刻是新样子（验收标准 6）。

    「立刻生效」断两处，都不必重进这一页：索引上的条数当场变了，再 ``enter`` 进去看到的
    就是新的那一份成员。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        name = await new_high_priority_view(pilot, app)
        before = row_of(screen_text(app), name)

        await move_cursor_to(pilot, app.index_page(), name)
        await pilot.press("e")
        await pilot.pause()
        prefilled = field_value(screen_text(app), "优先级")

        for _ in range(3):  # 名字 → 清单范围 → 截止时间 → 优先级
            await pilot.press("tab")
        # 那一格打开时是**全选**的（Textual 的 select_on_focus）：直接打就是整个换掉。
        await pilot.press(*"5,3")
        await pilot.press("enter")
        await pilot.pause()
        after = row_of(screen_text(app), name)

        await pilot.press("enter")  # 进这个视图，看到的就是新的成员
        await pilot.pause()
        inside = screen_text(app)

    assert prefilled == "高", "打开时填着当前的条件（不是空白）"
    assert "2" in before and "3" in after, "条数当场从 2 变成 3"
    assert fake.updated_views[0].priorities == (5, 3)
    assert "整理桌面" in inside, "中优先级的那条现在也在里面了"


# ------------------------------------------------------------------ d：删视图（一次 y/n）


async def test_d_asks_once_and_y_deletes_the_view_without_touching_any_task():
    """``d`` 一次 ``y/n`` 确认后删除该视图；**删视图不删任务**（验收标准 7、8）。

    ``n`` 那一次什么都不做；``y`` 那一次只摘掉视图那一行——同一份缓存上，那些任务还在
    各自的清单里（不是「也还在缓存里」：``enter`` 进清单看得见它们）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        name = await new_high_priority_view(pilot, app)
        view_id = next(row.id for row in app.index_page().rows() if row.name == name)
        await move_cursor_to(pilot, app.index_page(), name)

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

        await move_cursor_to(pilot, app.index_page(), "工作")
        await pilot.press("enter")
        await pilot.pause()
        work = screen_text(app)

    assert f"删除视图「{name}」" in asked, "问的是哪一个视图"
    assert "不会动任何任务" in asked, "视图只是一组过滤条件：这一句要如实写出来"
    assert CUSTOM_MARK in row_of(after_cancel, name), "``n`` 之后视图还在"
    assert fake.deleted_views == [view_id], "只有 y 那一次真的删了"
    assert CUSTOM_MARK not in after_yes, "``y`` 之后那一行没了"
    assert "交报告" in work and "整理桌面" in work, "工作里的任务一条都没少"


# ------------------------------------------------------------------ 内置视图那三行


async def test_a_builtin_view_row_cannot_be_edited_or_deleted():
    """内置视图的条件是写死的：``e`` / ``d`` 都说清原因，一个字都不写、也不开浮层。

    它们不是「用户建的视图」，本地库里根本没有对应的行——所以这里既不给表单也不问那一句
    （问一句就等于「有可能删」，而它不会）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "今天")
        await pilot.press("e")
        await pilot.pause()
        after_edit = screen_text(app)

        await pilot.press("d")
        await pilot.pause()
        after_delete = screen_text(app)

    assert messages.BUILTIN_VIEW_MESSAGE in after_edit
    assert "清单范围" not in after_edit, "没开表单"
    assert messages.BUILTIN_VIEW_MESSAGE in after_delete
    assert "y 确认" not in after_delete, "没问那一句"
    assert fake.updated_views == [] and fake.deleted_views == []


# ------------------------------------------------------------------ 谁改得动：两张拒绝表


def test_the_list_path_refuses_a_view_row_and_the_view_path_refuses_a_builtin():
    """两张拒绝表各管一行，而且都**不肯**在自己不认识的行上说「可以」。

    ``list_write_refusal`` 对视图行仍然说「不」：清单列表页现在先看行的类型再分派，
    但兜底必须留着——万一有调用方没先看，它也不会把一个视图当成清单改掉。
    ``view_write_refusal`` 只拒绝**内置**视图：它们没有本地行，改不了也删不掉。
    """
    from dida.sync.engine import ListKind, ListRow
    from dida.tui.pages.index import list_write_refusal, view_write_refusal

    custom = ListRow(id="view-1", name="我的一天", kind=ListKind.CUSTOM, unfinished=0)
    builtin = ListRow(id="today", name="今天", kind=ListKind.BUILTIN, unfinished=0)

    assert list_write_refusal(custom) == messages.VIEW_ROW_MESSAGE
    assert list_write_refusal(builtin) == messages.VIEW_ROW_MESSAGE
    assert view_write_refusal(custom) is None, "自建视图改得动也删得掉（#36）"
    assert view_write_refusal(builtin) == messages.BUILTIN_VIEW_MESSAGE


# ------------------------------------------------------------------ 清单范围（多选）从表单走到求值


async def test_the_list_scope_takes_several_list_names_and_filters_the_members():
    """清单范围那一格写两个清单的名字，视图就只收这两个清单里的任务（验收标准 2）。

    这一条把那一维从**表单**走到**求值**：格子里写的是名字（用户认的是名字），存下去的是
    id，成员由 ``evaluate_view`` 算——三处都得对上，屏幕上才只有那两个清单的任务。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        await pilot.press(*"fanwei")
        await pilot.press("tab")
        await pilot.press(*"工作,生活")  # 两个名字，逗号分开
        await pilot.press("enter")
        await pilot.pause()
        await move_cursor_to(pilot, app.index_page(), "fanwei")
        await pilot.press("enter")
        await pilot.pause()
        inside = screen_text(app)

    assert fake.created_views[0].lists == ("work", "life"), "名字认成了 id"
    assert "交报告" in inside and "买牛奶" in inside
    assert "整理桌面" in inside, "两个清单的未完成任务都在（这是范围，不是别的维度）"


async def test_a_list_scope_that_names_a_list_we_do_not_have_is_refused_with_its_name():
    """范围里有一个不存在的清单：整张表单**不保存**，并把那个词写出来（不静默存一半）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_view_form(pilot)
        await pilot.press(*"fanwei")
        await pilot.press("tab")
        await pilot.press(*"工作,meiyouzhege")
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert fake.created_views == []
    assert "meiyouzhege" in after, "说清是哪一个词认不出来"
    assert "fanwei" in after, "表单重新打开时把用户填的还给他，不用重填七个格子"
