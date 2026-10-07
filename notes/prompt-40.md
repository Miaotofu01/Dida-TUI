# Prompt — ticket #40（删除与顺延）

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t40`，branch `ticket/40-delete-and-defer`。验收标准从 tracker 取，逐条为准：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/40 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/40/comments --jq '.[].body'
```

**Read before designing:** `api-shapes.md` §A6（delete 的精确形状与「没有任何恢复端点」的证据）、§E2(a)/§E3、
`wave-plan-and-seams.md`（顺延的接缝）、`codebase-map.md` §2/§7、`wave-4-kickoff.md`。

## 站规

- **TDD**：先调 `tdd` skill，红 → 绿 → 重构；在既定两接缝上测（内存 `FakeBackend`/`InMemorySource` +
  `run_test()` pilot；可注入的 HTTP transport），**只断外部行为**，不断控件树/内部状态/渲染字符串的颜色码。
- 永不碰 `~/.config/dida-tui/config.toml`、永不打印 token、永不对真实 TickTick API 做写实验（用户的真账号）；
  不跑 `env`/`set -x`/`bash -x`；永不 `git fetch`（死 SOCKS 代理挂住 SSH），要推用
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`。
- **不要自己合进 `feat/v2-terminal-client`**（merger subagent 做）；工单落地**只评论、不关闭**。

## 事实更正：`cross-ticket-corrections.md` **没有 #40 条目**——下面是与你有关的、已核过的三条

1. **确认文案不能承诺恢复，这是文档级事实。** 全文件搜索：没有任何 trash / undelete / restore / recovery 端点，
   唯一的 `DELETED` 命中是 batch 的错误码（`openapi-dida365.md:567`），它的意思是「这个 id 已经没了」——
   是「删掉的 id 不可用」的证据，不是恢复路径的证据。所以安全、有据的措辞是「文档没有任何回收站 / 恢复接口」。
2. **`inbox` 当 `projectId` 用在 delete 上是文档沉默的。** 文档只在 `/project/{projectId}/data`（`:1068`）、
   `/task/undone`（`:796`）、`/task/completeTasks`（`:433`、`:438`）写了 `inbox` 别名；delete（`:468`）与
   complete（`:398`）一个字都没说。spec :234 说别名在删除上也行——那是**实测**（§E3）。代码注释里按实测写，
   别写成「文档说」。
3. **顺延的算术已经实现并有测试了，别重写。** `sync/schedule.py` 的 `defer()`/`_defer_changes()`/
   `_logical_day_after()` 都在：逻辑日落点由 `logical_day()` 逐步问出来、原墙钟时刻放进目标逻辑日、「比日界早的
   墙钟时刻」（`04:00` 边界上的 02:00）再往后挪一天、全天任务只挪日期标记、没有截止时间就**不凭空补一个**；
   `tests/test_sync_defer.py` 钉着这些（含 `test_deferring_at_2am_lands_on_the_next_logical_day`）。**浇你的
   界面，消费 `defer(days=1|7)`**；真发现算术不对，就在那一个文件里改并说明。

## 你的接缝（引 `wave-plan-and-seams.md`）

> Task-list page | `dida/tui/pages/tasks.py` | #34, extended by #37, #38, #39, #40 | Layer 2: cursor, `space`,
> `n`, `d`, `g`/`G`, `enter`, empty state, struck-through completed rows sunk to the bottom.

删除这一支走写行为表里已有的 `WriteKind.DELETE`（`local=REMOVE`、`whole_row=True`，`sync/writes.py`）——
**不要为删除加第二种写**。`ConfirmOverlay` 已经存在（`overlays.py`：一句提示 + `y`/`n`/`Esc`），复用它，
不要另画一个。`d` 在清单列表页是清单删除（#42 的），你只在任务列表页绑。顺延沿用 `WriteKind.UPDATE` 那条写路径。

## 已测到的地雷

- **模态的绑定链截断在最后一个模态控件**：浮层开着时 `app._bindings` 到不了；浮层要的键必须绑在**浮层上**、
  用动作命名空间（`Binding(key, "app.quit")`，不是 `"quit"`——后者解析到浮层，按键被**静默吃掉**）。
  `overlays.py` 的 `QUIT_BINDINGS` 是模式；你的确认框测试要**真的把浮层挂起来按键**（不挂起来的测试会在真机
  上死掉而它自己是绿的）。
- **`g`/`G`/`d`/`y`/`n` 都是可靠键**（字母，量过；`unreliable_reason` 只点名 `ctrl+enter`/`shift+*`/`alt+*` 那几类）。
  键位表只有 `keys.py` 一份（`BINDINGS[LAYER_TASKS]` 加行），帮助正文跟着它走，所以标签要过
  **`tests/test_keymap.py:108`** 的宽度守卫（**不是** `test_theme.py`——`—`/`·`/`↓`/`↑` 是东亚歧义宽度，会硬红）；
  要给列加字形就照 #37 的守卫来：`len==1`、`cell_len==1`、EAW ∉ A/W/F。
  `page.BINDINGS == bindings_for(LAYER)` 的守卫别弄坏。
- **确认文案本身也要过宽度这一关**：浮层是 `width: auto`（最宽那行决定宽度），文案里放 `—`/`·` 在 CJK 字体下
  会把右边框挤掉。要断行就自己断，别指望终端。
- **颜色进 Rich span，不进 base style**（`Text(..., style="cyan")` 会静默发真彩色）；SGR 断言查**参数**；
  `CursorPage._redraw` 给光标整行 `stylize(theme.SELECTED)`，别写依赖那一行颜色的断言。
- 每个 `await` 之后先问 `self.is_running` 再碰 DOM；状态栏只有一个写入入口 `DidaApp._write_status`；推不动要
  留在重试队列里（`tests/test_sync_defer.py::test_a_failed_defer_push_stays_in_the_retry_queue` 已是这个口径）；
  别用 `len()` 量宽度；`apply_refresh(prune_*)` 默认 `False` 别传 `True`；#34 删掉的 `date_parser.py` 与那几个
  `tui/*.py` 不要复活（顺延**不经过任何日期解析**是你的 AC）。

- **假引擎要实现全 `Engine` 协议**（#46 新增的 `set_day_end`/`logical_day`）：`tests/test_fake_backend.py` 的
  `isinstance(FakeBackend, Engine)` 会红且报错不指向缺的方法；**优先委托真引擎**，别自己存一份日界。
- **断「什么都不该发生」用探针**（#46 的现成模式）：种一条只进缓存、谁都没通知界面的新任务，操作之后断言它
  **没有出现在屏幕上**、且屏幕逐字符与操作前相同——这比断言「某个内部方法被调了几次」结实得多，因为它是外部行为。

- **任务行是 #37 的接缝，签名是 `task_line(item, *, width, show_list_name=False)`（`width` 必填）**：你往行里加
  内容（完成标记/删除/顺延后的日期）时必须**自己**把它装进 `_line_width()` 给出的宽度里，否则 `#page-body` 的
  `nowrap` + ellipsis 会**再裁一次**，用户看到被裁两遍的行。窄屏丢弃顺序是用户的决定、别改：标签 → 标记 →
  截止 → 清单 → 最后才裁标题（实测丢弃点 48/41/36/24 列，标题 18 列才动；仓库有按格钉死的字面断言）。**别覆盖
  `CursorPage._row_width`**（它同时是规则线的宽度，覆盖过一次就短 2 格被测试抓住）——要按行算就学 `tasks.py` 的
  `_line_width()` 局部减 2 格。**`theme.clip()` 全树只许一处调用**（`tasks.py:125`，有测试钉「只有一个省略号」），
  别再添第二处裁剪；`app.refresh_view()` 是同步的，读屏幕前先 `await pilot.pause()`。

## 验收标准（工单原文，逐条）

- [ ] 任务列表页按 `d` 弹一次 y/n 确认，确认后删除当前任务并立即推送
- [ ] 确认文案明确告知删除在服务端不可恢复
- [ ] 取消确认时不发生任何写入
- [ ] 按 `g` 顺延到下一个逻辑日，其余字段不变
- [ ] 按 `G` 顺延一周（按逻辑日算）
- [ ] 边界配成 `04:00` 时，凌晨两点顺延落到下一个逻辑日
- [ ] 顺延不经过日期解析器，也不改动截止时间以外的字段

补四条：「取消确认时不发生任何写入」要在**两个**接缝上看得见（本地快照没变、transport 上零请求）；
删除的请求形状用 transport 接缝钉住（`DELETE /open/v1/project/{projectId}/task/{taskId}`，无请求体，`:468`/`:471–475`）；
「其余字段不变」要断**请求体里只多/只改了截止时间那一项**，不是断本地对象；详细页的 `d` 走同一条路径是 #43 落地
之后才接的线——本票只把任务列表页这条做对，**别提前去改详细页**。

## 范围

**别把 #53 的 bug 重新引进来**（见 `prompt-39.md`）：任务认领真 id 时，队列里那笔改动也要挪到真 id 上，推送
循环每轮重读队列。你拥有任务列表页的 `d`/`g`/`G`、删除与顺延的界面接线、`sync/schedule.py`（只在算术真不对时
动）。不做 #38/#39/#36/#42/#43/#44/#45。

## 交接（待补）

_#37 与 #42 的实测交接已并入上文各节（`task_line(width=)` 必填、两套排序并存、窄屏丢弃顺序、
`_row_width` 不许按行覆盖、`refresh_view()` 是同步的、`FormOverlay` 继承与 Ctrl+C 的 `priority=True`、
`Engine` 协议新增的两条方法）。#43 的交接若在本票开工之后才落地，编排者会另行送达。_
