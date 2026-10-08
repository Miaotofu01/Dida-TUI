"""同值收敛（工单 #79）：写的那一次自己回报「改了 / 没改」。

判据只有一个本体（``dida.sync.writes.is_a_change``）：拿**这次要盖上去的字段**与**本地那一份
原文**逐位比，任何一位不同才算一次真改动。六条写路径——改字段、搬运、改期、顺延、改清单、
改视图——从 #79 起回报布尔；删除 / 完成 / 取消完成没有「什么都没改」这一档，签名与行为不动。

接缝按 ``docs/architecture.md``：真引擎 + 真本地库（临时文件 SQLite）+ 假传输层。这里断的
两件事都是外部行为——引擎**回报**了什么，以及**发出去的请求**是什么（在传输层看，不看替身
自己记的账本）。所以「没改」的每一条都同时钉住：不入队（``store.pending()`` 空）、不排推送
（``transport.requests`` 空）、本地那一份一个字没动。

归一化口径与本地库读那一份时一致（``Store._snapshot``）：文本缺省是空串、优先级缺省是 ``0``、
标签比**集合**、``isAllDay`` 看真假、``dueDate`` 的「缺省」与「显式 ``null``」是同一件事、
认不出的字段原样比。``test_writing_back_exactly_what_the_read_model_says_is_no_change`` 是这
条口径的兜底：把读模型说的每一个字段原样写回去，一次都不许算改动。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import SyncEngine, ViewDefinition
from dida.sync.writes import WriteKind
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（``03-14`` 是周六）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)
"""默认的「现在」。"""


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def project(id: str = "work", name: str = "工作") -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 1}


def inbox() -> dict:
    """收集箱：服务端的清单索引里没有它，客户端自己补那一行。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def seed(store: Store, *tasks: dict, lists: list[dict] | None = None) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=lists if lists is not None else [inbox(), project()],
        tasks=list(tasks),
    )


