# Handoff — dida TUI v2：从工单到实现

> 交接时间：`/to-tickets` 跑完、17 张工单发布之后。工作目录 `/home/tofu/我的项目/Tips`。
> **这份文件只写「别处没有的东西」。** 需求看 issue #30，工单看 #32–#48，设计理由看 `docs/adr/`，术语看 `GLOSSARY.md`，模块边界与两个测试接缝看 `docs/architecture.md`——不要在这里重复它们。
> 它取代 `/tmp/dida-tui-v2-handoff.md` 里的「立即要做的三件事」与「仓库与环境状态」两节；那份文件其余的部分（终端按键调查、写实验脚本的坑）本次仍然有效，末尾有指引。

## 一句话状态

v2 的 spec 已发布（#30），域名文档**已在 `main` 上**（PR #31 已合），**17 张工单已发布**（#32–#48，25 条原生 GitHub 阻塞边，全部 `ready-for-agent`）。**v2 一行代码还没写。** 下一步是领 #32。

## 立即要做的

1. **从 `origin/main` 起步。** 本地这个 checkout 停在 `docs/v2-domain-model`（已合并，内容与 `origin/main` 逐字节相同），本地 `main` 落后 8 个提交。动手前 `git checkout main && git pull`，或直接从 `origin/main` 开分支。
2. **领 #32**（清路）。它是当前唯一的前沿：`blocked_by == 0` 且未 assign。它一落地，#33 与 #41 就能并行。
3. 实现阶段用 `tdd`（见文末 Suggested skills）。

## 仓库与环境状态（不在任何 artifact 里）

- `origin/main` = `4548577`。**它的树与 v2 文档分支 `1880791` 逐字节相同**——`git diff --stat 1880791 origin/main` 是空的。原因是 PR #49 把 `feat/v1-today-console` 又合并了一次，而那三个文档提交在那条分支上是**同内容不同 SHA 的副本**（`d3833a7`/`dd2941c`/`4ebea3d`）。所以 #49 事实上一个文件都没改，不用去找它带来的新东西。
- `feat/v1-today-console` 本地与远端都还在，已被合并两次，可以删。
- `uv run pytest --collect-only -q` → **521 条**（本次复核）。
- 测试目录：**34 个 `test_*.py`，其中 20 个 import `dida.tui` 或 `DidaApp`**——要作废的 203 条界面测试就在这 20 个文件里。按条数的 318 / 203 拆分来自上一份 handoff，本次只复核了总数与文件数。
- 17 张工单**全部没有 assignee**。前端查询必须自己过滤 PR：`gh api "repos/Miaotofu01/Dida-TUI/issues?state=open&labels=ready-for-agent" --jq '.[] | select(.pull_request == null) | ...'`——GitHub 的 issues 端点会把 PR 混进来。
- `gh` 已认证为 `Miaotofu01`，scopes 含 `repo`。

## `gh` 在这个仓库里有一个硬故障（会立刻卡住你）

**`gh issue view <n>` 在这个仓库里一律失败**：

```
GraphQL: Projects (classic) is being deprecated in favor of the new Projects experience ...
(repository.issue.projectCards)
```

`gh issue list` 与 `gh pr view` 都正常，只有单张 issue 的 `view` 走不通。用 REST 顶替：

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/<n>                      # 正文、标签、状态
gh api repos/Miaotofu01/Dida-TUI/issues/<n>/comments             # 评论
gh api repos/Miaotofu01/Dida-TUI/issues/<n> --jq '.id'           # 数据库 id（阻塞边要用它，不是 #number）
```

改正文要把 body 从文件读进去，`-f` 不认 `@`，得用 `-F`：

```bash
gh api --method PATCH repos/Miaotofu01/Dida-TUI/issues/<n> -F body=@/tmp/body.md
```

原生阻塞边（本仓库确实支持，`issue_dependencies_summary` 有数）：

```bash
gh api --method POST repos/Miaotofu01/Dida-TUI/issues/<child>/dependencies/blocked_by \
  -F issue_id=<blocker 的数据库 id>
