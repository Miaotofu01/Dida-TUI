# Dida TUI

一个终端里的滴答清单客户端。它不复制滴答清单的全部能力：它把「今天该做什么」这一屏做到极致，其余交给官方客户端。

技术栈：Python 3.12 + Textual。安装后以 `dida` 命令启动。

当前状态：**spec 阶段**，还没有代码。v1 spec 在 [issue #1](https://github.com/Miaotofu01/Dida-TUI/issues/1)。

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `Miaotofu01/Dida-TUI`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles; label strings are identical to the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, decisions in `docs/adr/`. See `docs/agents/domain.md`.
