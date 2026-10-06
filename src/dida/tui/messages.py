"""界面对用户说的每一句话，集中在这一个模块里。

**为什么单独一处**：这些话是产品文案，不是布局也不是同步。改一句措辞时不该动到别的任何
东西，而读的人（写工单的、写帮助文档的、翻译的）只需要看这一个文件。

它们全是**纯函数**：输入是错误对象或一个数，输出是那句话。所以它们可以不起界面就测
（``delete_prompt`` / ``quit_prompt`` 那两句确认文案也一样——#40 / #47 会连同浮层一起
扩展它们，措辞里已经核实过的那几条结论不要丢：不许暗示删除可以恢复、不许说待推送改动
「丢了」）。

两条一直生效的规矩（ADR-0002）：

- 「服务端权威可以覆盖本地，但**覆盖必须被用户看见**」——:func:`overwritten_message`
  就是那个「看见」。
- 失败不许静默：写没写成功、同步成没成、浏览器能不能开，都要出声。
"""

from __future__ import annotations

from dida.sync.engine import (
    AuthError,
    DidaError,
    UnclaimedListError,
    UnclaimedTaskError,
    UnknownListError,
    UnknownTaskError,
    UnknownViewError,
    ViewFormProblem,
)
from dida.sync.engine import PRIORITY_NAMES as _ENGINE_PRIORITY_NAMES

UNKNOWN_TASK_MESSAGE = "没有改成：这条任务已经不在本地缓存里了，刷新之后再试一次"
"""引擎拒绝写入（本地没有这条任务的底稿，工单 #25）时的话：如实说没改成。"""

NO_TITLE_MESSAGE = "没写标题：新建至少得有个标题"
"""新建输入框里一个字的标题都没写时的话（#39 接管新建）。任务得有名字。"""

UNKNOWN_DELETE_MESSAGE = "没有删：这条任务已经不在本地缓存里了"
"""删除没有底稿时的话（工单 #16）。

与改期那句分开写：这里**不能**说「刷新之后再试一次」——刷新会把它拉回来，看着像删掉了
其实没有；而删除这条路径上「本来就没这条」与「删掉了」必须一眼分得清。
"""

EMPTY_INDEX_MESSAGE = "（还没有清单）"
"""清单列表页一行都没有时的话（缓存是空的、第一次运行还没刷新）。"""

EMPTY_TASKS_MESSAGE = "（这个清单里还没有任务）"
"""空清单那一句明确的空态文案（用户故事 53）——不是一片什么都没有的黑。"""

EMPTY_DETAIL_MESSAGE = "（这条任务已经不在本地缓存里了）"
"""详细页指向的任务刷新之后没了（远端删掉了）时的话。"""

EMPTY_FIELD_TEXT = "（空）"
"""详细页上一个**可编辑**字段没有内容时画的那一句（工单 #43）。

描述与备注可以是空的，但空字段照样要留在字段列表里、光标照样停得上去——否则「给一条没有
描述的任务加描述」这件事在界面上无路可走。读的那一段（子任务/提醒/重复）不一样：那些没有
内容时整行不画，它们只是告知，不是入口。
"""

PRIORITY_NAMES: dict[int, str] = _ENGINE_PRIORITY_NAMES
"""优先级四个档位的**用户语言**（GLOSSARY：无 / 低 / 中 / 高）。

**这一张表不是这个模块的**：它是引擎公开面上的一员，表本体在
:data:`dida.sync.view.PRIORITY_NAMES`（工单 #58 的 T3 之前，这里与
``sync/views.py`` 各写了一份同样映射，而这里的名字被注释称作「唯一一张表」）。
界面只许 import ``dida.sync.engine``，所以它经引擎的公开面到这里；这一行是**转出**
（同一个对象），不是第二张表——改档位的文案请改那一处。

与 API 的线上编码 ``0/1/3/5`` 是两套东西：那一套在 ``dida.sync.view``（``PRIORITY_CYCLE``）
里。表外的取值读作「无」，与 ``priority_mark`` 同一条口径。#45 的挑选器按**这里的插入顺序**
画四档（无 → 低 → 中 → 高），所以次序也是承重的。
"""

