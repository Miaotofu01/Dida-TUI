"""写类型只有一份定义（t32）：引擎与存储说的是同一套词汇。

新增一种写以前要在四个地方各改一次：引擎的 ``WriteKind``、存储的 ``ChangeKind``、两层之间的
换算、以及各自的成员表。两份成员表一字不差地重复——真正的问题不是打字多，是**它们可以漂**：
一边加了成员另一边没加，只在运行到那条写路径时才炸。

这个文件钉住外部结论，不钉实现：

- 引擎眼里的写类型与存储眼里的改动种类是**同一个对象**（``is``，不是值相等）；
- 一份词表在整棵源码树里只有一处定义（AST 数出来的，不是人工核对）；
- 一个写类型从引擎的写入口进、从存储的队列里出来，还是同一个成员——中间没有任何换算表。

钉的是「词表只有一个来源」这件事本身：两张表哪天又被分开写，这里当场红。
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dida.storage.store import ChangeKind, Store
from dida.sync.engine import LocalEffect, WireCall, WriteKind

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

TZ = timezone(timedelta(hours=8))

WRITE_MEMBERS = {"CREATE", "UPDATE", "COMPLETE", "DELETE"}
"""一套写词汇的成员：新建 / 改 / 完成 / 删。"""


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def _task() -> dict:
    return {"id": "t1", "projectId": "inbox", "title": "写周报", "status": 0}


def _enum_members(path: Path) -> dict[str, set[str]]:
    """这份源码里每个 ``Enum`` 子类定义了哪些成员：``{"WriteKind": {"CREATE", ...}}``。"""
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            (isinstance(base, ast.Name) and base.id == "Enum")
            or (isinstance(base, ast.Attribute) and base.attr == "Enum")
            for base in node.bases
        ):
            continue
        names = {
            item.targets[0].id
            for item in node.body
            if isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
        }
        if names:
            found[node.name] = names
    return found


def _definitions_of_the_write_vocabulary() -> dict[str, set[str]]:
    """整棵源码树里，哪些类定义了这套写词汇（正常应当是恰好一个）。"""
    owners: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for name, members in _enum_members(path).items():
            if members >= WRITE_MEMBERS:
                owners[name] = members
    return owners


def test_the_engine_and_the_storage_speak_the_very_same_write_kind():
    """``is`` 而不是 ``==``：两份成员表长得一样也仍然是两份，会各自漂。"""
    assert ChangeKind is WriteKind


def test_the_write_vocabulary_is_defined_in_exactly_one_place():
    """一份词表一处定义（``dida.sync.writes``），别处只能是它的别名。"""
    owners = _definitions_of_the_write_vocabulary()

    assert len(owners) == 1, f"写类型被定义了不止一处：{sorted(owners)}"


def test_a_write_kind_survives_the_trip_through_the_queue_as_the_same_member(store):
    """引擎写进去、存储读出来，还是同一个成员：中间没有第二张表要同步。"""
    store.apply_refresh(lists=[{"id": "inbox", "name": "收集箱"}], tasks=[_task()])

    store.enqueue(
        task_id="t1",
        kind=WriteKind.UPDATE,
        payload={"title": "改过的标题"},
        now=datetime(2026, 3, 14, 12, 0, tzinfo=TZ),
    )

    queued = store.pending()
    assert [change.kind for change in queued] == [WriteKind.UPDATE]
    assert queued[0].kind is WriteKind.UPDATE


def test_every_member_of_the_engine_vocabulary_is_a_member_of_the_storage_one():
    """引擎能说的每一种写，存储都认得——不是靠值碰巧对得上。"""
    for kind in WriteKind:
        assert ChangeKind(kind.value) is kind


def test_every_write_kind_carries_its_behaviour_in_the_one_table():
    """每个成员都带齐自己的行为说明：本地效果 + 推送走哪一个端点。

    加一种写而没写说明，这里当场红——而补说明的地方也只有一处（``dida.sync.writes``）。
    """
    described = {}

    for kind in WriteKind:
        described[kind] = (kind.local, kind.wire)

    assert set(described) == set(WriteKind)
    assert len(set(described.values())) == len(described), "两种写的行为说明不该完全相同"
    assert all(isinstance(local, LocalEffect) for local, _ in described.values())
    assert all(isinstance(wire, WireCall) for _, wire in described.values())


def test_no_second_module_dispatches_on_the_write_vocabulary():
    """除了词表本身，没有哪个生产模块按成员逐个分派——那种地方就是「第二处」。

    逐个 ``if kind is WriteKind.X`` 的分派正是「新增一种写要四处同步」的形状：成员表改了，
    分派忘了改，直到那条路径被走到才炸。分派一律读词表上的说明（``kind.local`` /
    ``kind.wire`` / ``kind.whole_row`` / ``kind.marks_completed``），所以除词表自己以外，
    任何生产模块都不该拿成员名去比较。
    """
    dispatched: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "dida" / "sync" / "writes.py":
            continue  # 词表自己：成员名就住在这里
        members: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Compare):
                continue
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Attribute) and isinstance(side.value, ast.Name):
                    if side.value.id in {"WriteKind", "ChangeKind"}:
                        members.add(side.attr)
        if members:
            dispatched[path.relative_to(SRC).as_posix()] = members

    assert dispatched == {}, f"这些模块还在按写类型逐个分派：{dispatched}"


@pytest.mark.parametrize(
    ("kind", "keeps_its_row"),
    [
        (WriteKind.CREATE, True),
        (WriteKind.UPDATE, True),
        (WriteKind.COMPLETE, True),
        # 删除的本地效果是「这条任务不再存在」：入队当场摘掉快照（不是等推送成功再摘）。
        (WriteKind.DELETE, False),
    ],
)
def test_the_local_effect_of_a_write_is_what_its_kind_says(store, kind, keeps_its_row):
    """四种写各自的本地效果（外部行为，独立于实现里那张表）。"""
    store.apply_refresh(lists=[{"id": "inbox", "name": "收集箱"}], tasks=[_task()])

    store.enqueue(
        task_id="t1",
        kind=kind,
        payload={"title": "改过的标题"},
        now=datetime(2026, 3, 14, 12, 0, tzinfo=TZ),
    )

    assert (store.task_payload("t1") is not None) is keeps_its_row
    assert [change.kind for change in store.pending()] == [kind]
