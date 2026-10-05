"""写词汇：一次乐观写有哪几种（t32）。

**这个模块是写类型的唯一定义处。** 引擎的 ``WriteKind`` 与存储的 ``ChangeKind`` 说的是同一
套词汇，指的也是**同一个对象**（``dida.storage.store`` 里那个名字只是别名）：加一种写只需要
在这里加一个成员，两层立刻都认得它。

在 t32 之前这套词汇被写了两遍——引擎一份、存储一份，成员一字不差地重复，中间还有一个按值
换算的 ``_storage_kind()``。重复本身不难受，难受的是它能漂：一边加了成员另一边没加，直到真
走到那条写路径才炸，而那时错误离原因已经很远了。

为什么单独一个模块，而不是把词表留在存储层：``dida.storage.store`` 在模块级 import
``dida.sync.view``，所以引擎**不能**在顶层 import 存储（``engine._storage_kind`` 那条延迟
import 的注释记着这件事）。这一层只 import 标准库，谁先 import 都行。

加一种写类型，改动落在这里与它的推送分支（:meth:`dida.sync.engine.SyncEngine._send`）——
词表本身不再有第二处要同步。
"""

from __future__ import annotations

from enum import Enum

__all__ = ["WriteKind"]


class WriteKind(Enum):
    """一次乐观写的种类。

    值与存储层 ``pending_changes.kind`` 那一列直接对应（``ChangeKind`` 就是本枚举的别名）。
    :meth:`~dida.sync.engine.SyncEngine.write` 只服务**已有任务**的改 / 完成 / 删；新建走
    :meth:`~dida.sync.engine.SyncEngine.create`，它的本地效果是「凭空多出一条任务」，
    与那三条盖字段的路径不是一回事。
    """

    CREATE = "create"
    """新建：本地先造一条（临时 id），推送走 ``POST /open/v1/task``（t15）。"""

    UPDATE = "update"
    """改字段：把 ``changes`` 推给 ``POST /open/v1/task/{taskId}``。"""

    COMPLETE = "complete"
    """完成：本地立刻标记完成，推送走 ``POST .../task/{taskId}/complete``（无请求体）。"""

    DELETE = "delete"
    """删除：本地立刻摘掉快照，推送走 ``DELETE .../task/{taskId}``。"""