def make_engine(
    store: Store,
    transport: FakeTransport | None = None,
    *,
    clock: ManualClock | None = None,
    day_end: str = "24:00",
) -> SyncEngine:
    """接上真存储与真客户端；网络钉在假传输上。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        day_end=day_end,
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


# ------------------------------------------------------------------ 改字段


def test_writing_the_same_field_value_reports_no_change_and_touches_nothing(store):
    """同一个值再写一次 = 没改：回报 ``False``，不入队、不排推送、本地一个字不动。

    「连按三次 ``enter``、一个字都没改」产生的那一笔空写就是这句话要消灭的东西（ADR-0008
    第二节记的实测）。
    """
    seed(store, task(id="t1", title="写周报"))
    transport = FakeTransport()
    engine = make_engine(store, transport)

    assert engine.write("t1", changes={"title": "写周报"}) is False

    assert store.pending() == (), "空操作不许入队"
    assert store.pending_count() == 0, "状态栏那个数不许为一个空操作亮着"
    assert transport.requests == [], "一个字节都不许上网"
    assert store.task_payload("t1")["title"] == "写周报"


async def test_writing_a_different_field_value_reports_the_change_and_pushes_it(store):
    """真改了照旧：回报 ``True``，入队并立刻推一笔（收敛不许把真改动一起挡掉）。"""
    seed(store, task(id="t1", title="写周报"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport)

    assert engine.write("t1", changes={"title": "写周报（我改的）"}) is True

    assert store.pending_count() == 1, "真改动照旧入队"
    assert store.task_payload("t1")["title"] == "写周报（我改的）", "本地当场生效"

    await engine.wait_for_pushes()

    assert [str(request.url) for request in transport.requests] == [
        "https://api.dida365.com/open/v1/task/t1"
    ]
    assert transport.last_json["title"] == "写周报（我改的）"


def test_a_missing_text_field_and_the_empty_string_are_the_same_thing(store):
    """文本缺省是空串：本地没有这个字段，写一个空串不算改动（与本地库读那一份同一口径）。"""
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, FakeTransport())

    assert engine.write("t1", changes={"content": ""}) is False
    assert engine.write("t1", changes={"desc": ""}) is False
    assert store.pending() == ()


def test_a_missing_priority_and_zero_are_the_same_thing(store):
    """优先级缺省是 ``0``：本地没有这个字段，写 ``0`` 不算改动。"""
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, FakeTransport())

    assert engine.write("t1", changes={"priority": 0}) is False
    assert store.pending() == ()


def test_the_same_tags_in_another_order_are_not_a_change(store):
    """标签比**集合**：顺序不同不是改动（用户故事 3）。"""
    seed(store, task(id="t1", title="写周报", tags=["工作", "季度"]))
    engine = make_engine(store, FakeTransport())

    assert engine.write("t1", changes={"tags": ["季度", "工作"]}) is False
    assert store.pending() == ()


def test_a_missing_tag_list_and_an_empty_one_are_the_same_thing(store):
    """本地一个标签都没有时，写一份空标签同样不算改动。"""
    seed(store, task(id="t1", title="写周报"))
    engine = make_engine(store, FakeTransport())

    assert engine.write("t1", changes={"tags": []}) is False
    assert store.pending() == ()


def test_due_date_is_written_the_way_the_local_copy_reads_it(store):
    """``dueDate`` 的「缺省」与「显式 ``null``」是同一件事：两个方向都不算改动。

    这是详细页「一个字都没改就提交」那一次空写的判据本身：用户按完 ``enter``，写出去的那一刻
    就是本地那一份（同一个时刻，写法的 ``+08:00`` / ``+0800`` 不算差别）。
    """
    seed(
        store,
        task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+08:00", isAllDay=False),
        task(id="t2", title="没有日期"),
    )
    engine = make_engine(store, FakeTransport())

    assert engine.reschedule("t1", due=at(14, 18), all_day=False) is False
    assert engine.reschedule("t2", due=None, all_day=False) is False

    assert store.pending() == ()


def test_the_all_day_flag_is_compared_as_a_truth_value(store):
    """``isAllDay`` 看真假：本地那一份是全天，写回全天不算改动。"""
    seed(
        store,
        task(id="t1", title="还信用卡", dueDate="2026-03-14T00:00:00.000+0000", isAllDay=True),
    )
    engine = make_engine(store, FakeTransport())

    assert engine.reschedule("t1", due=at(14), all_day=True) is False
    assert store.pending() == ()


def test_a_field_we_do_not_recognise_is_compared_exactly_as_it_stands(store):
    """认不出的字段原样比：同一个值不算改动，另一个值算。"""
    seed(store, task(id="t1", title="写周报", timeZone="Asia/Shanghai"))
    engine = make_engine(store, FakeTransport(json={"id": "t1"}))

    assert engine.write("t1", changes={"timeZone": "Asia/Shanghai"}) is False
    assert engine.write("t1", changes={"timeZone": "UTC"}) is True


def test_writing_back_exactly_what_the_read_model_says_is_no_change(store):
    """把读模型说的每个字段原样写回去，一次都不算改动（归一化口径的兜底）。

    这条把判据与本地库读那一份的口径钉在一起：读模型给的成品（缺省已经补成空串 / ``0`` /
    空集合）写回去，不该有任何一位不同。两边的口径一旦漂开，这里当场红。
    """
    seed(
        store,
        task(
            id="t1",
            title="写周报",
            dueDate="2026-03-14T18:00:00+08:00",
            isAllDay=False,
            priority=3,
            tags=["工作", "季度"],
        ),
    )
    engine = make_engine(store, FakeTransport())
    detail = engine.task_detail("t1")
    assert detail is not None

    assert engine.write("t1", changes={"title": detail.title}) is False
    assert engine.write("t1", changes={"content": detail.content}) is False
    assert engine.write("t1", changes={"desc": detail.desc}) is False
    assert engine.write("t1", changes={"priority": detail.priority}) is False
    assert engine.write("t1", changes={"tags": list(detail.tags)}) is False
    assert engine.reschedule("t1", due=detail.due, all_day=detail.all_day) is False

    assert store.pending() == ()


# ------------------------------------------------------------------ 搬运


def test_moving_a_task_to_the_list_it_is_already_in_is_not_a_move(store):
    """搬到它**已经在**的那个清单不算一次搬运（用户故事 6）：不入队、不排推送。"""
    seed(store, task(id="t1", title="写周报", project_id="work"))
    transport = FakeTransport()
    engine = make_engine(store, transport)

    assert engine.move_task("t1", to_list_id="work") is False

    assert store.pending() == ()
    assert transport.requests == []


async def test_moving_a_task_to_another_list_reports_the_change(store):
    """真搬一次照旧：回报 ``True``，请求走搬运端点。"""
    seed(store, task(id="t1", title="写周报", project_id="work"))
    transport = FakeTransport(json=[{"id": "t1"}])
    engine = make_engine(store, transport)

    assert engine.move_task("t1", to_list_id="inbox") is True
    await engine.wait_for_pushes()

    assert [str(request.url) for request in transport.requests] == [
        "https://api.dida365.com/open/v1/task/move"
    ]
    assert store.pending_count() == 0


# ------------------------------------------------------------------ 顺延


def test_deferring_a_task_that_already_lands_on_the_target_day_is_no_change(store):
    """顺延到它**已经**在的那个逻辑日不算改动：不入队、不排推送（用户故事 7）。

    边界 04:00、现在是 03-14 中午：下一个逻辑日是 03-15，而这条任务的截止已经在 03-15 10:00
    ——再把「原来的墙钟时刻」放进那一个逻辑日，落点一个字都不变。
    """
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-15T10:00:00+0800"))
    transport = FakeTransport()
    engine = make_engine(store, transport, day_end="04:00")

    assert engine.defer("t1") is False

    assert store.pending() == ()
    assert transport.requests == []


async def test_deferring_a_task_a_logical_day_forward_reports_the_change(store):
    """真顺延照旧：回报 ``True``，``dueDate`` 挪到下一个逻辑日的同一墙钟时刻。"""
    seed(store, task(id="t1", title="写周报", dueDate="2026-03-14T18:00:00+08:00"))
    transport = FakeTransport(json={"id": "t1"})
    engine = make_engine(store, transport, day_end="04:00")

    assert engine.defer("t1") is True
    await engine.wait_for_pushes()

    assert transport.last_json["dueDate"] == "2026-03-15T18:00:00+0800"


def test_deferring_a_task_that_cannot_be_moved_is_no_change(store):
    """没有日期可挪（本地没这条任务、或者它本来就没有截止时间）时报「没改」，不报错。"""
    seed(store, task(id="t1", title="没有日期"))
    engine = make_engine(store, FakeTransport())

    assert engine.defer("t1") is False
    assert engine.defer("ghost") is False
    assert store.pending() == ()


# ------------------------------------------------------------------ 删除 / 完成 / 取消完成（签名与行为不动）


async def test_complete_uncomplete_and_delete_never_converge(store):
    """完成 / 取消完成 / 删除**没有**「什么都没改」这一档：同一个动作再来一次照旧入队。

    它们不做同值收敛是**故意的**（spec 的已知例外）：一个已经完成的任务再按一次完成没有
    「省下这一笔」的道理，而完成与删除都是不可逆的对外动作。签名也照旧（回 ``None``）。
    """
    seed(store, task(id="t1", title="写周报", status=2))
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    transport.enqueue(httpx.Response(200))
    transport.enqueue(httpx.Response(200))
    engine = make_engine(store, transport)

    assert engine.complete("t1") is None
    assert engine.uncomplete("t1") is None
    assert engine.delete("t1") is None

    assert store.pending_count() == 3, "三种不可逆的写一条都不许被收敛掉"


# ------------------------------------------------------------------ 改清单


def test_saving_a_list_form_that_was_not_touched_is_no_change(store):
    """清单表单没改不算一次写（既有断言 `test_list_overlay` 的引擎那一半）。"""
    seed(store)
    transport = FakeTransport()
    engine = make_engine(store, transport)

    assert engine.update_list("work", name="工作", color=None) is False

    assert store.pending_lists() == ()
    assert transport.requests == []


async def test_renaming_a_list_reports_the_change_and_pushes_it(store):
    """真改名照旧：回报 ``True``，请求走改清单那个端点。"""
    seed(store)
    transport = FakeTransport(json={"id": "work"})
    engine = make_engine(store, transport)

    assert engine.update_list("work", name="haiwai") is True
    await engine.wait_for_pushes()

    assert [str(request.url) for request in transport.requests] == [
        "https://api.dida365.com/open/v1/project/work"
    ]


# ------------------------------------------------------------------ 改视图


def test_saving_a_view_form_that_was_not_touched_is_no_change(store):
    """视图表单没改不算一次写（视图只在本地，所以看的是引擎的回报）。"""
    store.save_view(ViewDefinition(id="v1", name="高优先级", priorities=(5,)))
    engine = make_engine(store, FakeTransport())
    current = engine.view_definition("v1")
    assert current is not None

    assert engine.update_view(current) is False


def test_changing_a_view_reports_the_change(store):
    """真改了条件照旧：回报 ``True``（视图只在本地，没有请求可发）。"""
    store.save_view(ViewDefinition(id="v1", name="高优先级", priorities=(5,)))
    engine = make_engine(store, FakeTransport())

    assert engine.update_view(ViewDefinition(id="v1", name="高优先级", priorities=(5, 3))) is True
    assert engine.view_definition("v1").priorities == (5, 3)
