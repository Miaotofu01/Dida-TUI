# Prompt — ticket #44（截止时间）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`。Worktree `/home/tofu/dida-v2-worktrees/t44`，
branch `ticket/44-due-date`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/44 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/44/comments --jq '.[].body'
```

**Read before designing:** `api-shapes.md` §A2/§D17/§E2(d)、`cross-ticket-corrections.md`、`wave-plan-and-seams.md`
（你扩展详细页）、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 事实更正：`cross-ticket-corrections.md` **没有 #44 条目**——下面是与你直接相关、已核过的四条

1. **「服务端会自动补一个同值的开始时间」是文档沉默的**（§E2(d)：没有这句话，`dueDate`/`startDate` 是两个独立
   字段，`:2277`/`:2285`）。旁证只到色彩级：同时出现的例子两者相等（`:753–754`、`:771–772`），`/task/filter`
   过滤的是 `startDate`（`:706–707`）——**没有一条是规则**。所以不写「服务端会补」的测试，也不假设它不补；
   **「写截止时间时要不要一起写 `startDate`」是一个明确决定**，选一个并在报告里说清。
2. **`timeZone` 原样回写＝用 `api_date` 那条口径**：文档对「写错会怎样」沉默（§D17），仓库的对策在
   `api/guards.py` 的 `api_date`——字符串**原样返回**（`+0800` 不变 `+08:00`、不转 UTC、毫秒不抹）、
   `datetime` 按**它自己的** offset 写、naive 直接拒绝。换时区就是「静默位移」，别自己序列化。
3. **重复规则那条守卫已经存在，消费它**：没有日期就写 `repeatFlag` 会被服务端**静默清空**（spec :158），
   `guard_repeat_rule`（`api/guards.py`，`DatelessRepeatError`）在请求出门前就拦。要「重复规则一字节不变」，
   请求体走**完整底稿 ⊕ 改动**（`merge_snapshot` 的既有策略），别只发 `{id, projectId, dueDate}`——更新端点
   省略字段是替换还是合并**文档没说**（`:309–333`），而底稿里带着未知字段与你不想动的一切。
4. **完成态无法在这个端点上重申**：`status` 不在 update 请求体里（`:309–333`），改一条已完成任务的截止时间
   不能重新声明 `status: 2`，它会不会被保住**没有文档**——UI 与注释都不许承诺。**清空截止时间的请求形状文档
   也没写**（`dueDate: null`？空串？）——本票必须自己决定、用形状测试钉住、写进报告。

## 你的接缝（引 `wave-plan-and-seams.md`）

> Detail page | `dida/tui/pages/detail.py` | #43, extended by #44, #45 | Layer 3: field list, per-field edit,
> read-only subtasks/reminders/repeat.

引擎侧也**已经有**改期写路径：`sync/schedule.py` 的 `reschedule(task_id, due=…, all_day=…)` 只写 `dueDate` +
`isAllDay`，并写明「`all_day=True` 时 `due` 是那一天的 00:00，是**日期标记**」。**先合集成分支再看它**，
能塞进去就别新开一条写路径。**可读的截止时间文案在 `sync/view.py`**（`format_due`、`TaskItem.due_text`、`TaskDetail.due_text`）——**不在
`sync/rows.py`**（#37 的 `rows.py` 只有排序键、完成窗口起点、完成状态判据；去那里找 `format_due` 会找不到）。
「没有截止时间」读作 **ASCII 的 `-`**，不是 `—`（U+2014 是东亚歧义宽度，进对齐列整列会歪）；#37 已把
`NO_DUE_TEXT` 从 `—` 改成 `-` 并加了守卫（`len==1`、`cell_len==1`、EAW ∉ A/W/F）——照它做。
**能在自己文件里改就在自己文件里改**；`app.py`/`keys.py`/`overlays.py` 越小越晚越好。

## 已测到的地雷

- **表单的 Ctrl+C #42 已实现，继承不要重定**：`Binding("ctrl+c", "app.quit", priority=True)`（`priority=True`
  是关键——没有它 `screen.copy_text` 更靠前，键的含义会跟着「有没有选中文字」跑）；`q` 不绑（字母归输入框），
  所以**`Esc` 是出口**、有焦点时可用；你的日期选择器也是一个 `Input`/`TextArea` 宿主，见下面 #43 的交接。
- **模态绑定链截断在最后一个模态控件**：浮层开着时 `app._bindings` 到不了；键要绑在**浮层上**、用
  `Binding(key, "app.quit")` 的动作命名空间（`"quit"` 会被**静默吃掉**，照 `QUIT_BINDINGS` 写）；
  **测试要真把浮层挂起来按键**。
- **歧义宽度**：日期/时间选择器若有对齐的列，列字形要钉住（`len==1` 且 `cell_len==1` 且 EAW ∉ A/W/F）——
  **#37 的既定做法**（`NO_DUE_TEXT` 从 `—` 换成 ASCII `-`、低/无优先级从 `·` 换成 `.`，并给每个列字形加
  守卫），照做别另发明。帮助标签过 **`tests/test_keymap.py:108`** 的宽度守卫（**不是** `test_theme.py`）。
  **绝不用 `len()` 量宽度**；裁断用 `theme.clip()`（按格裁、CJK 安全，#37 正依赖它）。
- **颜色进 Rich span**（`Text(..., style="cyan")` 是 CSS 解析＝`#00FFFF` 真彩色）；SGR 断言查**参数**；
  `CursorPage._redraw` 用 `theme.SELECTED` 盖住光标行——别写依赖那一行颜色的断言。
