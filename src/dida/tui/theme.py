"""视觉常量**唯一的出处**：颜色、字形、间距、动效时长（工单 #51 / ADR-0007）。

没有这一处会怎样：实测一次改版要碰**五处**——app 的 CSS、浮层的 CSS、各页面约 20 处
``Text(style=…)``、Textual 的主题默认值、以及 ``keys.py`` 里的排版算术。五处各自漂移的
结果就是「跟随终端主题」这件事只在某一个角落成立。

## 颜色有两个词汇，它们不是同一批词

============  ==========================================  ==========================
语境            写 ``cyan`` 是什么                           写 ``ansi_cyan`` 是什么
============  ==========================================  ==========================
Rich 样式串     ANSI 6 —— 终端主题说了算 ✓                   不认识
（span）        （``Text().append(x, style=…)``）
Textual CSS     ``#00FFFF`` —— CSS 具名色，真彩色 ✗          ANSI 6 ✓
============  ==========================================  ==========================

所以两种拼法在这个模块里**分开命名**（``ACCENT`` 对 ``CSS_ACCENT``），而且只在这个模块里
出现。``Text("x", style="cyan")`` 是最容易踩的一个：它把样式放进 **base style**，Textual
拿自己的 CSS 颜色解析器去读，于是那个「看起来写的是 ANSI 名」的调用点静默发出了真彩色。
**颜色要进 span**——用 :func:`styled`，或者 ``Text().append(..., style=…)``。

## 层级不靠亮度

用户的 16 色槽位里 8–15 与 1–6 同值（环境审计 §1a），加亮买不到任何颜色对比。层级只能来自
**字重、``dim``、反色、下划线、留白**。所以下面那些角色里没有一个「更亮的同色」。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from rich.cells import cell_len, set_cell_size
from rich.text import Text

__all__ = [
    "ACCENT",
    "ANIMATIONS_MODES",
    "BAR",
    "BLANK_MARK",
    "BLOCKED_GLYPH",
    "CHECK_OFF",
    "CHECK_ON",
    "BUILTIN_MARK",
    "CURSOR_BAR_MS",
    "CURSOR_MARK",
    "CSS_ACCENT",
    "CSS_EDGE",
    "CSS_PAGE",
    "CSS_QUIET",
    "CSS_ROLES",
    "CSS_SURFACE",
    "CUSTOM_MARK",
    "DECORATION_GLYPHS",
    "DONE",
    "DONE_MARK",
    "ELLIPSIS",
    "HEADING",
    "HEADING_RULE",
    "INBOX_MARK",
    "LIST_MARK",
    "MUTED",
    "NO_VALUE",
    "OVERDUE",
    "PAN_MS",
    "PICKER_VISIBLE_ROWS",
    "PENDING",
    "PLAIN",
    "REMINDER_MARK",
    "REPEAT_MARK",
    "RICH_ROLES",
    "RULE",
    "SELECTED",
    "SPINNER_DELAY_MS",
    "SPINNER_FRAMES",
    "STRUCTURAL_GLYPHS",
    "SUBTASK_DONE_MARK",
    "SUBTASK_TODO_MARK",
    "SURFACE",
    "WORDMARK_ICON",
    "animations_enabled",
    "animations_setting",
    "app_css",
    "clip",
    "form_css",
    "overlay_css",
    "pad",
    "rpad",
    "styled",
]

# ---------------------------------------------------------------------------
# 颜色角色：Rich 的词汇（16 色名），走 span
# ---------------------------------------------------------------------------

ACCENT = "cyan"
"""强调色 = 槽 6（他那一格是 ``#94E2D5`` 软薄荷）。只给**词标与光标**用，别处不用。"""

SELECTED = "cyan bold"
"""光标那一行的正文：颜色 + 字重。终端画不出半格，所以「选中」只能靠这两个信号。"""

OVERDUE = "red"
"""逾期 = 槽 1。**只是一个语义 token**：谁逾期由读模型判（``TaskItem.overdue``，#35 接上
视图求值），颜色由这里给——TUI 不许自己拿截止时间去比（架构规则：日期判断全在引擎里）。"""

