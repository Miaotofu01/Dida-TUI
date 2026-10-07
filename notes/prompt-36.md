# Prompt — ticket #36（自定义视图的建 / 改 / 删）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`。Worktree `/home/tofu/dida-v2-worktrees/t36`，
branch `ticket/36-custom-views`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/36 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/36/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`（`#36` 两条）、`wave-plan-and-seams.md`（求值接缝 +
浮层壳）、`api-shapes.md` §A8、`codebase-map.md` §2/§6、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 你的接缝（引 `wave-plan-and-seams.md`）

> View evaluation (pure) | `dida/sync/views.py` | #35 | … #36 extends the same file with custom-view persistence
> and the filter form's data model.

> Shared form overlay | `dida/tui/overlays.py` | #42, then #36 | … **#42 lands first, so #42 builds the shell and
> #36 reuses it.**

`sync/views.py` 的内置与自定义走**同一个** `evaluate_view`（没有 `if builtin`，加一个就弄坏本票），现在只有
`due`/`completion` 两维，扩展是**加法**。读路径口子已留好：`ViewRow`、`ViewReader.views()`、
`list_index(..., views=)`、`SyncEngine._view_rows()`；**本地库还没有 views 表**——表、读写、求值接线都是你的。
**#42 已把共享浮层壳搭好：`FormOverlay(title=, fields=)` + `FormField`/`FormOption`/`ChoiceField`（样式
`theme.form_css()`，字段集是参数，它用视图条件形状的字段集测过自己与清单无关）——继承它，不要另起一套。**
它同时划了边界：`e`/`d` 落在**视图行**上时**拒绝并说实话、一个字都不写**（视图字段集与 `n` 的「清单还是视图」
选择器归你）；`inbox` 行拒绝改/删（客户端合成，改名会被下一次刷新抹掉）。**能在自己文件里改就在自己文件里
改**；`app.py`/`keys.py`/`overlays.py` 越小越晚越好。

## 事实更正

1. **视图住本地 SQLite，不是 `config.toml`**（ADR-0005 与工单一致）。更正记录说 `GLOSSARY.md:70` 曾写成配置
   文件——集成分支上 **#34 的 `6685cb7` 已改成**「只存在本地库里（…不是 `config.toml`）」。别再写回去：
   **`config.toml` 永远不许出现 views 段**（那是放 token 的文件）。ADR-0005 的**文件名**仍写着 local config、
   正文说的是本地库——要改文件名就连 spec 链接一起改。
2. **`task/filter` 的前提是对的**：日期区间过滤的是 `startDate`（`openapi-dida365.md:706–707`），硬顶 200 条
   无分页（`:700`、`:703–711`）——本地求值是唯一能表达「今天到期」的路。
3. **`DueWindow` 简写会静默丢任务**：「今天」是 `DueWindow(first=None, last=0)`；`DueWindow(last=0)` 会保留默认
   的 `first=0`，**每条逾期任务被静默丢掉**（#35 的 merger 实测：三个成员变一个）——要「含逾期」就显式
   `first=None`。
4. **工单漏了一维（要你决定）**：维度列表只有「完成状态」，AC 却要求能建「最近完成」（「按完成状态**与完成
   时间**筛」）。现在没有完成时间维度；加在哪、与 `Completion` 怎么分工由你定，**只写一处**（`views.py`），
   并写进报告。

## 已测到的地雷

- **歧义宽度**：`—`(U+2014)、`·`(U+00B7)、`↓`/`↑` 被 `rich` 量 1 格、CJK 字体画 2 格。守卫分三处：帮助正文在
  **`tests/test_keymap.py:108`**（**不是** `test_theme.py`）、结构字形在 `tests/test_theme.py`（`len==1` 且
  `cell_len==1` 且 EAW ∉ A/W/F）、渲染级在 `tests/test_visual_identity.py`。**#37 的既定做法**：列字形换 ASCII
  （`—`→`-`、`·`→`.`）并给每个列字形加守卫——照做；你往 `keys.py` 加的标签会进帮助正文，一个歧义字形都不许有。
- **绝不用 `len()` 量宽度**：`cell_len`/`set_cell_size`、`theme.pad()`；裁断用 **`theme.clip()`**（按格裁、CJK
  安全，#37 正依赖它）。
- **颜色进 Rich span**（`Text(..., style="cyan")` 走 CSS 解析＝`#00FFFF` 静默真彩色）；SGR 断言查**参数**；
  光标整行被 `CursorPage._redraw` 的 `theme.SELECTED` 盖住——别写依赖那一行颜色的测试。
