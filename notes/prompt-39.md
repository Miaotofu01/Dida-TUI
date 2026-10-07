# Prompt — ticket #39（新建任务）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`。Worktree `/home/tofu/dida-v2-worktrees/t39`，
branch `ticket/39-create-task`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/39 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/39/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`（`#39` 那条——API 没问题，**替你撒谎的是测试替身**）、
`api-shapes.md` §A1、`wave-plan-and-seams.md`、`codebase-map.md` §2/§9、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 必做（新票 #53，同一张工单里改完）：断网先建、再改，那笔改动永远出不了队

不对称就在 `src/dida/storage/store.py` 紧挨着的两个函数：`adopt_created_list()` 认领时**连带把排在后面的改动挪
到真 id 上**（`UPDATE pending_list_changes SET list_id = ? WHERE list_id = ?`，注释里写了理由）；
`adopt_created()`（任务）**没有** `UPDATE pending_changes SET task_id = ?`。后果：那笔改动 POST 到
`/open/v1/task/local-<uuid>`（服务端从没见过的临时 id）→ 404 → 退避重试 → **永远卡住**，状态栏永久非零，
而用户的编辑永远到不了服务端——正是 `UnknownTaskError` 的注释所指的那类安静错误。

**修法照抄清单版的形状**：(a) `adopt_created()` 在**同一个事务**里补一句，把该 task 的待推送改动挪到真 id 上
（`target != local_id` 时才做）；(b) **推送循环每轮重新读一次队列**（只挪 id 不够：本轮若拿的是一开始读进来的
快照，仍会用旧 id 去推——#42 两件都做了）；(c) 两条测试：一条钉「认领后队列里那笔改动的 id 已是真 id」，一条
端到端钉「断网先建后改 → 恢复推送 → 队列清空、改动落到服务端、待推送数回到 0」。

## 事实更正：API 是对的，会骗你的是替身

`projectId` 在 create 上是**必填**（`openapi-dida365.md:222`），示例发真实清单 id（`:263`）；客户端本来就是透传
（`api/client.py:142–148`）。写死收集箱是**仓库这一侧**的事——更正里引的 `sync/engine.py:914` 是 #32 切模块**之前**
的位置；集成分支上是 `sync/create.py`（`_create_payload(..., project_id=INBOX_ID)` 与 `enqueue(list_id=INBOX_ID)`），
它自己的 docstring 就写着「v2 的落点规则由 #39 改」——**按 `INBOX_ID` grep，别按行号找**。

**真正的陷阱在测试替身，先修它再修行为**（否则「在 工作 里新建落进 工作」会静默断言成收集箱）：
`FakeBackend.create()` **没有落点参数**、把 `list_name=INBOX_NAME` 写死（签名的行号已变，别照抄更正里的
`:307–333`）；`InMemorySource.add_task` 会**凭空造出**那行清单（`self._lists.setdefault(...)`），所以「新建后
那个清单里有这条任务」在生产路径上并没有被验证。同一处的顺手 bug：`FakeBackend.create` 记下了 `tags` 却**没有
放到**它加进去的那条任务上——一并修掉并在报告里说。

## 工单没写、要你决定的一件（不是事实错误）

「在视图里建……如果视图隐含了日期（比如在「今天」里建），新任务自动带上那个日期」——现在**没有任何读模型
字段**携带「隐含日期」，而「今天」的 `DueWindow(first=None, last=0)` 里 `first` 是 `None`（承重：逾期也在），
所以**不能读 `first`**。定义放一处、写清「今天」与「最近七天」各自隐含什么，报告里说你选了什么。判断「这个
容器是不是视图」用读模型已有的位（`TaskList.shows_list_name`，True＝视图），别在两处各判一次。

## 你的接缝（引 `wave-plan-and-seams.md`）

> Task-list page | `dida/tui/pages/tasks.py` | #34, extended by #37, #38, #39, #40 | Layer 2: cursor, `space`,
> `n`, `d`, `g`/`G`, `enter`, empty state, struck-through completed rows sunk to the bottom.

「先问一句『清单还是视图』」是 #36 在清单列表页的 `n`；**你的 `n` 在任务列表页，是「只填标题的输入框」**。
表单若用 #42 搭好的浮层壳（`FormOverlay(title=, fields=)` + `ChoiceField`，样式 `theme.form_css()`，字段集是
参数），**继承它、别另起一套**，连 Ctrl+C 的规矩一起继承（见地雷）。**能在自己文件里改就在自己文件里改**；
`app.py`/`keys.py`/`overlays.py` 越小越晚越好。

## 已测到的地雷

- **表单的 Ctrl+C 规矩 #42 已实现，继承不要重定**：`Binding("ctrl+c", "app.quit", priority=True)`（常量取
  #47 的 `QUIT_ACTION`/`QUIT_KEYS`）。**`priority=True` 是关键**——没有它 `screen.copy_text` 在绑定链里更靠前，
  键的含义会跟着「用户有没有选中文字」跑（实测过）；代价是表单里没有 Ctrl+C 复制；`q` 故意不绑（字母归输入框），
  所以**`Esc` 是表单的出口**、有焦点时可用。它用三种状态测过（空输入框 / 选择字段聚焦 / 输入框带选中）并断言
  `app.focused`——自己写表单就照这套测。