SURFACE = "black"
"""抬起来的面 = 槽 0（他那一格是 ``#45475A`` 石板）。它比页面底色**亮**，所以只用于浮层。"""

PLAIN = "default"
"""不指定颜色：让终端自己的前景色说话。"""

MUTED = "dim"
"""安静的那一档 = 真的 SGR 2（原生 ANSI 模式下 ``dim`` 不再被混成灰色实色）。"""

DONE = "dim strike"
"""已完成的一条：暗 + 删除线。**还在列表里**，所以它得看起来「做过但不重要」。"""

PENDING = "bold reverse"
"""待推送非零时那一段：反色 + 字重，不换颜色。

反色也是「不靠亮度做强调」的落点：换一个更亮的颜色买不到对比（槽 8–15 与 1–6 同值），
而反色一定看得见。
"""

HEADING = "bold"
"""分区标题 / 页面标题：字重。"""

BAR = "on cyan"
"""会追赶的那条装饰光标条：强调色实心两格。"""

RICH_ROLES: Final = (
    "ACCENT",
    "SELECTED",
    "OVERDUE",
    "SURFACE",
    "PLAIN",
    "MUTED",
    "DONE",
    "PENDING",
    "HEADING",
    "BAR",
)
"""上面这些名字的清单：守卫照着它逐个查「是不是 ANSI 槽位」。"""

# ---------------------------------------------------------------------------
# 颜色角色：Textual 的词汇（CSS），只许 ansi_* 拼法
# ---------------------------------------------------------------------------

CSS_PAGE = "ansi_default"
"""页面底色 = ``\\x1b[49m``，即**用户自己的背景**。app 不覆盖终端背景。

平移过渡需要页面有不透明背景（Textual 每帧重绘，滑走的那块会漏出后面的东西），
而 ``ansi_default`` 既填满区域又画的正是他自己的底色。
"""

CSS_ACCENT = "ansi_cyan"
"""词标与光标在 CSS 里的那一半（同一个槽 6）。"""

CSS_SURFACE = "ansi_black"
"""浮层面：同一块青灰在这里是资产——比页面亮一层，读作浮起。"""

CSS_EDGE = "ansi_bright_black"
"""浮层那条细边框 = 槽 8。它比槽 0 亮一档而比前景暗，正好是「安静的一条边」。"""

CSS_QUIET = "ansi_black"
"""滚动条：不引入新色相，只借槽 0 这一块石板。"""

CSS_ROLES: Final = ("CSS_PAGE", "CSS_ACCENT", "CSS_SURFACE", "CSS_EDGE", "CSS_QUIET")
"""CSS 那一半的清单：守卫照着它查「是不是 ansi_* 拼法」。"""

# ---------------------------------------------------------------------------
# 字形
# ---------------------------------------------------------------------------

CURSOR_MARK = "❯"
"""光标那一行的行首标记（U+276F，EAW=Neutral，1 格）。"""

BLANK_MARK = " "
"""非光标行的行首占位，保证列不错位。"""

INBOX_MARK = "▪"
"""收集箱：它是真实清单，但置顶显示，给它一个一眼能认出来的记号。"""

BUILTIN_MARK = "▸"
"""内置视图（今天 / 最近七天 / 所有）。"""

CUSTOM_MARK = "✦"
"""用户自建的视图。"""

LIST_MARK = "⋮"
"""真实清单（API 叫 project）。

原来是 ``☰``：rich 量它 **2 格**，而它的东亚宽度是中性——每一行都比终端实际画出来的宽
一格。今天没有对齐列所以看不出来，一旦有列（条数、截止时间）就整列歪。
"""

DONE_MARK = "☑"
"""已完成的行。"""

REPEAT_MARK = "↻"
"""重复任务（服务端给了 ``repeatFlag``）。

U+21BB，东亚宽度**中性**、rich 量 1 格：它进的是任务行里那一列注解，宽度含糊就会歪。
"""

REMINDER_MARK = "⚑"
"""有提醒的任务（服务端的 ``reminders`` 非空）。

U+2691，东亚宽度中性、rich 量 1 格。提醒只读（v2 不改它），所以这里只需要「有没有」。
"""

