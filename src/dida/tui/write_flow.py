"""写出去之后：**一次写入的完整后果**只在这里说一遍（工单 #83）。

十一个处理函数各自走完过那十二行——接住失败、挑一句话、推一轮、重画、把结果留在底部
那一行——其中三处逐字相同（改清单名字 / 改视图条件 / 删任务那几段）。次序、落点、以及
「屏幕还在不在」的守卫因此散在十一个地方：想改顺序（先推再刷）、或者把报告从状态栏挪到
详细页底部，得同时改十一处；漏一处就是两种说法并存（同一个「保存行」有十一个写入口，
其中只有一部分记得先问「屏幕还在不在」）。

收深之后这条链只有**一份实现**：

    执行一次写 → 分拣两类失败（本地没有这条 / 服务端与网络）
    → 挑出那一句话与它的落点（状态栏 · 详细页底部）→ 推一轮 → 重画

每个处理函数只声明两件事：**写了什么**（:attr:`Write.perform`）与**成功那句说什么**
（:attr:`Write.said`）。失败怎么说、结果落在哪由它挑的那一支 :class:`Reply` 说——表在下面，
一条 = 一种写。浮层交回来的那一份字符串值到一次写的翻译也只在一处
（:meth:`WriteFlow.pick_write` 与它调用的 :meth:`WriteFlow._apply_pick`）。

**两处落点，一条规矩。** 报告落在详细页底部的那几笔（改字段 / 改期 / 搬运）要**等这一轮
推送落地**再重画：那一行说的就是「你刚才那一下出去没有」（``已保存`` / ``待推送（N）``），
不等它就说不准——这也是「先推再刷」那条次序的由来。落在状态栏的那几笔不等：引擎自己排了
一轮立刻推送（ADR-0002），状态栏那个「待推送 N」自己会跟上，界面不必替它等网络。次序与这
条规矩都长在这一个文件里，全程序只有一种说法。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from dida.sync.engine import (
    DidaError,
    Engine,
    UnknownListError,
    UnknownTaskError,
    UnknownViewError,
)
from dida.tui import messages
from dida.tui.overlays import multi_values
from dida.tui.pages.detail import LIST_FIELD, PRIORITY_FIELD, TAGS_FIELD

__all__ = [
    "LIST_WRITE",
    "LOCAL_MISSING",
    "Landing",
    "Reply",
    "TASK_COMPLETE",
    "TASK_CREATE",
    "TASK_DEFER",
    "TASK_DELETE",
    "TASK_FIELD",
    "VIEW_WRITE",
    "Write",
    "WriteFlow",
]


class Landing(Enum):
    """一次写的结果**说在哪儿**——全程序只有这两处（工单 #83 的用户故事 22）。"""

    STATUS = "status"
    """状态栏：数据怎么样，以及这一笔的下场（新建 / 删除 / 顺延 / 完成 / 清单 / 视图）。"""

    DETAIL = "detail"
    """详细页底部那一行：就在编辑现场，说的是「你刚才那一下出去没有」。"""


LOCAL_MISSING = (UnknownTaskError, UnknownListError, UnknownViewError)
"""失败的第一类：**本地那一份里没有这一条**（用户该做的是刷新一下再看）。

它与第二类（别的 :class:`~dida.sync.engine.DidaError`：断网、凭据失效、服务端拒绝）要做的
下一步完全不同，所以从这一层就分开走，各自有各自那句话。措辞自己分得清两类的（清单 / 视图 /
完成 / 删除 / 新建那几句 ``messages`` 里都分好了），:class:`Reply` 只给 ``failed`` 一支。
"""


