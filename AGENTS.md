# Dida TUI

一个终端里的滴答清单客户端：在终端里浏览全部清单与任务、直接新建和编辑，写操作立即推送到滴答清单。

技术栈：Python 3.12 + Textual。安装后以 `dida` 命令启动。

当前状态：**v2 已落地并进 `main`**（PR #50；spec 是 [issue #30](https://github.com/Miaotofu01/Dida-TUI/issues/30) 与后续的 [issue #61](https://github.com/Miaotofu01/Dida-TUI/issues/61)，两张都已关闭）。v1 的三栏「今日执行台」实测判定不可用，已整条删除；现在是通用客户端：一栏、三层页面（清单列表 → 任务列表 → 任务详细页），「今天」降级成三个内置视图之一。

- `uv run pytest` 全绿（**约 4 分钟**，近千条——跑得久不等于挂了），`uv run dida` 起的就是那一栏三层页面
  （清单列表页 → 任务列表页 → 任务详细页，`→` 进入、`←` 退回；`enter` / `esc` 在这三页上不再导航，
  详细页上 `esc` 只结束编辑＝保存）。键位表在 `src/dida/tui/keys.py`（应用内按 `h` 看到的就是它）。
- 设计决定落进 [GLOSSARY.md](GLOSSARY.md) 与 [docs/adr/](docs/adr/)——尤其看
  [ADR-0004](docs/adr/0004-terminal-client-not-today-console.md)（定位转变，含作废与保留清单）、
  [ADR-0002](docs/adr/0002-immediate-push-irreversible-complete.md)（「完成不可逆」已被实测推翻）、
  [ADR-0006](docs/adr/0006-disable-kitty-keyboard-protocol.md)（为什么关掉 kitty 协议推送——别顺手删）、
  [ADR-0008](docs/adr/0008-arrows-replace-enter-and-escape.md)（`→` / `←` 与 `h`，以及编辑态 `esc` 保存）。
- 模块边界、依赖方向与两个测试接缝见 [docs/architecture.md](docs/architecture.md)；面向使用者的入口是 [README.md](README.md)。

## 工作笔记（`notes/`）

编排的 brief、进度日志与派活模板都在仓库里的 **[`notes/`](notes/)**（索引：[`notes/README.md`](notes/README.md)）：

- [`notes/brief.md`](notes/brief.md)——交付决定、**按后果分流**的路由规则（开工前先读它，它决定这张票要不要独立分支）、**测试预算**，以及各轮实测出来的地雷。
- [`notes/progress.md`](notes/progress.md)——append-only 的进度日志。[`notes/implementer-template.md`](notes/implementer-template.md) / [`notes/merger-template.md`](notes/merger-template.md)——派活用。
- [`notes/codebase-map.md`](notes/codebase-map.md)、[`notes/terminal-input-evidence.md`](notes/terminal-input-evidence.md)、[`notes/openapi-dida365.md`](notes/openapi-dida365.md)——代码地图、终端实测、官方 API 文档。

仓库注释里写的 `notes/...` 指的就是这个目录——它现在在仓库里，grep 得到。

## 干活时的纪律

1. **等下属：结束轮次，让通知唤醒——不要 `sleep` 自旋。**
   派活前先武装 continuation：`create_goal` 按**预计轮次**留足 `max_goal_rounds`（量级 ≈ 下属数 × 2–4。结束轮次会被 goal 续跑立刻再唤醒并扣一轮，**不是**停到下属跑完——上一轮 25 个轮次里 19 个是空转再唤醒）；`fork` / `resume` 之后记得 `update_goal action=resume`。
   N 个下属**包成一个 workflow**（一个 job = 一次唤醒）；同时挂着的后台 job 保持个位数。查进度一次就够，不要每轮都去 `list_agents`。
   预计超过 20 分钟的任务，在 1/3 处设一个检查点。**例外**：返工贵的（大范围写盘、外部抓取）或会静默跑偏的（网络、限流、代理），检查点必留。

2. **一轮发多个工具调用。** 知道要读哪几份时，一轮发 3–5 个 `read` / `grep` / `glob`；不知道读哪份时先 `grep` 定位，再一轮把命中文件读掉；大文件带 `offset` / `limit`。
   **例外**：有依赖链的（先 `glob` 才知道路径）、参数不确定的（`bash`、`write`）老实串行。

3. **全量套件一票只跑一次**（收工前那次）。中途跑受影响的文件子集。**例外：复核者的变异验证要跑全量**——子集只能证明「有测试挡住了」，证不了「没有测试挡得住」；用子集会把「其实拦得住」误判成「守卫缺失」。复核者的模板见 [`notes/reviewer-template.md`](notes/reviewer-template.md)。

4. **派活时把基线 commit 写死在 prompt 里**（`git -C <worktree> rev-parse HEAD`）。

5. **合 PR 自动关票。** 一张 PR 解决的每张票，PR 正文里都要有 `Closes #<NN>`（完成 spec 时连 spec 父票一起）；**合并 PR 就是关闭动作**，GitHub 会关掉它们。不手工 `gh issue close`，也不留「等用户关」的旧规矩。还没被这张 PR 解决的票（后续票 / 仍在挡别的票的父票）**不要**写 `Closes`。关键字必须在合并前就在正文里——合并后再改不会补关。

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `Miaotofu01/Dida-TUI`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles; label strings are identical to the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, decisions in `docs/adr/`. See `docs/agents/domain.md`.