SUBTASK_DONE_MARK = "☑"
SUBTASK_TODO_MARK = "☐"
"""子任务的两种状态标记（只读显示：v2 不在客户端里勾子任务）。"""

CHECK_ON = SUBTASK_DONE_MARK
CHECK_OFF = SUBTASK_TODO_MARK
"""多选那一列的两个字形（工单 #45）：与子任务那对**是同一对**（``☑`` / ``☐``），只是名字按用途取。

两个名字指向同一个字符串，不是两份常量——宽度守卫（:data:`STRUCTURAL_GLYPHS`）只认那一份。
取第二个名字是因为「标签挑中了没有」与「子任务做完了没有」是两件事：读标签那一列的人
不该在代码里看到 ``SUBTASK_``。
"""

PICKER_VISIBLE_ROWS = 8
"""多选那一格最多画几行（工单 #45）。

再多就只画光标周围那一段、末尾补一句「还有 N 个未显示」
（:func:`~dida.tui.messages.hidden_choices_message`）。不设上限的话，标签一多浮层就顶到
``max-height: 80%`` 被裁掉——被裁的是**末尾那几行**，连底部那行提示一起，而且不报错。
8 行在任何终端上都放得下（30 列的窄屏也一样）。
"""

BLOCKED_GLYPH = "⚠"
"""进不去的清单（``kind`` 是 NOTE 或没有写权限）的记号。"""

RULE = "_"
"""通栏细线（U+005F，ASCII）。

**为什么不是 ``─``**：U+2500 是东亚**歧义**宽度——rich 量它 1 格，CJK 字体下终端可能画 2 格。
Unicode 里根本没有宽度不含糊的制表符（原型 README 的结论），而通栏线一旦每格画 2 格就会
把整行挤出屏幕（多折一行）。ASCII 的 ``_`` 在每种 locale 里都是 1 格，而且等宽字体下
上下相连，看起来是一条连续的线。
"""

HEADING_RULE = "-"
"""行内抬头两侧那一段横线（U+002D，ASCII）。

**为什么不是 ``─``**：与 :data:`RULE` 同一条理由——U+2500 是东亚**歧义**宽度，rich 量它 1 格，
CJK 字体下终端画 2 格。抬头是**行内**的、不是列，所以这一格差在列里看不出来；它坏的是另一件事：
``── X ──`` 里四个 ``─`` 让这一行比 rich 的量法宽 4 格，而 ``?`` 那块浮层是 ``width: auto``
——宽度正由 rich 量出来的最宽那行决定。**抬头一旦就是最宽那行**，它就比框的内容区宽 4 格、
从右边框上溢出去（#48 复现：body 只放一个旧抬头时，框内 16 格、抬头画 20 格，多出来的 2 格压过
右边框）。今天浮层里还有更宽的 CJK 说明行，所以这道差被余量盖住了；它是一笔**随时会显形的假账**，
而 ASCII 的 ``-`` 在任何 locale 里都是 1 格，两边永远一致。
"""

ELLIPSIS = "…"
"""裁断记号（U+2026，EAW=Ambiguous）。

它是**行尾最后一个字形**，后面没有别的东西要对齐，所以歧义宽度在这里坏不了列——这类位置
（词标、状态栏、行尾记号）是宽度风险唯一可以接受的落点。
"""

NO_VALUE = "无"
"""「这一格没有值」的读法（U+65E0，CJK，两边都量 2 格）。

引擎那边的 ``sync.view.NO_DUE_TEXT`` 是 ``—``（U+2014，东亚**歧义**宽度）：rich 量它 1 格，
CJK 字体下终端可能画 2 格。详细页是按**屏幕行**算光标与滚动的（``#43``：那一页折行），
一格之差就是一行之差，所以那一页把没有截止时间的读法换成这个不含糊的汉字。
"""

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
"""转圈的那些帧（盲文点，全部 EAW=Neutral、1 格）。"""

