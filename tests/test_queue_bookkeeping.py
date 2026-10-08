"""队列记账（#82）：这本账只有一份实现。

接缝是 :class:`~dida.storage.queue.PendingQueue` 自己的窄接口——它只要「表名 + 谁的 id 那一列」，
其余（``kind`` / ``payload`` / 任务行上那个 ``list_id``）全由调用方给。所以这里当场造**第三张**
队列表：它既不是任务队列也不是清单队列，却走一遍两者都走过的记账——入账的账面、失败一次
（尝试次数 +1、最后一次错误、下次重试时刻）、出队、认领换名。它跑得通，说明新队列拿到的是一段
共享的机制，而不是抄一份；加一条队列级的事实只改机制那一处。

**两份词汇表照旧分开**：任务 id 与清单 id 是两件事，所以这里连表名与 id 列名都是自己起的第三套
——机制对它们一视同仁，但存储层里那两张表、两个 id 仍然各是各的。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dida.storage.queue import PendingQueue, QueueShape

STORAGE = Path(__file__).resolve().parents[1] / "src" / "dida" / "storage"
"""存储层这一支（新机制与它的两个调用者都在里面）。"""

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 4, 9, 0, tzinfo=TZ)


def third_queue() -> PendingQueue:
    """造第三本队列账：形状与两张真表一样，但「谁」是 ``note_id``。"""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE pending_note_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id       TEXT NOT NULL,
            kind          TEXT NOT NULL,
            payload       TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            attempts      INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            last_error    TEXT
        )
        """
    )
    return PendingQueue(db, QueueShape(table="pending_note_changes", owner_column="note_id"))


def test_a_new_queue_gets_the_same_bookkeeping_without_a_second_implementation():
    """一本全新的队列账，五件事一样不缺——用的就是任务与清单用的那一个类。"""
    queue = third_queue()

    change_id = queue.insert(
        owner="note-1", values={"kind": "UPDATE", "payload": "{}"}, now=T0
    )

    fresh = queue.rows()[0]
    assert fresh["note_id"] == "note-1"
    assert (fresh["attempts"], fresh["next_retry_at"], fresh["last_error"]) == (0, None, None), (
        "账面的起点（没试过、没下次、没错误）是机制写下的，不是每个调用方各写一遍"
    )

    queue.record_attempt(change_id, error="断网了", next_retry_at=T0 + timedelta(seconds=5))

    failed = queue.row(change_id)
    assert failed is not None
    assert failed["attempts"] == 1
    assert failed["last_error"] == "断网了"
    assert datetime.fromisoformat(failed["next_retry_at"]) == T0 + timedelta(seconds=5)

    queue.repoint("note-1", "server-9")

    assert [row["note_id"] for row in queue.rows()] == ["server-9"], "认领改的是这一列"

    queue.dequeue(change_id)

    assert queue.rows() == [], "出队就是没了，没有第二本账要一起清"


def test_the_mechanism_is_the_only_home_for_the_queue_bookkeeping():
    """结构说明那一半：两本账的做法只许住在一个地方。

    ``store.py`` 里不再有这本账的 SQL，而它在整个存储层里正好出现一次——两处就是两本账，
    正是这张票要收掉的东西。守卫按**源码**数，所以第二个实现一落地就会红。
    """
    sources = {path.name: path.read_text(encoding="utf-8") for path in STORAGE.glob("*.py")}
    homes = sorted(name for name, text in sources.items() if "attempts = attempts + 1" in text)

    assert homes == ["queue.py"], f"「试一次 +1」这份账应当只有机制一处，实际在 {homes}"

    throttled = [
        fragment
        for fragment in (
            "INSERT INTO pending_changes",
            "INSERT INTO pending_list_changes",
            "DELETE FROM pending_changes",
            "DELETE FROM pending_list_changes",
        )
        if fragment in sources["store.py"]
    ]
    assert throttled == [], (
        "入队与出队的账也只许机制写（store.py 只把它自己的数据形状递进去）："
        + "、".join(throttled)
    )
