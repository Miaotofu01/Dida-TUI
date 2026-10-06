"""本地临时 id：前缀登记处与形状判断都只有一处（#39 / #54 的裁定）。

「这个 id 服务端见过没有」这条判断在这个仓库里只许有**一处**——#54 为清单立的规矩，#39 的
任务那一族跟着同一条走。这个文件钉住它的两条外部性质：

- **前缀不许重叠**：``local-`` 曾经是 ``local-list-`` 的前缀，于是「OR 起来判断两族」必须
  先比长的——顺序依赖，加一个前缀就错（老前缀 ``local-`` 已经改成 ``local-task-``）。
  这道守卫让那种前缀**加不进来**，而不是等到某次合并把两条路合成一处时才炸。
- **合成判断与顺序无关**：两族互不为前缀之后，``any(...)`` 什么顺序都是同一个答案。

形状谓词住在 :mod:`dida.sync.writes`（叶模块：``dida.sync.lists`` 一 import 就跑
``from dida.sync.push import backoff_delay``，``push`` 又 import ``writes``，反过来放会成环
——实测四个入口全部 ``ImportError: cannot import name ... from partially initialized module``）。
存储层生成 id、写路径判断能不能发，读的都是那一处；两族各自的窄化读法
（:func:`~dida.sync.writes.is_local_list_id` / :func:`~dida.sync.writes.is_local_task_id`）
只是它的窄化，不是第二份形状判断。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.storage.store import LOCAL_LIST_PREFIX as STORE_LIST_PREFIX
from dida.storage.store import PendingChange
from dida.sync.writes import (
    LOCAL_ID_PREFIXES,
    LOCAL_LIST_PREFIX,
    LOCAL_TASK_PREFIX,
    WriteKind,
    is_addressable,
    is_addressable_task,
    is_local_id,
    is_local_list_id,
    is_local_task_id,
)

TZ = timezone(timedelta(hours=8))


def _change(*, task_id: str = "srv-1", list_id: str = "work", **payload: object) -> PendingChange:
    """一条队列里的改动（形状照 :class:`~dida.storage.store.PendingChange`）。

    直接摆一条改动，而不是起一个库：这里问的是**判据**，不是存储怎么入队（那一条在
    ``tests/test_engine_writes.py`` 里走真库）。
    """
    return PendingChange(
        id=1,
        task_id=task_id,
        list_id=list_id,
        kind=WriteKind.UPDATE,
        payload=dict(payload),
        created_at=datetime(2026, 3, 14, 12, 0, tzinfo=TZ),
    )


def test_no_local_prefix_is_a_prefix_of_another():
    """任何一条前缀都不许是另一条的前缀——否则「是不是本地 id」就成了顺序依赖。

    ``local-``（#39 的老任务前缀）正好是 ``local-list-`` 的前缀：两族各自喂自己的 id 时
    看不出问题，一旦有人把它们 OR 成一个判断，答案就跟着比较顺序走。改成 ``local-task-``
    之后这条性质成立，而它**由测试守着**：将来谁再加一族（比如标签）加错了，红的是这里。
    """
    offenders = [
        (short, long)
        for short in LOCAL_ID_PREFIXES
        for long in LOCAL_ID_PREFIXES
        if short != long and long.startswith(short)
    ]

    assert offenders == [], f"这些前缀互相包含，判断会有歧义：{offenders}"


def test_the_local_id_predicate_is_the_union_of_the_two_families():
    """``is_local_id`` = 「是哪一族都算」；两族各自的窄化读法合起来正好是它。

    「与顺序无关」不单独做实验：只要没有任何前缀是别人的前缀（上一条），``any(...)`` 的
    顺序就没有语义——这条在这里把三种判断对同一批 id 的答案逐一对上。
    """
    samples = (
        f"{LOCAL_LIST_PREFIX}1",
        f"{LOCAL_TASK_PREFIX}0d0e",
        "inbox123456",  # 服务端返回的收集箱 id：每账户一串，不是本地临时 id
        "srv-1",
        "local",  # 只有前缀本身、没有后面的号：不算
        "",
    )

    for value in samples:
        expected = is_local_list_id(value) or is_local_task_id(value)
        assert is_local_id(value) is expected, f"{value!r} 上两族判断与合成判断对不上"


def test_the_two_families_do_not_recognize_each_others_ids():
    """窄化读法各认自己那一族——这是老 ``local-`` 前缀真正的坑。

    ``is_local_task_id("local-list-1")`` 在重叠前缀下是 ``True``：改一条任务时会顺手把一条
    **清单**的 id 当成任务 id 拦下来（或者反过来）。今天各自只喂自己那一族的 id 所以没坏，
    但那种「碰巧对」正是要拆掉的东西。
    """
    assert is_local_task_id(f"{LOCAL_TASK_PREFIX}0d0e") is True
    assert is_local_task_id(f"{LOCAL_LIST_PREFIX}1") is False, "清单的 id 不是任务的 id"
    assert is_local_list_id(f"{LOCAL_LIST_PREFIX}1") is True
    assert is_local_list_id(f"{LOCAL_TASK_PREFIX}0d0e") is False, "任务的 id 不是清单的 id"


def test_the_storage_layer_reexports_the_one_list_prefix():
    """存储层那个名字是**转发**，不是第二份定义（#54 把它移走之后也不许回来）。"""
    assert STORE_LIST_PREFIX is LOCAL_LIST_PREFIX