FIELD_SAVE_FAILED_PREFIX = "保存失败："
"""保存失败那一行的开头：**后面必须跟上具体原因**（用户故事 81）。

一个「保存失败」了事的话用户不知道该重试、该刷新、还是该去查网络——四种原因（本地没有
底稿、凭据失效、断网、服务端拒绝）要做的事完全不同。
"""

NO_TITLE_EDIT_MESSAGE = "标题不能为空：这一下没有改"
"""把标题清空之后按 ``esc`` 时的话（用户故事 66：空标题不被接受）。

说清「没有改」而不是「保存失败」：这不是网络或服务端的问题，是这一栏本来就不能是空的，
而字段里的旧标题原样还在（没有「取消」，所以也不能把用户留在一个退不出去的编辑态里）。
"""

READ_ONLY_NOTE = "以上只读：子任务、提醒、重复规则要回官方客户端改"
"""只读那三段下面那一句（用户故事 76 / 77 / 78：**明确告知**在客户端里改不了）。

这三样都不是「暂时没做」：子任务的勾选要连同整条任务一起写回、重复规则的编辑是一整套
UI，都不在 v2 的范围里。用户在这一页能做的就是读到它们，所以这里直说。"""

DUE_DATE_FORMAT_HINT = "YYYY-MM-DD"
"""日期那一格要写成什么样（工单 #44）。

**这不是一句自然语言的提示，而是格式本身**：v2 不做自然语言日期输入（#34 删掉了 v1 的
``date_parser``），用户敲进去的就是一个结构化的日子。写成 ``2026-03-15`` 这样，月与日
各两位——``2026-3-15`` 也不认（那会同时认好几种写法，写错的时候才显形）。
"""

DUE_TIME_FORMAT_HINT = "HH:MM"
"""时刻那一格要写成什么样（留空 = 只有日期，也就是全天）。"""

ALL_DAY_LABEL = "全天"
"""「全天」那个开关的名字（工单 #44 验收标准 2）。

它是**一个开关**，不是「时刻留空」这一条隐含规则：用户要能看见当前在哪一档，也要能按一下
就换过去（按键是 ``x``，画在那一行上）。
"""

DUE_INVALID_MESSAGE = "没改成：{reason}"
"""日期或时刻那一格不认时的话（工单 #44 验收标准 6：非法日期在发出前被本地拦下）。

带上**具体**那一句（哪一格、为什么不认），与 ``FIELD_SAVE_FAILED_PREFIX`` 那条口径一样：
用户要能一眼看出是自己敲错了哪一格，而不是「保存失败」四个字。措辞是「没改成」而不是
「保存失败」——这一次根本没有请求出去（本地就拦下了），说成保存失败会让人去查网络。
"""


def all_day_toggle_text(all_day: bool) -> str:
    """「全天」开关画成什么（``[x]`` / ``[ ]``，方括号是 ASCII，宽度不含糊）。

    ``x`` 与空格都是 1 格的字形：这一行要能在一屏里与日期、时刻排在一起，歧义宽度的字形
    会把后面那几格推歪（ADR-0007 的「字形宽度是承重项」）。
    """
    return f"{ALL_DAY_LABEL} [{'x' if all_day else ' '}]"


def due_invalid_message(reason: object) -> str:
    """日期/时刻不认时那一行：把 :class:`ValueError` 里那句具体的话原样带出来。"""
    return DUE_INVALID_MESSAGE.format(reason=reason)


def pending_message(pending: int) -> str:
    """详细页底部那一行：还有几处改动没推上去（用户故事 65）。

    与状态栏那个「待推送 N」是同一个数（队列只有一条），措辞按工单写全角括号：这一行在
    编辑现场，说的是「你刚才那一下到底出去没有」。
    """
    return f"待推送（{pending}）"


def saved_message() -> str:
    """详细页底部那一行：队列是空的，改完就出去了（用户故事 65）。"""
    return "已保存"


