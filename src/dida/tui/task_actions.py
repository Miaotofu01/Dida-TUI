"""光标下那一条任务的写动作：完成、顺延、优先级、删除、用浏览器打开。

**一个变化原因**：用户按键之后，「光标下那一条」会怎么样。这一片里没有排版、没有同步泵、
也不认识输入框——每个方法都是三五行：取光标下那条的 id → 交给引擎 → 重画。

写本身仍然全在引擎里（乐观写、立即推送、退订重试都是 ``dida.sync`` 的事）；这里只负责
「按哪个键、对哪一条、失败时说什么」。

几条一以贯之的口径（v1 定下来的，v2 继续）：

- **空屏上按键不该报错**：光标下没有任务就什么都不做，不是错误。
- 引擎拒绝写入（本地没有底稿）时如实说一句，不崩、也不拿一个猜来的清单 id 硬发。
- 删除先问一句，确认是唯一的防线。
- 完成之后那一行亮一下：完成在服务端不可逆（ADR-0002），这是它唯一的补偿。
"""

from __future__ import annotations

from dida.sync.engine import TaskItem, UnknownTaskError
from dida.tui.escape import task_url
from dida.tui.messages import (
    UNKNOWN_DELETE_MESSAGE,
    delete_prompt,
    no_browser_message,
)
from dida.tui.panes import ConfirmScreen, TaskPane

FLASH_SECONDS = 0.45
"""完成后那一行高亮多久：够看清这一下生效了，又不至于拖住下一次分诊。"""


