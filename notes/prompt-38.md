# Prompt — ticket #38（完成 ↔ 取消完成）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`。Worktree `/home/tofu/dida-v2-worktrees/t38`，
branch `ticket/38-complete-uncomplete`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/38 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/38/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`（`#38` 条）、`api-shapes.md` §A3/§A5、`wave-plan-and-seams.md`、
`codebase-map.md` §2/§7、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 三条事实更正（第一条是命门）

1. **本地守卫禁止 `status`，而 `status: 0` 是你整条路的机制**：`api/guards.py:49` 的
   `NON_WRITABLE_FIELDS = ("status",)` 经 `guard_writable`（`:157–169`）用在每个写请求体上，`merge_snapshot`
   （`:172–184`）还把它从底稿里剥掉。**只对 batch 这一条路放宽**（范围＝实测确认的那一个端点），不是全局删掉：
   普通写路径继续拒绝 `status`，并**把 `tests/test_api_client.py:640`
   `test_status_is_refused_because_the_server_would_ignore_it` 留在绿**（`:651` 是 create 那一半）。
   取消完成的请求体**不要走 `merge_snapshot`**（AC 要「只发 id、projectId、status」）：在 `guards.py` 里加一个
   **窄的** batch 准备函数，照 `prepare_completed_window_body` 的样子。
2. **`/task/batch` 把每个任务的失败塞在 `200 OK` 里**：响应是 `{id2etag, id2error}`（`openapi-dida365.md:567`），
   而 `api/client.py:290–309` 只按状态码分类——**整批全失败也会被报成成功**。必须读 `id2error`，让失败进重试
   队列/用户看得见的地方（码表见 `:567`）。
3. **「文档一字未提」是真的，别去「修」它**：`:551–590` 里 `status` 一次都没出现，也没有 `delete` 数组；注释按
   「实测确认的用法」写（spec :322、GLOSSARY「完成 / 取消完成」），别写成文档化特性。完成那一半是文档化的：
   `POST /open/v1/project/{projectId}/task/{taskId}/complete`，**无请求体**（`:398–418`）。

**顺带**：#32 的更正点名 `sync/engine.py:519` 与 `tui/app.py:436` 的过时理由（「没有本地『取消完成』这第二条
路」）；集成分支上活下来的是 `src/dida/sync/push.py:149–152` 与 `api/guards.py:161` 的文案——**按句子 grep，
别按行号找**。`tests/test_engine_writes.py:109` 已是更正后的口径，保持绿。

## 接缝与「一处判定」

> Task-list page | `dida/tui/pages/tasks.py` | #34, extended by #37, #38, #39, #40 | Layer 2: cursor, `space`,
> `n`, `d`, `g`/`G`, `enter`, empty state, struck-through completed rows sunk to the bottom.

写路径一侧由写行为表兜住：加一种写＝加一个 `WriteKind` 成员 + `_BEHAVIOUR` 一行；这条写要打**新形状**的端点，所以还要在 `WireCall` 里
加一种调用形状（`writes.py` 自己的模块文档就是这么说的）。`status` 的取值只有一处：`storage/store.py:59` 的
`COMPLETED_STATUS = 2`；把 `0` 写在它旁边，别在调用点写字面量。本地「不再是已完成」由 `store._snapshot`
（`:696`）统一翻，所以本地效果＝写 `status: 0` 而 `completedTime` 不动——AC 要的正是这个；服务端的 `status`
是权威（刷新会盖回来），别另立一份「取消过完成」的状态。**能在自己文件里改就在自己文件里改**；
`app.py`/`keys.py`/`overlays.py` 越小越晚越好。

## 已测到的地雷

- **`space` 只在任务列表页**：聚焦的 `Input` 把空格当字符吃掉、app 级绑定**静默失效**（量过，
  `terminal-input-evidence.md` §3）；这一页不许有抢焦点的输入框，两条 AC 都要真按键测。
- 键位表只有 `keys.py` 一份（`BINDINGS[LAYER_TASKS]` 加行），帮助正文跟着它走，那行标签要过
  **`tests/test_keymap.py:108`** 的宽度守卫（**不是** `test_theme.py`）；键要过 `unreliable_reason`；
  `page.BINDINGS == bindings_for(LAYER)` 别弄坏。
- **歧义宽度**：`—`/`·`/`↓`/`↑` 被 `rich` 量 1 格、CJK 字体画 2 格。结构字形守卫在 `tests/test_theme.py`
  （`len==1` 且 `cell_len==1` 且 EAW ∉ A/W/F）；**#37 的既定做法**是列字形换 ASCII 并给每个列字形加守卫——
  照做。**绝不用 `len()` 量宽度**，裁断用 `theme.clip()`（#37 正依赖它）。
