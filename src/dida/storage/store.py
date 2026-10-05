"""本地存储（第 3 个深模块）：SQLite（stdlib），无 ORM、无迁移框架。

四类数据：

- 清单（API 叫 project，界面叫清单）：名称、颜色、排序、项目组、是否收集箱，
- 任务快照：服务端原始字段 **含未知字段** + 本地已生效的改动，
- 待推送改动（Pending Change）：创建时间、尝试次数、下次重试时间、最后一次错误，
- 同步状态：已完成流游标、上次刷新完成时间、上次算出的逻辑日。

待推送改动有**两张表**：``pending_changes`` 是任务改动（一行一条任务），
``pending_list_changes`` 是清单改动（建 / 改 / 删一个清单，#42）。分成两张是因为它们改的
根本不是同一种东西——把 ``list_id`` 塞进 ``task_id`` 那一列，一个字段两个意思，
读的人第一步就错。状态栏那个「待推送 N」两张一起数（``pending_count()``）。

「增量」是这一层的概念：全量拉回来的数据在这里比对，只写变化（ADR 0001）。服务端已经没有
的东西也在这里删掉（剪枝，#41）：清单与未完成任务只在「这一路这次取全了」的断言下才剪，
断言就是 ``apply_refresh`` 的 ``prune_*`` 参数。冲突裁决也在这里：服务端权威胜出，但待推送
改动豁免（ADR 0002）——**没有这个豁免，一次推送失败加一次全量刷新就会把用户刚做出的操作
悄悄撤销掉**。豁免是逐字段的：改动碰过的字段本地值赢，其余字段服务端照旧赢；未推送的删除
则整条任务豁免。剪枝读的是同一份豁免：有没推成功的改动的任务一个都不剪。

``Store`` 同时是 t05 的 ``ViewSource`` 的生产实现（``lists`` / ``tasks`` / ``sync_state``），
所以引擎与 TUI 拿到的形状和它们已经写好的测试一致。

时间一律由调用方给（``now`` / ``last_refresh_at``）：这一层没有时钟，
「现在」永远从注入的 ``Clock`` 来。写操作都是乐观的——``enqueue`` 当场让改动在本地生效，
不等网络；推不推得动是 t10 的事。

公开接口：

- 开关：``close()``、``with Store(path) as store``；
- 读（``ViewSource``）：``lists()`` / ``tasks()`` / ``sync_state()``；
- 读（完整记录）：``list_records()`` / ``task_payload(task_id)`` / ``stored_sync_state()``；
- 写：``apply_refresh(lists=, tasks=, prune_lists=, prune_unfinished_tasks=)``（只写变化、
  顺手剪枝，返回 ``RefreshReport``）、``enqueue(...)`` / ``pending()`` / ``pending_count()`` /
  ``record_attempt(...)`` / ``resolve(change_id)`` / ``set_sync_state(...)``；
- 写（清单，#42）：``save_list(...)`` / ``drop_list(list_id)`` / ``list_payload(list_id)`` /
  ``new_local_list_id()`` / ``enqueue_list(...)`` / ``pending_lists()`` /
  ``record_list_attempt(...)`` / ``resolve_list(change_id)`` / ``adopt_created_list(...)``。

``task_payload()`` 是给 t07 的 ``update_task(snapshot=)`` 用的那一份：**字典形状**，
不是领域 dataclass，客户端不认识的字段一个都不丢。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from dida.sync.lists import ListLocalEffect, ListWriteKind
from dida.sync.view import INBOX_ID, ListSnapshot, SyncState, TaskSnapshot
from dida.sync.writes import LocalEffect, WriteKind

ChangeKind = WriteKind
"""改动种类：对应 API 的四个写操作（新建 / 更新 / 完成 / 删除）。

**别名，不是第二份定义**（t32）：词表住在 :mod:`dida.sync.writes`，引擎的 ``WriteKind`` 与
这里的 ``ChangeKind`` 是同一个枚举。以前这里另写了一份成员一字不差的枚举，于是新增一种写
要在两处各改一次——两层现在只剩一个来源。``ChangeKind`` 这个名字留着是因为「待推送改动的
种类」在存储层读起来顺，指的还是同一个对象。每种写的**本地效果**（``kind.local``）与
**冲突豁免**（``kind.whole_row``）也读这张词表，这一层不再按成员名分派。"""

COMPLETED_STATUS = 2
"""任务「已完成」的 ``status`` 值（api-contracts.md：Completed 是 2，不是 1）。"""

LOCAL_LIST_PREFIX = "local-list-"
"""本地临时清单 id 的前缀（#42）：新建的清单在服务端给出真 id 之前先用它占位。

