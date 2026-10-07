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

## 本机的工作笔记（不在仓库里）

编排的 brief、进度日志与派活模板都在 **`/home/tofu/dida-v2-worktrees/notes/`**：

- `brief.md`——交付决定、**按后果分流**的路由规则（开工前先读它，它决定这张票要不要独立分支），以及各轮实测出来的地雷。
- `progress.md`——append-only 的进度日志。`implementer-template.md` / `merger-template.md`——派活用。
- `codebase-map.md`、`terminal-input-evidence.md`、`openapi-dida365.md`——代码地图、终端实测、官方 API 文档。

**仓库注释里写的 `notes/...`（以及 `Dida-TUI-notes/...` 这种拼法）指的都是这个目录**：它不在仓库里，所以 grep 不到，要读就按上面的绝对路径直接打开。

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `Miaotofu01/Dida-TUI`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles; label strings are identical to the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, decisions in `docs/adr/`. See `docs/agents/domain.md`.
