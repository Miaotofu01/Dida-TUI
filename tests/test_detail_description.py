"""右栏常驻显示描述 / 标签 / 备注（故事 20；评审修 A4）。

子任务那一半（:class:`~dida.tui.panes.SubtaskPane`）已经落地，也有自己的测试；这里补的是
另外三样，以及它们从**服务端原文**一路到右栏的那条路：

``TaskSnapshot``（缓存里的事实）→ ``TaskItem``（引擎算好的成品）→ :func:`detail_body`（画）。

字段映射按 ``api-contracts.md`` 的 ``Task`` 字段表：``desc`` 是任务的描述、``content`` 是
备注/正文、``tags`` 是标签名。TUI 一个都不解析，只画视图模型给的东西——所以「有没有描述」
这种判断也不在这一层，空的那一行根本不出现。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.storage.store import Store
from dida.sync.view import TaskItem
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import DetailPane, detail_body
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """一条带描述、标签与备注的任务，光标默认就停在它上面。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task(
        "写周报",
        list_name="工作",
        id="t1",
        due=at(14, 18, 0),
        desc="本周的三件事",
        content="记得附上上周的对比数据",
        tags=("工作", "季度"),
    )
    return backend


def selected_item(backend: FakeBackend) -> TaskItem:
    """视图模型里那一条（右栏画的就是它）。"""
    return backend.view().groups[0].items[0]


def test_the_detail_body_shows_the_description_the_tags_and_the_notes():
    """三行都在：描述 / 标签 / 备注。"""
    body = detail_body(selected_item(make_backend())).plain

    assert "描述  本周的三件事" in body
    assert "标签  #工作 #季度" in body
    assert "备注  记得附上上周的对比数据" in body


def test_a_task_without_them_shows_no_empty_rows():
    """没有描述 / 标签 / 备注时，右栏不长出三个空标签。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("买牛奶", list_name="生活", id="t1")

    body = detail_body(selected_item(backend)).plain

    assert "描述" not in body
    assert "标签" not in body
    assert "备注" not in body


async def test_the_right_pane_shows_them_for_the_selected_task():
    """接缝一：选中哪一条，右栏就常驻显示它的描述、标签与备注。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert app.query_one(DetailPane).task_id == "t1"
        text = screen_text(app)

    assert "本周的三件事" in text
    assert "#工作 #季度" in text
    assert "记得附上上周的对比数据" in text


def test_a_refresh_carries_them_from_the_server_payload_into_the_snapshot(tmp_path):
    """缓存那一段：服务端原文里的 ``desc`` / ``content`` / ``tags`` 落到快照上。

    期望的字段名来自 ``api-contracts.md`` 的 ``Task`` 字段表，不是照代码再抄一遍。
    """
    store = Store(tmp_path / "dida.sqlite3")
    try:
        store.apply_refresh(
            lists=[{"id": "work", "name": "工作", "sortOrder": 1}],
            tasks=[
                {
                    "id": "t1",
                    "projectId": "work",
                    "title": "写周报",
                    "desc": "本周的三件事",
                    "content": "记得附上上周的对比数据",
                    "tags": ["工作", "季度"],
                }
            ],
        )
        snapshot = next(task for task in store.tasks() if task.id == "t1")
    finally:
        store.close()

    assert snapshot.desc == "本周的三件事"
    assert snapshot.content == "记得附上上周的对比数据"
    assert snapshot.tags == ("工作", "季度")