- **假引擎**：见下面「交接（#43 实测）」最后两条——`Engine` 的方法要一次跟全，**优先委托真引擎**。
- 详细页字段行**自动换行**，光标/滚动按**屏幕行**算（#43 的规矩）；`focus(scroll_visible=False)`；
  `animate(...)` 要给 `on_complete`；属性名别撞 Textual 的（`_animate`/`layer`/`_name`）。
- **状态栏只有 `DidaApp._write_status` 一个写入口**，**每个 `await` 之后先问 `self.is_running` 再碰 DOM**；
  **非法日期在请求出门前拦下**＝在 transport 接缝上断「零请求」（`api_date` 已经是那个守卫）；
  `apply_refresh(prune_*)` 默认 `False`；#34 删掉的 `date_parser.py` 与那几个 `tui/*.py` 不要复活。

## 验收标准（工单原文，逐条）

- [ ] 详细页的截止时间字段用结构化输入改：先选日期、再选时间
- [ ] 「全天」开关能在「有具体时刻」与「只有日期」之间切换
- [ ] 能把截止时间清除，任务变回「没有日期」
- [ ] 改截止时间时重复规则一字节不变
- [ ] 没有日期的任务绝不写入重复规则
- [ ] 非法日期在发出前被本地拦下，请求不出门
- [ ] 时区字段原样保留并回写，截止时间不发生静默位移
- [ ] 回写带上服务端给的未知字段
- [ ] 有测试钉死「改截止时间」的请求形状

补两条：「重复规则一字节不变」要断在**请求体**上（`repeatFlag` 与底稿里的值逐字相等），不是断本地对象；
「带上未知字段」也要在那个请求体上看得见（底稿里服务端给的陌生 key 原样出现）。

## 范围

你拥有详细页的**截止时间那一行 + 它的结构化选择器**，必要时加 `sync/schedule.py` 上清空截止时间那一条。
不做 #45 的三个挑选型字段、不动任务列表页、不重做详细页框架（#43 的）；#43 的 per-field 口子不对就停下报告。

## 交接（#43 实测）

- **`Field.wire = None` 就是你插进去的接缝**：`pages/detail.py` 的字段注册表（7 格）里「清单/截止/优先级/标签」
  四格的 `wire` 留成 `None`，按 `enter` 是空操作——照这个模式接，别另建字段机制。
- **Textual 的 `Input`/`TextArea` 会吐真彩色**（`$surface`/`$boost`/`$input-cursor-*` 这些 `$` 变量）：#43 覆盖了
  看得见的那几处，**你每挂一个新组件都要照样覆盖**，否则真终端出现 `38;2;`——ADR-0007 要消灭的正是它，而且
  **静默**（只有真 pty 抓取看得见）。
- **`on_*` 处理器按 MRO 每个类各触发一次**（`_get_dispatch_methods` 遍历 MRO）：覆盖处理器又调 `super()` 的
  测试探针会「一次按键、两件事发生」（#43 让 `open_container` 跑了两遍）。
- **`dock: bottom` 的子组件占一行可见空间**：不减掉它，光标会滚到它后面（详细页已有一个保存行）。
- **`pilot.press("：")` 会被当成键名**：测试让 pilot 打字符串要用纯中文或字母数字，别带标点。
- **详细页的行是变高的**：光标/滚动走屏幕行偏移表（rich `divide_line(fold=True)`），基类给了 `_row_lines`/
  `_total_lines`/`_cursor_lines`；**列表页仍一行一条，别去改列表那边**。
- **`NO_VALUE = "无"`** 替代了 `—`（歧义宽度）：显示「没有截止时间」沿用 `NO_VALUE`，别把 `—` 拿回来。
- **假引擎一次跟全**：`Engine` 多了 `write`，`SyncStatus` 多了 `last_error` 与 `pending_error()`，`FakeBackend`
  对应 `write`/`writes`/`write_error`，**再加 #46 的 `set_day_end`/`logical_day`**——否则 `test_fake_backend.py`
  的 `isinstance` 会红且不指向缺的方法。
- **两条边界**：描述/备注被 #43 对调过（描述 = `content`、备注 = `desc`），以代码为准；「提醒」格的 humanise
  已另开 **#55**，**不要顺手改**（同文件会撞）。

## 交接（待补）

_#37 与 #42 的实测交接已并入上文各节（`task_line(width=)` 必填、两套排序并存、窄屏丢弃顺序、
`_row_width` 不许按行覆盖、`refresh_view()` 是同步的、`FormOverlay` 继承与 Ctrl+C 的 `priority=True`、
`Engine` 协议新增的两条方法）。#43 的交接若在本票开工之后才落地，编排者会另行送达。_