@dataclass(frozen=True)
class Reply:
    """一种写的「失败怎么说 + 结果落在哪」（表见下面几条 ``TASK_*`` / ``LIST_WRITE`` / ``VIEW_WRITE``）。

    ``refused`` 是给「两类失败要分开说」的那一种用的（改字段：本地没有这条与保存失败在
    ``messages`` 里本来就是两句话）；``failed`` 缺省 ``None`` 表示**这一种写没有失败这一档**
    （引擎那一条路没有可抛的结构化错误，比如顺延）——那种情况下照旧把异常往外抛，不吞。
    """

    failed: Callable[[DidaError], str] | None
    refused: Callable[[DidaError], str] | None = None
    landing: Landing = Landing.STATUS

    def when_missing(self, error: DidaError) -> str | None:
        """第一类失败（**本地没有这一条**）该说的那一句话。

        这一类没有专门说法的几种（清单 / 视图 / 完成 / 删除 / 新建）由 ``failed`` 那一支自己
        分——``messages`` 里那几句本来就认得出两种，所以不必各写一遍。
        """
        return self._sentence(self.refused if self.refused is not None else self.failed, error)

    def when_rejected(self, error: DidaError) -> str | None:
        """第二类失败（服务端与网络）该说的那一句话。"""
        return self._sentence(self.failed, error)

    @staticmethod
    def _sentence(word: Callable[[DidaError], str] | None, error: DidaError) -> str | None:
        return None if word is None else word(error)


# --------------------------------------------------------------------------- 措辞与落点那张表
#
# 一条 = 一种写。加了新的写操作就在这里加一条，处理函数只是挑一条——落点与失败的说法因此
# 各只有一处：想挪报告就从这一张表上挪。

TASK_FIELD = Reply(
    failed=messages.field_save_failed_message,
    refused=lambda _error: messages.UNKNOWN_TASK_MESSAGE,
    landing=Landing.DETAIL,
)
"""改一条任务的字段（改字段 / 改期 / 搬运）：报告落在详细页底部那一行（用户故事 22）。"""

TASK_CREATE = Reply(failed=messages.create_failed_message)
"""新建任务：报告落在状态栏（它在层二上，那一页没有「保存行」）。"""

TASK_COMPLETE = Reply(failed=messages.toggle_complete_failed_message)
"""完成 / 取消完成：同样落在状态栏。"""

TASK_DELETE = Reply(failed=messages.delete_failed_message)
"""删除任务：落在状态栏，而且说的是「**没**删成」。"""

TASK_DEFER = Reply(failed=None)
"""顺延：没有失败这一档（引擎那一支没有可抛的结构化错误），所以没有那句话。"""

LIST_WRITE = Reply(failed=messages.list_write_failed_message)
"""清单的建 / 改 / 删：落在状态栏。"""

VIEW_WRITE = Reply(failed=messages.view_write_failed_message)
"""视图的建 / 改 / 删（只在本地）：落在状态栏。"""


@dataclass(frozen=True)
class Write:
    """一次写的声明：**写了什么**（:attr:`perform`）与**成功那句说什么**（:attr:`said`）。

    ``perform`` 是那一次引擎调用（``engine.write`` / ``create`` / ``delete``……）。它的返回值
    只有一件事要读：``False`` = **什么都没改**（工单 #79 的回报值）——那一笔不入队、不推、
    不重画、也不出声。``reply`` 指向上面那张表的一条：失败怎么说、结果落在哪。
    ``said`` 是成功之后要说出来的那一句（``None`` = 没什么可说；详细页那几笔的「已保存」
    由底部那一行自己从引擎状态读出来，不在这里）。
    """

    perform: Callable[[], object]
    reply: Reply
    said: str | None = None


