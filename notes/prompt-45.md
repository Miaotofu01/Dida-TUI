# Prompt — ticket #45（挑选型字段：所属清单 / 优先级 / 标签）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`。Worktree `/home/tofu/dida-v2-worktrees/t45`，
branch `ticket/45-picker-fields`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/45 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/45/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`（`#45` 那条——**一条声明是假的**）、`api-shapes.md`
§A4（move）/§D16（tags）/§D17（priority 编码）、`wave-plan-and-seams.md`、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 事实更正（第一条最重要：工单里有一句话是假的）

1. **`POST /open/v1/tag` 是文档里有的端点**（`:1612–1654`）：`name` 与 `label` **都必填**、≤64、小写，
   `label` 必须等于 `name` 的小写形式（`:1620–1621`）。所以「不能在客户端新建标签」**不是 API 限制**；实现成
   「API 做不到」就是**对用户说了假话**。**范围不要自己扩大**：spec 的 Out of Scope 已定 v2 不做标签创建，
   **仍不要实现它**；文案改成「这个客户端不做 / 还不能新建标签」，并在报告里点出这处不一致。**删除标签那一半
   是真的**：`:1576–1654` 只有 `GET` 与 `POST`，没有 update、没有 delete。
2. **搬运（`POST /open/v1/task/move`，`:497–548`）两个形状陷阱**：**请求体是 JSON 数组**（`:504`）、
   **响应是 `{id, etag}` 的数组**（`:516`），**不是**被搬的 Task；仓库里没有 move 方法，是你加。「收集箱与真实
   清单双向可搬」**文档沉默**（`:497–548` 里没有 `inbox`），spec :230 说它是**实测**——形状测试能钉字段名、
   钉不了别名，注释别写成「文档说」。
3. **优先级是两套东西**：线上编码 `None 0 / Low 1 / Medium 3 / High 5`（`:2279`、`:708`），**2 与 4 不合法**、
   文档不给含义；两个请求体表对默认值的措辞还不一致（`:232` "default is 0" vs `:324` "default is normal"）。
   界面只说四档。**编码表已经有一处**：`sync/view.py` 的 `PRIORITY_CYCLE = (0, 1, 3, 5)`/`next_priority`，写
   路径在 `sync/priority.py` 的 `cycle_priority`——消费它们，别写第二张表，别把 `0/1/3/5` 写进 UI 文案。
4. **搬运对其它字段的影响文档沉默**（`:497–548` 没有保留性声明），而 `status` 不在 update 请求体里
   （`:309–333`，替换还是合并也**文档沉默**）——UI 与注释都不许承诺「搬运/改字段后其它字段都会保留」。改优先级/
   标签走 update 时，底稿要**整份带回去**（`merge_snapshot` 的既有策略），别只发你改的那一个字段。

## 你的接缝（引 `wave-plan-and-seams.md`）

> Detail page | `dida/tui/pages/detail.py` | #43, extended by #44, #45 | Layer 3: field list, per-field edit,
> read-only subtasks/reminders/repeat.

**可读的文案在 `sync/view.py`**（`priority_mark`、`tags_text`、`format_due`）；**不在 `sync/rows.py`**（#37 的
`rows.py` 只有排序键、完成窗口起点、完成状态判据）。标签列表的客户端方法已经有：`api/client.py` 的
`list_tags`（`GET /open/v1/tag`，`OpenTag` 的 `name`/`label`/`sortOrder`/`color`/`type`）——brief 说它「没有
生产调用方」，**本票就是给它调用方的那一张**。挑选界面用 #42 搭好的 `FormOverlay`/`ChoiceField` 那套（字段集是
参数、样式 `theme.form_css()`），**Ctrl+C 的规矩直接继承**（见地雷）。**可选值属于哪一层**：API 数据住 `sync/`
（如清单可选颜色 `LIST_COLORS` 在 `sync/lists.py`），`tui/` 只放视觉常量。搬运要在 `api/client.py` 新增（数组请求体、数组响应）；写路径
那边照 `sync/writes.py` 自己的话：打**新形状**端点时才在 `WireCall` 里加一种调用形状。**能在自己文件里改就在
自己文件里改**；`app.py`/`keys.py`/`overlays.py` 越小越晚越好。