def field_save_failed_message(reason: object) -> str:
    """保存失败那一行：把引擎/服务端说的那句话原样带出来（用户故事 81）。

    ``reason`` 是**具体**的那一句（``UnknownTaskError`` 的「本地没有这条任务的底稿」、断网
    时的「连不上」、服务端拒绝时的原话），不是一个笼统的「保存失败」。
    """
    return f"{FIELD_SAVE_FAILED_PREFIX}{reason}"


NO_CHOICES_TEXT = "（没有可选项）"
"""多选那一格一个可挑的都没有时画的那一句（工单 #45）。

它不是「空状态」，而是**事实**：这个客户端不做新建标签（见
:data:`TAGS_PICKER_HINT`），所以一个标签都没有时这一格确实是空的——用户要看到的是
「一个都没有」加上「去哪儿能建」，而不是一片什么都没有的黑。
"""

TAGS_PICKER_HINT = (
    "空格 打上或取消 / 上下方向键 换一个 / Enter 确认 / Esc 取消 / Ctrl+C 退出\n"
    "这个客户端不做新建标签：要打一个还没有的标签，请回官方客户端建。\n"
    "删除标签的接口官方文档里没有，所以这里也删不掉。"
)
"""挑标签那一格的底部提示（工单 #45，验收标准 6）——**这一票最要紧的一行字**。

措辞里那两件事，一件是范围决定、一件是接口事实，**不许混**：

- **新建标签**：``POST /open/v1/tag`` 是文档里**有**的端点（``openapi-dida365.md:1612–1654``，
  ``name`` 与 ``label`` 都必填、小写、且 ``label`` 必须等于小写后的 ``name``，``:1620–1621``）。
  所以「不能新建」是**这个客户端**的范围决定（spec 的 Out of Scope :306），不是接口做不到。
  写成「API 没有这个能力」就是对用户说假话——那句话会被一个真去查文档的人当场戳穿。
- **删除标签**：``:1576–1654`` 整节只有 ``GET`` 与 ``POST``，没有 update、没有 delete。
  这一半是接口真的没有（用户故事 113 要的就是这句如实告知）。

``空格``/``上下方向键`` 而不是 ``⎵``/``↑↓``：后两个是东亚**歧义**宽度（rich 量 1 格、CJK
字体下终端可能画 2 格），而这块浮层是 ``width: auto``——宽度正由最宽那行算出来（#48 在
帮助正文上立的同一条规矩）。
"""


def hidden_choices_message(hidden: int) -> str:
    """多选那一格没画出来的那几个（工单 #45）：说个数，**不静默地藏**。

    选项多于 :data:`~dida.tui.theme.PICKER_VISIBLE_ROWS` 时只画光标周围那一段；被藏起来的
    那几个照样走得过去（光标会把它带进窗口），但用户得知道「下面还有」。
    """
    return f"还有 {hidden} 个未显示"


def tags_load_failed_message(error: object) -> str:
    """标签列表没拉到（工单 #45）时的那一句：挑标签那一格照旧开出来，这一句写在它的提示里。

    与 :func:`field_save_failed_message` 同一条口径：后面跟的是**具体**那一句（断网、凭据
    失效、服务端拒绝），不是一个笼统的「失败」。

    **为什么写在浮层里而不是状态栏上**：浮层是模态的，用户的眼睛在它身上——状态栏那一行
    在浮层底下（实测：模态开着时 ``screen_text`` 只画得出浮层自己）。而且这一句说的正是
    「你现在看到的这一份是什么」，写在被说的那份名单旁边才对得上。

    后半句是**必须**的：不说清「这一份是本地的」，用户会以为自己一个标签都没有。
    """
    return f"标签列表没拉到：{error}（下面这一份是本地已经见过的）"


def tags_picker_hint(notice: str = "") -> str:
    """挑标签那一格的底部提示；``notice`` 是这一次没拉到名单时那一句，放在最前面。

    默认那一份是 :data:`TAGS_PICKER_HINT`（键位 + 那两条如实告知）。
    """
    return f"{notice}\n{TAGS_PICKER_HINT}" if notice else TAGS_PICKER_HINT


def priority_name(priority: int) -> str:
    """优先级那一格的读法（``0/1/3/5`` → 无 / 低 / 中 / 高）。"""
    return PRIORITY_NAMES.get(priority, PRIORITY_NAMES[0])