# ---------------------------------------------------------------- 可寻址：要发的 id 都要确认


def test_a_write_that_names_an_unclaimed_list_is_not_addressable():
    """任务的「可寻址」= **``projectId`` 与 ``taskId`` 都已确认**（#53 的补充，实测过）。

    落点在一条还没推出去的清单里时，任务自己的 id 可能已经是服务端给的真 id，但这次请求要
    说的 ``projectId`` 还是 ``local-list-…``——服务端没有那个清单，于是 404、退避、
    **永远出不了队**（#45 的 merger 探针量到 ``kind=move record list_id='local-list-1'``
    之后那笔改动仍留在队列里）。搬运还要说清**搬到哪去**，那一边同样要确认，所以
    ``target_project_id`` 一起查。
    """
    assert is_addressable_task("srv-1", project_id="work") is True, "两个 id 都是真的"
    assert is_addressable_task("srv-1", project_id="local-list-1") is False, "落点还没被认领"
    assert is_addressable_task("srv-1", project_id="work", target_project_id="life") is True
    assert (
        is_addressable_task("srv-1", project_id="work", target_project_id="local-list-2") is False
    )
    assert is_addressable_task(LOCAL_TASK_PREFIX + "0d0e", project_id="work") is False, (
        "这条任务自己还没被认领"
    )
    assert is_addressable_task("srv-1", WriteKind.CREATE, project_id="work") is True, (
        "新建的 URL 里没有任务 id（带着临时任务 id 的正是它自己），但它落进去的清单要确认"
    )
    assert (
        is_addressable_task(
            LOCAL_TASK_PREFIX + "0d0e", WriteKind.CREATE, project_id="local-list-1"
        )
        is False
    )


def test_a_queued_change_is_judged_on_every_id_it_would_send():
    """推送循环问的是同一件事、判据也是同一处——它读的是**这条改动真要发的那几个 id**。

    ``projectId`` 在改动行上（``list_id``：搬运用它当 ``fromProjectId``，完成与删除写在路径
    里），也可能在 payload 里（搬运的目标清单）。两边都要看，否则一条「搬进还没推出去的清单」
    的改动会安静地打到一个服务端不认识的名字上。
    """
    assert is_addressable(_change(list_id="work")) is True
    assert is_addressable(_change(list_id="local-list-1")) is False, "改动行上那个"
    assert is_addressable(_change(list_id="work", projectId="local-list-2")) is False, (
        "payload 里那个（搬运的目标清单）"
    )
    assert is_addressable(_change(list_id="work", projectId="life")) is True
    assert is_addressable(_change(task_id=LOCAL_TASK_PREFIX + "0d0e")) is False


def test_not_addressable_always_means_one_of_the_ids_is_local():
    """不变量：判据说「发不出去」，永远是**这次要说的 id 里有一个是本地占位的**。

    它把判据与那份前缀登记表钉在一起，也让「拒绝的时候说不出是哪一半」这一支不可达——
    ``dida.sync.push._unaddressable`` 靠的就是这条（它只在那三个 id 上认族）。判据哪天自己
    长出一条与 id 无关的理由（比如「这条改动太老」），这条测试先红，那时得先决定用户看到
    哪一种原因。
    """
    ids = ("srv-1", "work", LOCAL_LIST_PREFIX + "1", LOCAL_TASK_PREFIX + "0d0e")
    checked = 0
    for task_id in ids:
        for project in ids:
            for target in ids:
                addressable = is_addressable_task(
                    task_id, WriteKind.UPDATE, project_id=project, target_project_id=target
                )
                local_named = any(is_local_id(value) for value in (task_id, project, target))
                assert addressable is not local_named, (
                    f"判据与登记表不一致：task={task_id} project={project} target={target}"
                )
                checked += 1
    assert checked == len(ids) ** 3, "四个 id 的三重组合都查过"