WORDMARK_ICON = "\uf0ae"
"""词标前面那个 Nerd Font 图标（fa-tasks，私用区 U+F0AE；已核实 FiraCode NF 里有）。

私用区的东亚宽度是**歧义**，所以它只许站在装饰位：顶栏那一段没有要对齐的列，宽度错了也
看不出来（验收标准：「Nerd Font 图标只许出现在错了也不破对齐的装饰位」）。
"""

STRUCTURAL_GLYPHS: Final = (
    CURSOR_MARK,
    BLANK_MARK,
    INBOX_MARK,
    BUILTIN_MARK,
    CUSTOM_MARK,
    LIST_MARK,
    DONE_MARK,
    REPEAT_MARK,
    REMINDER_MARK,
    SUBTASK_DONE_MARK,
    SUBTASK_TODO_MARK,
    BLOCKED_GLYPH,
    RULE,
    HEADING_RULE,
    *SPINNER_FRAMES,
)
"""会进入**列结构或宽度算式**的每一个字形：守卫照着它逐个查宽度。

两个方向都会错，所以两个方向都要查（``tests/test_theme.py``）：``☰`` 是 rich 量 2 格而
Unicode 说中性；``▣ ★ · ─ ╭╮╰╯ ↑↓`` 是 rich 量 1 格而 Unicode 说是**歧义**宽度。

「宽度算式」这一半是 #48 加的：抬头不在列里，但 ``width: auto`` 的浮层宽度**就是**由最宽那行
算出来的，所以行内抬头里的字形与列里的字形一样承重。
"""

DECORATION_GLYPHS: Final = (ELLIPSIS, WORDMARK_ICON)
"""宽度可以含糊、但**错了也不破对齐**的位置：行尾的裁断记号、顶栏的词标图标。"""

# ---------------------------------------------------------------------------
# 动效
# ---------------------------------------------------------------------------

PAN_MS = 180
"""换层的横向平移时长。"""

CURSOR_BAR_MS = 120
"""装饰性光标条追上选中的时长（验收标准写的上限就是 120ms）。

**数据选中是瞬间到位的**，这条只是装饰：终端没法在两条之间画半格，所以「滑动」只能是
一根独立的条子自己追。若让它承担选中，那 ~120ms 里用户看到的位置与实际选中不一致，
按下 ``space`` 会打中另一条——那不是手感问题，是看起来像 bug 的正确性问题。
"""

SPINNER_DELAY_MS = 300
"""同步超过这个时长才出现转圈（本地缓存那一屏不该有个东西一直在转）。"""

ANIMATIONS_MODES: Final = ("auto", "on", "off")
"""``animations`` 开关的三个档；默认 ``auto``。"""


def animations_setting(env: Mapping[str, str] | None = None) -> str:
    """``DIDA_ANIM`` 里那个档位（原型用的就是这个环境变量）；不认识的值一律当 ``auto``。

    用户**按得动**的开关就是它：``DIDA_ANIM=off dida`` 在 ssh 里一点都不动。
    （落进 ``config.toml`` 是另一件事：那个文件放着 token，谁动它谁负责——本票不碰。）
    """
    environ = os.environ if env is None else env
    value = (environ.get("DIDA_ANIM") or "auto").strip().lower()
    return value if value in ANIMATIONS_MODES else "auto"


