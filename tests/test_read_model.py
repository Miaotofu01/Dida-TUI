"""v2 的三种读形状（#33）：清单索引 / 某个容器的任务列表 / 单条任务的详情。

两条接缝，与 ``docs/architecture.md`` 说好的那两个一致：

- **接缝一（主）**：``FakeBackend`` + 内存缓存。引擎的读路径跑的是生产那一份纯函数，
  替身只摆数据，所以「假后端返回什么样的 projectId，任务就归到哪一行」这件事在这里
  测得出来。
- **无接缝（纯函数）**：``dida.sync.read`` 的组装函数直接测，期望值来自 spec 与
  GLOSSARY（收集箱那一行的身份、三种行的身份、条数），不是照抄实现。

**收集箱的身份是这一组的核心**（spec 已实测事实 #2）：服务端的清单索引里没有收集箱，
它的 projectId 是**每账户不同的一串**（形如 ``inbox`` 加一截数字，下面示例用合成值 ``inbox1234567890``），
``"inbox"`` 只是请求侧别名。归类、分组、计数一律用服务端返回的那个 id。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.sync.engine import ListKind
from dida.testing import FakeBackend, ManualClock

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

INBOX_SERVER_ID = "inbox1234567890"
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


# ---------------------------------------------------------------- 清单索引：三种行


def test_the_index_carries_the_three_builtin_views_with_their_unfinished_counts():
    """内置视图也要在索引里，各带自己的未完成条数（用户故事 10 / 13 / 25–27）。

    期望值来自 spec 的三个定义：「今天」= 逾期 ∪ 截止于当前逻辑日；「最近七天」= 截止时间
    落在从今天起的七个逻辑日内；「所有」= 全部未完成。
    """
    backend = make_backend()
    backend.add_task("昨天到期", list_name="work", due=T0 - timedelta(days=1))
    backend.add_task("今天到期", list_name="work", due=T0.replace(hour=18))
    backend.add_task("下个月", list_name="work", due=T0 + timedelta(days=30))
    backend.add_task("没日期", list_name="work")
    backend.add_task("做完了", list_name="work", completed=True, completed_at=T0)

    rows = {row.id: row for row in backend.list_index()}

    assert [rows[view_id].name for view_id in ("today", "next7", "all")] == [
        "今天",
        "最近七天",
        "所有",
    ]
    assert [rows[view_id].kind for view_id in ("today", "next7", "all")] == [
        ListKind.BUILTIN,
        ListKind.BUILTIN,
        ListKind.BUILTIN,
    ]
    assert rows["today"].unfinished == 2, "逾期 ∪ 今天到期"
    assert rows["next7"].unfinished == 1, "逾期的不算在最近七天里"
    assert rows["all"].unfinished == 4, "全部未完成（含未来的与没日期的）"
    assert len(backend.tasks_in("today").items) == rows["today"].unfinished, (
        "行上的条数与进去看到的列表来自同一次求值"
    )
    assert len(backend.tasks_in("all").items) == 4


def test_real_list_rows_carry_colour_group_kind_and_permission():
    """真实清单那一行带颜色、项目组、``kind``、``permission``（用户故事 12 / 23 / 24）。"""
    backend = make_backend()
    backend.add_list(
        "工作", id="work", color="#FF6161", group_id="g1", kind="TASK", permission="write"
    )
    backend.add_list("只读", id="readonly", kind="TASK", permission="read")
    backend.add_list("笔记", id="notes", kind="NOTE", permission="write")

    rows = {row.id: row for row in backend.list_index()}
    work = rows["work"]

    assert (work.kind, work.color, work.group_id, work.project_kind, work.permission) == (
        ListKind.LIST,
        "#FF6161",
        "g1",
        "TASK",
        "write",
    )
    assert work.enterable is True
    assert rows["readonly"].enterable is False, "permission 不是 write 的进不去"
    assert rows["notes"].enterable is False, "kind 是 NOTE 的清单装不了任务"


def test_custom_view_rows_sit_between_the_builtin_views_and_the_real_lists():
    """自定义视图也是一行，排在内置视图之后、真实清单之前（用户故事 10）。"""
    backend = make_backend()
    backend.add_list("工作", id="work")
    backend.add_task("交报告", list_name="work", priority=5)
    # 视图摆的是**条件**（#36）：成员由那一条求值路径算，替身不再自己编一份成员名单。
    backend.add_view("高优先级", id="v1", priorities=(5,))

    rows = backend.list_index()

    assert [row.id for row in rows] == ["inbox", "today", "next7", "all", "v1", "work"]
    assert [row.kind for row in rows] == [
        ListKind.LIST,
        ListKind.BUILTIN,
        ListKind.BUILTIN,
        ListKind.BUILTIN,
        ListKind.CUSTOM,
        ListKind.LIST,
    ]
    custom = rows[4]
    assert (custom.name, custom.unfinished) == ("高优先级", 1)
    assert [item.task_id for item in backend.tasks_in("v1").items] == ["t1"]


def test_the_inbox_row_is_synthesised_even_when_nothing_is_known_yet():
    """空缓存也要有收集箱那一行（客户端自己补），id 先用请求侧别名占位。

    这一行下面没有任何任务可归类，所以拿它当行 id 不会把谁归错——等刷新学到服务端那一串
    （见 :func:`test_tasks_from_the_server_inbox_id_classify_under_the_inbox_row`）就换掉。
    """
    backend = make_backend()

    rows = backend.list_index()

    assert [row.id for row in rows] == ["inbox", "today", "next7", "all"]
    assert rows[0].is_inbox is True
    assert (rows[0].name, rows[0].unfinished) == ("收集箱", 0)


def test_two_inbox_rows_collapse_into_the_one_keyed_by_the_server_id():
    """库里既有旧的 ``inbox`` 行、又有服务端那一串的行时，收集箱只留一行，用服务端那一串。

    v1 的库可能有一行 id 是字面量（那时服务端的 ``project`` 对象就叫 ``inbox``）；#33 的
    刷新按服务端返回的 id 另写一行。两行并存时条数会分散在两行上，所以认出来的那一行要
    把另一行并进来（被并的那一行不再单独出现）。
    """
    backend = make_backend()
    backend.add_list("收集箱", id="inbox", is_inbox=True)
    backend.add_list("收集箱", id=INBOX_SERVER_ID, is_inbox=True)
    backend.add_task("买牛奶", list_name=INBOX_SERVER_ID)

    rows = backend.list_index()

    assert [row.id for row in rows if row.is_inbox] == [INBOX_SERVER_ID]
    assert "inbox" not in {row.id for row in rows}
    assert rows[0].unfinished == 1


# ---------------------------------------------------------------- 任务列表：某个容器


def test_a_list_task_list_holds_every_unfinished_task_including_future_ones():
    """某个清单的任务列表是这个清单的**全部**未完成任务——截止时间在未来的也在。

    v1 的读路径把未来的任务整条丢掉（「未来的任务不属于今日」），所以这是 v2 与 v1 的
    分界线之一：进了清单就要看到这个清单里的全部东西。已完成的沉在底部的已完成区里。
    """
    backend = make_backend()
    backend.add_list("工作", id="work")
    backend.add_task("昨天到期", list_name="work", due=T0 - timedelta(days=1))
    backend.add_task("今天到期", list_name="work", due=T0.replace(hour=18))
    backend.add_task("下个月", list_name="work", due=T0 + timedelta(days=30))
    backend.add_task("没日期", list_name="work")
    backend.add_task("做完了", list_name="work", completed=True, completed_at=T0)
    backend.add_task("别的清单", list_name="life")
    backend.add_task("别的清单做完了", list_name="life", completed=True, completed_at=T0)

    task_list = backend.tasks_in("work")

    assert [item.title for item in task_list.items] == [
        "昨天到期",
        "今天到期",
        "下个月",
        "没日期",
    ], "有截止时间的按时间升序，没日期的排最后；未来的那条不能丢"
    assert {item.list_name for item in task_list.items} == {"工作"}
    assert [row.title for row in task_list.completed.items] == ["做完了"]
    assert task_list.container_id == "work"


def test_an_empty_or_unknown_container_reads_as_empty():
    """空清单与认不出来的容器都给空列表，不是错误（空态文案是界面的事）。"""
    backend = make_backend()
    backend.add_list("空清单", id="empty")

    assert backend.tasks_in("empty").items == ()
    assert backend.tasks_in("empty").completed.items == ()
    assert backend.tasks_in("已经不在的清单").items == ()


# ---------------------------------------------------------------- 任务详情


def test_the_detail_reads_repeat_reminders_subtasks_and_unknown_fields():
    """单条任务的详情：标题/描述/备注/清单/截止/优先级/标签 + 只读的重复、提醒、子任务，
    以及原文里**我们不认识**的字段（#33 验收 #8）。

    ``desc`` / ``content`` 只按服务端字段名读，不在这一层下「哪个是描述」的判断：
    v1 把两者标反了，翻过来是 #43 的事（``GLOSSARY.md`` 说 描述=``content``、备注=``desc``）。
    """
    backend = make_backend()
    backend.add_list("工作", id="work")
    backend.add_task(
        "交季度报告",
        list_name="work",
        due=T0.replace(hour=18, minute=0),
        priority=5,
        desc="本周的三件事",
        content="记得附上上周的对比数据",
        tags=("工作", "季度"),
        raw={
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            "reminders": ["TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S"],
            "items": [{"id": "s1", "title": "收集数据", "status": 1}],
            "someFieldTheClientNeverHeardOf": {"nested": [1, 2]},
        },
    )

    detail = backend.task_detail("t1")

    assert detail is not None
    assert detail.title == "交季度报告"
    assert (detail.list_id, detail.list_name) == ("work", "工作")
    assert detail.due_text == "今天 18:00"
    assert detail.priority_mark == "!"
    assert detail.tags_text == "#工作 #季度"
    assert (detail.desc, detail.content) == ("本周的三件事", "记得附上上周的对比数据")
    assert detail.repeat_flag == "RRULE:FREQ=DAILY;INTERVAL=1"
    assert detail.reminders == ("TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S")
    assert [(row.title, row.completed) for row in detail.subtasks] == [("收集数据", True)]
    assert detail.unknown["someFieldTheClientNeverHeardOf"] == {"nested": [1, 2]}
    assert "repeatFlag" not in detail.unknown, "认识的字段不算未知"
    assert detail.raw["items"][0]["id"] == "s1", "原文整份带着（回写不丢字段）"


def test_a_task_that_is_not_in_the_cache_has_no_detail():
    """本地没有这条任务（光标停在一条已经不在的行上）时给 ``None``，不是错误。"""
    assert make_backend().task_detail("没有这条任务") is None



