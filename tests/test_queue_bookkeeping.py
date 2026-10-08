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

import ast
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dida.storage.queue import PendingQueue, QueueShape

STORAGE = Path(__file__).resolve().parents[1] / "src" / "dida" / "storage"
"""存储层这一支（新机制与它的两个调用者都在里面）。"""

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 4, 9, 0, tzinfo=TZ)

SQL_STATEMENT = re.compile(
    r"^\s*(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE)\b", re.IGNORECASE
)
"""一段字符串「像不像一句 SQL」——表名出现在 docstring、散文与 ``QueueShape(...)`` 里都正当。"""

WRITE_STATEMENT = re.compile(r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b", re.IGNORECASE)
READ_STATEMENT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
SCHEMA_STATEMENT = re.compile(r"^\s*CREATE\s+TABLE\b", re.IGNORECASE)
QUEUE_TABLE = re.compile(r"\bpending_changes\b|\bpending_list_changes\b")
ATTEMPT_INCREMENT = re.compile(r"attempts\s*=\s*attempts\s*\+\s*1")
"""``attempts = attempts + 1``：正则容忍空白，``attempts=attempts+1`` 这种写法照样抓到。"""

QUEUE_READERS = frozenset(
    {
        "_held_local_list_ids",
        "_exempt_fields",
        "_has_pending_change",
        "_has_dirty_list_change",
        "_has_pending_list_change",
    }
)
"""允许读两张队列表的那几处：**发号**（哪个本地 id 还占着）与**冲突豁免**（谁有没推成功的
改动）。它们按各自的谓词读，不是记账本身；除此之外 store.py 一个读语句都不许有。"""


def _string_literals(source: str) -> list[str]:
    """这份源码里所有字符串字面量（AST 取；相邻字面量已经拼成一段）。"""
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _queue_statements(source: str) -> list[tuple[str | None, str]]:
    """点名两张队列表的 SQL 语句，连同它落在哪个函数里（模块级是 ``None``）。"""
    found: list[tuple[str | None, str]] = []
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Constant(self, node: ast.Constant) -> None:
            if (
                isinstance(node.value, str)
                and QUEUE_TABLE.search(node.value)
                and SQL_STATEMENT.match(node.value)
            ):
                found.append((stack[-1] if stack else None, node.value))

    Visitor().visit(ast.parse(source))
    return found


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


def test_store_never_writes_the_queue_tables():
    """写语句（INSERT / UPDATE / DELETE）一条都不许留在 ``store.py``。

    按**结构**断，不按字面串：认领重指（``UPDATE pending_changes SET task_id = ?``）与失败
    一次（``attempts = attempts + 1``）只要被内联回来，不管空格怎么摆都会红。
    """
    writes = [
        " ".join(text.split())[:80]
        for _, text in _queue_statements((STORAGE / "store.py").read_text(encoding="utf-8"))
        if WRITE_STATEMENT.match(text)
    ]

    assert writes == [], "两张队列表的写语句只许住在 queue.py，store.py 里又出现了：\n" + "\n".join(
        writes
    )


def test_store_reads_the_queue_tables_only_where_the_policy_lives():
    """读语句只许出现在发号与豁免那几处；表结构只许是模块级的 ``_SCHEMA``。"""
    readers: set[str | None] = set()
    for owner, text in _queue_statements((STORAGE / "store.py").read_text(encoding="utf-8")):
        if SCHEMA_STATEMENT.match(text):
            assert owner is None, "表结构只许是模块级那个 _SCHEMA"
            continue
        assert READ_STATEMENT.match(text), (
            f"认不出这段点名队列表的 SQL：{' '.join(text.split())[:60]}"
        )
        readers.add(owner)

    assert readers == QUEUE_READERS, (
        "读两张队列表的应当是且只是那几处豁免 / 发号，实际：" + repr(sorted(map(str, readers)))
    )


def test_the_mechanism_itself_names_no_queue_table():
    """机制里一句写死表名的 SQL 都没有——表名只能从 ``QueueShape`` 来。

    这一条把「机制对两张表一视同仁」钉成结构事实：它不可能只为任务或只为清单而存在。
    """
    assert _queue_statements((STORAGE / "queue.py").read_text(encoding="utf-8")) == []


def test_the_attempt_increment_lives_only_in_the_mechanism():
    """``attempts = attempts + 1`` 只许在 queue.py 出现一次（空白怎么摆都算同一句）。"""
    counted = {
        path.name: sum(
            len(ATTEMPT_INCREMENT.findall(text))
            for text in _string_literals(path.read_text(encoding="utf-8"))
        )
        for path in sorted(STORAGE.glob("*.py"))
    }
    homes = {name: count for name, count in counted.items() if count}

    assert homes == {"queue.py": 1}, f"「试一次 +1」这份账应当只有机制一处，实际在 {homes}"
