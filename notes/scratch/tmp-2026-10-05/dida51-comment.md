落到集成分支 `feat/v2-terminal-client`：merge commit **`45c8faa`**（分支 `ticket/51-visual-foundation`，tip `699271d`），集成分支新 tip 就是 `45c8faa`。快进式干净合并——分支切自 `86611e5`，集成分支上多出来的只有 `045d010`（ADR-0007 那句「按 SGR 参数匹配」的措辞修正），两处没有碰同一个文件。

**这张工单落地了什么**：颜色改成跟随终端主题（`App(ansi_color=True)`，强调色 = 槽 6 / 页面底 = `ansi_default` / 浮层面 = `ansi_black`）；视觉常量收进唯一出处 `dida/tui/theme.py`；顶栏（词标 + 导航路径）取代 Footer，chrome 仍是两行；`☰` 换成宽度无歧义的 `⋮`；换层横向平移 + 会追赶的光标条 + 超阈值转圈 + 原生 toast；`animations = auto|on|off`（构造参数与 `DIDA_ANIM`）；守卫做成真的两条（AST 源码扫描 + 渲染级 SGR 断言）；`docs/architecture.md` 那条「只用 16 色」的假象改成实测事实。

**合并后自己复核的结果**（不是照抄实现者的报告）：
- `uv run pytest` → `446 passed in 45.02s`；合并前 `045d010` 上收集到 **407** 条，正好 +39。`uv run python -c "import dida.bootstrap"` 通过；`tests/test_architecture.py` 32 passed。
- 真 pty 抓取（`env -u NO_COLOR TERM=xterm-kitty COLORTERM=truecolor`，临时 HOME + 假 token + `refresh_on_start=false`，`python -m dida` 100×30 真按键走三层 + 浮层 + 退出）：185748 字节里 `38;2;` **0** 次、`48;2;` **0** 次；强调色以 SGR **参数** `36` 出现 63 次（形态是 `\x1b[1;36;49m` / `\x1b[1;2;36;49m`，不是整串 `\x1b[36m`）；`49` 出现 2395 次、`40` 303 次、装饰光标条的 `46` 16 次；alt-screen 进出都在，进程 exit 0。
- 颜色守卫是真的：在合并后的树的一份副本里种一条 `Text("x", style="red")` 与一条 `rgb(255,0,0)`，`test_no_colour_goes_into_a_base_style` 与 `test_the_tui_names_colours_in_exactly_one_place` 立刻变红。
- 结构字形宽度自己重量过一遍：`theme.STRUCTURAL_GLYPHS` 里每一个都是 rich 1 格且 `unicodedata.east_asian_width` 为 N/Na；`…` 与 Nerd Font 图标只在装饰位。
- 六条 wave-4 接缝都在、形状没变：`pages/index.py` / `pages/tasks.py` / `pages/detail.py` / `keys.py`（按层的 `BINDINGS` 一张表，`bindings_for()` 与 `help_rows()` 同源）/ `overlays.py`（`MessageOverlay` + `ConfirmOverlay` 复用 `theme.overlay_css()`，表单壳子留给 #42）/ `app.py`（只剩组装、状态栏、同步泵、退出流程；657 行，页面渲染都在 `pages/`）。

**下一批七张要接的已知项**（不阻塞，但别重新发明）：行截断交给 `#page-body` 的 `nowrap+ellipsis`，右对齐列是 #37 的活；`theme.pad/rpad/clip` 已按格实现；`animations` 还没进 `config.toml`（`config.py` 与 #37/#46 共用，落点待定，候选 #52）；任务级「完成」toast 归 #38；`sync/view.py` 的低优先级标记 `·`（U+00B7，东亚歧义）仍未进 TUI 字形表，#37 把它放进对齐列之前必须先映射或替换。

集成分支未进 main 之前不关这张工单。