- **模态绑定链截断在最后一个模态控件**：浮层开着时 `app._bindings` 到不了；键要绑在**浮层上**、用
  `Binding(key, "app.quit")` 的动作命名空间（`"quit"` 会被**静默吃掉**）；**测试要真把浮层挂起来按键**。
- **歧义宽度 + 宽度算法**：`—`/`·`/`↓`/`↑` 被 `rich` 量 1 格、CJK 字体画 2 格；你往 `keys.py` 加的标签会过
  **`tests/test_keymap.py:108`** 的宽度守卫（**不是** `test_theme.py`），结构字形守卫在 `tests/test_theme.py`
  （`len==1` 且 `cell_len==1` 且 EAW ∉ A/W/F）。**#37 的既定做法**是列字形换 ASCII 并加守卫——照做。
  **绝不用 `len()` 量宽度**；裁断用 `theme.clip()`（按格裁、CJK 安全）。
- **颜色进 Rich span**（`Text(..., style="cyan")` 是 CSS 解析＝`#00FFFF` 真彩色）；SGR 断言查**参数**；
  `CursorPage._redraw` 用 `theme.SELECTED` 盖住光标行——别写依赖那一行颜色的断言。
- **假引擎要实现全 `Engine` 协议**（#46 新增的 `set_day_end`/`logical_day`）：`tests/test_fake_backend.py` 的
  `isinstance(FakeBackend, Engine)` 会红且报错不指向缺的方法；**优先委托真引擎**。**断「什么都不该发生」用探针**
  （#46 的现成模式）：种一条只进缓存、谁都没通知界面的任务，操作后断言它不在屏幕上、屏幕逐字符未变。
- 空标题不许建（`create()` 的 docstring 明说拦它的**是调用方**，也就是你）；`create()` 返回 `local-…` 临时 id，
  推成功后认领服务端 id——「建完停在列表里、能接着建下一条」要在**真按键**的测试里断言。
- 状态栏只有 `DidaApp._write_status` 一个写入口，**每个 `await` 之后先问 `self.is_running` 再碰 DOM**；推不动要
  留在重试队列里（AC 明写）；`focus(scroll_visible=False)`；`animate(...)` 要给 `on_complete`；属性名别撞
  Textual 的（`_animate`/`layer`/`_name`）；**新建路径不许再碰日期解析器**（#34 已删 `date_parser.py`）。

- **任务行是 #37 的接缝，签名是 `task_line(item, *, width, show_list_name=False)`（`width` 必填）**：你往行里加
  内容（完成标记/删除/顺延后的日期）时必须**自己**把它装进 `_line_width()` 给出的宽度里，否则 `#page-body` 的
  `nowrap` + ellipsis 会**再裁一次**，用户看到被裁两遍的行。窄屏丢弃顺序是用户的决定、别改：标签 → 标记 →
  截止 → 清单 → 最后才裁标题（实测丢弃点 48/41/36/24 列，标题 18 列才动；仓库有按格钉死的字面断言）。**别覆盖
  `CursorPage._row_width`**（它同时是规则线的宽度，覆盖过一次就短 2 格被测试抓住）——要按行算就学 `tasks.py` 的
  `_line_width()` 局部减 2 格。**`theme.clip()` 全树只许一处调用**（`tasks.py:125`，有测试钉「只有一个省略号」），
  别再添第二处裁剪；`app.refresh_view()` 是同步的，读屏幕前先 `await pilot.pause()`。

## 验收标准（工单原文，逐条）

- [ ] 任务列表页按 `n` 弹出只填标题的输入框，回车即建
- [ ] 建完停在列表里，能接着建下一条
- [ ] 在某个清单里建，任务落在那个清单里（不是收集箱）
- [ ] 在视图里建，任务落在收集箱
- [ ] 在隐含日期的视图（如「今天」）里建，新任务自动带上那个日期
- [ ] 新建立即推送，失败进重试队列
- [ ] 新建路径不再依赖日期解析器
- [ ] 假后端支持按清单新建，否则测试会静默断言成收集箱

补：请求形状用 transport 接缝钉住（`title` + **必填** `projectId`；没写日期就不许出现 `dueDate`/`isAllDay`——
`_create_payload` 已是这个口径）；在**视图**里建完那条任务可能不满足该视图的过滤条件，「建完停在列表里」显示
什么由你决定并写进报告。

## 范围

你拥有任务列表页的 `n`、那个只填标题的浮层、`sync/create.py` 的落点参数（与替身）、视图隐含日期那一处判定，
以及 #53 的修法与两条测试。不做 #40/#38/#36/#43/#44/#45。

## 交接（待补）

_#37 与 #42 的实测交接已并入上文各节（`task_line(width=)` 必填、两套排序并存、窄屏丢弃顺序、
`_row_width` 不许按行覆盖、`refresh_view()` 是同步的、`FormOverlay` 继承与 Ctrl+C 的 `priority=True`、
`Engine` 协议新增的两条方法）。#43 的交接若在本票开工之后才落地，编排者会另行送达。_