class WriteFlow:
    """一次写的后半段（工单 #83）——十一个入口共用的一份实现。

    它按混入方式长在 :class:`~dida.tui.app.DidaApp` 上，所以下面用到的 ``engine`` /
    ``refresh_view`` / ``_write_status`` / ``_write_save_line`` / ``_notify_step`` 都是 app
    那几道已经带「屏幕还在不在」守卫的门。
    """

    engine: Engine
    """引擎（由 app 装进来）；TUI 只通过它读写。"""

    async def finish_write(self, write: Write) -> None:
        """走完那一条链：执行一次写 → 分拣失败 → 挑话与落点 → 推一轮 → 重画。

        次序（先推再刷）、落点、以及 ``await`` 之后「屏幕还在不在」的守卫都在这里，不再靠
        十一个入口各自记得。
        """
        try:
            wrote = write.perform()
        except LOCAL_MISSING as error:
            # 第一类：**本地没有这一条**（用户该做的是刷新一下再看）。它与第二类分开接，
            # 因为下一步完全不同，说的话也是两句。
            self._fail(write.reply, error, sentence=write.reply.when_missing(error))
            return
        except DidaError as error:
            # 第二类：服务端与网络（断网、凭据失效、服务端拒绝）。
            self._fail(write.reply, error, sentence=write.reply.when_rejected(error))
            return
        if wrote is False:
            # 「什么都没改」（工单 #79）：不入队、不推、不重画、不出声——空操作在屏幕上
            # 一点痕迹都不留（用户故事 7）。界面在这里是**纯消费**那个回报值。
            return
        if write.reply.landing is Landing.DETAIL:
            # 落在详细页底部的那几笔：等这一轮推送落地，那一行才知道写「已保存」还是
            # 「待推送（N）」。**次序的唯一出处就是这三行**（先推再刷）。落在状态栏的那几笔
            # 不等它：引擎按配置自己排推送（默认立刻，ADR-0002），这一层不替它等网络。
            await self.engine.push_pending()
            if not self.is_running:
                return
        self.refresh_view()
        if write.said is not None:
            # 成功那句是**短暂回声**（toast）：它不占状态栏那一行——那一行说的是「数据怎么样」
            # （ADR-0007 四），不该被一次按键挤掉。
            self._notify_step(write.said)

    def _fail(self, reply: Reply, error: DidaError, sentence: str | None) -> None:
        """把这次失败说到它的落点上；声明了「没有失败这一档」的那种照旧往外抛（不吞）。

        那句话由调用方按**失败的种类**从 :class:`Reply` 上取好递进来（``when_missing`` /
        ``when_rejected``），这里只管它落在哪、以及先说还是先重画。
        """
        if sentence is None:
            raise error
        if reply.landing is Landing.DETAIL:
            # 先按当下那一份重画，再说这句话：重画会把底部那一行写成「已保存」/「待推送
            # （N）」，反过来的话这句话当场被抹掉。落在状态栏的那几笔不重画——写被拒绝了，
            # 本地那一份没动，屏幕本来就是对的（重画反而会把没通知界面的东西带上来）。
            self.refresh_view()
            self._write_save_line(sentence)
            return
        self._write_status(sentence)

    # ---------------------------------------------------------------- 浮层值 → 一次写

    async def pick_write(self, task_id: str, field: str, values: Mapping[str, str]) -> None:
        """挑选浮层关掉了：把交回来那一份值翻译成一次写，再走完整条链。

        回调只把值递进来（**它是哪一格、值怎么读**都归这一层）。表单交出来的**一定**是一份
        值：``FormOverlay`` 只有 ``dismiss(self.values())`` 一条路，没有「取消」那一档（#66）。
        """
        await self.finish_write(
            Write(
                perform=lambda: self._apply_pick(task_id, field, values),
                reply=TASK_FIELD,
            )
        )

    def _apply_pick(self, task_id: str, field: str, values: Mapping[str, str]) -> bool:
        """挑完的那一份怎么变成一次写（三条路各自的形状只在这一个地方）。

        **回报「真的写了一笔吗」——那个判断整个归引擎**（工单 #79）：挑回原来那一档时写的
        那一次自己收敛掉（不写、不回推），回一个 ``False``；这一层不再自己比一遍（在 #79
        之前这里比过三样东西，每样比的还是不同的底稿，而第七处漏掉了）。

        优先级与标签只是普通更新（整份底稿带回去那件事由 ``update_task`` 的 ``snapshot=``
        管）；清单那一路走 ``move_task``——搬运不是一次普通字段更新。

        ``int(...)`` 与 ``multi_values(...)`` 是**线上编码**：选项的值是 ``0/1/3/5``、标签
        是一串值（顺序不是改动的一部分）。表外的值不该出现（选项就是从那张表生成的），
        认不出来就当没挑——不替服务端猜一个档位；这**不是**「改了没有」的判断。
        """
        if field == LIST_FIELD:
            return self.engine.move_task(task_id, to_list_id=values[LIST_FIELD])
        if field == PRIORITY_FIELD:
            picked = values[PRIORITY_FIELD]
            if not picked.isdigit():
                return False
            return self.engine.write(task_id, changes={"priority": int(picked)})
        if field == TAGS_FIELD:
            picked = multi_values(values[TAGS_FIELD])
            return self.engine.write(task_id, changes={"tags": list(picked)})
        return False
