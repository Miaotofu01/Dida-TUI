"""挑选型字段（工单 #45）：所属清单 / 优先级 / 标签。

两个既定接缝都用：

- **接缝二**（真引擎 + 真库 + 可注入的 HTTP 传输）钉**搬运的请求形状**与「搬完之后
  读路径上这条任务在哪」——那两件事是网络与本地库这一侧的事实，替身说了不算。
- **接缝一**（内存 ``FakeBackend`` + ``run_test()`` pilot）钉三个挑选浮层的**外部行为**：
  按了什么键、屏幕上出现了什么、写出去的是哪一笔。

只断外部行为：不断控件树、不断内部状态对象、不断渲染字符串里的颜色码。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import DidaError, MalformedResponseError
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, FakeTransport, ManualClock
from dida.tui import messages, theme
from dida.tui.app import DidaApp
from support import screen_sgr, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

TITLE = "交季度报告"
CONTENT = "记得附上上周的对比数据"
DESC = "先问一下财务再发"


# ------------------------------------------------------------------ 接缝二：搬运的请求形状


async def test_the_move_request_is_a_json_array_and_the_response_is_an_id_etag_array():
    """``POST /open/v1/task/move`` 的两个形状陷阱（工单 #45 补的那两条）。

    请求体**顶层是数组**、每项三个字段都必填（``openapi-dida365.md:504``、``:508–510``）；
    响应是 ``{id, etag}`` 的数组（``:516``），**不是**被搬的那条 Task。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    results = await client.move_task("work", "life", "t1")

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/move"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert transport.last_json == [
        {"fromProjectId": "work", "toProjectId": "life", "taskId": "t1"}
    ], "顶层必须是数组，不是对象"
    assert results == [{"id": "t1", "etag": "43p2zso1"}]


async def test_a_move_response_that_is_a_task_object_is_rejected_as_bad_shape():
    """响应按 ``{id, etag}`` 的**数组**解析：给一条 Task 对象是坏形状，不是「搬好了」。"""
    transport = FakeTransport(json={"id": "t1", "title": TITLE})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.move_task("work", "life", "t1")


async def test_a_move_that_answers_201_with_no_body_is_a_success():
    """``201 → No Content``（``:517``）是成功形状：空响应体不是坏数据。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(201))
    client = DidaApiClient(token="tok-123", transport=transport)

    assert await client.move_task("work", "life", "t1") == []


# ------------------------------------------------------------------ 接缝二：搬完人真的换了清单


def real_engine(tmp_path, transport, *, lists=None, tasks=None) -> SyncEngine:
    """接缝二：真引擎 + 真库 + 打给假服务端的真客户端（``test_detail_page.py`` 那一套）。

    搬运的**请求形状**与「搬完之后读路径上这条任务在哪」都是网络与本地库这一侧的事实，
    替身说了不算——``FakeBackend`` 的写只记录，读路径上的搬家效果在它那里根本不存在。
    """
    store = Store(tmp_path / "dida.sqlite3")
    store.apply_refresh(
        lists=lists
        if lists is not None
        else [
            {"id": "work", "name": "工作", "sortOrder": 0},
            {"id": "life", "name": "生活", "sortOrder": 1},
        ],
        tasks=tasks
        if tasks is not None
        else [{"id": "t1", "projectId": "work", "title": TITLE, "status": 0}],
    )
    return SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


async def test_moving_a_task_goes_to_the_move_endpoint_not_an_ordinary_field_update(tmp_path):
    """搬运打的是搬运端点，**不是**把它当成一次普通的字段更新（验收标准 2）。

    两个形状一起钉：顶层是数组、每项三个字段（``:504``、``:508–510``）；而且这次推送里
    **一个** ``POST /open/v1/task/{taskId}`` 都没有——那正是「当成字段更新」的样子。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    engine = real_engine(tmp_path, transport)

    engine.move_task("t1", to_list_id="life")
    await engine.push_pending()

    urls = [str(request.url) for request in transport.requests]
    assert urls == ["https://api.dida365.com/open/v1/task/move"], f"搬运走错了端点：{urls}"
    assert transport.last_json == [
        {"fromProjectId": "work", "toProjectId": "life", "taskId": "t1"}
    ]


