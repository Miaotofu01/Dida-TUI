# notes/ —— 工作笔记（agent 的工作记忆）

**这个目录是 agent 的工作记忆，不是用户手册**（用户手册是 [README.md](../README.md)）。它原本在仓库外
（`/home/tofu/dida-v2-worktrees/notes/`），2026-10 搬进仓库——因为仓库里的注释与测试一直在引用它，
而没有人 grep 得到。

## 从哪儿开始

| 你要做什么 | 先读 |
|---|---|
| 接一张票、跑一轮编排 | [`brief.md`](brief.md)——交付决定、**按后果分流**的路由规则、**测试预算**、各轮实测出来的地雷 |
| 看已经落地了什么 | [`progress.md`](progress.md)——append-only 的进度日志 |
| 派活给 subagent | [`implementer-template.md`](implementer-template.md) / [`merger-template.md`](merger-template.md) |
| 找某个文件在哪、某个形状长什么样 | [`codebase-map.md`](codebase-map.md) |
| 终端的键到底送不送得上来 | [`terminal-input-evidence.md`](terminal-input-evidence.md)（ADR-0006 / ADR-0008 的实测依据） |
| 官方 API 的形状 | [`openapi-dida365.md`](openapi-dida365.md)（原文）、[`api-shapes.md`](api-shapes.md)（按端点提取） |
| 哪张票的前提是错的 | [`cross-ticket-corrections.md`](cross-ticket-corrections.md) |

## 子目录

- `handoffs/`——历次会话的交接（写给下一个 agent 的）。最新的一份是入口。
- `scratch/`——会话碎料：评论草稿、工单快照、临时探针、README 草稿。留着是为了可追溯，**不是必读**。

## 一条规矩

仓库里注释写的 `notes/...` 指的就是这个目录——**它现在在仓库里，grep 得到**。
（历史上有过 `Dida-TUI-notes/...` 这种拼法，那是它还在仓库外的时候；见到就改成 `notes/...`。）