EMPTY_LIST_NAME_MESSAGE = "没写名字：清单得有个名字"
"""建清单时名字一格是空的（#42）。与任务的 ``NO_TITLE_MESSAGE`` 同一条口径。"""

UNKNOWN_LIST_MESSAGE = "没有改成：这个清单已经不在本地缓存里了，刷新之后再试一次"
"""引擎拒绝清单写入（本地没有这一行的原文，#42）时的话：如实说没改成。"""

INBOX_LIST_MESSAGE = "收集箱不在这里改：它是客户端补出来的默认落点，改了下次刷新就变回去"
"""``e`` / ``d`` 落在收集箱那一行时的话（#42）。

措辞不许说成「接口不支持」——服务端拿字面量 ``inbox`` 当 projectId 是收的。做不到的是
**这个客户端**：收集箱那一行是本机补出来的（服务端的清单索引里没有它），它的名字与颜色
每次刷新都由服务端那一份说了算，所以在这里改它只会看起来成功、下一次刷新就变回去。
"""

VIEW_ROW_MESSAGE = "这是视图，不是清单：视图走它自己那条建 / 改 / 删"
"""视图行落到**清单**那条路上时的话（#42 的兜底；视图那三条从 #36 起归 :mod:`dida.sync.views`）。

它现在是一条走不到的路：清单列表页先看行的类型再分派（``app._refusal_for``），视图行不会
再进清单那三个口子。留着是因为 :func:`dida.tui.pages.index.list_write_refusal` 仍然只回答
「能不能当清单改」——它要是对视图行说「能」，那才是真的坑。
"""

VIEW_LOCAL_ONLY = "自定义视图只存在这台机器上：手机端、网页版没有它，换台机器就没了"
"""浮层里那句实话的**陈述**（验收标准 4，ADR-0005）。

滴答清单的 Open API 里没有「保存一组过滤条件」这个接口——只有清单与任务，而
``/task/filter`` 过滤的是开始时间、硬顶 200 条、没有分页。所以自定义视图**只存在本机**：
手机端、网页版上没有它，换台机器也没了。这是能力上限，不是实现疏漏，所以照实说，
也不许暗示「以后会同步上去」。

它单独成一个常量、理由另起一个（:data:`VIEW_LOCAL_ONLY_WHY`）：这一句要在一行里排得下
（浮层宽 74 格，CJK 一格算两格），拼上理由就会折行——而折行处夹着边框，屏幕上就再也
读不出一整句了。
"""

VIEW_LOCAL_ONLY_WHY = "（API 没有保存一组过滤条件的接口）"
"""上面那句话的**理由**：只说「只存在本机」用户会以为是客户端没做，说清是接口里没有这一条，
他才知道这不是能补上的功能。
"""

VIEW_FORM_SYNTAX = "多个值用空格或逗号分开；留空 = 不限"
"""视图表单底部那行提示的第一句：清单范围 / 优先级 / 标签都能写好几个。

这三格是输入框而不是多选框（#45 的挑选型字段是另一张票），所以「怎么写多个」得说清楚——
说不清楚用户只会填一个，而工单要的是多选。
"""

NEW_KIND_HINT = "清单装任务；视图只是一组过滤条件，只存在这台机器上"
"""``n`` 那句「清单还是视图」下面的一行说明（#36 的验收标准 1）。

问这一句是有理由的：两种东西后面完全是两回事（一个是服务端的容器，一个是本地的过滤
条件），顺手说清哪一种是哪一种，用户才不会以为自己建了个「会同步的清单」。
"""

BUILTIN_VIEW_MESSAGE = "内置视图改不了也删不掉：它的条件是写死的（今天 / 最近七天 / 所有）"
"""``e`` / ``d`` 落在**内置**视图行上时的话（#36）。

内置视图不是本地库里的行，它是三个写死的定义（:func:`dida.sync.views.builtin_view_definitions`）
——没有「改它」这回事，删掉它也没有落点。所以这里既不开表单也不问那一句：问一句就等于
「有可能删」，而它不会。
"""