async def test_after_the_move_the_target_list_has_the_task_and_the_source_list_does_not(tmp_path):
    """搬完**在读路径上**断言：目标清单里有它、原清单里没有（验收标准 8）。

    断的不是本地某个集合，而是两层页面真正读的那两个口子（``tasks_in`` 与详情页的清单名）
    ——本地集合对了而读路径没变，屏幕上就还是原来的样子。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    engine = real_engine(tmp_path, transport)

    engine.move_task("t1", to_list_id="life")
    await engine.push_pending()

    assert [item.task_id for item in engine.tasks_in("life").items] == ["t1"], "目标清单里没有这条任务"
    assert [item.task_id for item in engine.tasks_in("work").items] == [], "原清单里还留着这条任务"
    detail = engine.task_detail("t1")
    assert detail is not None and detail.list_name == "生活", "详情页那一格还写着原清单"


async def test_a_task_moves_both_ways_between_the_inbox_and_a_real_list(tmp_path):
    """收集箱与真实清单之间**双向**可搬（验收标准 3）。

    文档对这一节里的收集箱一个字都没提（``:497–548``），所以这里不替它写一条「文档说」；
    能钉的是**形状**：收集箱那一行带的是什么 id，搬过去就发什么 id（本测试用的就是
    ``move_targets()`` 真正会给挑选器的那个 id），回来时再搬一次。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "e1"}])
    engine = real_engine(
        tmp_path,
        transport,
        tasks=[{"id": "t1", "projectId": "inbox", "title": TITLE, "status": 0}],
    )
    inbox_id = next(row.id for row in engine.move_targets() if row.is_inbox)

    engine.move_task("t1", to_list_id="work")
    await engine.push_pending()
    assert [item.task_id for item in engine.tasks_in("work").items] == ["t1"], "搬出收集箱没成"
    assert engine.task_detail("t1").list_name == "工作"

    engine.move_task("t1", to_list_id=inbox_id)
    await engine.push_pending()
    assert [item.task_id for item in engine.tasks_in(inbox_id).items] == ["t1"], "搬回收集箱没成"
    assert [item.task_id for item in engine.tasks_in("work").items] == []

    assert [json.loads(request.content) for request in transport.requests] == [
        [{"fromProjectId": "inbox", "toProjectId": "work", "taskId": "t1"}],
        [{"fromProjectId": "work", "toProjectId": inbox_id, "taskId": "t1"}],
    ], "两次搬运的请求体不是文档那个数组形状"


async def test_a_list_the_server_has_not_seen_is_never_offered_as_a_move_target(tmp_path):
    """本地刚建、还没推上去的清单**不**当搬运目标（#53/#54 是同一类）。

    它的 id 是本地临时的（服务端没见过），拿它当 ``toProjectId`` 会 404，而那条改动
    **永远推不出去**——状态栏那个数从此一直非零，读起来像「等一下就好」。判据是队列里
    还有没有这一行的 ``CREATE``：推成功、认领了服务端的 id 之后它就该出现在可选里。
    """
    transport = FakeTransport(json={"id": "srv-1", "name": "新清单", "sortOrder": 0})
    engine = real_engine(tmp_path, transport)

    local_id = engine.create_list("新清单")

    assert local_id in {row.id for row in engine.list_index()}, "本地那一行本来就该在清单索引里"
    assert local_id not in {row.id for row in engine.move_targets()}, (
        "服务端还没见过的清单被当成了搬运目标"
    )

    await engine.push_pending()  # 新建推成功：本地那一行认领服务端的 id

    offered = {row.id for row in engine.move_targets()}
    assert local_id not in offered
    assert "srv-1" in offered, "服务端认过的清单该能当搬运目标"


# ------------------------------------------------------------------ 接缝二：标签列表从哪儿来


