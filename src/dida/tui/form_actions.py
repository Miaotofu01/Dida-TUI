"""两个输入框的流程：``e`` 改期、``a`` 新建（工单 #14 / #15）。

**一个变化原因**：用户在框里打完一行、按了 Enter 之后会发生什么。两个框共用同一套语法与
同一套规矩，所以它们住在一起：

- **先解析，再决定提不提交**；``diagnostics`` 非空一律提示、绝不提交，而且不按 code 名单
  挑着报（``invalid_date`` 与 ``duplicate_priority`` 一样重要——静默吞掉一个写错的日期，
  用户三天后才发现）。
- 改期没有日期就拒绝（那等于什么都没改）；新建没有标题就拒绝（任务总得有个名字，新建
  可以没有日期）。
- 引擎拒绝写入（本地没有这条任务的底稿）时如实说一句，不崩。
- 被拒绝时框留在原地、原文一个字不删——用户改一改再按 Enter 就行。

**v2 的走向**：这条「一行自然语言」的路由日期解析器支撑，而解析器整体作废——新建改成一
个标题字段（#39），改截止时间改成结构化选择器（#44）。这一片因此是**待拆的**：
:mod:`dida.tui.messages` 里那几句提示、以及这里两个 ``on_*_submitted`` 会跟着那两张工单走。
"""

from __future__ import annotations

from dida.sync.engine import UnknownTaskError
from dida.tui.messages import (
    NO_DATE_MESSAGE,
    NO_TITLE_MESSAGE,
    UNKNOWN_TASK_MESSAGE,
)
from dida.tui.panes import QuickAddInput, RescheduleInput, TaskPane


class FormActionsMixin:
    """``e`` / ``a`` 两个框的打开与提交（t14 / t15）。

    mixin 不是完整的 app：它用 ``self.engine`` / ``self.query_one`` / ``self.refresh_view``。
    """

    # ---------------------------------------------------------------- 改期（t14）

    def action_reschedule(self) -> None:
        """``e``：打开改期输入框（工单 #14）。

        光标下没有任务就什么都不做——空屏上按键不该报错（与 ``x``/``g`` 同一条口径）。
        瞄准的是**按下 e 那一刻**光标下那条任务：输入框拿到焦点之后 j/k 都成了文本，
        不再移动光标，所以这一次改期永远落在那一条上。
        """
        task_id = self.query_one(TaskPane).selected_task_id
        if task_id is None:
            return
        self.query_one(RescheduleInput).open(task_id)

    def on_reschedule_input_submitted(self, event: RescheduleInput.Submitted) -> None:
        """改期输入框按了 ``Enter``：先解析，再决定提不提交（工单 #14）。

        三条规矩：

        - **非空 ``diagnostics`` 一律提示、绝不提交**。不按 code 名单挑着报：``invalid_date``
          与 #23 的 ``duplicate_priority`` 一样重要——「13-45」被当成标题的一部分静默吞掉，
          用户三天后才发现任务没有日期，正是「如实呈现」要消灭的那类安静错误。
        - 一个日期都没写（``due is None``）也拒绝：改期不写日期等于什么都没改。新建可以没有
          日期，改期不行。
        - 引擎拒绝写入（本地已经没有这条任务的底稿，#25）时如实说一句「没有改成」，不崩、
          也不拿一个猜来的清单 id 硬发。

        被拒绝时输入框留在原地、原文一个字不删——用户改一改再按 Enter 就行。
        """
        box = self.query_one(RescheduleInput)
        parsed = self.engine.plan(event.text)
        if parsed.diagnostics:
            box.show_message("；".join(item.message for item in parsed.diagnostics))
            return
        if parsed.due is None:
            box.show_message(NO_DATE_MESSAGE)
            return
        try:
            self.engine.reschedule(event.task_id, due=parsed.due, all_day=parsed.all_day)
        except UnknownTaskError:
            box.show_message(UNKNOWN_TASK_MESSAGE)
            return
        box.close()
        self.query_one(TaskPane).focus()
        self.refresh_view()

    # ---------------------------------------------------------------- 新建（t15）

    def action_quick_add(self) -> None:
        """``a``：打开顶部新建输入框（工单 #15）。

        不瞄准任何一条任务，所以光标在哪都无所谓——空屏上按 ``a`` 照样能建。
        """
        self.query_one(QuickAddInput).open()

    def on_quick_add_input_submitted(self, event: QuickAddInput.Submitted) -> None:
        """新建输入框按了 ``Enter``：先解析，再决定建不建（工单 #15）。

        与改期同一条规矩：**非空 ``diagnostics`` 一律提示、绝不提交**，而且不按 code
        名单挑着报——``invalid_date`` 与 #23 的 ``duplicate_priority`` 一样重要。新建这条
        路上它更重：静默建出一个没有日期的任务，服务端还会顺手清掉重复规则，是双重错误，
        用户三天后在手机上才发现这一条根本不是自己写的样子。

        标题全是空的也不行（整行只写了「明天 !高」）：任务总得有个名字。

        被拒绝时输入框留在原地、原文一个字不删——用户改一改再按 Enter 就行。
        """
        box = self.query_one(QuickAddInput)
        parsed = self.engine.plan(event.text)
        if parsed.diagnostics:
            box.show_message("；".join(item.message for item in parsed.diagnostics))
            return
        if not parsed.title:
            box.show_message(NO_TITLE_MESSAGE)
            return
        # 解析出来的四样东西原样交给引擎：TUI 不重算日期、不重排优先级、不动标签。
        self.engine.create(
            parsed.title,
            due=parsed.due,
            all_day=parsed.all_day,
            priority=parsed.priority,
            tags=parsed.tags,
        )
        box.close()
        # 写完必须重画：新建是**多出一条**任务，引擎的视图已经变了，但栏位还端着旧的一份。
        # 少了这一行，任务确实建了、也进了队列，屏幕上却看不见——t14 的改期路径里有这一步
        # （见 on_reschedule_input_submitted），这里当初漏了，整套测试是在与兄弟工单合并后
        # 才把它照出来的。
        self.refresh_view()
        self.query_one(TaskPane).focus()