EMPTY_VIEW_NAME_MESSAGE = "没写名字：视图得有个名字"
"""建视图时名字那一格是空的（#36）。与清单 / 任务那两句同一条口径：空态不许静默。"""


def delete_view_prompt(name: str) -> str:
    """**删视图**的确认文案（#36）。

    与 :func:`delete_list_prompt` 的分别正是这一屏要说的那句话：视图只是一组过滤条件，
    删掉它**不会动任何任务**——那些任务本来就在各自的清单里。所以这里没有「找不回来」那种
    警告（那一句留给真会丢东西的删除），只说清删的是什么、以及不牵连什么。
    """
    return (
        f"删除视图「{name}」？\n"
        "视图只是一组过滤条件，删掉它不会动任何任务。\n\n"
        "y 确认删除 · n / Esc 取消"
    )


def view_write_failed_message(error: DidaError) -> str:
    """视图的建 / 改 / 删当场失败时的话（#36）。

    与 :func:`list_write_failed_message` 同一条口径：引擎当场拒绝的（本地已经没有那一行）
    与别的失败分开说。视图不推服务端，所以这里没有网络那几种失败。
    """
    if isinstance(error, UnknownViewError):
        return UNKNOWN_VIEW_MESSAGE
    return f"视图没改成：{error}"


UNKNOWN_VIEW_MESSAGE = "没有改成：这个视图已经不在本地库里了"
"""本地没有那一行视图时的话（#36）。

它不说「刷新之后再试一次」：视图不是从服务端拉回来的，刷新不会把它变回来——再说那句
就是给一条走不通的路指路。
"""


def view_form_problem(problem: ViewFormProblem) -> str:
    """那张表单填不下去时状态栏里的话（#36）。

    逐条说清**哪一处、哪几个词**：认不出的清单名 / 优先级词一律拒绝保存，因为沉默地存下
    一个筛不出东西的视图，用户要过一阵子才发现，而且发现时不知道是自己填错了还是客户端
    没做。句子在这里拼（产品文案住这一个模块），判据在 :func:`dida.sync.views.parse_view_form`。
    """
    sentences: list[str] = []
    if problem.missing_name:
        sentences.append(EMPTY_VIEW_NAME_MESSAGE)
    if problem.unknown_lists:
        sentences.append(
            f"清单范围里认不出这些清单：{'、'.join(problem.unknown_lists)}"
            "（写清单的名字或 id，空格分隔）"
        )
    if problem.unknown_priorities:
        sentences.append(
            f"优先级只认 高 / 中 / 低 / 无（也可以写 5 / 3 / 1 / 0）："
            f"认不出 {'、'.join(problem.unknown_priorities)}"
        )
    if problem.never_matches:
        sentences.append(
            "这个条件永远筛不出任务：完成状态是「未完成」时没有完成时间可筛"
        )
    return "；".join(sentences) or "这张表单还没填完"


def completed_message(title: str) -> str:
    """按下 ``space`` 把一条任务标成完成时的回声（工单 #38，用户故事 41、43）。

    说的是**本地已经生效**（乐观写，ADR-0002），不是「服务端已经收到了」：推不上去时那条
    改动还在队列里，状态栏那个「待推送 N」一直说着这件事——两句话各说各的那一半。
    """
    return f"已完成「{title}」"


def uncompleted_message(title: str) -> str:
    """按下 ``space`` 把一条已完成的任务改回未完成时的回声（工单 #38，用户故事 42、43）。

    与 :func:`completed_message` 同一条口径。措辞里认下的是「本地不再是已完成」：
    完成时间戳可能还在（实测取消完成不会清掉它），而「还算不算已完成」只看 ``status``。
    """
    return f"已取消完成「{title}」"


def toggle_complete_failed_message(error: DidaError) -> str:
    """完成 / 取消完成**当场**失败时状态栏里的话（工单 #38）。

    与别的写路径同一条口径：引擎当场拒绝的（本地已经没有这条任务的底稿）与别的失败分开说，
    因为用户该做的事不一样——前者刷新一下再看，后者是网络或权限。按下去什么都不说，
    用户会以为它成了（ADR-0002 要消灭的正是这个）。
    """
    if isinstance(error, UnknownTaskError):
        return UNKNOWN_TASK_MESSAGE
    return f"没改成：{error}"


