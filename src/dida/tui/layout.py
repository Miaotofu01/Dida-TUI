"""窄屏降级：终端有多宽，三栏就摆成什么样（工单 #18）。

**这一段是 v1 的**：v2 是一栏三层页面，没有三栏可收放。它单独一个模块，正是为了让
#34 删它的时候一眼看得见删干净了没有（``pane_tier`` 的三个档位、两个阈值、以及
:meth:`~dida.tui.app.DidaApp._apply_tier` 里那两行 ``display``）。

纯函数 + 两个常量，不起界面就能测：宽度进来、档位出去。
"""

from __future__ import annotations

WIDE_MIN_WIDTH = 110
"""三栏常驻的最小列数（工单 #18）。"""

MEDIUM_MIN_WIDTH = 80
"""收掉左栏的最小列数：比这更窄就连清单也进浮层。"""


def pane_tier(width: int) -> str:
    """按终端列数分档（工单 #18）：``wide`` / ``medium`` / ``narrow``。

    - ``wide``（≥110）：三栏常驻。
    - ``medium``（80–109）：收起右栏，``Enter`` 以浮层打开详情。
    - ``narrow``（<80）：再收起左栏，清单也进浮层。

    纯函数：宽度进来、档位出去。分档是布局的事，跟终端里有什么数据无关，所以它在这里
    而不是在引擎里——也不需要在测试里开一个 app 才能问「95 列算哪一档」。
    """
    if width >= WIDE_MIN_WIDTH:
        return "wide"
    if width >= MEDIUM_MIN_WIDTH:
        return "medium"
    return "narrow"
