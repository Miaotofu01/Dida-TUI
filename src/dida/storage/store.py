"""本地存储（第 3 个深模块）：SQLite（stdlib），无 ORM、无迁移框架。

六类数据：

- 清单（API 叫 project，界面叫清单）：名称、颜色、排序、项目组、是否收集箱，
- 任务快照：服务端原始字段 **含未知字段** + 本地已生效的改动，
- 待推送改动（Pending Change）：创建时间、尝试次数、下次重试时间、最后一次错误，
- 同步状态：已完成流游标、上次刷新完成时间、上次算出的逻辑日，
- **自定义视图**（#36）：过滤条件的原文 + 它在清单列表页上的位置，
- **id 别名**（#75 / ADR-0009）：认领换过名的那几个本地 id 现在是哪个 id——只在**这一次打开**
  期间有效（开库时清空，见 :meth:`Store.resolve_id` 与 ADR-0009 二）。

自定义视图**只存在这里**（ADR-0005）：``config.toml`` 是放 token 的文件，为了改一个过滤
条件去手写凭据是不对的；而 API 里没有「保存一组过滤条件」这个接口，所以它也不进待推送
队列——那条队列的每一行都是「要推给服务端」的改动，视图没有服务端那一半。

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

``Store`` 同时是 t05 的 ``ViewSource`` 的生产实现（``lists`` / ``tasks`` / ``sync_state`` /
``resolve_id``），所以引擎与 TUI 拿到的形状和它们已经写好的测试一致。

时间一律由调用方给（``now`` / ``last_refresh_at``）：这一层没有时钟，
「现在」永远从注入的 ``Clock`` 来。写操作都是乐观的——``enqueue`` 当场让改动在本地生效，
不等网络；推不推得动是 t10 的事。

公开接口：

- 开关：``close()``、``with Store(path) as store``；
- 读（``ViewSource``）：``lists()`` / ``tasks()`` / ``sync_state()`` / ``resolve_id(id)``；
- 读（完整记录）：``list_records()`` / ``task_payload(task_id)`` / ``stored_sync_state()``；
- 写：``apply_refresh(lists=, tasks=, prune_lists=, prune_unfinished_tasks=)``（只写变化、
  顺手剪枝，返回 ``RefreshReport``）、``enqueue(...)`` / ``pending()`` / ``pending_count()`` /
  ``record_attempt(...)`` / ``resolve(change_id)`` / ``set_sync_state(...)``；
- 写（清单，#42）：``save_list(...)`` / ``drop_list(list_id)`` / ``list_payload(list_id)`` /
  ``new_local_list_id()`` / ``enqueue_list(...)`` / ``pending_lists()`` /
  ``record_list_attempt(...)`` / ``resolve_list(change_id)`` / ``adopt_created_list(...)``；
- 读 / 写（自定义视图，#36）：``view_definitions()`` / ``view_definition(view_id)`` /
  ``save_view(definition)`` / ``drop_view(view_id)`` / ``new_view_id()``——**没有队列**，
  视图只在本地（:class:`~dida.sync.views.ViewStore` 就是这五个方法）。

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

from dida.vocabulary import (
    COMPLETED_STATUS,
    INBOX_ID,
    LOCAL_LIST_PREFIX,
    LOCAL_TASK_PREFIX,
    UNCOMPLETED_STATUS,
    FieldOverride,
    ListLocalEffect,
    ListSnapshot,
    ListWriteKind,
    LocalEffect,
    PendingChange,
    PendingListChange,
    RefreshReport,
    StoredSyncState,
    SyncState,
    TaskSnapshot,
    ViewDefinition,
    WriteKind,
    is_local_list_id,
    read_priority,
    read_tags,
    read_text,
    read_time,
    view_from_payload,
    view_payload,
)

ChangeKind = WriteKind
"""改动种类：对应 API 的四个写操作（新建 / 更新 / 完成 / 删除）。