- **模态绑定链截断在最后一个模态控件**：浮层开着时 `app._bindings` 到不了；键要绑在**浮层上**、用
  `Binding(key, "app.quit")` 这种动作命名空间（`"quit"` 会被**静默吃掉**）。照 `overlays.py` 的 `QUIT_BINDINGS`
  写，**测试要真把浮层挂起来按键**。
- **表单的 Ctrl+C #42 已实现，继承不要重定**：模态内 `Binding("ctrl+c", "app.quit", priority=True)`（常量取
  #47 的 `QUIT_ACTION`/`QUIT_KEYS`）。**`priority=True` 是关键**——没有它 `screen.copy_text` 在绑定链里更靠前，
  键的含义会跟着「用户有没有选中文字」跑（实测过）；代价是表单里没有 Ctrl+C 复制；`q` 故意不绑（字母归输入框），
  所以**`Esc` 是表单的出口**、有焦点时可用。它用三种状态测过（空输入框 / 选择字段聚焦 / 输入框带选中）并断言
  `app.focused`——自己写表单就照这套测。
- **一处判定**：`is_overdue`（`sync/view.py`）、求值（`sync/views.py`）、绑定表（`keys.py`）、写行为表
  （`sync/writes.py`）各只有一份——消费，别写第二份。视图成员别用 `by_due` 重排：逾期档是 `order_key` 用
  `due_day` 定的，重排会被 `timestamp()` 盖掉。
- **可选值属于哪一层**：API 数据住 `sync/`（清单可选颜色 `LIST_COLORS` 在 `sync/lists.py`），`tui/` 只放视觉
  常量——你要加「一组可选值」时说清它在哪一层。
- `apply_refresh(prune_*)` 默认 `False` 别传 `True`；`build_app(..., config_file=None)` 默认**不重读配置**
  （#46 在飞）；`focus(scroll_visible=False)`；`animate(...)` 要给 `on_complete`；属性名别撞 Textual 的
  （`_animate`/`layer`/`_name`）；#34 删掉的 `date_parser.py` 与那几个 `tui/*.py` 不要复活。

- **假引擎要实现全 `Engine` 协议**（#46 新增的 `set_day_end`/`logical_day`）：`tests/test_fake_backend.py` 的
  `isinstance(FakeBackend, Engine)` 会红且报错不指向缺的方法；**优先委托真引擎**，别自己存一份日界。
- **`_view_day`（屏幕上的行按哪个逻辑日算的）必须在 `refresh_view()` 画行之前记下**：记晚了，日界在那几句里
  跨过去就会记下一个比行更新的日子，那一屏永远没人认领、再也不会重画。

- **两套排序并存是故意的，不要统一**：普通清单走 `row_sort_key`（**不置顶逾期**），视图走 #35 的 `order_key`
  （**逾期置顶**）。你要建的自定义视图走后者（`evaluate_view` 内部就是它）；别为了「看起来一致」去改 `read.py`，
  让普通清单也置顶逾期是另一张票的事。

## 验收标准（工单原文，逐条）

- [ ] 清单列表页按 `n` 先被问「清单还是视图」
- [ ] 选视图后能在浮层里填名字与过滤条件：清单范围、日期区间、优先级、标签、完成状态
- [ ] 视图定义落本地库，不写进配置文件
- [ ] 浮层里明确告知视图只存在本机
- [ ] 建好的视图出现在清单列表页，`enter` 进去看到过滤后的任务
- [ ] 按 `e` 能改条件，改完立刻生效（不必重进这一页）
- [ ] 按 `d` 一次 y/n 确认后删除该视图
- [ ] 删除视图不删除任何任务
- [ ] 能建出「高优先级未完成」和一个「最近完成」的例子（后者按完成状态与完成时间筛）
- [ ] 求值走纯函数那条路径，与内置视图同一份实现

补：「删视图不删任务」在同一份缓存上断言（任务还在各自清单里）；求值输入一律从参数进来（`now`/`day_end`），
`views.py` **不读时钟**；「只存在本机」照 ADR-0005 写——API 确实没有「保存一组过滤条件」的接口。

## 范围

你拥有 `sync/views.py`（加法）、`storage/store.py` 的 views 表与 `Store.views()`、清单列表页的视图行与
`n`/`e`/`d`、浮层里视图表单的**字段**。不做清单 CRUD（#42）、任务 CRUD（#39/#40）、详细页（#43/#44/#45）。

## 交接（待补）

_#37 与 #42 的实测交接已并入上文各节（`task_line(width=)` 必填、两套排序并存、窄屏丢弃顺序、
`_row_width` 不许按行覆盖、`refresh_view()` 是同步的、`FormOverlay` 继承与 Ctrl+C 的 `priority=True`、
`Engine` 协议新增的两条方法）。#43 的交接若在本票开工之后才落地，编排者会另行送达。_