async def test_the_tag_picker_loads_the_names_from_the_tag_endpoint(tmp_path):
    """标签列表来自 ``GET /open/v1/tag``——``list_tags`` 从此有了生产调用方（工单 #45）。

    ``OpenTag`` 的 ``name`` 是标识符（小写、trimmed），任务上 ``tags`` 数组里装的也是名字，
    所以挑选用的是它；``label`` 只是显示形式（文档要求它小写之后必须等于 ``name``）。
    """
    transport = FakeTransport(
        json=[
            {"name": "work", "label": "Work", "sortOrder": 0},
            {"name": "urgent", "label": "urgent", "sortOrder": 1},
        ]
    )
    engine = real_engine(tmp_path, transport)

    names = await engine.load_tags()

    request = transport.last_request
    assert request.method == "GET"
    assert str(request.url) == "https://api.dida365.com/open/v1/tag"
    assert names == ("work", "urgent")
    assert engine.tags() == ("work", "urgent")


async def test_a_tag_already_on_a_cached_task_stays_pickable_when_the_list_cannot_be_fetched(tmp_path):
    """拉不到标签列表时，本地任务上已经打着的标签**照样挑得动**（断网也不能卡住取消）。

    拉不到是**说出来**的（结构化错误照旧往外抛，调用方去说），不是假装「你没有标签」：
    一条任务上已经有的标签必须留在可挑的那一份里，否则断网时「取消一个标签」无路可走。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.ConnectError("连不上服务器"))
    engine = real_engine(
        tmp_path,
        transport,
        tasks=[
            {
                "id": "t1",
                "projectId": "work",
                "title": TITLE,
                "status": 0,
                "tags": ["季度"],
            }
        ],
    )

    with pytest.raises(DidaError) as caught:
        await engine.load_tags()

    assert "连不上服务器" in str(caught.value)
    assert engine.tags() == ("季度",)


# ------------------------------------------------------------------ 接缝一：三个挑选浮层


def backend() -> FakeBackend:
    """一份够用的缓存：两个清单、一条在「工作」里、带优先级与两个标签的任务。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_list("生活", id="life")
    fake.add_task(
        TITLE,
        list_name="work",
        id="t1",
        due=T0.replace(hour=18, minute=0),
        priority=5,
        content=CONTENT,
        desc=DESC,
        tags=("工作", "季度"),
    )
    return fake


def field_row(text: str, label: str) -> str:
    """字段列表里 ``label`` 那一行（行首那两格是光标记号，所以从第三格认起）。"""
    for line in text.splitlines():
        if line[2:].startswith(label):
            return line
    raise AssertionError(f"字段列表里没有「{label}」那一行：\n{text}")


async def enter_detail(pilot, app: DidaApp) -> None:
    """走进「工作」清单的第一条任务的详细页，光标停在标题那一格上。"""
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def walk_to(pilot, app: DidaApp, key: str) -> None:
    """把详细页的光标走到某一格上（``j`` 一次一个字段）。"""
    for _ in range(20):
        if app.detail_page().selected_id == key:
            return
        await pilot.press("j")
    raise AssertionError(f"光标没能走到 {key} 上，停在 {app.detail_page().selected_id}")


