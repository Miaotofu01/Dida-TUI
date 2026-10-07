"""本地存储（t08）的外部行为。

接缝：``Store`` 的公开接口——t09/t10 消费的那一层。断言的都是「写进去、读出来」的
外部行为，不碰私有方法、不直接查 SQL。未知字段那一条额外借用既有的接缝二
（传输层可注入），证明存储里那份快照真的能喂给 t07 的 ``update_task(snapshot=)``。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import ChangeKind, Store, StoredSyncState
from dida.sync.view import SyncState
from dida.testing import FakeTransport

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def project(id: str = "p1", name: str = "工作", **extra: object) -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "color": "#ff0000", "sortOrder": 7, "groupId": "g1", **extra}


def task(id: str = "t1", title: str = "写周报", project_id: str = "p1", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def test_lists_land_and_are_readable(store):
    store.apply_refresh(lists=[project(name="工作"), project(id="p2", name="生活")], tasks=[])

    assert [(item.id, item.name) for item in store.lists()] == [("p1", "工作"), ("p2", "生活")]
    assert store.list_records()[0].color == "#ff0000"
    assert store.list_records()[0].sort_order == 7
    assert store.list_records()[0].group_id == "g1"


def test_list_rows_carry_the_project_kind_and_permission(store):
    """``Project.kind`` 与 ``permission`` 要落库、读得出来（用户故事 23 / 24）。

    清单索引页靠这两样标出「装不了任务的」（``NOTE``）与「改不动的」（``permission`` 不是
    ``write``）那些行——v1 的库把这两个字段整个丢掉了（连列都没有）。
    """
    store.apply_refresh(
        lists=[project(kind="NOTE", permission="read"), project(id="p2", name="生活")], tasks=[]
    )

    rows = {item.id: item for item in store.lists()}

    assert (rows["p1"].kind, rows["p1"].permission) == ("NOTE", "read")
    assert (rows["p2"].kind, rows["p2"].permission) == (None, None), "服务端没给就是不知道"
    assert (store.list_records()[0].kind, store.list_records()[0].permission) == ("NOTE", "read")


def test_an_older_cache_gets_the_new_list_columns(tmp_path):
    """v1 时代的库没有这两列：开库时补上（``CREATE TABLE IF NOT EXISTS`` 不给已有的表加列）。

    用户手上就是这样一个库，所以这条 ``ALTER TABLE`` 路径是必须存在的，不是锦上添花。
    """
    path = tmp_path / "dida.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE lists (id TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT,"
            " sort_order INTEGER, group_id TEXT, is_inbox INTEGER NOT NULL DEFAULT 0)"
        )

    opened = Store(path)
    try:
        opened.apply_refresh(lists=[project(kind="NOTE", permission="read")])
        row = opened.lists()[0]
        assert (row.kind, row.permission) == ("NOTE", "read")
    finally:
        opened.close()


def test_a_task_without_a_project_id_is_not_guessed_into_the_inbox(store):
    """缺失的 ``projectId`` **不许**再被猜成字面量 ``inbox``（#33 明令去掉的那条）。

    收集箱的真实 id 是每账户不同的一串，猜出来的字面量跟它一条都对不上——左栏徽标是 0、
    清单名也对不上，而且不报错。所以这里读作「不知道在哪个清单」（空串），原文里也不凭空
    多出一个 ``projectId`` 字段。
    """
    store.apply_refresh(tasks=[{"id": "t1", "title": "不知道在哪个清单", "status": 0}])

    assert store.tasks()[0].list_id == ""
    assert "projectId" not in (store.task_payload("t1") or {})



def test_task_snapshot_keeps_server_fields_the_client_does_not_know(store):
    """未知字段留在快照里，回写时才能一并带回（spec 的静默失败陷阱之一）。"""
    store.apply_refresh(
        tasks=[
            task(
                title="写周报",
                kind="TEXT",
                focusSummaries=[{"n": 1}],
                someFieldTheClientNeverHeardOf={"nested": [1, 2]},
            )
        ]
    )

    payload = store.task_payload("t1")

    assert payload is not None
    assert payload["title"] == "写周报"
    assert payload["focusSummaries"] == [{"n": 1}]
    assert payload["someFieldTheClientNeverHeardOf"] == {"nested": [1, 2]}


def test_a_refresh_carries_desc_content_and_tags_onto_the_snapshot(store):
    """服务端原文里的 ``desc`` / ``content`` / ``tags`` 落到快照的同名字段上。

    深模块的那条路是：``TaskSnapshot``（缓存里的事实）→ ``TaskItem``（引擎算好的成品）→
    右栏。这一条钉的是头一段——期望的**字段名**来自 ``api-contracts.md`` 的 ``Task`` 字段表。

    它刻意不碰中文标签（「描述」「备注」各对应哪一个字段）：v1 把两者标反了，翻过来的地方
    在详情页那一层（``GLOSSARY.md`` 说 描述=``content``、备注=``desc``；#43 已落地，见
    ``tests/test_detail_page.py`` 那条描述/备注各画各的）。哪一边是哪一边与「服务端给的
    东西有没有原样落进快照」是两件事，这一条只管后者。原本钉在
    ``test_detail_description.py`` 里（#32 搬出来的）。
    """
    store.apply_refresh(
        lists=[project(name="工作")],
        tasks=[
            task(
                title="写周报",
                desc="本周的三件事",
                content="记得附上上周的对比数据",
                tags=["工作", "季度"],
            )
        ],
    )

    snapshot = next(item for item in store.tasks() if item.id == "t1")

    assert snapshot.desc == "本周的三件事"
    assert snapshot.content == "记得附上上周的对比数据"
    assert snapshot.tags == ("工作", "季度")


def test_tasks_expose_the_view_source_snapshot(store):
    """``tasks()`` 是 ``ViewSource`` 要的形状：事实、带时区的截止时刻、已完成也返回。"""
    store.apply_refresh(
        tasks=[
            task(title="写周报", dueDate="2026-03-04T18:00:00+0800", priority=5),
            task(
                id="t2",
                title="买牛奶",
                status=2,
                dueDate="2026-03-05T00:00:00+0800",
                isAllDay=True,
            ),
        ]
    )

    snapshots = {item.id: item for item in store.tasks()}

    assert snapshots["t1"].title == "写周报"
    assert snapshots["t1"].list_id == "p1"
    assert snapshots["t1"].due == at(4, 18, 0)
    assert snapshots["t1"].all_day is False
    assert snapshots["t1"].priority == 5
    assert snapshots["t1"].completed is False
    assert snapshots["t2"].completed is True
    assert snapshots["t2"].all_day is True
    assert snapshots["t2"].due == at(5)


def test_task_without_a_due_date_reads_as_no_due(store):
    store.apply_refresh(tasks=[task(title="收集箱里的杂事")])

    assert store.tasks()[0].due is None
    assert store.tasks()[0].all_day is False


def test_enqueue_lands_a_pending_change_and_takes_effect_locally(store):
    """写操作是乐观的：入队那一刻本地就生效，不等网络（t10 的写路径靠这个）。"""
    store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-04T18:00:00+0800")])

    change = store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )

    assert change.kind is ChangeKind.UPDATE
    assert change.created_at == at(4, 9, 30)
    assert change.attempts == 0
    assert change.next_retry_at is None
    assert change.last_error is None
    assert change.list_id == "p1"
    assert store.tasks()[0].due == at(6, 9, 0)
    assert store.pending_count() == 1


def test_attempt_count_next_retry_and_last_error_round_trip(store):
    """t10 的退避重试要读的四个字段（创建时间、尝试次数、下次重试、最后一次错误）。"""
    store.apply_refresh(tasks=[task()])
    change = store.enqueue(
        task_id="t1", kind=ChangeKind.UPDATE, payload={"priority": 5}, now=at(4, 9, 30)
    )

    store.record_attempt(change.id, error="网络不可达", next_retry_at=at(4, 9, 32))

    failed = store.pending()[0]
    assert failed.attempts == 1
    assert failed.last_error == "网络不可达"
    assert failed.next_retry_at == at(4, 9, 32)
    assert failed.created_at == at(4, 9, 30)
    assert failed.payload == {"priority": 5}


def test_a_pushed_change_leaves_the_queue(store):
    store.apply_refresh(tasks=[task()])
    change = store.enqueue(
        task_id="t1", kind=ChangeKind.COMPLETE, payload={"status": 2}, now=at(4, 9, 30)
    )

    store.resolve(change.id)

    assert store.pending() == ()
    assert store.pending_count() == 0
    assert store.tasks()[0].completed is True


def test_a_deleted_task_stops_being_cached(store):
    """删除立即从本地消失；清单 id 留在待推送改动上，推送时还要用它拼 URL。"""
    store.apply_refresh(tasks=[task(), task(id="t2", title="买牛奶", project_id="p2")])

    change = store.enqueue(task_id="t1", kind=ChangeKind.DELETE, payload={}, now=at(4, 9, 30))

    assert [item.id for item in store.tasks()] == ["t2"]
    assert change.list_id == "p1"
    assert [item.task_id for item in store.pending()] == ["t1"]


def test_a_pending_change_survives_a_refresh_that_contradicts_it(store):
    """ADR-0002 的豁免，本工单最要紧的一条。

    推送失败让改动滞留在队列里，此时服务端还是旧值。若没有豁免，紧接着的一次全量刷新
    就会把用户刚做出的操作悄悄撤销掉——用户看到的是「我改的日期自己变回去了」。
    """
    store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-04T18:00:00+0800")])
    store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )

    report = store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-04T18:00:00+0800")])

    assert store.tasks()[0].due == at(6, 9, 0)
    assert store.task_payload("t1")["dueDate"] == "2026-03-06T09:00:00+0800"
    assert [(item.task_id, item.field) for item in report.suppressed] == [("t1", "dueDate")]
    assert report.suppressed[0].server == "2026-03-04T18:00:00+0800"


def test_the_exemption_covers_only_the_fields_the_change_touched(store):
    """服务端权威在没被改动过的字段上照旧生效——豁免是逐字段的，不是整条任务停摆。"""
    store.apply_refresh(tasks=[task(title="旧标题", dueDate="2026-03-04T18:00:00+0800")])
    store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )

    report = store.apply_refresh(tasks=[task(title="新标题", dueDate="2026-03-04T18:00:00+0800")])

    payload = store.task_payload("t1")
    assert payload["title"] == "新标题"
    assert payload["dueDate"] == "2026-03-06T09:00:00+0800"
    assert [item.field for item in report.overwritten] == ["title"]


def test_after_the_change_is_pushed_the_server_wins_again(store):
    """豁免只在改动还没推成功时有效：出队之后服务端权威立刻恢复。"""
    store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-04T18:00:00+0800")])
    change = store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )
    store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-04T18:00:00+0800")])

    store.resolve(change.id)
    store.apply_refresh(tasks=[task(title="写周报", dueDate="2026-03-07T08:00:00+0800")])

    assert store.tasks()[0].due == at(7, 8, 0)


def test_a_task_with_an_unpushed_delete_is_not_resurrected(store):
    """未推送的删除同样豁免：服务端还在返回它，但它不能在界面上复活。"""
    store.apply_refresh(tasks=[task(title="写周报")])
    store.enqueue(task_id="t1", kind=ChangeKind.DELETE, payload={}, now=at(4, 9, 30))

    report = store.apply_refresh(tasks=[task(title="写周报")])

    assert store.tasks() == ()
    assert store.task_payload("t1") is None
    assert [item.field for item in report.suppressed] == ["*"]


def test_a_completed_task_is_not_uncompleted_by_a_refresh(store):
    """完成的推送失败时，服务端的 ``status: 0`` 不能把完成撤销掉（服务端不可逆）。"""
    store.apply_refresh(tasks=[task(title="写周报")])
    store.enqueue(
        task_id="t1",
        kind=ChangeKind.COMPLETE,
        payload={"status": 2, "completedTime": "2026-03-04T09:30:00+0800"},
        now=at(4, 9, 30),
    )

    store.apply_refresh(tasks=[task(title="写周报", status=0)])

    assert store.tasks()[0].completed is True



def test_refetching_the_same_payload_writes_nothing(store):
    """同一份数据拉第二次不产生任何写入——「刷新」与「刷新且屏幕不闪」的区别。"""
    payload_lists = [project()]
    payload_tasks = [task(title="写周报", dueDate="2026-03-04T18:00:00+0800")]

    first = store.apply_refresh(lists=payload_lists, tasks=payload_tasks)
    second = store.apply_refresh(lists=payload_lists, tasks=payload_tasks)

    assert (first.written_lists, first.written_tasks) == (1, 1)
    assert (second.written_lists, second.written_tasks) == (0, 0)
    assert second.overwritten == ()
    assert second.suppressed == ()


def test_key_order_is_not_a_change(store):
    """同一份内容换了键序仍然是同一份内容，不该当成变化写一遍。"""
    store.apply_refresh(tasks=[{"id": "t1", "projectId": "p1", "title": "写周报"}])

    report = store.apply_refresh(tasks=[{"title": "写周报", "projectId": "p1", "id": "t1"}])

    assert report.written_tasks == 0


def test_only_the_task_that_changed_is_written(store):
    store.apply_refresh(tasks=[task(id="t1"), task(id="t2", title="买牛奶")])

    report = store.apply_refresh(tasks=[task(id="t1"), task(id="t2", title="买牛奶（改了）")])

    assert report.written_tasks == 1


def test_sync_state_keeps_cursor_last_refresh_and_logical_day(store):
    """spec 的同步状态三件套：已完成流游标、上次刷新完成时间、上次算出的逻辑日。"""
    store.set_sync_state(
        completed_cursor="2026-03-04T09:00:00+0800",
        last_refresh_at=at(4, 9, 30),
        logical_day=date(2026, 3, 4),
    )

    state = store.stored_sync_state()

    assert state.completed_cursor == "2026-03-04T09:00:00+0800"
    assert state.last_refresh_at == at(4, 9, 30)
    assert state.logical_day == date(2026, 3, 4)


def test_view_source_sync_state_reports_the_live_pending_count(store):
    """状态栏要常驻的待推送数量，从队列现算，不是另存一个会漂的计数。"""
    store.apply_refresh(tasks=[task()])
    store.set_sync_state(last_refresh_at=at(4, 9, 30), logical_day=date(2026, 3, 4))
    store.enqueue(
        task_id="t1", kind=ChangeKind.UPDATE, payload={"priority": 5}, now=at(4, 9, 31)
    )

    state = store.sync_state()

    assert state.last_refresh_at == at(4, 9, 30)
    assert state.pending_count == 1


def test_a_fresh_store_has_an_empty_sync_state(store):
    assert store.stored_sync_state() == StoredSyncState()
    assert store.sync_state() == SyncState()


def test_everything_survives_reopening_the_database(tmp_path):
    """本地库的意义就是重开还在（启动秒开靠它）。"""
    path = tmp_path / "dida.sqlite3"
    with Store(path) as first:
        first.apply_refresh(lists=[project()], tasks=[task(title="写周报")])
        first.set_sync_state(
            completed_cursor="c1", last_refresh_at=at(4, 9, 30), logical_day=date(2026, 3, 4)
        )
        first.enqueue(
            task_id="t1", kind=ChangeKind.UPDATE, payload={"priority": 5}, now=at(4, 9, 31)
        )

    with Store(path) as second:
        assert [item.name for item in second.lists()] == ["工作"]
        assert [item.title for item in second.tasks()] == ["写周报"]
        assert second.task_payload("t1")["priority"] == 5
        assert second.stored_sync_state() == StoredSyncState(
            completed_cursor="c1", last_refresh_at=at(4, 9, 30), logical_day=date(2026, 3, 4)
        )
        assert second.pending()[0].attempts == 0
        assert second.pending_count() == 1


async def test_the_stored_snapshot_feeds_the_api_client_write_back(store):
    """存储里那份**字典形状**的快照直接喂给 t07 的 ``update_task(snapshot=)``。

    这是「未知字段透传」在存储层的落点：客户端不认识的字段必须出现在请求体里，
    否则一次改期就会把手机端设的提醒、专注记录之类悄悄抹掉。
    """
    transport = FakeTransport(json={"id": "t1"})
    client = DidaApiClient(token="tok", transport=transport)
    store.apply_refresh(
        tasks=[task(title="写周报", focusSummaries=[{"n": 1}], someUnknown={"a": 1})]
    )

    await client.update_task(
        "p1",
        "t1",
        {"dueDate": "2026-03-06T09:00:00+0800"},
        snapshot=store.task_payload("t1"),
    )

    body = transport.last_json
    assert body["someUnknown"] == {"a": 1}
    assert body["focusSummaries"] == [{"n": 1}]
    assert body["title"] == "写周报"
    assert body["dueDate"] == "2026-03-06T09:00:00+0800"
    assert body["id"] == "t1"
    assert body["projectId"] == "p1"


def test_the_inbox_is_flagged_as_the_inbox(store):
    """收集箱在 API 里是 ``"inbox"`` 这个别名，界面上它是独立一栏（spec 的清单 schema）。"""
    store.apply_refresh(lists=[project(), project(id="inbox", name="收集箱")])

    inbox = {item.id: item for item in store.list_records()}["inbox"]

    assert inbox.is_inbox is True
    assert {item.id: item for item in store.list_records()}["p1"].is_inbox is False


def test_a_malformed_due_date_does_not_break_the_refresh(store):
    """一条脏日期不该把整次刷新带崩：读作「没有截止时间」，原文照旧留着。"""
    store.apply_refresh(tasks=[task(title="写周报", dueDate="下周三")])

    assert store.tasks()[0].due is None
    assert store.task_payload("t1")["dueDate"] == "下周三"


def test_a_new_task_shows_up_locally_before_it_is_pushed(store):
    """新建也是乐观的：还没推到服务端，本地已经能看见它。"""
    change = store.enqueue(
        task_id="local-1",
        kind=ChangeKind.CREATE,
        payload={"title": "买牛奶", "projectId": "inbox"},
        now=at(4, 9, 30),
    )

    assert change.list_id == "inbox"
    assert [(item.id, item.title, item.list_id) for item in store.tasks()] == [
        ("local-1", "买牛奶", "inbox")
    ]


def test_two_changes_on_one_task_are_both_exempt(store):
    """同一条任务上有多条未推送改动时，豁免是它们碰过的字段的并集。"""
    store.apply_refresh(tasks=[task(title="写周报", priority=0, dueDate="2026-03-04T18:00:00+0800")])
    store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )
    store.enqueue(task_id="t1", kind=ChangeKind.UPDATE, payload={"priority": 5}, now=at(4, 9, 31))

    report = store.apply_refresh(
        tasks=[task(title="写周报", priority=0, dueDate="2026-03-04T18:00:00+0800")]
    )

    payload = store.task_payload("t1")
    assert payload["priority"] == 5
    assert payload["dueDate"] == "2026-03-06T09:00:00+0800"
    assert {item.field for item in report.suppressed} == {"priority", "dueDate"}
    assert store.pending_count() == 2


def test_resolving_one_change_narrows_the_exemption(store):
    """推成功的那条改动立刻把它碰过的字段交还给服务端权威，另一条照旧豁免。"""
    store.apply_refresh(tasks=[task(title="写周报", priority=0, dueDate="2026-03-04T18:00:00+0800")])
    pushed = store.enqueue(
        task_id="t1",
        kind=ChangeKind.UPDATE,
        payload={"dueDate": "2026-03-06T09:00:00+0800"},
        now=at(4, 9, 30),
    )
    store.enqueue(task_id="t1", kind=ChangeKind.UPDATE, payload={"priority": 5}, now=at(4, 9, 31))
    store.resolve(pushed.id)

    store.apply_refresh(tasks=[task(title="写周报", priority=0, dueDate="2026-03-05T08:00:00+0800")])

    payload = store.task_payload("t1")
    assert payload["dueDate"] == "2026-03-05T08:00:00+0800"
    assert payload["priority"] == 5


def test_a_refresh_that_blows_up_halfway_writes_nothing(store):
    """一次刷新要么整份落地要么完全不落地：半份数据比旧数据更难查。"""
    with pytest.raises(KeyError):
        store.apply_refresh(tasks=[task(id="t1"), {"title": "服务端给了个没有 id 的东西"}])

    assert store.tasks() == ()