## 已测到的地雷

- **表单的 Ctrl+C #42 已实现，继承不要重定**：`Binding("ctrl+c", "app.quit", priority=True)`（`priority=True`
  是关键——没有它 `screen.copy_text` 更靠前，键的含义会跟着「有没有选中文字」跑）；`q` 不绑（字母归输入框），
  所以**`Esc` 是出口**、有焦点时可用。
- **模态绑定链截断在最后一个模态控件**：浮层开着时 `app._bindings` 到不了；键要绑在**浮层上**、用
  `Binding(key, "app.quit")` 的动作命名空间（`"quit"` 会被**静默吃掉**，照 `QUIT_BINDINGS` 写）；
  **测试要真把浮层挂起来按键**。
- **歧义宽度**：多选列表若有对齐的列（勾选标记、清单名），列字形要钉住（`len==1` 且 `cell_len==1` 且
  EAW ∉ A/W/F）——**#37 的既定做法**（列字形换 ASCII 并加守卫；`↻`(U+21BB) 与 `⚑`(U+2691) 量过合格），
  照做别另发明。帮助标签过 **`tests/test_keymap.py:108`** 的宽度守卫（**不是** `test_theme.py`）。
  **绝不用 `len()` 量宽度**；裁断用 `theme.clip()`（按格裁、CJK 安全，#37 正依赖它）。
- **颜色进 Rich span**（`Text(..., style="cyan")` 是 CSS 解析＝`#00FFFF` 真彩色）；SGR 断言查**参数**；
  `CursorPage._redraw` 用 `theme.SELECTED` 盖住光标行——别写依赖那一行颜色的断言。
- **假引擎**：见下面「交接（#43 实测）」最后两条——`Engine` 的方法要一次跟全，**优先委托真引擎**。
- 详细页字段行**自动换行**，光标/滚动按**屏幕行**算（#43 的规矩）；`focus(scroll_visible=False)`；
  `animate(...)` 要给 `on_complete`；属性名别撞 Textual 的（`_animate`/`layer`/`_name`）。
- **状态栏只有 `DidaApp._write_status` 一个写入口**，**每个 `await` 之后先问 `self.is_running` 再碰 DOM**；
  三字段都要「改完立刻推送 + 底部常驻『已保存 / 待推送（N）』」，失败要有**具体**错误而不是「保存失败」；
  `apply_refresh(prune_*)` 默认 `False`；#34 删掉的 `date_parser.py` 与那几个 `tui/*.py` 不要复活。

## 验收标准（工单原文，逐条）

- [ ] 所属清单能从我的清单里挑一个，任务真的搬过去
- [ ] 搬运走搬运端点，而不是普通字段更新；有测试钉死请求形状
- [ ] 收集箱与真实清单之间双向可搬
- [ ] 优先级能在无 / 低 / 中 / 高之间改
- [ ] 标签能从已有标签里多选打上
- [ ] 明确告知不能在客户端新建标签，要新标签得回官方客户端
- [ ] 三个字段都遵循「改完立刻推送 + 底部常驻状态」
- [ ] 搬运后目标清单的列表里有这条任务，原清单的列表里没有

补两条：搬运的请求形状要断**顶层是数组**、每项 `{fromProjectId, toProjectId, taskId}` 都必填（`:504`、
`:508–510`），响应按 `{id, etag}` 数组解析（`:516`）；「原清单里没有」在**读路径**上断言（不是断本地某个集合）。

## 范围

你拥有详细页的**所属清单 / 优先级 / 标签**三行与挑选界面、`api/client.py` 的 move 方法、标签列表的接线。
不做 #44 的截止时间选择器与标签创建（spec 的 Out of Scope）、不动任务列表页与清单 CRUD（#42）；#43 的
per-field 口子不对就停下报告。

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
