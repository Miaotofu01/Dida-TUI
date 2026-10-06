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

from dida.storage.store import LOCAL_LIST_PREFIX as STORE_LIST_PREFIX
from dida.sync.writes import (
    LOCAL_ID_PREFIXES,
    LOCAL_LIST_PREFIX,
    LOCAL_TASK_PREFIX,
    is_local_id,
    is_local_list_id,
    is_local_task_id,
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
