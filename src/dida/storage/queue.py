"""队列表的**记账机制**（#82）：一本账的做法，与它记的是谁无关。

本地有两张队列表——``pending_changes``（任务改动）与 ``pending_list_changes``（清单改动，
#42）——因为任务 id 与清单 id 是两件事（``list_id`` 塞进 ``task_id`` 那一列，一个字段两个意思，
读的人第一步就错）。**分开的是词汇表与表**；重复的是记这本账的**做法**：

- 新入队那一行的账面：还没试过（``attempts = 0``）、没有下次重试、没有错误；
- 失败一次：尝试次数 +1、写下最后一次错误与下次重试时刻；
- 出队：推成功了就从队列里删掉；
- 认领：这一笔（以及后面排着的几笔）改的是服务端刚给的那个 id 了。

这四件事原来在 :mod:`dida.storage.store` 里成对写了两遍（``record_attempt`` /
``record_list_attempt``、``resolve`` / ``resolve_list``、``adopt_created`` /
``adopt_created_list``），于是加一条队列级的规则（比如「试满几次」怎么记）要改两处。它们
现在只住在这里一份。

**这一层不知道任务与清单是什么**：表名与「谁的 id」那一列由调用方以 :class:`QueueShape`
递进来，其余字段（``kind`` / ``payload`` / 任务行上那个 ``list_id``）也由调用方给。
所以新队列、或者队列级的一条新事实，改的是这一个类，而不是每一张表各一遍。

SQL 里的表名与列名来自 :class:`QueueShape`——那是模块里写死的两张形状（``Store`` 构造时
给出），不是外面递进来的字符串，所以这里拼 SQL 没有注入口。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class QueueShape:
    """一本队列账的**形状**：机制唯一需要调用方给的两样东西。

    ``table`` 是队列表名，``owner_column`` 是「这一笔改的是谁」那一列（任务的 ``task_id``、
    清单的 ``list_id``）。两份词汇表照旧分开——这里只是说清：机制按这两样认出各自的表，
    而不是把它们合成一张。
    """

    table: str
    owner_column: str


class PendingQueue:
    """一张队列表上的记账：入账、失败一次、出队、认领换名。

    每个方法都只写这一层的事，事务由调用方开（``with store._db``）：入队要与「本地立刻生效」
    同一个事务，认领要与那张本地表的挪行同一个事务——那两样是各自的数据形状，不是这本账
    的事。
    """

    def __init__(self, db: sqlite3.Connection, shape: QueueShape) -> None:
        self._db = db
        self._shape = shape

    def insert(self, *, owner: str, values: Mapping[str, Any], now: datetime) -> int:
        """记下一条新改动，返回它的行号；账面的起点在这里写。

        ``values`` 是调用方那一行的其余字段（``kind`` / ``payload``，任务那行还有 ``list_id``）
        ——任务与清单各自的数据形状。``attempts`` / ``next_retry_at`` / ``last_error`` 与
        ``created_at`` 由机制统一写。
        """
        row: dict[str, Any] = {
            self._shape.owner_column: owner,
            **values,
            "created_at": now.isoformat(),
            "attempts": 0,
            "next_retry_at": None,
            "last_error": None,
        }
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cursor = self._db.execute(
            f"INSERT INTO {self._shape.table} ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        return int(cursor.lastrowid or 0)

    def rows(self) -> list[sqlite3.Row]:
        """还没推成功的改动，按发生顺序（两个推送循环都按这个顺序挑）。"""
        return self._db.execute(f"SELECT * FROM {self._shape.table} ORDER BY id").fetchall()

    def row(self, change_id: int) -> sqlite3.Row | None:
        """一条改动那一行（刚入队时用它取回账面上的样子）；没有就是 ``None``。"""
        return self._db.execute(
            f"SELECT * FROM {self._shape.table} WHERE id = ?", (change_id,)
        ).fetchone()

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次推送失败：尝试次数 +1、写下最后一次错误与下次重试时刻。

        退避怎么算（第几次、隔多久）是引擎的事；这一层只负责把结果存住。
        """
        self._db.execute(
            f"UPDATE {self._shape.table}"
            " SET attempts = attempts + 1, last_error = ?, next_retry_at = ?"
            " WHERE id = ?",
            (error, next_retry_at.isoformat() if next_retry_at else None, change_id),
        )

    def dequeue(self, change_id: int) -> None:
        """这条改动已经推到服务端了，出队。"""
        self._db.execute(f"DELETE FROM {self._shape.table} WHERE id = ?", (change_id,))

    def repoint(self, old_owner: str, new_owner: str) -> None:
        """认领：指向 ``old_owner`` 的改动（一笔或几笔）现在都归 ``new_owner``。

        服务端建好之后才知道真 id；不挪的话，排在临时 id 后面的那几笔永远推不出去
        （服务端没有那个 id），状态栏那个数一直非零。
        """
        self._db.execute(
            f"UPDATE {self._shape.table} SET {self._shape.owner_column} = ?"
            f" WHERE {self._shape.owner_column} = ?",
            (new_owner, old_owner),
        )