**别名，不是第二份定义**（t32）：词表住在 :mod:`dida.vocabulary`（#78 从 ``sync`` 与这一层
共用的那批词汇里搬过去的），引擎的 ``WriteKind`` 与这里的 ``ChangeKind`` 是同一个枚举。
以前这里另写了一份成员一字不差的枚举，于是新增一种写要在两处各改一次——两层现在只剩一个
来源。``ChangeKind`` 这个名字留着是因为「待推送改动的种类」在存储层读起来顺，指的还是同一
个对象。每种写的**本地效果**（``kind.local``）与**冲突豁免**（``kind.whole_row``）也读这
张词表，这一层不再按成员名分派。"""


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

CREATE TABLE IF NOT EXISTS views (
    id          TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    position    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS id_aliases (
    from_id     TEXT PRIMARY KEY,
    to_id       TEXT NOT NULL
);
"""

# 自定义视图那一行只存两样东西：它的定义（一份 JSON 原文）与它在列表页上的位置。
# **一维一列**是另一条路，但那条路每加一维都要改表（#36 的六个维度就是这么长出来的）；
# 而这份原文的形状是 `sync/views.py` 的判断（`view_payload` / `view_from_payload`），
# 不是存储层的——这一层只负责把一份 JSON 原样放下、原样拿回来。
# `position` 显式存着，是因为 `INSERT OR REPLACE` 会换掉 rowid：不记位置的话，改一次条件
# 就会让那个视图在列表页上跳到末尾。
VIEW_ID_PREFIX = "view-"
"""本地视图 id 的前缀（#36）。

视图**只在本地**（API 没有「保存一组过滤条件」这个接口），所以它没有「服务端给了真 id
之后认领」那一套——这个前缀是它从头到尾的身份，不是临时占位（与 ``local-list-`` 的分别
正在这里：那个前缀的意思是「还没推上去」）。
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


def _decode_payload(text: str) -> Mapping[str, Any]:
    """一行视图的定义原文 → 字典；读不成样子时给空字典（那一行退回默认值）。

    读路径上没有「坏一行就整屏空掉」的道理。**写**那一侧不吞：写的是我们自己刚编好的
    一份原文，编不出来是 bug，不该静默变成一行空视图。
    """
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


class Store:
    """本地副本。噪声都在里面：schema、diff、冲突裁决。"""

    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._migrate()
        # id 别名**只活这一次打开**（ADR-0009 二）：一个旧 id 只可能被这一次打开期间已经画出去的
        # 界面握着，重开之后没有任何界面还握着它。清在这里而不是剪在某处，是因为「有效期」这件事
        # 没有别的答案——按时间剪要一只这一层没有的钟，按条数剪是编一个数。
        # 措辞是「这一次打开」而不是「这个进程」：同进程再开一个 Store 会把前一个实例的别名清掉，
        # 生产上走不到（组合根只建一个），要精确的是这句 ADR 对应的话。
        self._db.execute("DELETE FROM id_aliases")
        self._db.commit()

    def resolve_id(self, id: str) -> str:
        """这个名字**现在是**哪个 id（认领换过名的话给新的那个，否则给原样）。

        认领（:meth:`adopt_created` / :meth:`adopt_created_list`）会把本地那个临时 id 换成
        服务端给的 id，而**已经画在屏幕上的那个旧 id 并不会跟着变**——界面拿着它再回来找这条
        任务时，本地这一行已经在真 id 底下了。这一层记下了那次换名，所以「谁手里的旧 id 都还
        认」（工单 #75 / ADR-0009 一）。

        只跟一跳：别名表的 ``to_id`` 永远是服务端给的 id，而它不会再被认领一次——链不会长出来。
        认不出来就是它自己，读路径上那与「这个名字确实没有对应任何东西」是同一条口径。
        """
        row = self._db.execute(
            "SELECT to_id FROM id_aliases WHERE from_id = ?", (id,)
        ).fetchone()
        return id if row is None else str(row["to_id"])

    def _remember_alias(self, old_id: str, new_id: str) -> None:
        """记下「这个本地 id 现在叫 ``new_id``」——**必须与那次换名在同一个事务里**。

        分开写会留下一个窗口：行已经挪走了、别名还没落下，那一刻界面拿着旧 id 回来就是
        「不在本地缓存里了」（正是 #75 报的那句话）。调用点都在 ``with self._db`` 里面。
        """
        if old_id == new_id:
            return
        self._db.execute(
            "INSERT INTO id_aliases (from_id, to_id) VALUES (?, ?) "
            "ON CONFLICT(from_id) DO UPDATE SET to_id = excluded.to_id",
            (old_id, new_id),
        )

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

        递进来的可能是**认领换名之前**的那个临时 id（界面手里就是旧的，工单 #75）：先过一道
        :meth:`resolve_id`，所以旧 id 读到的仍然是这一行。返回的那份原文里的 ``id`` 因此是这条
        任务**现在**的名字——引擎的写路径正是从它那里拿真 id（``PushMixin.write``）。
        """
        row = self._db.execute(
            "SELECT raw FROM tasks WHERE id = ?", (self.resolve_id(task_id),)
        ).fetchone()
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

        ``payload`` 是服务端回的原文（含我们不认识的字段），原样存。三步在同一个事务里：
        不会留下「两条都在」或者「一条都没有」的中间状态。

        服务端给的 id 与临时 id 相同时只写、不删（删了就是把刚写的那一行删掉）。

        **这条任务后面还排着几笔改动时，它们也跟着挪到真 id 上**（#53）：建好之后又改了
        它（断网时先建后改，很正常）会留下一条 ``task_id`` 指向本地临时 id 的改动，而服务端
        没有那个 id——不挪的话它 POST 到 ``/open/v1/task/local-…``、404、退避重试、
        **永远出不了队**，状态栏那个数一直非零（与 :class:`~dida.sync.writes.UnknownTaskError`
        挡的是同一类安静错误）。形状照抄清单版的 :meth:`adopt_created_list`。

        **第四个持有者也在这一步落账**（#75 / ADR-0009）：屏幕。队列能重指是因为它在库里，
        而界面手里那个 id 在屏幕上——它不会跟着变，所以这里顺手记一份别名（
        :meth:`_remember_alias`），让那个旧 id 在本进程里继续认得出这条任务。这一笔与挪行
        在**同一个事务**里：不然就有一个窗口，屏幕拿着旧 id 回来正好撞上「行已经挪走、别名还没
        落下」（#75 报的就是那句话）。
        """
        target = str(payload["id"])
        with self._db:
            self._write_task(payload)
            self._remember_alias(local_id, target)
            if target != local_id:
                self._db.execute("DELETE FROM tasks WHERE id = ?", (local_id,))
                self._db.execute(
                    "UPDATE pending_changes SET task_id = ? WHERE task_id = ?",
                    (target, local_id),
                )

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

        **认领记录不算**（#54）：``AWAIT_ID`` 那一种不是一笔要发出去的改动，它只是「这一行
        还欠一个真 id」。建完就回 ``201`` 空 body 的那条路若把它算进去，用户会看到状态栏挂着
        一个永远不动的「待推送 1」——而那条清单其实早就建好了。等用户真改了名字，那一笔会
        换成普通的 ``UPDATE``，照旧算数。

        **算不算由词表说了算**（#57 的检查 8）：:attr:`~dida.sync.lists.ListWriteKind.counts_as_pending`
        一处分类，加一种记录时不会在这里被默默归错类（默认是「算」，保守的那一侧）。
        """
        counted = self._db.execute("SELECT COUNT(*) AS n FROM pending_changes").fetchone()
        task_changes = int(counted["n"])
        list_changes = sum(
            1
            for row in self._db.execute("SELECT kind FROM pending_list_changes")
            if ListWriteKind(row["kind"]).counts_as_pending
        )
        return task_changes + list_changes

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

        与 :meth:`task_payload` 同一条口径：递进来的可能是认领换名之前的临时 id（#75），
        先过一道 :meth:`resolve_id`；返回那份原文里的 ``id`` 是这个清单**现在**的名字。
        """
        row = self._db.execute(
            "SELECT * FROM lists WHERE id = ?", (self.resolve_id(list_id),)
        ).fetchone()
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

        前缀本身的意思（「服务端没见过这个 id」）在 :mod:`dida.sync.lists`——写路径靠它决定
        一笔改动发不发得出去，所以它是词汇，不是存储层的实现细节。

        服务端建好之后才给真 id，而「建完立刻出现在清单列表页」是 ADR-0002 的手感要求。
        取最小的空号而不是计数器：上一次没推成功的那一行还占着它的号，重开也不会撞上它。

        **占着号的有三处**（#57 / #75）：``lists`` 里那一行、队列里还挂着它记录的那些 id，
        以及**本进程里认领换过名的那些别名**（:meth:`_held_local_list_ids`）。只看行是不够的
        ——一行可以**在记录还在的时候**被剪掉（服务端索引里找不到它、而它又没有「还没到服务端的
        改动」，见 :meth:`_prune_lists`），那个号就从行那一侧空了出来；再发一次就是两条清单用同
        一个临时 id，而按 id 找记录的地方会挑错**一条**，最坏是拿另一条清单的名字去删服务端上的
        一行（#57 的探针）。别名那一处是 #75 加上的：认领之后号确实空出来了，但别名还记着它现在
        指哪一行——真发出去的话，新那条拿自己的 id 改名会落到**老那条**上。

        所以规矩一句话：**临时 id 的所有权跟着记录走，而记过别名的那几个号一直要等到本进程
        结束**——记录还在，这个号就不许再发；别名还在，也一样。行的寿命与记录的寿命因此可以
        不一样长（剪枝只剪行），而号永远是安全的。

        任务那边的临时 id 是 uuid（``sync/create.py::_local_task_id``），撞不上，所以这条只
        管清单这一族的发号器。
        """
        used = {
            int(value[len(LOCAL_LIST_PREFIX) :])
            for value in self._held_local_list_ids()
            if value[len(LOCAL_LIST_PREFIX) :].isdigit()
        }
        number = 1
        while number in used:
            number += 1
        return f"{LOCAL_LIST_PREFIX}{number}"

    def _held_local_list_ids(self) -> set[str]:
        """本地临时 id 的**全部**占用者：``lists`` 里的行 + 队列里的记录 + 认领换名留下的别名。

        前两处是 #57 的，第三处是 #75 的（:meth:`resolve_id` 那张表）：一个号被认领（行挪到服务端
        id 上、记录也出队）之后看起来是空的，但别名还指着它——再发一次就会撞上「同一个临时 id 两个
        意思」。

        「这是不是本地临时 id」只在 :func:`~dida.sync.lists.is_local_list_id` 一处判断（这里不写
        ``LIKE`` 之类第二条判据），所以几张表先各取一列、在 Python 这边筛。
        """
        values = {str(row["id"]) for row in self._db.execute("SELECT id FROM lists")}
        values |= {
            str(row["list_id"])
            for row in self._db.execute("SELECT list_id FROM pending_list_changes")
        }
        values |= {str(row["from_id"]) for row in self._db.execute("SELECT from_id FROM id_aliases")}
        return {value for value in values if is_local_list_id(value)}

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

        与任务那一版一样，同一个事务里也记一份别名（#75 / ADR-0009）：界面可能正站在这个刚建好
        的清单里，或者光标正停在它那一行上——那张表两族 id 共用（本地前缀不重叠）。
        """
        target = str(payload["id"])
        with self._db:
            self._write_list(payload)
            self._remember_alias(local_id, target)
            if target != local_id:
                self._db.execute("DELETE FROM lists WHERE id = ?", (local_id,))
                self._db.execute(
                    "UPDATE pending_list_changes SET list_id = ? WHERE list_id = ?",
                    (target, local_id),
                )

    def amend_list_change(
        self,
        change_id: int,
        *,
        kind: ListWriteKind | None = None,
        payload: Mapping[str, Any] | None = None,
        list_id: str | None = None,
        local: Mapping[str, Any] | None = None,
    ) -> None:
        """改写一条还没推成功的清单改动（#54）。

        为什么需要它：一条**还没被服务端认领**的清单只许有**一条**队列记录。后来的改名并进
        这一条（而不是在它后面再排一条——那条只会打到一个服务端没见过的 id 上）；认领的那一刻
        也要改这一条（换成一次普通的改名，或者干脆认领记录出队）。

        ``local`` 给出来就顺手把本地那一行写成它：与入队时「本地生效与入队同一个事务」同一条
        规矩——并进去的改名，屏幕上那一行与队列里那一份必须是同一个意思。
        """
        with self._db:
            if kind is not None:
                self._db.execute(
                    "UPDATE pending_list_changes SET kind = ? WHERE id = ?",
                    (kind.value, change_id),
                )
            if payload is not None:
                self._db.execute(
                    "UPDATE pending_list_changes SET payload = ? WHERE id = ?",
                    (_dumps(payload), change_id),
                )
            if list_id is not None:
                self._db.execute(
                    "UPDATE pending_list_changes SET list_id = ? WHERE id = ?",
                    (list_id, change_id),
                )
            if local is not None:
                self._write_list(dict(local))

    def identify_list(
        self,
        *,
        local_id: str,
        real_id: str,
        row: Mapping[str, Any] | None = None,
        remove: bool = False,
    ) -> None:
        """认领一条「服务端已经建好、但没回 id」的清单（#54）：一个事务里做完三件事。

        1. 队列里指向 ``local_id`` 的改动**全部挪到 ``real_id``** 上——认领之后它们才是可
           寻址的（改 / 删要打一个服务端认得的 id）；
        2. 本地那行临时 id 删掉（真 id 那一行刚刚由刷新写进来了）；
        3. ``row`` 给出来就把它写成真 id 那一行（改名并进去过的那一份：用户要的名字 / 颜色）。
           ``remove=True`` 是另一头——用户已经删了这一条清单，而刷新刚把服务端那行写进来，
           这里要把它**摘掉**，否则屏幕上就是「删掉的清单又回来了」。

        **别名也在这里落账**（#75 / ADR-0009）：这是认领清单的**第二条**路（推送回 201 空体时，
        认领推迟到下一次刷新按名字对上，#54），而屏幕上的那一行不认路——它拿着的还是旧 id。
        记在这个方法里而不是三个调用点上，是因为三条路（``AWAIT_ID`` / 改名并进去 / 用户已经
        删了）共用这一步，记漏一条就是半修。
        """
        with self._db:
            if remove:
                self._db.execute("DELETE FROM lists WHERE id = ?", (real_id,))
            elif row is not None:
                self._write_list(dict(row))
            self._remember_alias(local_id, real_id)
            self._db.execute("DELETE FROM lists WHERE id = ?", (local_id,))
            self._db.execute(
                "UPDATE pending_list_changes SET list_id = ? WHERE list_id = ?",
                (real_id, local_id),
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

    # ---------------------------------------------------------------- 读 / 写：自定义视图（#36）

    def view_definitions(self) -> tuple[ViewDefinition, ...]:
        """本地库里那些自定义视图的定义，按用户自己的顺序（#36）。

        给的是**定义**不是算好的成员：成员要「全量缓存 + 当前逻辑日」才算得出来，而这一层
        与 ``sync.view`` 一样不读时钟（「现在」一律由调用方给）。求值在引擎里发生，与内置
        视图走同一个 ``evaluate_view``。

        读不成样子的那一行**退回默认值**（:func:`~dida.sync.views.view_from_payload`），
        不把整个清单列表页带走——与空缓存给空视图同一条口径。
        """
        return tuple(
            view_from_payload(_decode_payload(row["definition"]), view_id=row["id"])
            for row in self._db.execute(
                "SELECT id, definition FROM views ORDER BY position, rowid"
            )
        )

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """一个视图的定义；本地没有就是 ``None``（``e`` 的表单要拿它填当前值）。"""
        row = self._db.execute(
            "SELECT id, definition FROM views WHERE id = ?", (view_id,)
        ).fetchone()
        if row is None:
            return None
        return view_from_payload(_decode_payload(row["definition"]), view_id=row["id"])

    def save_view(self, definition: ViewDefinition) -> None:
        """写下一行视图（新建与改都是覆盖式地写）。

        **位置照旧不动**：改名 / 改条件不该让它在清单列表页上跳到末尾。新的一行排在最后
        （``position`` 取当前最大值 +1），所以列表页上的顺序就是用户建它们的顺序。
        """
        with self._db:
            row = self._db.execute(
                "SELECT position FROM views WHERE id = ?", (definition.id,)
            ).fetchone()
            position = int(row["position"]) if row is not None else self._next_view_position()
            self._db.execute(
                "INSERT OR REPLACE INTO views (id, definition, position) VALUES (?, ?, ?)",
                (
                    definition.id,
                    json.dumps(view_payload(definition), ensure_ascii=False),
                    position,
                ),
            )

    def drop_view(self, view_id: str) -> None:
        """本地摘掉一行视图：**只动这一行**。

        它只是一组过滤条件，不是容器——它「里面」的任务本来就在各自的清单里，所以这里
        一条任务都不删（验收标准「删视图不删任务」）。
        """
        with self._db:
            self._db.execute("DELETE FROM views WHERE id = ?", (view_id,))

    def new_view_id(self) -> str:
        """一个还没被占用的本地视图 id（``view-1``、``view-2``……）。

        视图没有服务端那一半，所以这个 id 是**最终的**身份，不存在「推送成功之后认领真 id」
        那一步（与 :meth:`new_local_list_id` 的分别正在这里）。
        """
        used = {row["id"] for row in self._db.execute("SELECT id FROM views")}
        index = 1
        while f"{VIEW_ID_PREFIX}{index}" in used:
            index += 1
        return f"{VIEW_ID_PREFIX}{index}"

    def _next_view_position(self) -> int:
        """新的一行排在哪：当前最大位置 +1（一行都没有时从 1 开始）。"""
        row = self._db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM views"
        ).fetchone()
        return int(row["next"])

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
            and not self._has_dirty_list_change(str(row["id"]))
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

    def _has_dirty_list_change(self, list_id: str) -> bool:
        """这条清单上有没有**一笔还没到服务端的改动**（认领记录不算，#54）。

        剪枝读的是这一条：一条 ``AWAIT_ID``（建好了、在等真 id）的本地行如果在服务端的索引里
        找不到，说明那条清单**本来就不存在了**（在别处被删了），本地这行是个影子——剪掉它才对。
        而真正的改动（建 / 改 / 删还没出去）必须留着，否则剪掉的是用户刚做的那一下。

        **哪一种算「真正的改动」由词表说了算**（#57 的检查 8）：
        :attr:`~dida.sync.lists.ListWriteKind.holds_its_row` 一处分类，加一种记录时不会在这里
        被默默归错类（默认是「留住行」，保守的那一侧）。

        ⚠ 剪掉那一行**不代表**那条记录也没了：记录还在原地等认领，并且**仍然占着那个临时
        id**（:meth:`new_local_list_id` 两处一起看，#57）——行的寿命与记录的寿命可以不一样长，
        但号永远有主。
        """
        rows = self._db.execute(
            "SELECT kind FROM pending_list_changes WHERE list_id = ?", (list_id,)
        ).fetchall()
        return any(ListWriteKind(row["kind"]).holds_its_row for row in rows)

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

    每一格怎么读（文本缺省空串 / 优先级缺省 ``0`` / 标签只留字符串 / 日期吃不下就当没有）
    归 :func:`dida.vocabulary.read_text` 一族：写路径的「这次到底改了没有」（#79）读同一份
    口径，两边才不会一个说改了、一个说没改。
    """
    due = payload.get("dueDate")
    completed_at = payload.get("completedTime")
    return TaskSnapshot(
        id=str(payload["id"]),
        title=read_text(payload.get("title")),
        list_id=_list_id_of(payload),
        due=read_time(due),
        all_day=bool(payload.get("isAllDay")),
        priority=read_priority(payload.get("priority")),
        completed=payload.get("status") == COMPLETED_STATUS,
        completed_at=read_time(completed_at),
        desc=read_text(payload.get("desc")),
        content=read_text(payload.get("content")),
        tags=read_tags(payload.get("tags")),
        # 重复规则与提醒只读（v2 不改它们），行里要的只是「有没有」；原文照旧整份留在 raw 里。
        repeat_flag=read_text(payload.get("repeatFlag")),
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


def _texts(value: Any) -> tuple[str, ...]:
    """服务端的一段**字符串数组**（``reminders``）→ 元组；不是数组就当没有。

    与 :func:`dida.vocabulary.read_text` 同一条口径：脏字段不该把整次刷新带崩。元素逐条转成
    字符串——触发器原文长什么样不由这一层解释（那是只读展示的事）。
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)