def readonly_list_message(row: object) -> str:
    """``e`` / ``d`` 落在没有写权限的清单上时的话（用户故事 24）。

    与 :func:`blocked_list_message` 分开写：那一句说的是「进不去」，这一句说的是「改不动」
    ——两件事，用户要看到的也是两种原因。
    """
    return f"「{getattr(row, 'name', '')}」改不动：这个清单没有写权限"


def list_write_failed_message(error: DidaError) -> str:
    """清单的建 / 改 / 删当场失败时的话（#42）。

    与任务的写路径同一条口径：引擎当场拒绝的（本地已经没有那行清单了）与别的失败分开说，
    因为用户该做的事不一样——前者刷新一下再看，后者是网络或权限。
    """
    if isinstance(error, UnknownListError):
        return UNKNOWN_LIST_MESSAGE
    return f"清单没改成：{error}"


def delete_list_prompt(name: str) -> str:
    """**删清单**的确认文案（#42）。

    措辞是这一屏最要紧的一行字，两条都核实过（api-shapes §B11）：

    - 删掉一个清单时它里面的任务在服务端会发生什么，**文档一个字都没写**（:1278–1302
      通篇只有路径、参数、响应表与一个请求示例），我们也没有实测过。所以只能如实说
      「不知道」，绝不许升级成「一起删掉」或「会移到收集箱」——那两句都是编的。
    - 没有回收站、没有撤销删除的接口（§A6：整份文档里搜不到 undelete / restore /
      已删除列表），所以**不许承诺任何恢复手段**。
    """
    return (
        f"删除清单「{name}」？\n"
        "它里面的任务会怎样，官方文档没写，我们也没有实测过。\n"
        "滴答清单没有回收站，也没有撤销删除的接口：删了就找不回来。\n\n"
        "y 确认删除 · n / Esc 取消"
    )


def blocked_list_message(row: object) -> str:
    """``enter`` 一个进不去的清单时状态栏里的话（用户故事 23 / 24）。

    两种进不去的原因分开说：``kind`` 是 NOTE 的清单装不了任务，``permission`` 不是 write
    的改不动。行上那个记号只说「不可进入」，进不去的时候得说清是哪一种——否则用户会去
    别处找原因。
    """
    name = getattr(row, "name", "")
    if getattr(row, "project_kind", None) == "NOTE":
        return f"「{name}」进不去：备注清单装不了任务"
    return f"「{name}」进不去：这个清单没有写权限"


def overwritten_message(count: int) -> str:
    """服务端盖掉本地改动时的话（工单 #21，用户故事 59）。

    ADR-0002 的规矩：服务端权威可以覆盖本地，但**覆盖必须被用户看见**。这一句就是那个
    「看见」——不说的话，用户刚做过的改动会在这一屏上悄悄变回服务端那一份。
    被待推送改动豁免挡回去的那些不算：用户的改动还在，没有任何东西被盖掉。
    """
    return f"{count} 处本地改动被覆盖"


def refresh_failed_message(error: DidaError) -> str:
    """同步失败时状态栏里的话：凭据失效与其它失败分开说（工单 #21，用户故事 6）。

    「凭据失效」必须直接引导重新粘贴 token：说成笼统的网络失败，用户会去查网络，
    而问题在他那把过期或被吊销的 token 上（t03 的 ``Credentials`` 提供了那条重新粘贴的路）。
    """
    if isinstance(error, AuthError):
        return f"凭据失效，请重新粘贴 token：{error}"
    return f"同步失败：{error}"


def create_failed_message(error: DidaError) -> str:
    """新建当场失败时的话（#39）。

    被拒绝的三种情形分开说，因为用户该做的事不一样：``UnclaimedTaskError`` 是「**你自己**刚
    建的那条还没同步完」、``UnclaimedListError`` 是「你要落进去的那个**清单**还没同步完」
    （两种都是等一下再按一次就对——下一次刷新会把真 id 带回来，判据只有
    ``dida.sync.writes.is_addressable_task`` 一处）；别的失败是网络或服务端说不行。
    这一句不许暗示「已经建好了」：那会让用户以为东西在服务端上。
    """
    if isinstance(error, (UnclaimedListError, UnclaimedTaskError)):
        return f"没建成：{error}"
    return f"新建失败：{error}"


