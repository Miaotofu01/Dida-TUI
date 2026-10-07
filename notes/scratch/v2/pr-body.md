## Summary

**Draft — this is the v2 integration branch, and it is not finished.** It exists so the whole spec can be
implemented and reviewed on one branch instead of 17. Spec: #30. Tickets: #32–#48.

`dida` is pivoting from v1's three-pane 「今日执行台」(judged unusable) to a general client: **one column,
three page layers** — 清单列表页 → 任务列表页 → 任务详细页, with 今天 demoted to one of three built-in views.
The design decisions are already on `main` (`GLOSSARY.md`, `docs/adr/`); this branch is the code.

Only **#32** has landed so far. It changes no user-visible behaviour — it exists so the write-path tickets
don't each have to read one giant module, and so v1's engine-level conclusions aren't deleted along with
its interface.

```diff
 src/dida/
 ├── sync/
-│   ├── engine.py        1347 lines: 公开面 + 读 + 刷新 + 推送 + 已完成流 + 时间写 + 新建 + 优先级 + 子任务
+│   ├── engine.py         公开面与读
+│   ├── read.py                 三种读形状：清单索引 / 任务列表 / 单条详情
+│   ├── writes.py               写类型的唯一定义处：一个成员 + 一行 _BEHAVIOUR
+│   ├── views.py                (wave 4) 视图求值
+│   └── refresh.py  push.py  completed.py  schedule.py  create.py  priority.py  subtasks.py
 └── tui/
-    ├── app.py            812 lines: 组装 + 键位 + 三层动作 + 浮层 + 状态栏 + 同步泵 + 退出流
+    ├── app.py            薄 app：组装 / 状态栏唯一写入口 _write_status / 同步泵 / 退出流
+    ├── keys.py                 按层的单一键位表，真实绑定与 `?` 帮助同源（#48 接手）
+    ├── overlays.py             浮层：#42/#36 的行类型表单接在这里
+    └── pages/                  一栏三层：index.py → tasks.py → detail.py(#43 接手)
-    ├── keys.py  messages.py  layout.py
-    └── task_actions.py  form_actions.py  pane_actions.py
+src/dida/date_parser.py         已删（v2 不做自然语言日期输入）
```

The write vocabulary is the load-bearing part. Adding a kind of write used to mean editing nine production
sites — two enumerations, a conversion function, three dispatch sites, serialisation, a public preset and the
test fake. It is now one `WriteKind` member plus one `_BEHAVIOUR` row:

```diff
 WriteKind.COMPLETE  →  WriteBehaviour(local=MERGE, wire=COMPLETE_TASK, marks_completed=True)
```

`Store.enqueue`, `_exempt_fields`, `PushMixin._send` and `_local_effect` all *read* that table instead of
branching per member, and an AST guard fails if anyone enumerates members again. A write only needs a second
edit site if it targets a **new endpoint shape**, which is new external behaviour rather than duplicated
vocabulary — that is the case v2's `task/move` and `task/batch` will hit.

### Ticket progress

| | Ticket | State |
|---|---|---|
| 1 | #32 清路 | ✅ `4b91b46` |
| 2 | #41 刷新拿全 + 剪枝 · #33 引擎三种读形状 | ✅ `087a354` · `44d4b75` |
| 3 | #34 界面重写（清单列表页 ↔ 任务列表页） | ✅ `062042c` — **checkpoint** |
| — | *(unplanned)* 关窗竞态修复 + v1 文档转 v2 | ✅ `f08b32d` · folded into #34 |
| 4 | #35 #37 #42 #43 #46 #47 #48 | next |
| 5 | #36 #38 #39 #40 #44 #45 | blocked |

This PR will be marked ready only once every ticket has landed. Per the repo's tracker convention, **tickets
are resolved by a comment pointing at the commits, not closed by this PR** — closing them is the maintainer's.

## Evidence

```text
baseline (origin/main)       521 passed
after #32  (清路)             584 passed
after #41  (翻页 + 剪枝)      593 passed
after #33  (三种读形状)       617 passed
after id scrub               617 passed
after the teardown fix       622 passed
after #34  (界面重写)         407 passed in 31.4s
```

**The count goes DOWN at #34 and that is the point.** v1's 521 tests included 203 in files that drove the
Textual pilot; #34 deletes 267 of them (192 pilot-driven + 75 covering the date parser, which the spec voids
outright) and adds 53 v2 tests. The deletion is only safe because of the ordering the task graph enforces:
**#32 moved the engine-level conclusions out first** (53 assertions), so what #34 deletes is the interface
layer, not the knowledge. One silent loss was still caught during the rewrite — `paste_token`'s three credential
conclusions were held *only* by a file on the delete list — and rescued into `tests/test_credentials.py`.

Worth calling out because headless tests cannot show it: #34 also runs `build_app()` under a **real pty**, and
asserts the Linux driver **never writes the kitty CSI-u request** (`\x1b[>…u`). That is the driver-level half of
ADR-0006 — the reason Chinese IME input works at all. The automated half can only pin the constant; a real IME
pass remains manual, and is the maintainer's.

```text
uv run python -c "import dida.bootstrap"   → OK
uv run pytest tests/test_architecture.py   → 17 passed        (module boundaries, AST-enforced)
```

One piece of housekeeping worth calling out because it is not ticket work: the per-account inbox id observed
from the real account was written into `GLOSSARY.md` and ADR-0001 on `main`, and #33 propagated it into
`store.py`, the architecture doc and four test files. It is now a synthetic value. Two reasons — it is
account-derived, and **a real account value makes a poor test fixture**: those tests pin the *shape*
(`inbox` + digits), which any number satisfies, while a real value couples them to one account's data.

Nothing in this PR is user-visible yet, so there is no before/after screen to show. The checkpoint that
matters is #34: it is the first commit where `uv run dida` behaves differently, and the maintainer reviews it
by actually running the app before the remaining 13 tickets build on top of it.

## Merge Danger

**Door:** two-way for everything on this branch. Nothing has shipped, `main` is untouched, and every commit
here is additive or a pure refactor with the suite green on both sides. Rolling it back means deleting the
branch.

The one thing that is **not** two-way is #34 onward: it deletes `dida/date_parser.py` and all 19 v1 interface
test files. That is only safe because #32 moved the engine-level conclusions out first — the ordering is the
whole point of #32 being first in the task graph, and it is enforced by a GitHub blocking edge rather than by
memory. A reviewer who wants to check one thing on this branch should check that the rescued assertions are
genuinely independent of the interface: the new files are
[test_engine_writes.py](tests/test_engine_writes.py), `test_composition_root.py` and `test_deep_link.py`,
and none of them imports `dida.tui` or `DidaApp`.

**Blast radius:** refactor-only, confined to `src/dida/` internals. The `dida` command still starts and v1's
behaviour is unchanged, which the suite asserts end to end through the real Textual app.