async def test_the_list_field_offers_my_lists_and_moving_really_moves_the_task():
    """``enter`` 落在「清单」上开挑选浮层，挑一个 → 任务**真的搬过去**（验收标准 1）。

    屏幕上两头都断：字段列表里那一行换成了新清单名，而写出去的是**搬运**（``fake.moved``），
    不是一次普通字段更新（``fake.writes`` 里一笔都不该有）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to(pilot, app, "list")
        await pilot.press("enter")
        await pilot.pause()
        picker = screen_text(app)
        await pilot.press("right")  # 工作 → 生活（选项顺序就是引擎给的清单索引）
        await pilot.pause()
        picked = screen_text(app)
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert "搬到哪个清单" in picker, f"开出来的不是挑选浮层：\n{picker}"
    assert "< 工作 >" in picker, f"挑选器没有停在当前清单上：\n{picker}"
    assert "< 生活 >" in picked, f"右方向键没有换到另一个清单：\n{picked}"
    assert fake.moved == [("t1", "life")], f"任务没有搬过去：{fake.moved}"
    assert fake.writes == [], f"搬运不该走普通字段更新：{fake.writes}"
    assert "生活" in field_row(after, "清单"), f"搬完那一格还写着原清单：\n{after}"


async def test_the_inbox_is_offered_as_a_move_target_too():
    """收集箱也在可选里（验收标准 3 的一半：真实清单 → 收集箱）。

    收集箱那一行是客户端自己补的，它的 id 是服务端返回的那一串（不是字面量 ``inbox``）；
    挑选器给的就是引擎 ``move_targets()`` 那一份，所以这里搬过去发的是那个 id。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to(pilot, app, "list")
        await pilot.press("enter")
        await pilot.pause()
        picker = screen_text(app)
        await pilot.press("left")  # 工作 → 收集箱（收集箱置顶）
        await pilot.pause()
        picked = screen_text(app)
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert "< 工作 >" in picker, f"挑选器没有停在当前清单上：\n{picker}"
    assert "< 收集箱 >" in picked, f"左方向键没有换到收集箱：\n{picked}"
    assert fake.moved == [("t1", "inbox")], f"没有搬进收集箱：{fake.moved}"
    assert "收集箱" in field_row(after, "清单"), f"搬完那一格没变：\n{after}"


async def test_escape_closes_the_picker_without_writing_anything():
    """``esc`` 关掉挑选浮层：一个字节都不写（与表单那条规矩同一条）。

    出口必须是 ``Esc``：这一层没有文本框，``q`` 在表单里本来就不绑（#42 的决定，继承不重定）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to(pilot, app, "list")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("escape")
        await pilot.pause()
        text = screen_text(app)

    assert fake.moved == [] and fake.writes == [], "取消不该写任何东西"
    assert "搬到哪个清单" not in text, f"浮层没有关掉：\n{text}"
    assert field_row(text, "清单").startswith(theme.CURSOR_MARK), f"没有回到字段列表：\n{text}"


def picked_option(text: str) -> str:
    """挑选器上现在写着哪一档（``< 中 >``）——选择框一行只画当前那一档。"""
    match = re.search(r"< (.+?) >", text)
    if match is None:
        raise AssertionError(f"屏幕上没有挑选项：\n{text}")
    return match.group(1)


async def test_the_priority_field_offers_the_four_levels_and_writes_the_wire_code():
    """优先级能在**无 / 低 / 中 / 高**之间改（验收标准 4）：屏幕上是四档用户语言，
    写出去的是线上编码。

    四档的名字是 spec 用户故事 73 那几个字（不是照抄实现里那张表）；``0/1/3/5`` 是线上编码，
    **不进文案**——所以这里逐个走一遍，看到的只许是那四个汉字。
    """
    fake = backend()  # 优先级 5（高）
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_detail(pilot, app)
        await walk_to(pilot, app, "priority")
        await pilot.press("enter")
        await pilot.pause()
        opened = picked_option(screen_text(app))
        walked = []
        for _ in range(3):
            await pilot.press("left")
            await pilot.pause()
            walked.append(picked_option(screen_text(app)))
        await pilot.press("right")  # 无 → 低（左右都能换档）
        await pilot.pause()
        back = picked_option(screen_text(app))
        await pilot.press("enter")
        await pilot.pause()
        after = screen_text(app)

    assert opened == "高", f"挑选器没有停在当前那一档上：{opened!r}"
    assert walked == ["中", "低", "无"], f"四档不是按顺序排的：{walked}"
    assert back == "低", "右方向键换不回去"
    assert fake.writes == [("t1", {"priority": 1})], f"写出去的不是线上编码：{fake.writes}"
    assert "低" in field_row(after, "优先级"), f"改完那一格没变：\n{after}"
