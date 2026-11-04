"""标签：能挑的那些名字从哪儿来（工单 #45）。

挑标签这一格要的是一份**名字**，两份来源并起来：

- **服务端给过的那一份**（``GET /open/v1/tag``，:meth:`TagMixin.load_tags`）——含那些还没
  打在任何人身上的标签，是权威的那一份；
- **本地缓存里任务上出现过的那些**（:meth:`TagMixin.tags`）——断网时它就是全部能挑的东西，
  而一条任务上已经打着的标签必须挑得动，否则「取消一个标签」在断网时无路可走。

## 为什么是「用的时候拉一次」，不是跟着全量刷新拉

全量刷新（:mod:`dida.sync.refresh`）是**取全**那一条路：中途任何一次失败都不落库、不留半份
刷新。标签列表不是那个整体的一部分——它缺席不会让任何一条任务读错，而把它塞进那一趟会让
「标签这一次没拉到」变成「整次刷新失败」。所以它是**用户打开挑标签那一格时**的一次独立
调用（与 ``r`` 触发一次同步同一个形状：用户按了键，才去问一次服务端）。

拉回来的那一份**只活在引擎内存里**，不落库：它的用处是「这一次挑的时候有哪些标签」，
而标签的持久事实在每一条任务的 ``tags`` 里（全量刷新每次都带回来）。落一张表要多一套
schema 与迁移，换不到什么。

## 这个客户端不做的两件事（如实告知，不是「接口做不到」）

- **新建标签**：``POST /open/v1/tag`` 是文档里**有**的端点（``openapi-dida365.md:1612–1654``），
  所以「不能新建」是**这个客户端**的范围决定（spec 的 Out of Scope :306），不是接口的能力上限。
  文案必须说「这个客户端不做」。
- **删除标签**：``:1576–1654`` 只有 ``GET`` 与 ``POST``，没有 update、没有 delete——这一半
  是接口真的没有。

文案在 :data:`dida.tui.messages.TAGS_PICKER_HINT`（那才是给用户看的那一份）。
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

__all__ = ["TagMixin", "TagReader", "tag_names"]


@runtime_checkable
class TagReader(Protocol):
    """标签列表要的那一次网络调用；t07 的 ``DidaApiClient`` 满足它。"""

    async def list_tags(self) -> list[dict[str, Any]]:
        """``GET /open/v1/tag``：全部标签（``OpenTag``：name / label / sortOrder / color / type）。"""
        ...


def tag_names(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """``OpenTag`` 那几行 → 挑选用的一串名字。

    取 ``name`` 而不是 ``label``：任务的 ``tags`` 数组里装的是名字，写回去的也只能是名字
    （文档要求 ``label`` 小写之后等于 ``name``，``:1620–1621``）。认不出来的行跳过——
    ``list_tags`` 已经按「每项都得有 ``name``」把形状钉死了，这里是第二道，不是第一道。
    """
    return tuple(str(row["name"]) for row in rows if row.get("name"))


class TagMixin:
    """标签列表的读：拉一次、记在内存里，另加本地已经见过的那些。

    方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上（要用 ``self._client`` /
    ``self._source``），单独一个 mixin 不完整。
    """

    async def load_tags(self) -> tuple[str, ...]:
        """拉一次标签列表（``GET /open/v1/tag``），记下来并返回这一份。

        失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，往上抛——调用方
        （详细页那一格）要**说出来**它没拉到，同时照旧让用户挑本地已知的那些
        （:meth:`tags`）。悄悄换成空列表就是「你没有标签」，那是对用户说假话。
        """
        reader = self._tag_reader()
        self._tags = tag_names(await reader.list_tags())
        return self._tags

    def tags(self) -> tuple[str, ...]:
        """读：现在能挑的那些标签名——服务端给过的那一份 ∪ 本地任务上出现过的那些。

        去重保序（服务端那一份在前）：同一份名单在两个来源里都有时只画一次，而顺序按
        「先权威、后本地」——服务端的 ``sortOrder`` 是它自己的顺序，这里不重排。
        """
        names: list[str] = list(self._tags)
        if self._source is not None:
            for task in self._source.tasks():
                names.extend(task.tags)
        return tuple(dict.fromkeys(names))

    def _tag_reader(self) -> TagReader:
        """拉标签列表要的那个客户端。没接上就大声报错——绝不假装拉过了。"""
        return self._caps.tag_reader()
