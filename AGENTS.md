# Dida TUI

一个终端里的滴答清单客户端：在终端里浏览全部清单与任务、直接新建和编辑，写操作立即推送到滴答清单。

技术栈：Python 3.12 + Textual。安装后以 `dida` 命令启动。

当前状态：**正在从 v1 转向 v2。** v1 已实现，跑在 `feat/v1-today-console` 分支上（PR #28 尚未合并）——它是「今日执行台」：三栏、只显示与今天有关的任务、清单栏是装饰性的。真实使用后判定不可用，v2 改成通用客户端：一栏、三层页面（清单列表 → 任务列表 → 任务详细页），「今天」降级成内置视图之一。**v2 spec 落地前，不要按 v1 的定位去扩展代码。**

- 现状（#34 落地后）：`uv run pytest` 全绿（400+ 条，约半分钟），`uv run dida` 起的是 **v2 的一栏三层页面**
  （清单列表页 → 任务列表页 → 任务详细页，`→` 进入、`←` 退回；`enter` / `esc` 在这三页上不再导航，
  详细页上 `esc` 只结束编辑＝保存）。v1 的三栏布局、`Tab` 焦点切换、
  清单浮层、窄屏响应式降级与日期解析器都已删除；每层剩下的能力按 #35–#48 补齐，键位表在
  `src/dida/tui/keys.py`（应用内按 `h` 看到的就是它）。
- v2 的设计决定已经落进 [GLOSSARY.md](GLOSSARY.md) 与 [docs/adr/](docs/adr/)——尤其看
  [ADR-0004](docs/adr/0004-terminal-client-not-today-console.md)（定位转变，含作废与保留清单）、
  [ADR-0002](docs/adr/0002-immediate-push-irreversible-complete.md)（「完成不可逆」已被实测推翻）、
  [ADR-0006](docs/adr/0006-disable-kitty-keyboard-protocol.md)（为什么关掉 kitty 协议推送——别顺手删）。
- v1 spec 在 [issue #1](https://github.com/Miaotofu01/Dida-TUI/issues/1)；v2 spec 会另起一份并取代它。
- 模块边界、依赖方向与两个测试接缝见 [docs/architecture.md](docs/architecture.md)；面向使用者的入口是 [README.md](README.md)。

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `Miaotofu01/Dida-TUI`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles; label strings are identical to the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, decisions in `docs/adr/`. See `docs/agents/domain.md`.