它**不是**收集箱那种「形如 ``inbox`` 加数字」的 id（``sync.read.is_inbox_id`` 认的是那个），
所以本地新建的清单不会被误认成收集箱。
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT,
    sort_order  INTEGER,
    group_id    TEXT,
    is_inbox    INTEGER NOT NULL DEFAULT 0,
    kind        TEXT,
    permission  TEXT
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

CREATE TABLE IF NOT EXISTS pending_list_changes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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


@dataclass(frozen=True)
class ListRecord:
    """缓存里一条清单的完整记录（spec 的清单 schema）。

    ``kind`` / ``permission`` 是服务端 ``Project`` 上那两个字段（``TASK``/``NOTE``、
    ``write``/``read``/``comment``）：清单索引页靠它们标出进不去的行（用户故事 23 / 24）。
    """

    id: str
    name: str
    color: str | None = None
    sort_order: int | None = None
    group_id: str | None = None
    is_inbox: bool = False
    kind: str | None = None
    permission: str | None = None


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
class PendingListChange:
    """一条还没推到服务端的**清单**改动（工单 #42）。

    与 :class:`PendingChange` 长得像但不共用：清单改动没有任务 id，也不是作用在任务快照
    上的字段合并。``payload`` 对建 / 改是要发出去的请求体（只有真的要写的字段），对删除是
    空的——推送要 echo 回去的东西（``sortOrder``）不在队列里，而在 ``lists`` 那一行的
    原文里（:meth:`Store.list_payload`），因为那才是用户看到的那一份。
    """

    id: int
    """本地行号；推送成功时用它 :meth:`Store.resolve_list`。"""

    list_id: str
    """这一笔改的是哪个清单（新建时是本地那个临时 id）。"""

    kind: ListWriteKind
    payload: dict[str, Any]
    """要发出去的请求体（删除是空的）。"""

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

    ``task_id`` 在清单那一行上装的是**清单 id**（#42）：清单的整行豁免（本地有一笔还没推
    成功的建 / 改 / 删）与任务是同一条规矩，报告的读者要的就是那个 id 加「整行」这个事实。
    沿用 ``task_id`` 这个名字是因为这一处是清单侧唯一用到它的地方，为它另起一个类型不值当。
    """

    task_id: str
    field: str
    local: Any
    server: Any


@dataclass(frozen=True)
class RefreshReport:
    """一次全量刷新的结果。

    ``written_*`` 是这次真正写进去的行数：同一份数据拉第二次全是 0，界面因此不闪、光标
    因此不丢。``pruned_*`` 是这次**从本地库里删掉**的行数——服务端已经没有它们了（#41）。

    ``overwritten`` 是服务端权威真的把本地值盖掉的字段（ADR-0002 要求这种覆盖能被
    用户看见）；``suppressed`` 是被待推送改动挡回去的那些——两者都不含没变化的东西，
    所以重复拉同一份数据的报告是空的。
    """

    written_lists: int = 0
    written_tasks: int = 0
    pruned_lists: int = 0
    pruned_tasks: int = 0
    overwritten: tuple[FieldOverride, ...] = ()
    suppressed: tuple[FieldOverride, ...] = ()


class Store:
    """本地副本。噪声都在里面：schema、diff、冲突裁决。"""

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """老库补列：``CREATE TABLE IF NOT EXISTS`` 不给已经存在的表加列。

        没有迁移框架，也不需要有：补的都是可空列，``NULL`` 有明确的「不知道」语义，
        所以 ``ALTER TABLE ... ADD COLUMN`` 一条就够，加过的不再加（用户手上那个 v1 时代的
        库就是这样长出 ``kind`` / ``permission`` 的）。
        """
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(lists)")}
        for name in ("kind", "permission"):
            if name not in columns:
                self._db.execute(f"ALTER TABLE lists ADD COLUMN {name} TEXT")

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
        prune_lists: bool = False,
        prune_unfinished_tasks: bool = False,
    ) -> RefreshReport:
        """把一次全量刷新的原文写进来，只写变化，并裁决冲突，顺手剪枝。

        整次刷新是一个事务：要么整份落地，要么一点都不落地。剪枝也在**这同一个事务**里
        （#41）：删到一半失败不会留下半份刷新。

        冲突裁决的规矩就一句（ADR-0002）：**服务端权威，除非有还没推成功的待推送改动**。
        每个字段单独判：改动碰过的字段本地值赢，其余字段服务端照旧赢。

        服务端传来的是一份完整的任务原文，所以本地多出来的、服务端没有的字段会被丢掉
        ——除了被豁免的那些。

        ``prune_lists`` / ``prune_unfinished_tasks`` 是**调用方的断言**（#41）：它在断言
        「这一路这一次真的取全了」。断言成立时，本地那些服务端已经不再给的清单 / 未完成
        任务就在这里删掉。缺省是 ``False``：不落库、不删——只拿回来一部分的调用方（已完成
        流只拉一个时间窗口）绝不会顺手剪掉窗口外的东西。
        """
        written_lists = 0
        written_tasks = 0
        pruned_lists = 0
        pruned_tasks = 0
        overwritten: list[FieldOverride] = []
        suppressed: list[FieldOverride] = []
        remote_tasks: set[str] = set()

        # 整次刷新一个事务：成功才提交，中途出岔子就整份回滚。半份刷新比旧数据更难查。
        with self._db:
            for payload in lists:
                list_id = str(payload["id"])
                if self._has_pending_list_change(list_id):
                    # 这条清单上有一笔还没推成功的改动（#42）：服务端这份原文整个不许写回来，
                    # 否则用户刚改的名字/颜色会在下一次刷新时悄悄变回去（ADR-0002 的豁免，
                    # 与任务那条 whole_row 同一条规矩）。改动推成功之后由后来的刷新裁决。
                    suppressed.append(FieldOverride(list_id, "*", None, dict(payload)))
                    continue
                written_lists += int(self._write_list(payload))
            for payload in tasks:
                task_id = str(payload["id"])
                # 服务端这次给了的 id 全记下来：剪枝要的就是「这次没给的那些」（#41）。
                remote_tasks.add(task_id)
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
            if prune_lists:
                pruned_lists = self._prune_lists({str(payload["id"]) for payload in lists})
            if prune_unfinished_tasks:
                pruned_tasks = self._prune_unfinished_tasks(remote_tasks)

        return RefreshReport(
            written_lists=written_lists,
            written_tasks=written_tasks,
            pruned_lists=pruned_lists,
            pruned_tasks=pruned_tasks,
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
        """全部清单（``ViewSource``）：清单索引页要的事实都在这里。

        v1 只给了 id 与名字，颜色、项目组、``kind``、``permission`` 全被丢掉——清单索引页
        因此标不出「装不了任务的」与「改不动的」那些行（用户故事 23 / 24）。
        """
        return tuple(
            ListSnapshot(
                id=row["id"],
                name=row["name"],
                color=row["color"],
                group_id=row["group_id"],
                kind=row["kind"],
                permission=row["permission"],
                is_inbox=bool(row["is_inbox"]),
            )
            for row in self._list_rows()
        )

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
                kind=row["kind"],
                permission=row["permission"],
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

        本地效果**读词表**（``kind.local``，t32）：``REMOVE`` 直接摘掉快照，其余盖字段。
        清单 id 记在改动行上，推送时仍然拼得出 URL。
        """
        resolved_list = list_id or self._list_of(task_id)
        # 本地生效与入队同一个事务：不会出现「生效了但没进队列」这种下次刷新就丢的状态。
        with self._db:
            if kind.local is LocalEffect.REMOVE:
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

    def adopt_created(self, local_id: str, payload: Mapping[str, Any]) -> None:
        """新建推成功：把本地那条临时 id 的任务挪到服务端给的 id 上（t15）。

        服务端建好之后才知道真 id。不挪的话，真 id 那条会被下一次全量刷新拉回来，而临时
        id 这条要等那一次刷新的剪枝才消失（#41）——中间这段时间同一条任务在屏幕上出现
        两遍；挪一下则是当场合上，一次都不多画。

        ``payload`` 是服务端回的原文（含我们不认识的字段），原样存。两步在同一个事务里：
        不会留下「两条都在」或者「一条都没有」的中间状态。
        """
        with self._db:
            self._write_task(payload)
            self._db.execute("DELETE FROM tasks WHERE id = ?", (local_id,))

    def pending(self) -> tuple[PendingChange, ...]:
        """还没推成功的改动，按发生顺序（t10 的重试队列按这个顺序挑）。

        「什么时候该重试」不在这里筛：判断「现在」要有钟，那是引擎的事。
        """
        rows = self._db.execute("SELECT * FROM pending_changes ORDER BY id").fetchall()
        return tuple(_change(row) for row in rows)

    def pending_count(self) -> int:
        """待推送数量；状态栏常驻显示这个数（ADR 0002 的豁免代价）。

        **两张表一起数**（#42）：清单的建 / 改 / 删与任务的改动一样是「本地比服务端新」，
        分开数的话删掉一个清单之后状态栏还是 0——用户读到的是「发出去了」。
        """
        row = self._db.execute(
            """
            SELECT (SELECT COUNT(*) FROM pending_changes)
                 + (SELECT COUNT(*) FROM pending_list_changes) AS n
            """
        ).fetchone()
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

    # ---------------------------------------------------------------- 写：清单的乐观写（#42）

    def list_payload(self, list_id: str) -> dict[str, Any] | None:
        """一条清单的原文（**字典形状**，与 :meth:`task_payload` 同一条口径）。

        这是喂给 ``update_project(snapshot=)`` 的那一份：改名时不打算改的字段靠它 echo
        回去（``sortOrder`` 最要紧——文档写着 "default 0"，省略它可能把清单顺序重置）。
        ``groupId`` / ``permission`` 也在这里，但**不会**进请求体：那份白名单在
        :func:`dida.api.guards.prepare_project_body`。

        本地没有这一行就是 ``None``：不猜一个空清单出来（猜出来的请求会打到一个不存在的
        清单上）。
        """
        row = self._db.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        return None if row is None else _list_payload(row)

    def save_list(self, payload: Mapping[str, Any]) -> None:
        """写下一行清单（乐观写的那一份）。内容没变就不写（ADR 0001 的「只写变化」）。"""
        with self._db:
            self._write_list(payload)

    def drop_list(self, list_id: str) -> None:
        """本地摘掉一行清单（删除的本地效果：不是「等推送成功再摘」）。"""
        with self._db:
            self._db.execute("DELETE FROM lists WHERE id = ?", (list_id,))

    def new_local_list_id(self) -> str:
        """一个还没被占用的本地临时清单 id（新建清单时先占位）。

        服务端建好之后才给真 id，而「建完立刻出现在清单列表页」是 ADR-0002 的手感要求。
        取最小的空号而不是计数器：上一次没推成功的那一行还占着它的号，重开也不会撞上它
        （撞上就是两条清单合成一条，用户刚建的那条不见了）。
        """
        rows = self._db.execute(
            "SELECT id FROM lists WHERE id LIKE ?", (f"{LOCAL_LIST_PREFIX}%",)
        ).fetchall()
        used = {
            int(str(row["id"])[len(LOCAL_LIST_PREFIX) :])
            for row in rows
            if str(row["id"])[len(LOCAL_LIST_PREFIX) :].isdigit()
        }
        number = 1
        while number in used:
            number += 1
        return f"{LOCAL_LIST_PREFIX}{number}"

    def enqueue_list(
        self,
        *,
        list_id: str,
        kind: ListWriteKind,
        payload: Mapping[str, Any],
        now: datetime,
        local: Mapping[str, Any] | None = None,
    ) -> PendingListChange:
        """入队一条待推送的**清单**改动，并让它在本地立刻生效（乐观写）。

        ``payload`` 是**要发出去的请求体**；``local`` 是本地那一行要写的字段——两者在
        新建时不一样（本地那行多一个临时 id），改名与删除时一样（删除干脆没有）。
        本地效果读词表（``kind.local``），不在这里点名成员。

        ``now`` 由调用方给：这一层没有时钟。本地生效与入队**同一个事务**：不会出现
        「生效了但没进队列」这种下次刷新就丢的状态（与 :meth:`enqueue` 同一条规矩）。
        """
        with self._db:
            if kind.local is ListLocalEffect.DROP:
                self._db.execute("DELETE FROM lists WHERE id = ?", (list_id,))
            else:
                self._write_list(dict(local if local is not None else payload))
            cursor = self._db.execute(
                """
                INSERT INTO pending_list_changes
                    (list_id, kind, payload, created_at, attempts, next_retry_at, last_error)
                VALUES (?, ?, ?, ?, 0, NULL, NULL)
                """,
                (list_id, kind.value, _dumps(payload), now.isoformat()),
            )
            change = self._list_change_row(int(cursor.lastrowid or 0))
        assert change is not None
        return change

    def adopt_created_list(self, local_id: str, payload: Mapping[str, Any]) -> None:
        """新建清单推成功：把本地那行临时 id 的清单挪到服务端给的 id 上。

        与任务的 :meth:`adopt_created` 同一件事与同一个理由：不挪的话，真 id 那条会被下一次
        全量刷新拉回来，而临时 id 这条要等那一次刷新的剪枝才消失——中间这段时间同一条清单在
        屏幕上出现两遍。两步在同一个事务里。

        服务端给的 id 与临时 id 相同时只写、不删（删了就是把刚写的那一行删掉）。

        **这条清单后面还排着几笔改动时，它们也跟着挪到真 id 上**：建好之后又改了名
        （断网时先建后改，很正常）会留下一条 ``list_id`` 指向本地临时 id 的改动，而服务端
        没有那个清单——不挪的话它永远推不出去，状态栏那个数一直非零，用户读到的是
        「等一下就好」（与 :class:`~dida.sync.writes.UnknownTaskError` 挡的是同一类安静错误）。
        """
        target = str(payload["id"])
        with self._db:
            self._write_list(payload)
            if target != local_id:
                self._db.execute("DELETE FROM lists WHERE id = ?", (local_id,))
                self._db.execute(
                    "UPDATE pending_list_changes SET list_id = ? WHERE list_id = ?",
                    (target, local_id),
                )

    def pending_lists(self) -> tuple[PendingListChange, ...]:
        """还没推成功的清单改动，按发生顺序（与 :meth:`pending` 同一条口径）。"""
        rows = self._db.execute("SELECT * FROM pending_list_changes ORDER BY id").fetchall()
        return tuple(_list_change(row) for row in rows)

    def record_list_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次清单改动的推送失败（尝试次数 +1、最后一次错误、下次重试时刻）。"""
        with self._db:
            self._db.execute(
                """
                UPDATE pending_list_changes
                   SET attempts = attempts + 1, last_error = ?, next_retry_at = ?
                 WHERE id = ?
                """,
                (error, next_retry_at.isoformat() if next_retry_at else None, change_id),
            )

    def resolve_list(self, change_id: int) -> None:
        """这条清单改动已经推到服务端了，出队。"""
        with self._db:
            self._db.execute("DELETE FROM pending_list_changes WHERE id = ?", (change_id,))

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

        「哪些改动整条豁免」读词表（``kind.whole_row``，t32），不在这里点名成员：下一批
        「这条任务不再存在」的写（例如 v2 的搬走）只要在表里标一下，这里自动跟着变。
        """
        rows = self._db.execute(
            "SELECT kind, payload FROM pending_changes WHERE task_id = ?", (task_id,)
        ).fetchall()
        if any(ChangeKind(row["kind"]).whole_row for row in rows):
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

    def _prune_lists(self, remote_ids: set[str]) -> int:
        """删掉服务端这次没给的清单（收集箱除外），返回删了几行。

        收集箱**不在** ``GET /open/v1/project`` 里（ADR-0001），所以索引里没有它不代表它
        不存在：它是默认清单，剪掉它的表现是左栏少一格、随手记的任务无处可去。收集箱那一行
        的 ``is_inbox`` 写进去就是 1（``_write_list`` 的口径），所以这一条也挡住了字面量
        ``inbox`` 那一行。

        第二条例外（#42）：本地还有**没推成功的清单改动**——刚刚建好、还没推上去的清单不在
        服务端的索引里只有一个原因，就是那一笔改动还没出去。剪掉它的表现是「用户刚建的清单
        刷新一次就没了」，与「删掉的清单刷新之后又回来」是同一个 bug 的两个方向。
        """
        rows = self._db.execute("SELECT id, is_inbox FROM lists").fetchall()
        gone = [
            str(row["id"])
            for row in rows
            if str(row["id"]) not in remote_ids
            and not row["is_inbox"]
            and not self._has_pending_list_change(str(row["id"]))
        ]
        for list_id in gone:
            self._db.execute("DELETE FROM lists WHERE id = ?", (list_id,))
        return len(gone)

    def _prune_unfinished_tasks(self, remote_ids: set[str]) -> int:
        """删掉服务端这次没给的未完成任务，返回删了几行。

        两条例外，都是「用户还没上去的那份不许被悄悄撤销」：

        - **本地还有没推成功的改动**（ADR-0002 的豁免就是为它立的，加这一次剪枝也一样）：
          连新建都算——本地刚建的那条服务端根本还没见过，剪掉它就是用户刚写的东西凭空
          消失。留着它，推的时候推不动会作为错误说出来，而不是静默丢掉。
        - **本地已经是已完成**：已完成任务是另一条流的地盘（``refresh_completed`` 按完成
          时间窗口拉），这次取的是未完成任务，没取到它不代表它没了。

        远端删掉一条任务、与把它勾选完成，对这条未完成流来说长得一样，所以本地那条会被
        删掉；它要真被完成了，下一趟已完成流会把它作为已完成带回来。
        """
        rows = self._db.execute("SELECT id, raw FROM tasks").fetchall()
        gone = [
            str(row["id"])
            for row in rows
            if str(row["id"]) not in remote_ids
            and json.loads(row["raw"]).get("status") != COMPLETED_STATUS
            and not self._has_pending_change(str(row["id"]))
        ]
        for task_id in gone:
            self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return len(gone)

    def _has_pending_change(self, task_id: str) -> bool:
        """这条任务上还有没有没推成功的改动（不关心是哪种）。"""
        row = self._db.execute(
            "SELECT 1 FROM pending_changes WHERE task_id = ? LIMIT 1", (task_id,)
        ).fetchone()
        return row is not None

    def _has_pending_list_change(self, list_id: str) -> bool:
        """这条清单上还有没有没推成功的改动（#42）。

        两种地方都读它：剪枝（还没推上去的新建清单**不是**「远端已删」）与刷新时的冲突
        裁决（服务端那份原文盖不掉用户刚做的改名）。与 :meth:`_has_pending_change` 同一条
        规矩——ADR-0002 的豁免。
        """
        row = self._db.execute(
            "SELECT 1 FROM pending_list_changes WHERE list_id = ? LIMIT 1", (list_id,)
        ).fetchone()
        return row is not None

    def _list_change_row(self, change_id: int) -> PendingListChange | None:
        row = self._db.execute(
            "SELECT * FROM pending_list_changes WHERE id = ?", (change_id,)
        ).fetchone()
        return None if row is None else _list_change(row)

    def _write_list(self, payload: Mapping[str, Any]) -> bool:
        """写一条清单；内容没变就不写（ADR 0001 的「只写变化」）。"""
        values = {
            "id": str(payload["id"]),
            "name": str(payload.get("name") or ""),
            "color": payload.get("color"),
            "sort_order": payload.get("sortOrder"),
            "group_id": payload.get("groupId"),
            "is_inbox": 1 if payload.get("isInbox") or payload.get("id") == INBOX_ID else 0,
            "kind": payload.get("kind"),
            "permission": payload.get("permission"),
        }
        row = self._db.execute("SELECT * FROM lists WHERE id = ?", (values["id"],)).fetchone()
        if row is not None and all(row[key] == value for key, value in values.items()):
            return False
        self._db.execute(
            """
            INSERT INTO lists (id, name, color, sort_order, group_id, is_inbox, kind, permission)
            VALUES (:id, :name, :color, :sort_order, :group_id, :is_inbox, :kind, :permission)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                color = excluded.color,
                sort_order = excluded.sort_order,
                group_id = excluded.group_id,
                is_inbox = excluded.is_inbox,
                kind = excluded.kind,
                permission = excluded.permission
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
            (task_id, _list_id_of(payload), raw),
        )
        return True


def _dumps(payload: Mapping[str, Any]) -> str:
    """任务原文的规范序列化：键排序，保证同一份内容只有一个字符串形式。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _list_payload(row: sqlite3.Row) -> dict[str, Any]:
    """``lists`` 那一行 → 服务端 ``Project`` 那样的字典（#42）。

    字段名按文档用驼峰：这一份是要 echo 回请求体、也是要跟服务端原文比对的，
    换个名字就得在两处翻译。``isInbox`` 不在文档里——那是**客户端自己**给收集箱那一行
    加的记号（服务端的 ``Project`` 定义里没有这种字段），所以 ``_write_list`` 认它。
    """
    return {
        "id": row["id"],
        "name": row["name"],
        "color": row["color"],
        "sortOrder": row["sort_order"],
        "groupId": row["group_id"],
        "isInbox": bool(row["is_inbox"]),
        "kind": row["kind"],
        "permission": row["permission"],
    }


def _list_change(row: sqlite3.Row) -> PendingListChange:
    """``pending_list_changes`` 那一行 → :class:`PendingListChange`。"""
    return PendingListChange(
        id=int(row["id"]),
        list_id=str(row["list_id"]),
        kind=ListWriteKind(row["kind"]),
        payload=json.loads(row["payload"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        attempts=int(row["attempts"]),
        next_retry_at=(
            datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None
        ),
        last_error=row["last_error"],
    )


def _list_id_of(payload: Mapping[str, Any]) -> str:
    """任务在哪个清单：**只有服务端说了才算**。

    缺失的 ``projectId`` 读作空串（「不知道在哪个清单」），不再猜成字面量 ``inbox``（#33）：
    收集箱的真实 id 是每账户不同的一串（实测 ``inbox1234567890``），猜出来的那个字面量跟它
    一条都对不上——左栏徽标是 0、清单名也对不上，而且不报错。取数侧（``sync.refresh``）知道
    这条任务是从哪个清单拉回来的，补也是由它补一个**服务端返回的** id。
    """
    return str(payload.get("projectId") or "")


def _snapshot(payload: Mapping[str, Any]) -> TaskSnapshot:
    """服务端原文 → ``ViewSource`` 要的任务快照。

    只翻译，不判断：``status`` 是不是「已完成」由 API 决定（Completed 是 ``2``），
    这条属于今日还是逾期则完全不是这里的事。
    """
    due = payload.get("dueDate")
    completed_at = payload.get("completedTime")
    return TaskSnapshot(
        id=str(payload["id"]),
        title=str(payload.get("title") or ""),
        list_id=_list_id_of(payload),
        due=_parse_time(due) if isinstance(due, str) else None,
        all_day=bool(payload.get("isAllDay")),
        priority=int(payload.get("priority") or 0),
        completed=payload.get("status") == COMPLETED_STATUS,
        completed_at=_parse_time(completed_at) if isinstance(completed_at, str) else None,
        desc=_text(payload.get("desc")),
        content=_text(payload.get("content")),
        tags=_tag_names(payload.get("tags")),
        # 重复规则与提醒只读（v2 不改它们），行里要的只是「有没有」；原文照旧整份留在 raw 里。
        repeat_flag=_text(payload.get("repeatFlag")),
        reminders=_texts(payload.get("reminders")),
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


def _texts(value: Any) -> tuple[str, ...]:
    """服务端的一段**字符串数组**（``reminders``）→ 元组；不是数组就当没有。

    与 :func:`_text` 同一条口径：脏字段不该把整次刷新带崩。元素逐条转成字符串——触发器
    原文长什么样不由这一层解释（那是只读展示的事）。
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _text(value: Any) -> str:
    """服务端的一段文字（``desc`` / ``content``）→ 快照上的字符串。

    不是字符串就当没有（与 :func:`_parse_time` 同一条口径）：一条脏字段不该把整次刷新带崩，
    右栏少一行远好过一屏都读不出来。
    """
    return value if isinstance(value, str) else ""


def _tag_names(value: Any) -> tuple[str, ...]:
    """任务上的 ``tags`` → 标签名。

    服务端给的是一串名字（``probe-update-semantics.py`` 实测写进去的是 ``["probe-tag"]``）。
    不是数组、或者数组里混了不是字符串的东西，就跳过那一个：标签是展示用的，不值得为它
    丢掉一整次刷新。顺序照服务端给的来——排序是服务端的事。
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)
