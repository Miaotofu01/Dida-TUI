"""本地存储（第 3 个深模块）：SQLite（stdlib），无 ORM、无迁移框架。

四类数据：

- 清单（API 叫 project，界面叫清单）：名称、颜色、排序、项目组、是否收集箱，
- 任务快照：服务端原始字段 **含未知字段** + 本地已生效的改动，
- 待推送改动（Pending Change）：创建时间、尝试次数、下次重试时间、最后一次错误，
- 同步状态：已完成流游标、上次刷新完成时间、上次算出的逻辑日。

「增量」是这一层的概念：全量拉回来的数据在这里比对，只写变化（ADR 0001）。
冲突裁决也在这里：服务端权威胜出，但待推送改动豁免（ADR 0002）——**没有这个豁免，
一次推送失败加一次全量刷新就会把用户刚做出的操作悄悄撤销掉**。豁免是逐字段的：
改动碰过的字段本地值赢，其余字段服务端照旧赢；未推送的删除则整条任务豁免。

``Store`` 同时是 t05 的 ``ViewSource`` 的生产实现（``lists`` / ``tasks`` / ``sync_state``），
所以引擎与 TUI 拿到的形状和它们已经写好的测试一致。

时间一律由调用方给（``now`` / ``last_refresh_at``）：这一层没有时钟，
「现在」永远从注入的 ``Clock`` 来。写操作都是乐观的——``enqueue`` 当场让改动在本地生效，
不等网络；推不推得动是 t10 的事。

公开接口：

- 开关：``close()``、``with Store(path) as store``；
- 读（``ViewSource``）：``lists()`` / ``tasks()`` / ``sync_state()``；
- 读（完整记录）：``list_records()`` / ``task_payload(task_id)`` / ``stored_sync_state()``；
- 写：``apply_refresh(lists=, tasks=)``（只写变化，返回 ``RefreshReport``）、
  ``enqueue(...)`` / ``pending()`` / ``pending_count()`` / ``record_attempt(...)`` /
  ``resolve(change_id)`` / ``set_sync_state(...)``。

``task_payload()`` 是给 t07 的 ``update_task(snapshot=)`` 用的那一份：**字典形状**，
不是领域 dataclass，客户端不认识的字段一个都不丢。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from dida.sync.view import INBOX_ID, ListSnapshot, SyncState, TaskSnapshot

COMPLETED_STATUS = 2
"""任务「已完成」的 ``status`` 值（api-contracts.md：Completed 是 2，不是 1）。"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT,
    sort_order  INTEGER,
    group_id    TEXT,
    is_inbox    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    list_id     TEXT NOT NULL,
    raw         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    list_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error    TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    completed_cursor TEXT,
    last_refresh_at  TEXT,
    logical_day      TEXT
);
"""


class ChangeKind(Enum):
    """改动种类：对应 API 的四个写操作（新建 / 更新 / 完成 / 删除）。"""

    CREATE = "create"
    UPDATE = "update"
    COMPLETE = "complete"
    DELETE = "delete"


@dataclass(frozen=True)
class ListRecord:
    """缓存里一条清单的完整记录（spec 的清单 schema：名称、颜色、排序、项目组、是否收集箱）。"""

    id: str
    name: str
    color: str | None = None
    sort_order: int | None = None
    group_id: str | None = None
    is_inbox: bool = False


@dataclass(frozen=True)
class PendingChange:
    """一条还没推到服务端的本地改动（spec 的待推送改动 schema）。"""

    id: int
    """本地行号；推送成功时用它 :meth:`Store.resolve`。"""

    task_id: str
    list_id: str
    """改动发生时任务所在的清单；推送要拿它拼 URL。"""

    kind: ChangeKind
    payload: dict[str, Any]
    """改动涉及的字段（本地已生效的那一份）。"""

    created_at: datetime
    attempts: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class StoredSyncState:
    """缓存里的同步状态（spec 的同步状态 schema）。

    比 ``sync.view.SyncState`` 宽：那个是引擎给 TUI 的视图模型，只有上次刷新时间与
    待推送数量；游标和逻辑日只在这一层与引擎之间流动，所以单独一个类型，
    不去改 t05 已经定稿的协议。
    """

    completed_cursor: str | None = None
    """已完成流拉到哪了（t09 的窗口从这里续）。"""

    last_refresh_at: datetime | None = None
    logical_day: date | None = None
    """上次算出来的逻辑日；缓存里那些「今天」的读法就是按它写的。"""


@dataclass(frozen=True)
class FieldOverride:
    """一次全量刷新对某条任务某个字段的处置。

    ``field`` 是 ``"*"`` 时表示**整条任务**：本地有一条还没推成功的删除，服务端那份
    整个不许写回来，否则用户删掉的任务会在下一次刷新时复活。
    """

    task_id: str
    field: str
    local: Any
    server: Any


@dataclass(frozen=True)
class RefreshReport:
    """一次全量刷新的结果。

    ``overwritten`` 是服务端权威真的把本地值盖掉的字段（ADR-0002 要求这种覆盖能被
    用户看见）；``suppressed`` 是被待推送改动挡回去的那些——两者都不含没变化的东西，
    所以重复拉同一份数据的报告是空的。
    """

    written_lists: int = 0
    written_tasks: int = 0
    overwritten: tuple[FieldOverride, ...] = ()
    suppressed: tuple[FieldOverride, ...] = ()


class Store:
    """本地副本。噪声都在里面：schema、diff、冲突裁决。"""

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        """关掉连接。"""
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- 写：全量刷新

    def apply_refresh(
        self,
        *,
        lists: Sequence[Mapping[str, Any]] = (),
        tasks: Sequence[Mapping[str, Any]] = (),
    ) -> RefreshReport:
        """把一次全量刷新的原文写进来，只写变化，并裁决冲突。

        整次刷新是一个事务：要么整份落地，要么一点都不落地。

        冲突裁决的规矩就一句（ADR-0002）：**服务端权威，除非有还没推成功的待推送改动**。
        每个字段单独判：改动碰过的字段本地值赢，其余字段服务端照旧赢。

        服务端传来的是一份完整的任务原文，所以本地多出来的、服务端没有的字段会被丢掉
        ——除了被豁免的那些。
        """
        written_lists = 0
        written_tasks = 0
        overwritten: list[FieldOverride] = []
        suppressed: list[FieldOverride] = []

        # 整次刷新一个事务：成功才提交，中途出岔子就整份回滚。半份刷新比旧数据更难查。
        with self._db:
            for payload in lists:
                written_lists += int(self._write_list(payload))
            for payload in tasks:
                task_id = str(payload["id"])
                exempt = self._exempt_fields(task_id)
                if exempt is None:
                    # 有未推送的删除：服务端这份整个不许写回来，否则任务会复活。
                    suppressed.append(FieldOverride(task_id, "*", None, dict(payload)))
                    continue
                local = self._raw_of(task_id) or {}
                merged = dict(payload)
                for field in exempt:
                    if field in local:
                        if local[field] != payload.get(field):
                            suppressed.append(
                                FieldOverride(task_id, field, local[field], payload.get(field))
                            )
                        merged[field] = local[field]
                overwritten.extend(
                    FieldOverride(task_id, field, local[field], value)
                    for field, value in payload.items()
                    if field in local and local[field] != value and field not in exempt
                )
                written_tasks += int(self._write_task(merged))

        return RefreshReport(
            written_lists=written_lists,
            written_tasks=written_tasks,
            overwritten=tuple(overwritten),
            suppressed=tuple(suppressed),
        )

    # ---------------------------------------------------------------- 读

    def task_payload(self, task_id: str) -> dict[str, Any] | None:
        """一条任务的完整原文（服务端字段含未知字段 + 本地已生效的改动）。

        这就是喂给 ``DidaApiClient.update_task(snapshot=)`` 的那一份：**字典形状**，
        不是领域 dataclass，所以客户端不认识的字段也在里面，回写时不会丢。
        """
        row = self._db.execute("SELECT raw FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return None if row is None else json.loads(row["raw"])

    def lists(self) -> tuple[ListSnapshot, ...]:
        """全部清单（``ViewSource``）。"""
        return tuple(ListSnapshot(id=row["id"], name=row["name"]) for row in self._list_rows())

    def list_records(self) -> tuple[ListRecord, ...]:
        """全部清单的完整记录。"""
        return tuple(
            ListRecord(
                id=row["id"],
                name=row["name"],
                color=row["color"],
                sort_order=row["sort_order"],
                group_id=row["group_id"],
                is_inbox=bool(row["is_inbox"]),
            )
            for row in self._list_rows()
        )

    def tasks(self) -> tuple[TaskSnapshot, ...]:
        """全部任务快照，含已完成（``ViewSource``）。

        只有事实，没有判断：哪条属于今日、逾期与否，是引擎拿 ``sync.view`` 的纯函数
        去算的，这一层不碰。
        """
        return tuple(_snapshot(json.loads(row["raw"])) for row in self._task_rows())

    # ---------------------------------------------------------------- 写：待推送改动

    def enqueue(
        self,
        *,
        task_id: str,
        kind: ChangeKind,
        payload: Mapping[str, Any],
        now: datetime,
        list_id: str | None = None,
    ) -> PendingChange:
        """入队一条待推送改动，并让它在本地立刻生效（乐观写）。

        ``now`` 由调用方给：这一层没有时钟，「现在」永远是注入进来的（CONTEXT 的硬规矩）。

        ``DELETE`` 的特殊之处是它的本地效果是「这条任务不再存在」——所以直接摘掉快照。
        清单 id 记在改动行上，推送时仍然拼得出 URL。
        """
        resolved_list = list_id or self._list_of(task_id)
        # 本地生效与入队同一个事务：不会出现「生效了但没进队列」这种下次刷新就丢的状态。
        with self._db:
            if kind is ChangeKind.DELETE:
                self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            else:
                self._apply_locally(task_id, payload)

            cursor = self._db.execute(
                """
                INSERT INTO pending_changes
                    (task_id, list_id, kind, payload, created_at,
                     attempts, next_retry_at, last_error)
                VALUES (?, ?, ?, ?, ?, 0, NULL, NULL)
                """,
                (
                    task_id,
                    resolved_list,
                    kind.value,
                    _dumps(payload),
                    now.isoformat(),
                ),
            )
            change = self._change_row(int(cursor.lastrowid or 0))
        assert change is not None
        return change

    def pending(self) -> tuple[PendingChange, ...]:
        """还没推成功的改动，按发生顺序（t10 的重试队列按这个顺序挑）。

        「什么时候该重试」不在这里筛：判断「现在」要有钟，那是引擎的事。
        """
        rows = self._db.execute("SELECT * FROM pending_changes ORDER BY id").fetchall()
        return tuple(_change(row) for row in rows)

    def pending_count(self) -> int:
        """待推送数量；状态栏常驻显示这个数（ADR 0002 的豁免代价）。"""
        row = self._db.execute("SELECT COUNT(*) AS n FROM pending_changes").fetchone()
        return int(row["n"])

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次推送失败：尝试次数 +1，写下最后一次错误与下次重试时刻。

        退避怎么算（第几次、隔多久）是引擎的事；这一层只负责把结果存住。
        """
        with self._db:
            self._db.execute(
                """
                UPDATE pending_changes
                   SET attempts = attempts + 1, last_error = ?, next_retry_at = ?
                 WHERE id = ?
                """,
                (error, next_retry_at.isoformat() if next_retry_at else None, change_id),
            )

    def resolve(self, change_id: int) -> None:
        """这条改动已经推到服务端了，出队。"""
        with self._db:
            self._db.execute("DELETE FROM pending_changes WHERE id = ?", (change_id,))

    # ---------------------------------------------------------------- 同步状态

    def set_sync_state(
        self,
        *,
        completed_cursor: str | None = None,
        last_refresh_at: datetime | None = None,
        logical_day: date | None = None,
    ) -> None:
        """整体写入同步状态。

        ``None`` 是「清空」而不是「不动」：引擎手里握着完整的状态，一次写完比
        三个各自可选的 setter 更难写错。时刻由调用方给，这一层没有时钟。
        """
        with self._db:
            self._db.execute(
                """
                INSERT INTO sync_state (id, completed_cursor, last_refresh_at, logical_day)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    completed_cursor = excluded.completed_cursor,
                    last_refresh_at = excluded.last_refresh_at,
                    logical_day = excluded.logical_day
                """,
                (
                    completed_cursor,
                    last_refresh_at.isoformat() if last_refresh_at else None,
                    logical_day.isoformat() if logical_day else None,
                ),
            )

    def stored_sync_state(self) -> StoredSyncState:
        """同步状态原样读回。"""
        row = self._db.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()
        if row is None:
            return StoredSyncState()
        return StoredSyncState(
            completed_cursor=row["completed_cursor"],
            last_refresh_at=(
                datetime.fromisoformat(row["last_refresh_at"]) if row["last_refresh_at"] else None
            ),
            logical_day=date.fromisoformat(row["logical_day"]) if row["logical_day"] else None,
        )

    def sync_state(self) -> SyncState:
        """``ViewSource`` 要的那一份：上次刷新时间 + 现算的待推送数量。"""
        stored = self.stored_sync_state()
        return SyncState(last_refresh_at=stored.last_refresh_at, pending_count=self.pending_count())

    # ---------------------------------------------------------------- 内部

    def _list_rows(self) -> list[sqlite3.Row]:
        """清单按服务端的 ``sortOrder`` 排；没有排序值的排在后面。"""
        return list(
            self._db.execute(
                "SELECT * FROM lists ORDER BY (sort_order IS NULL), sort_order, id"
            )
        )

    def _task_rows(self) -> list[sqlite3.Row]:
        """任务按 id 排：顺序稳定，读出来的东西就不随写入顺序漂。"""
        return list(self._db.execute("SELECT * FROM tasks ORDER BY id"))

    def _list_of(self, task_id: str) -> str:
        """任务所在的清单 id（收集箱兜底）。"""
        row = self._db.execute("SELECT list_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return str(row["list_id"]) if row is not None else INBOX_ID

    def _raw_of(self, task_id: str) -> dict[str, Any] | None:
        """本地那份任务原文（没有这条任务时 ``None``）。"""
        row = self._db.execute("SELECT raw FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return None if row is None else json.loads(row["raw"])

    def _exempt_fields(self, task_id: str) -> frozenset[str] | None:
        """这条任务上哪些字段豁免于服务端权威。

        返回 ``None`` 表示**整条任务**都豁免：本地有一条还没推成功的删除，
        服务端那份一个字也不许写回来。除此之外豁免是逐字段的——改动碰过的字段本地赢，
        没碰过的照旧服务端赢。
        """
        rows = self._db.execute(
            "SELECT kind, payload FROM pending_changes WHERE task_id = ?", (task_id,)
        ).fetchall()
        if any(row["kind"] == ChangeKind.DELETE.value for row in rows):
            return None
        exempt: set[str] = set()
        for row in rows:
            exempt.update(json.loads(row["payload"]))
        return frozenset(exempt)

    def _apply_locally(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """把改动盖到本地那份快照上。

        覆盖而不是替换：没被改动的字段（尤其是客户端不认识的）原样留着，这样
        ``task_payload()`` 交给 ``update_task(snapshot=)`` 时仍然是一份完整的底稿。
        """
        current = self._raw_of(task_id) or {"id": task_id}
        current.update(payload)
        self._write_task(current)

    def _change_row(self, change_id: int) -> PendingChange | None:
        row = self._db.execute(
            "SELECT * FROM pending_changes WHERE id = ?", (change_id,)
        ).fetchone()
        return None if row is None else _change(row)

    def _write_list(self, payload: Mapping[str, Any]) -> bool:
        """写一条清单；内容没变就不写（ADR 0001 的「只写变化」）。"""
        values = {
            "id": str(payload["id"]),
            "name": str(payload.get("name") or ""),
            "color": payload.get("color"),
            "sort_order": payload.get("sortOrder"),
            "group_id": payload.get("groupId"),
            "is_inbox": 1 if payload.get("isInbox") or payload.get("id") == INBOX_ID else 0,
        }
        row = self._db.execute("SELECT * FROM lists WHERE id = ?", (values["id"],)).fetchone()
        if row is not None and all(row[key] == value for key, value in values.items()):
            return False
        self._db.execute(
            """
            INSERT INTO lists (id, name, color, sort_order, group_id, is_inbox)
            VALUES (:id, :name, :color, :sort_order, :group_id, :is_inbox)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                color = excluded.color,
                sort_order = excluded.sort_order,
                group_id = excluded.group_id,
                is_inbox = excluded.is_inbox
            """,
            values,
        )
        return True

    def _write_task(self, payload: Mapping[str, Any]) -> bool:
        """写一条任务快照。

        ``raw`` 是权威的那一份：服务端给的全部字段原样序列化（``sort_keys`` 让同一份
        逻辑内容有唯一的字符串形式，diff 才能直接比字符串）。字段一个都不筛，
        因为客户端不认识的字段恰恰是回写时最容易丢的那些。
        """
        task_id = str(payload["id"])
        raw = _dumps(payload)
        existing = self._db.execute("SELECT raw FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is not None and existing["raw"] == raw:
            return False
        self._db.execute(
            """
            INSERT INTO tasks (id, list_id, raw) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET list_id = excluded.list_id, raw = excluded.raw
            """,
            (task_id, str(payload.get("projectId") or INBOX_ID), raw),
        )
        return True


def _dumps(payload: Mapping[str, Any]) -> str:
    """任务原文的规范序列化：键排序，保证同一份内容只有一个字符串形式。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _snapshot(payload: Mapping[str, Any]) -> TaskSnapshot:
    """服务端原文 → ``ViewSource`` 要的任务快照。

    只翻译，不判断：``status`` 是不是「已完成」由 API 决定（Completed 是 ``2``），
    这条属于今日还是逾期则完全不是这里的事。
    """
    due = payload.get("dueDate")
    return TaskSnapshot(
        id=str(payload["id"]),
        title=str(payload.get("title") or ""),
        list_id=str(payload.get("projectId") or INBOX_ID),
        due=_parse_time(due) if isinstance(due, str) else None,
        all_day=bool(payload.get("isAllDay")),
        priority=int(payload.get("priority") or 0),
        completed=payload.get("status") == COMPLETED_STATUS,
    )


def _change(row: sqlite3.Row) -> PendingChange:
    """数据库行 → 待推送改动。"""
    return PendingChange(
        id=int(row["id"]),
        task_id=str(row["task_id"]),
        list_id=str(row["list_id"]),
        kind=ChangeKind(row["kind"]),
        payload=json.loads(row["payload"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        attempts=int(row["attempts"]),
        next_retry_at=(
            datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None
        ),
        last_error=row["last_error"],
    )


def _parse_time(value: str) -> datetime | None:
    """解析服务端日期。

    文档的形状是 ``yyyy-MM-dd'T'HH:mm:ssZ``，实测里偏移既可能是 ``+0800`` 也可能是
    ``+08:00``，还可能带毫秒。``fromisoformat``（3.11+）这几种都吃得下；吃不下就当作
    没有截止时间，绝不让一条脏日期把整个刷新带崩。
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