class TaskActionsMixin:
    """挂在 app 上的五个动作（t11 / t13 / t16 / t17 / t19）。

    mixin 不是完整的 app：它用 ``self.engine`` / ``self.query_one`` / ``self.refresh_view``
    / ``self._write_status``——这些都由 :class:`~dida.tui.app.DidaApp` 提供。状态栏那句走
    ``self._write_status``，不再自己查 widget（见 app.py：关窗时谁都不许再往屏幕上写）。
    """

    def action_complete(self) -> None:
        """完成光标下的任务并立即推送（`x`）。

        ADR-0002：服务端**没有**「取消完成」接口，这一次按键是不可撤销的事实，所以这里
        只做一条路——交给引擎（乐观写 + 立即推送 + 进重试队列），不做任何本地的反向操作。

        完成之后分两步走：先让这一行亮一下（``flash``），再按本地结果重画（真引擎下这一行
        已经不在未完成里了）。两步都在这根线程上，读的都是同一份本地状态。

        ⚠ 上面那两句「服务端没有取消完成 / 不可撤销」是 v1 的结论，ADR-0002 已被实测推翻；
        ``sync/engine.py`` 里同一段话与这里是 **#38** 的地盘（取消完成是它加的），本工单
        原样搬运、不改结论。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:  # 空屏上按 x：什么都不做，不是错误
            return
        self.engine.complete(task_id)
        self.query_one(TaskPane).flash(task_id)
        self.set_timer(FLASH_SECONDS, self._settle_after_complete)

    def _settle_after_complete(self) -> None:
        """收起高亮，并按引擎的当前视图重画。定时器到点就调这一次。

        这一次回调也是「可能回来晚了」的那一种：定时器到点时用户可能已经关了窗
        （widget 都拆了），那就什么都不做——高亮没有人再看。
        """
        if not self.is_running:
            return
        self.query_one(TaskPane).flash(None)
        self.refresh_view()

    def action_defer(self) -> None:
        """``g``：把光标下那条任务顺延到下一个逻辑日。"""
        self._defer(days=1)

    def action_defer_week(self) -> None:
        """``G``：顺延到下周同一天（同一个星期几）。"""
        self._defer(days=7)

    def _defer(self, *, days: int) -> None:
        """顺延光标下那条任务，然后重画。

        落点由引擎按逻辑日算（TUI 不碰日界）；光标下没有任务就什么都不做——空屏上按键
        不该报错。``days`` 是逻辑日数：``g`` 1 天、``G`` 7 天。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.engine.defer(task_id, days=days)
        self.refresh_view()

    def action_priority(self) -> None:
        """``p``：把光标下那条任务的优先级推进一档（无 → 低 → 中 → 高 → 无）。

        推进哪一档由引擎定：线上编码 ``0/1/3/5`` 是 API 的事实，TUI 不认识优先级取值，
        只说「推进这一条」（与 ``x`` / ``g`` 一样）。光标下没有任务就什么都不做——
        空屏上按键不该报错。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.engine.cycle_priority(task_id)
        self.refresh_view()

    # ---------------------------------------------------------------- 删除（t16）

    def action_delete(self) -> None:
        """``d``：先问一句，确认了才删（工单 #16）。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g``/``e`` 同一条口径）。

        **确认是这里唯一的防线**：滴答清单的 Open API 里没有 undelete、没有回收站、也没有
        「已删除」列表，删掉就是删掉了。所以瞄准的是按下 ``d`` 那一刻光标下那条任务，
        提示语里点名是哪一条（``title``），而删除动作只发生在浮层回来 ``True`` 的时候。
        """
        pane = self.query_one(TaskPane)
        task_id = pane.selected_task_id
        if task_id is None:
            return
        self.push_screen(
            ConfirmScreen(delete_prompt(pane.selected_title or task_id)),
            lambda confirmed: self._finish_delete(task_id, confirmed),
        )

    def _finish_delete(self, task_id: str, confirmed: bool | None) -> None:
        """浮层关掉了：只有 ``True`` 才写。

        ``False``（``n``/``Esc``）与 ``None`` 都什么都不做——取消必须一点痕迹都不留：
        没有待推送改动、本地快照照旧、没有请求发出去。引擎拒绝写入（本地已经没有这条任务
        的底稿，#25）时如实说一句，不崩，也不拿一个猜来的清单 id 硬发。
        """
        if not confirmed:
            return
        try:
            self.engine.delete(task_id)
        except UnknownTaskError:
            self._write_status(UNKNOWN_DELETE_MESSAGE)
            return
        self.refresh_view()

    # ---------------------------------------------------------------- 逃生舱（t19）

    def action_open(self) -> None:
        """``o``：把光标下那条任务交给系统浏览器（工单 #19）。

        **只有浏览器这一条路。** ADR-0002 的「逃生舱的确切形态（已核实）」记着：官方
        桌面客户端不接受任务深链（``dida365://`` 不存在、Linux 的 ``.desktop`` 没注册
        协议处理器、主进程也不处理 argv），能做出来的就是厂商自己在「复制任务链接」里
        生成的那条网页版路由。所以这里不试任何 ``xxx://``，也不假装能切到桌面 App。

        URL 由 :func:`~dida.tui.escape.task_url` 拼（纯函数，含收集箱那条字面量替换），
        清单 id 取**光标下那一条**的：TUI 不做判断，只把视图模型里已经有的事实交出去。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g``/``e`` 同一条口径）。

        交不出去时**必须出声**：完成在服务端不可逆，这个键是它的补偿，静默失败比吵一句
        坏得多。两种失败都报同一句话（状态栏），并且把 URL 原样给人抄——``webbrowser``
        找不到浏览器时抛 ``webbrowser.Error``，``open()`` 回 ``False`` 也是一种失败。
        """
        item: TaskItem | None = self.query_one(TaskPane).selected_item
        if item is None:
            return
        url = task_url(item.list_id, item.task_id)
        try:
            opened = self._open_url(url)
        except Exception:
            # 开手当场抛（``webbrowser.Error`` 就是这一种）：按上面那条规矩如实说，
            # 不让一个找不到浏览器的机器把整个界面带走。
            opened = False
        if not opened:
            self._write_status(no_browser_message(url))
