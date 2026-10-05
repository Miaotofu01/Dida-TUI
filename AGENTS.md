# Dida TUI

一个终端里的滴答清单客户端。它不复制滴答清单的全部能力：它把「今天该做什么」这一屏做到极致，其余交给官方客户端。

技术栈：Python 3.12 + Textual。安装后以 `dida` 命令启动。

当前状态：**v1 已实现**——`uv run pytest` 全绿（500+ 条，约一分钟），`uv run dida` 起的就是
完整的今日执行台：三栏、键位齐、写操作立即推送、SQLite 缓存与重试队列都在。v1 的代码在
`feat/v1-today-console` 分支上（PR #28 尚未合并），`main` 目前还只有文档。
面向使用者的入口是 [README.md](README.md)；模块边界、依赖方向与两个测试接缝见
[docs/architecture.md](docs/architecture.md)，v1 spec 在
[issue #1](https://github.com/Miaotofu01/Dida-TUI/issues/1)。

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues in `Miaotofu01/Dida-TUI`, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles; label strings are identical to the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` at the repo root, decisions in `docs/adr/`. See `docs/agents/domain.md`.