def animations_enabled(mode: str, env: Mapping[str, str] | None = None) -> bool:
    """``auto|on|off`` 落到「这一台机器上到底动不动」。

    ``auto`` 在远端（ssh）、低能力终端（``dumb`` / ``linux`` / 没有 ``TERM``）以及 Textual
    自己关掉动画时一律不动：动效在慢链路上是纯粹的延迟。``on`` / ``off`` 是用户的明确选择，
    不再被环境推翻。
    """
    if mode == "on":
        return True
    if mode == "off":
        return False
    environ = os.environ if env is None else env
    if any(environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return False
    if environ.get("TERM", "") in ("dumb", "linux", "unknown", ""):
        return False
    return environ.get("TEXTUAL_ANIMATIONS") != "none"


# ---------------------------------------------------------------------------
# 工具：颜色进 span、按格裁断
# ---------------------------------------------------------------------------


def styled(text: str, style: str = "") -> Text:
    """一个颜色写在 **span** 里的 Text——颜色进入 ``Text`` 的唯一正确方式。

    ``Text("x", style="cyan")`` 走的是 Textual 的 **CSS** 颜色解析（``cyan`` = ``#00FFFF``），
    于是那个调用点静默发出真彩色；``Text().append("x", style="cyan")`` 走 Rich 的解析
    （``cyan`` = ANSI 6）。同一个词两个意思，这个函数是唯一允许拼它的地方。
    """
    out = Text()
    out.append(text, style=style)
    return out


def clip(text: str, width: int) -> str:
    """按**格**裁断（不是按字符——汉字是 2 格），裁掉了就补一个 ``…``。"""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if width == 1:
        return ELLIPSIS
    return set_cell_size(text, width - 1) + ELLIPSIS


def pad(text: str, width: int) -> str:
    """右边补空格到 ``width`` **格**（列对齐靠它，不靠 ``str.ljust``）。"""
    return text + " " * max(0, width - cell_len(text))


def rpad(text: str, width: int) -> str:
    """左边补空格到 ``width`` 格（右对齐的列）。"""
    return " " * max(0, width - cell_len(text)) + text


# ---------------------------------------------------------------------------
# CSS：整个 app 的外观都在这里
# ---------------------------------------------------------------------------

_APP_CSS = """
Screen {
    background: {page};
    color: {page};
}
#top-bar {
    dock: top;
    height: 1;
    width: 100%;
    background: {page};
}
#stage {
    height: 1fr;
    width: 100%;
    overflow-x: scroll;
    overflow-y: hidden;
    scrollbar-size-horizontal: 0;
    scrollbar-size-vertical: 0;
    background: {page};
}
CursorPage {
    width: 100%;
    height: 100%;
    background: {page};
    scrollbar-color: {quiet};
    scrollbar-background: {page};
    scrollbar-corner-color: {page};
}
#page-body {
    width: 100%;
    height: auto;
    background: {page};
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
/* 截断与折行**按页分工**（ADR-0007）：上面那条是列表页（裁断），这一条是详细页（换行）。
   ``fold`` 不是可有可无的：Textual 在换行分支里按 ``text-overflow: fold`` 决定 ``divide_line``
   的 ``fold`` 参数，而详细页的光标与滚动是按**屏幕行**算的（#43）——留着一个 ``ellipsis``
   的话，一个比页宽还长的词会被裁掉而不是折下来，量出来的行数与画出来的就不是一回事。 */
DetailPage #page-body {
    text-wrap: wrap;
    text-overflow: fold;
}
#cursor-bar {
    width: 100%;
    height: 1;
    visibility: hidden;
}
/* 详细页底部那一行：钉在这一页自己的底边上（不是第三个 chrome 行——它跟着这一页走）。

   ``dock: bottom`` 落在**这一页**里，与钉在屏幕底部的状态栏不是同一个容器，所以
   ADR-0007 里那条「两个 dock: bottom 会互相盖掉、吃掉汉字格」的教训在这里不适用。 */
#detail-save {
    dock: bottom;
    height: 1;
    width: 100%;
    background: {page};
}
/* 编辑器：平时藏着（进编辑才亮出来），颜色一律走 ANSI 槽位。

   Textual 那两个输入框自带的样式用的是主题变量（surface / boost / border 这几个名字），
   它们是**真彩色**——不覆盖就等于把「跟随终端主题」在编辑态里丢掉，而且屏幕上看不出来
   （只有读 SGR 才发现）。所以边框、底色、光标块、选中块全部在这里改写成 ansi_*，
   内层那几个类名（input--* / text-area--*）也逐一盖掉。 */
#detail-edit {
    display: none;
    height: 1fr;
    width: 100%;
    background: {page};
}
#detail-edit-label {
    height: 1;
    width: 100%;
    background: {page};
}
#detail-input, #detail-text {
    background: {page};
    color: ansi_default;
    border: none;
    padding: 0;
    height: 1fr;
}
#detail-input {
    height: 1;
}
#detail-input:focus, #detail-text:focus {
    border: none;
}
#detail-input .input--cursor {
    background: {accent};
    color: {page};
    text-style: bold;
}
#detail-input .input--selection {
    background: {accent};
    color: {page};
}
#detail-input .input--placeholder, #detail-input .input--suggestion {
    color: ansi_default;
    text-style: dim;
}
#detail-text .text-area--cursor {
    background: {accent};
    color: {page};
    text-style: bold;
}
#detail-text .text-area--cursor-line {
    background: {page};
}
#detail-text .text-area--selection {
    background: {accent};
    color: {page};
}
#detail-text .text-area--gutter, #detail-text .text-area--cursor-gutter {
    color: ansi_default;
    background: {page};
    text-style: none;
}
#detail-text .text-area--matching-bracket, #detail-text .text-area--suggestion {
    background: {page};
    color: ansi_default;
}
/* 截止时间那两格 + 「全天」那一行（工单 #44）：与自由文本框同一块编辑区、同一套覆盖。

   覆盖必须逐条重来，不能靠「父容器已经改过了」：Textual 那两个输入框的颜色来自**组件
   自己的** DEFAULT_CSS（surface / input-cursor 那几个主题变量＝真彩色），父级那条
   `background` 盖不住它们的内层类名。所以 `#due-date` / `#due-time` 与 `#detail-input`
   一样逐条写成 ansi_*；漏一条的表现是真终端里出现 `38;2;`（ADR-0007 要消灭的正是它），
   而且**静默**——只有真 pty 抓取才看得见。 */
#due-edit {
    display: none;
    height: auto;
    width: 100%;
    background: {page};
}
#due-hint {
    height: 1;
    width: 100%;
    background: {page};
}
#due-date, #due-time {
    background: {page};
    color: ansi_default;
    border: none;
    padding: 0;
    height: 1;
}
#due-date:focus, #due-time:focus {
    border: none;
}
#due-date .input--cursor, #due-time .input--cursor {
    background: {accent};
    color: {page};
    text-style: bold;
}
#due-date .input--selection, #due-time .input--selection {
    background: {accent};
    color: {page};
}
#due-date .input--placeholder, #due-time .input--placeholder,
#due-date .input--suggestion, #due-time .input--suggestion {
    color: ansi_default;
    text-style: dim;
}
#status-bar {
    dock: bottom;
    height: 1;
    width: 100%;
    background: {page};
    text-style: dim;
}
Toast {
    background: {surface};
    border-left: outer ansi_green;
}
Toast .toast--title {
    color: {accent};
    text-style: bold;
}
Toast.-warning {
    border-left: outer ansi_yellow;
}
Toast.-error {
    border-left: outer ansi_red;
}
Toast.-warning .toast--title, Toast.-error .toast--title {
    color: {page};
}
"""


def _fill(template: str) -> str:
    """把 CSS 模板里的 ``{page}`` / ``{accent}`` 这类占位符换成主题里的拼法。

    不用 ``str.format``：CSS 本身就是一堆花括号，转义之后没人读得下去。
    """
    out = template
    for name, value in (
        ("page", CSS_PAGE),
        ("accent", CSS_ACCENT),
        ("surface", CSS_SURFACE),
        ("edge", CSS_EDGE),
        ("quiet", CSS_QUIET),
    ):
        out = out.replace("{" + name + "}", value)
    return out


def app_css() -> str:
    """整个 app 的样式表（``DidaApp.CSS``）。

    toast 那四条是**必须**的覆盖：Textual 自带的规则用 ``$success`` / ``$text-success``，
    那是主题里的真彩色值，不覆盖的话通知会以真彩色出现，破坏「跟随终端主题」。
    """
    return _fill(_APP_CSS)


_FORM_CSS = """
{name} .overlay-box {
    width: 80%;
}
{name} .overlay-label {
    width: 100%;
    height: 1;
    text-style: dim;
}
{name} .overlay-hint {
    width: 100%;
    height: auto;
    margin-top: 1;
    text-style: dim;
}
{name} Input {
    width: 100%;
    height: 1;
    border: none;
    padding: 0 1;
    background: {surface};
    color: {page};
}
{name} Input:focus {
    border: none;
    background: {accent};
    color: {page};
}
{name} ChoiceField {
    width: 100%;
    height: 1;
}
/* 多选那一格（#45）：一行一个选项，高度按选项数长（所以是 auto），上限由窗口自己管
   （PICKER_VISIBLE_ROWS）。颜色照旧全部走 ANSI 槽位——Static 自己不带底色，这里显式按
   浮层的面画一遍，免得将来某一次继承把主题变量那类真彩色带回来（名字不写在这里：
   tests/test_architecture.py 的源码扫描连注释里的那几个名字一起拦，那是故意的）。
   两条 scrollbar-size 是**关掉滚动条**：这一格不长出滚动条，也就不会画出滚动条那串
   真彩色（app_css 给 CursorPage 盖那三条是同一个理由）。 */
{name} MultiChoiceField {
    width: 100%;
    height: auto;
    background: {surface};
    color: {page};
    text-wrap: nowrap;
    text-overflow: ellipsis;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}
"""


def form_css(name: str) -> str:
    """表单浮层的样式表（``FormOverlay`` 的 ``DEFAULT_CSS``，工单 #42）。

    在 :func:`overlay_css` 那层壳子之上只加四件：

    - **输入框**：Textual 自带的 ``Input`` 用 ``$surface`` / ``$primary`` 那些主题变量上色，
      出去是真彩色（ADR-0007 一）。所以这里按浮层的面（槽 0）重画一遍。
    - **字段名**与**底部那行提示**：``dim``（层级靠字重与明暗，不靠更亮的颜色）。
    - **选择框**一行高（它自己那两个 ``< >`` 是 ASCII，宽度不含糊）。

    ## 输入框为什么是**一行高、没有边框**（#36 实测改的）

    原来是 ``border: ascii``：一个 ``Input`` 因此占 **3 行**。两张字段的表单看不出问题，
    而视图那张表单有七个字段（五个文本框 + 两个选择框），实测在 30 行的终端上要 30 行，
    浮层只给得起 24 行（``max-height: 80%``）——**底部那几个字段与那行提示被整个裁掉**，
    而其中就有「自定义视图只存在这台机器上」那句必须被看见的实话。

    所以输入框改成一行高、去掉边框（``padding: 0 1`` 留一格缩进），聚焦的记号从边框换成
    **强调色的底色**（槽 6）：七格里只有一格有底色，比原来那条细边更醒目，也不引入新色相。
    代价是少了那一圈框；换来的是任何字段数的表单都排得下。

    ``width: 80%`` 是给输入框的：``overlay_css`` 的 ``width: auto`` 配一个 ``width: 100%``
    的子控件量不出宽度来（百分比要有个有宽度的容器参照）。80% 而不是更窄，是为了底部那行
    提示在一屏 100 格里排得下——排不下它会折行，折行本身不算坏，但两行的提示看起来像
    出了错。窄终端上折行是正常的（40 格时折成三行，仍然读得完）。
    """
    return overlay_css(name) + _fill(_FORM_CSS).replace("{name}", name)


_OVERLAY_CSS = """
{name} {
    align: center middle;
    background: transparent;
}
{name} .overlay-box {
    width: auto;
    max-width: 80%;
    height: auto;
    max-height: 80%;
    border: ascii {edge};
    border-title-color: ansi_default;
    border-title-style: bold;
    padding: 1 2;
    background: {surface};
}
{name} .overlay-body {
    width: auto;
    height: auto;
}
"""


def overlay_css(name: str) -> str:
    """浮层的样式表（``overlays.py`` 里两个类的 ``DEFAULT_CSS``）。

    **不做遮罩压暗**：ANSI 颜色混不出来——``$background 60%`` 会发出真彩色，
    ``ansi_default 60%`` 是空操作（两条都实测过）。浮层靠**边框 + 槽 0 的面**抬起来，
    底下那一层不动。

    边框用 ``ascii``：它是唯一宽度不含糊的边框字形集合（``round`` / ``solid`` 用的
    ``─│╭╮╰╯`` 全是东亚歧义宽度）。
    """
    return _fill(_OVERLAY_CSS).replace("{name}", name)