```

## 工单是怎么排出来的（决定的原因，别处没有）

- **编号 = 出版顺序 = 拓扑序，不是起草顺序。** 起草时「刷新拿全」是 16 号、「清单 CRUD」是 10 号，但后者阻塞于前者，所以出版时把刷新拿全提到 **#41**、清单 CRUD 落到 **#42**。你要对着我给用户的提案里的编号找工单，记得有这一位移。
- **为什么 #32 挡着几乎所有写工单**：它把引擎的写类型与存储的变更类型（四个成员一一对应）合成一处，此后新增一种写只改一个地方；不然每张写工单都要在四处同步。
- **为什么 #34 阻塞于 #32**：v1 那 203 条界面测试里混着约 50 条其实是引擎/API 级断言，#32 负责先把它们搬进不依赖界面的测试文件，#34 才去删那 203 个文件——顺序反了就**连结论一起丢**。
- **为什么 #42 阻塞于 #41**：落库只 upsert、从不剪枝，删掉的清单刷一次就回来了。没有剪枝，清单删除做不对。
- **一条刻意留的软边**：story 80（任务详细页里也能删任务）——**行为归 #40、详细页那个 `d` 的接线归 #43**。这是刻意的，不是遗漏；别把它当成缺失的阻塞边去「修」。
- 起草脚本还在 `/tmp/to-tickets/`：`tickets.py` 是 17 张的全部正文与阻塞边，`publish.py` 是出版器（含拓扑重编号）。`/tmp` 可能被系统清掉；要重发或审计就靠它。

## 上一次接口审计查出、但工单正文里没写的坑

写代码时一定会撞上，逐条都核过了：

- **`bootstrap` 从 TUI 里 import 常量再喂给引擎**：`bootstrap.py:32` 拿 `PUSH_TICK_SECONDS`、`:159` 把它交给引擎。#34 删 `tui/app.py` 之前必须先把那个常量搬走，否则 `dida` 起不来。
- **循环导入护栏，别拆**：`store.py:45` 在**模块顶层** import `dida.sync.view`；`engine → storage` 是**函数内延迟 import**（`engine.py:670/1040/1050`，理由写在 `engine.py:1036` 的注释里）。把延迟 import 提到顶层会炸导入顺序；重命名或删除 `dida.sync.view` 会连带炸 `storage`、`testing`、`bootstrap`。
- **存储的 sqlite 连接线程亲和**：没开 `check_same_thread=False`、也没加锁。刷新必须是事件循环上的协程，**不许**丢进 `threading.Thread`。
- **`SyncEngine(source=None)` 是有意的降级模式**，不是半成品：空视图、`subtasks()` 返回 `()`、写路径抛 `RuntimeError`。有些测试就是建在这个模式上（例如 `tests/test_app_shell.py`），别把它「修」成硬报错。
- **`FakeBackend` 必须继续满足 `Engine` 协议**（`@runtime_checkable`）：`DidaApp(engine)` 是唯一的注入点，换个名字或换个构造签名会同时炸掉 20 个测试文件。
- **`tests/` 既没有 `__init__.py` 也没有 `conftest.py`**，所以测试里是裸 `from support import screen_text`。新增共享测试文件要跟上这个约定（维护 `sys.path` 的机制不在仓库里）。
- **`tests/support.py` 的 `screen_text` 是全仓库唯一碰 Textual 合成器的地方。** 想在测试里看屏幕就复用它，不要再开第二个。
- **测试是 `asyncio_mode="auto"`**；TUI 测试一律 `async with app.run_test(size=(...)) as pilot:` + `await pilot.press(...)`（**105 处**）。
- **`test_architecture.py` 的三条规则**，改模块边界前先读它：① `SEVEN_MODULES` 白名单里的七个模块必须可导入——**白名单里有 `dida.date_parser`，#34 删它时要一起改，否则测试红**；② TUI 只许 import `dida.sync.engine` 与 `dida.tui` 两支，**`dida.sync.view` 不在表里**——新视图类型必须从引擎再导出；③ 扫描器自检（别删，它证明扫描真的看到了东西）。
- **状态栏是改不是建**：`format_status` 已经渲染「已同步 HH:MM · 待推送 N · 逻辑日 D」，#34 缺的只是「待推送非零时高亮」。
- **v1 里看着像有用、其实没有生产调用者的三处**，可以从它们下手做 #32/#34 的减法：`ListPane.selected_list_id`（只有测试读它）、`DidaApiClient.list_tags`、`Store.list_records`。
- `dida/tui/__init__.py` 在包顶层就 import `DidaApp`，删 `app.py` 要连它一起处理。
- 完成时间窗口的默认值现在写死是 **24 小时**（`config.py:101`），spec 要的是 7 天 → #37。

## 用户的 remit 与协作方式（承接上一份 handoff，仍然有效）

- 他的原话是「**你只产出 spec**」；这一轮把工单也产出了。**他还没授权写实现**——动代码前确认一句。
- **他会顶回你的推荐，而且顶得有道理。** 已知的两次：清单分三段显示 vs 平铺一列（他选平铺）、照服务端 `sortOrder` 排 vs 客户端统一重排（他选重排）。给选项时把**代价**说清楚，不要只把推荐塞给他。
- **他会主动问「还有没有什么不全的」。** 保持这个习惯：**事实自己查（派 subagent），决定交给他。** 本次的实例：他选了「清路的 01+02 合成一张」「自定义视图的建/改/删合成一张」，其余粒度照原样；阻塞边全认；#31 他自己合。全程没有让他做内部检查点式的决定。
- **#27 / #29 / #30 他没有让我碰，就保持没碰。** #27 的四条仍然要做的已折进工单（1→#46、2→#33、3→#37+#43、5→#34、6→#47、设计债→#32）；#29 整条讲的是 v1 三栏布局，需求由 #34 正面满足，要关它得他点头。

## 敏感信息

- 用户的个人 API token 在 `~/.config/dida-tui/config.toml`（0600）。**不要在聊天里索取、不要打印、不要 `cat` 那个文件。** 需要调 API 时写脚本读它、只输出结论。
- 运行任何会回显环境或变量的命令（`bash -x`、`set -x`、`env`）之前**先想一遍会不会把 token 打出来**。
- 本次没有把 token 写进任何 issue、ADR、提交或 PR；用户的清单名与任务标题也没有写进任何 artifact。

## 如果你要重跑写实验

- 可复用的脚本在 `/tmp/dida-api/`：`probe_readonly.py`（只发 GET 与无副作用的 `task/filter`）、`probe_write.py`（写实验）、`openapi.md`（官方 OpenAPI 副本，与线上逐字节一致，行号可直接引用）。`/tmp/ctx-research/` 是终端按键与 Textual 的调查脚本。
- **`probe_write.py` 里有个 `made[-1]` 索引 bug**：搬运那一步覆盖了另一条临时任务的清理记录，会漏删一条。重跑前先修，或者跑完按标题前缀 `ZZ-实验-可删-` 扫一遍。
- **那个脚本写的是用户真实账号的数据。** 上次是他明确授权后才跑的，**别默认可以再跑**。

## Suggested skills

按顺序：

1. **`tdd`** —— 实现阶段，现在就用。**在** skill 目录里，正常调 `skill` 工具。
2. **`code-review`** —— 每张（或每批）工单落地后按 Standards + Spec 两轴审。**在** skill 目录里。
3. **`domain-modeling`** —— 实现过程中模型又变了（写 GLOSSARY / ADR）时。
4. **`handoff`** —— 下一次交接。

**磁盘上有、但不在 skill 目录里的**：`/home/tofu/.dsh/skills/` 符号链接到 `/home/tofu/repos/matt-skills/skills/`，里面有 `to-spec`、`to-tickets`、`implement-spec`。**`skill` 工具找不到某个名字时，直接 read 它的 `SKILL.md`**，不要因为工具报错就以为流程断了——这次 `/to-tickets` 就是这么读的。
