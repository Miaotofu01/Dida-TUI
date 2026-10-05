"""v2 的三种读形状（#33）：清单索引 / 某个容器的任务列表 / 单条任务的详情。

两条接缝，与 ``docs/architecture.md`` 说好的那两个一致：

- **接缝一（主）**：``FakeBackend`` + 内存缓存。引擎的读路径跑的是生产那一份纯函数，
  替身只摆数据，所以「假后端返回什么样的 projectId，任务就归到哪一行」这件事在这里
  测得出来。
- **无接缝（纯函数）**：``dida.sync.read`` 的组装函数直接测，期望值来自 spec 与
  GLOSSARY（收集箱那一行的身份、三种行的身份、条数），不是照抄实现。

**收集箱的身份是这一组的核心**（spec 已实测事实 #2）：服务端的清单索引里没有收集箱，
它的 projectId 是**每账户不同的一串**（形如 ``inbox`` 加数字，实测 ``inbox1025205395``），
``"inbox"`` 只是请求侧别名。归类、分组、计数一律用服务端返回的那个 id。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.sync.engine import ListKind
from dida.testing import FakeBackend, ManualClock

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

INBOX_SERVER_ID = "inbox1025205395"
"""实测的那个形状：``inbox`` 加一截数字（spec 的已实测 API 事实 #2）。"""


def make_backend() -> FakeBackend:
    return FakeBackend(clock=ManualClock(T0), day_end="24:00")


# ---------------------------------------------------------------- 收集箱的身份


def test_tasks_from_the_server_inbox_id_classify_under_the_inbox_row():
    """假后端返回形如 ``inbox`` 加数字的 projectId：那条任务归到收集箱行下。

    服务端的清单索引里没有收集箱（实测），所以那一行是**客户端自己补的**——补出来的行
    必须用服务端返回的那一串当 id，否则 ``unfinished`` 是 0、任务一条也归不进去。
    """
    backend = make_backend()
    backend.add_task("买牛奶", list_name=INBOX_SERVER_ID)
    backend.add_task("写周报", list_name="work")

    rows = backend.list_index()

    assert rows[0].is_inbox is True, "收集箱排在最上面（用户故事 11）"
    assert rows[0].id == INBOX_SERVER_ID, "行 id 是服务端返回的那一串"
    assert rows[0].name == "收集箱"
    assert rows[0].unfinished == 1, "这条任务归在它下面"
    assert rows[0].kind is ListKind.LIST, "收集箱是真实清单那一类行，不是视图"

    by_id = {row.id: row for row in rows}
    assert by_id["work"].unfinished == 1, "别的清单照旧各数各的"
    assert [item.task_id for item in backend.tasks_in(rows[0].id).items] == ["t1"]


def test_the_literal_inbox_is_not_the_classification_key():
    """拿字面量 ``"inbox"`` 去比对**不是**正确实现——这条测试钉的就是这句话。

    把归类写成 ``list_id == "inbox"`` 的实现会在这里当场变红：行 id 会变成字面量
    （第二条断言）、条数会是 0（第三条）、而拿字面量去找容器还会找到那条任务
    （第四条）。所以这不是一条「怎么实现都过」的测试。
    """
    backend = make_backend()
    backend.add_task("买牛奶", list_name=INBOX_SERVER_ID)

    rows = backend.list_index()
    inbox = rows[0]

    assert inbox.id == INBOX_SERVER_ID
    assert "inbox" not in {row.id for row in rows}, "字面量不该作为收集箱那一行的 id 出现"
    assert inbox.unfinished == 1, "字面量比对会数出 0"
    assert backend.tasks_in("inbox").items == (), "拿字面量去找容器，什么也找不到"