def completed_failed_message(error: DidaError) -> str:
    """已完成流没拉到，但全量刷新与推送已经落地时的话（工单 #21）。

    这里**不能**说成整次同步都失败了：未完成任务那一份是新的，只有「已完成 N 项」还是旧的。
    """
    return f"已完成流没拉到：{error}"


def quit_prompt(pending: int) -> str:
    """待推送改动还在时退出的话（工单 #21，用户故事 58）。

    必须说出**有几处**：只说「还有改动没推」用户不知道是刚按的那一下，还是攒了一整天的十几笔。
    也必须说清退出之后它们去哪儿——**不能**说「就丢了」。

    待推送改动落在本地库的 ``pending_changes`` 表里（t08），进程结束不等于它们没了：下次
    启动时周期泵会照退避到点的时间接着推（t21），实测过一次——断网写一笔、退出、重开同一个
    库，``push_pending()`` 把它推了出去。这里要说的因此是「留在本地、下次接着补推」；
    吓唬用户说丢了，是拿一句不真的话换他一次犹豫。
    """
    return (
        f"还有 {pending} 处改动没推上去。\n"
        "退出不会丢：它们留在本地，下次打开 dida 接着补推。\n\n"
        "y 仍然退出 · n / Esc 留下"
    )


NO_BROWSER_PREFIX = "打不开浏览器：把这条链接自己粘到浏览器里 "
"""没有浏览器可用时那句话的开头（工单 #19）。

与 :func:`no_browser_message` 分开写：测试要断的是「出声了没有」，而那句话后面还挂着
一条随时会变的 URL。措辞里**不假装**有桌面客户端可以切——ADR-0002 已核实官方客户端
不接受任务深链，所以这里只有浏览器这一条路。
"""


def no_browser_message(url: str) -> str:
    """没有浏览器可用时状态栏里的话：说清楚打不开，并把 URL 原样给人抄（工单 #19）。

    这里**绝不能**静默：完成在服务端不可逆（ADR-0002），``o`` 是它的补偿，按下去什么都
    没发生比吵一句坏得多。也不说「重试一下就好」——``webbrowser`` 找不到浏览器时重试
    还是找不到，用户该做的是自己把这条链接粘走。
    """
    return f"{NO_BROWSER_PREFIX}{url}"


def delete_prompt(title: str) -> str:
    """删除确认浮层上的那句话（工单 #16 / #40）。

    措辞是这一屏最要紧的一行字：**不许暗示还能找回来**。滴答清单 Open API 里没有
    undelete、没有回收站、没有「已删除」列表，所以这里只说删了就没有了，绝不说「可恢复」
    「稍后可找回」「已移入回收站」——那种话会让用户在按 ``y`` 的时候以为还有退路，
    而实际上没有。

    最后一行那个分隔符是 **ASCII** 的 ``，`` 之外一个都不放：这块浮层是 ``width: auto``，
    宽度由 rich 量出来的最宽那行决定，而 ``·``（U+00B7）是东亚**歧义**宽度——rich 量 1 格、
    CJK 字体下终端画 2 格，多出来的那格会把右边框挤掉（#48 在帮助正文上立的是同一条规矩，
    ``tests/test_task_delete_defer.py`` 在这句文案上守它）。
    """
    return f"删除「{title}」？\n删掉就找不回来了，滴答清单没有回收站。\n\ny 确认删除，n / Esc 取消"


def delete_failed_message(error: DidaError) -> str:
    """删除当场失败时的话（工单 #40）。

    与 :func:`list_write_failed_message` 同一条口径：引擎当场拒绝的（本地已经没有这条任务的
    底稿）与别的失败分开说——前者刷新一下再看，后者是网络或权限。删除这一支尤其不能含糊：
    这句话说的是「**没**删成」，而不是「删了」。
    """
    if isinstance(error, UnknownTaskError):
        return UNKNOWN_DELETE_MESSAGE
    return f"没有删：{error}"