- **颜色进 Rich span**（`Text(..., style="cyan")` 是 CSS 解析＝`#00FFFF` 真彩色）；SGR 断言查**参数**；
  `CursorPage._redraw` 用 `theme.SELECTED` 盖住光标行——别写依赖那一行颜色的断言。
- **假引擎要满足 `Engine` 协议**（#46 新增的 `set_day_end`/`logical_day`）：`tests/test_fake_backend.py` 的
  `isinstance(FakeBackend, Engine)` 会红，且报错不指向缺的方法；**优先委托真引擎**，别自己存一份日界。
  **断「什么都不该发生」用探针**：种一条只进缓存、谁都没通知界面的任务，操作后断言它**不在屏幕上**、
  屏幕逐字符与之前相同。
- **`_view_day`（屏幕上的行按哪个逻辑日算的）必须在 `refresh_view()` 画行之前记下**：记晚了，日界在那几句里
  跨过去就会记下一个比行更新的日子，那一屏永远没人认领、再也不会重画。
- 视觉反馈走已有那套（toast/flash）；**状态栏只有 `DidaApp._write_status` 一个写入口**，**每个 `await` 之后先问
  `self.is_running` 再碰 DOM**；`apply_refresh(prune_*)` 默认 `False` 别传 `True`；#34 删掉的 `date_parser.py`
  与那几个 `tui/*.py` 不要复活。

- **任务行是 #37 的接缝，签名是 `task_line(item, *, width, show_list_name=False)`（`width` 必填）**：你往行里加
  内容（完成标记/删除/顺延后的日期）时必须**自己**把它装进 `_line_width()` 给出的宽度里，否则 `#page-body` 的
  `nowrap` + ellipsis 会**再裁一次**，用户看到被裁两遍的行。窄屏丢弃顺序是用户的决定、别改：标签 → 标记 →
  截止 → 清单 → 最后才裁标题（实测丢弃点 48/41/36/24 列，标题 18 列才动；仓库有按格钉死的字面断言）。**别覆盖
  `CursorPage._row_width`**（它同时是规则线的宽度，覆盖过一次就短 2 格被测试抓住）——要按行算就学 `tasks.py` 的
  `_line_width()` 局部减 2 格。**`theme.clip()` 全树只许一处调用**（`tasks.py:125`，有测试钉「只有一个省略号」），
  别再添第二处裁剪；`app.refresh_view()` 是同步的，读屏幕前先 `await pilot.pause()`。
- **完成这条路上的一个未验证假设**：`_completed_cursor` 在被 200 上限截断时回退到的位置，假设「截掉的那一批里
  装的是最新的完成」；而文档只说「至多 200 条」、**对顺序一个字都没说**。t12 留下的启发式，#37 没动——你碰完成
  这条路，知道它站在这个假设上，别在注释里把它写成事实。
## 验收标准（工单原文，逐条）

- [ ] 任务列表页按 `space` 把未完成任务标记完成，并立即推送
- [ ] 再按 `space` 把已完成任务改回未完成，并立即推送
- [ ] 取消完成打到批量更新端点且带 `status: 0`
- [ ] 批量更新只发 id、projectId、status；其余字段不因这次取消完成而改变
- [ ] 完成或取消完成后有短暂的视觉反馈
- [ ] 取消完成后本地立刻不再把它算作已完成，即使完成时间戳还在
- [ ] `space` 在输入框有焦点时不影响输入（该打空格就打空格）
- [ ] 一个测试完整走通「按 `space` → 假后端收到完成调用」，另一个走通取消完成那一路
- [ ] 有测试钉死「取消完成」的请求形状（端点 + `status: 0`）

补：取消完成的本地效果要经得起**一次刷新**（服务端可能仍回 `completedTime`，判据是 `status`）；
`tests/test_write_kind.py` 会逐个访问行为表属性，漏一个当场红。

## 范围

你拥有任务列表页的 `space`、取消完成那一支，以及 batch 需要的窄口子。不做新建（#39）、删除/顺延（#40）、
详细页（#43/#44/#45）、清单 CRUD（#42），也别顺手删 `NON_WRITABLE_FIELDS`。

## 交接（待补）

_#37 与 #42 的实测交接已并入上文各节（`task_line(width=)` 必填、两套排序并存、窄屏丢弃顺序、
`_row_width` 不许按行覆盖、`refresh_view()` 是同步的、`FormOverlay` 继承与 Ctrl+C 的 `priority=True`、
`Engine` 协议新增的两条方法）。#43 的交接若在本票开工之后才落地，编排者会另行送达。_
