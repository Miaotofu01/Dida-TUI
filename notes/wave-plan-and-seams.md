# Wave plan and the module seams it depends on

Written by the orchestrator. This is the **implementation-order contract** — every implementer and merger
subagent should read it. The tickets themselves live in the issue tracker (spec #30, tickets #32–#48);
this file adds only what the ticket bodies cannot know: the order they run in, and the **file seams** that
let them run concurrently without colliding.

> **Also read `cross-ticket-corrections.md` before designing anything.** Two independent exploration passes
> found places where a ticket's premise is factually wrong (a guard that forbids the only field #38 needs; a
> server-side filter #37 assumes but which does not exist; a 201-empty-body success that breaks #42's parser;
> a documented tag-create endpoint that contradicts #45's wording) and places where the ticket authors' counts
> were low (write types touch **9** production places, not 4; rescuable assertions ≈**66**, not ~50;
> date-parser blast radius **84** tests, not 75). Fact corrections live there; scope stays with the ticket.

## The task graph (from GitHub's native `blocked_by` edges, read this session)

```
#32 清路（写类型一处 + 大模块切开 + 救出界面测试里的引擎断言）
 ├─► #33 引擎三种读形状          #41 刷新拿全 + 剪枝
 │    └─► #34 界面重写：清单列表页 ↔ 任务列表页      ◄── ◄── ◄── **用户验收检查点**
 │         ├─► #35 内置视图        ├─► #37 任务行的样子与顺序   ├─► #43 任务详细页
 │         │    └─► #36 自定义视图  │    ├─► #38 完成↔取消完成    │    ├─► #44 截止时间
 │         │        建/改/删        │    ├─► #39 新建任务         │    └─► #45 挑选型字段
 │         │                        │    └─► #40 删除与顺延       │
 │         ├─► #42 清单建/改/删（还需 #32+#41）                  │
 │         ├─► #46 逻辑日立刻生效   ├─► #47 退出拦截（还需 #32）   └─► #48 键位守卫 + 分层帮助
```

Waves: **1** = #32 · **2** = #33, #41 · **3** = #34 · **(user checkpoint)** · **4** = #35, #37, #42, #43,
#46, #47, #48 · **5** = #36, #38, #39, #40, #44, #45.

## Why wave 4 needs seams cut in advance

Wave 4 runs **seven implementers concurrently**, and every one of them has a reason to edit the TUI module
that #34 is about to rewrite. In v1 that module is a single 812-line `DidaApp` class with a flat 20-entry
`BINDINGS` list (`tui/app.py:205–240`) and every action as a method on the app. Seven agents editing that
one class means seven-way conflicts and a merge queue that swallows the whole schedule.

**So #34 must cut these seams as part of its rewrite** — not for its own sake, but because six later tickets
depend on being able to own a file each. The split below is the orchestrator's contract; #34 may rename,
but it must not collapse two of these back together:

| Seam | File | Sole owner in wave 4 | What lives there |
|---|---|---|---|
| View evaluation (pure) | `dida/sync/views.py` | #35 | View definitions (built-in **and** custom, one path), filter evaluation, ordering. No network, no storage, no Textual. #36 extends the same file with custom-view persistence and the filter form's data model. |
| Row rendering (pure) | `dida/sync/rows.py` | #37 | Human-readable due text（「今天 18:00」/「昨天 09:00」/「3 天前」/「—」）, priority marks, repeat/reminder marks, the sort key, the completed-window cutoff. Pure functions, directly testable. |
| List-index page | `dida/tui/pages/index.py` | #34, extended by #35, #42 | Layer 1: the three row kinds with prefix characters, inbox pinned, project-group headings, counts, NOTE/permission markers, `n`/`e`/`d`, `enter`. |
| Task-list page | `dida/tui/pages/tasks.py` | #34, extended by #37, #38, #39, #40 | Layer 2: cursor, `space`, `n`, `d`, `g`/`G`, `enter`, empty state, struck-through completed rows sunk to the bottom. |
| Detail page | `dida/tui/pages/detail.py` | #43, extended by #44, #45 | Layer 3: field list, per-field edit, read-only subtasks/reminders/repeat. |
| Keymap + help data | `dida/tui/keys.py` | #48 | **The single bindings table**, keyed by layer, plus the help text derived from it. #48 requires "帮助里列出的键与真实绑定一致（跟着绑定表走，不是手抄一份）" — so the table must be one data structure that both the app's bindings and `?` read. #34 creates the seam; #48 owns the guard and the layered help. |
| Shared form overlay | `dida/tui/overlays.py` | #42, then #36 | The one overlay whose fields depend on the selected row's type (#42's list name+colour vs. #36's view filter conditions). Both tickets say "谁先落地谁把浮层壳子搭出来" — **#42 lands first, so #42 builds the shell and #36 reuses it.** |
| Thin app | `dida/tui/app.py` | #34, then #46, #47 | Composition, status bar (`format_status` — modify, don't create), the sync pump, the quit flow. #47 owns quit interception; #46 owns the logical-day re-read trigger. |

Two rules for wave 4, to be restated in every implementer prompt:

1. **Prefer your own seam.** If a change can live in your owned file, it must. Keep edits to
   `app.py`/`keys.py`/`overlays.py` as small and as late as possible.
2. **#42 lands before #36, and #43 lands before #44/#45** — those pairs share a file and are ordered by the
   graph anyway. Don't invert them.

## Cross-cutting things wave 4 will all want to touch (and who actually owns them)

- **`bootstrap.py`** — #34 moves `PUSH_TICK_SECONDS` out of `tui/app.py` and sets
  `TEXTUAL_DISABLE_KITTY_KEY` before the import block. **See `kitty-flag-import-order.md`: the flag is read
  once at import time, so `setdefault` inside `main()` is too late, and the obvious CSI-u feeding test
  asserts the opposite of what it looks like.** Nobody else edits `bootstrap.py`.
- **`config.py`** — #37 changes the completed-window default (24h → 168h); #46 adds the re-read path. Two
  tickets, one file, different concerns: #46 is blocked by #34 and #37 is not, so **whichever lands second
  merges around the other.** Keep edits surgical.
- **`storage/store.py`** — #41 (prune), #36 (views table), #37 (completed tasks in the snapshot). #41 lands
  in wave 2, well before the other two — good ordering, keep it.
- **`config.toml`** — views must **not** go here (ADR-0005: it is the file holding the token). #36 stores
  view definitions in the local sqlite store. If you find yourself adding a views section to the config,
  stop and re-read ADR-0005.

## What each wave must leave behind

- Wave 1 (#32): one place to add a write type; the two big modules split; the ~55 non-pilot assertions from
  the TUI test files rescued; a published checklist of `date_parser`'s consumers. Baseline **521 tests**,
  and the count must not drop.
- Wave 2 (#33, #41): three read shapes through the engine; the inbox classified by the **server-returned**
  project id, never the literal `inbox`; project-index paging; prune.
- Wave 3 (#34): **the checkpoint.** `uv run dida` must start, land on the list-index page with inbox on top,
  and navigate into a list and back with the cursor restored. v1's three panes, Tab focus, list overlay,
  narrow-screen fallback and the 203 TUI-file tests are gone; `date_parser` is gone against #32's checklist.
  A **draft PR** opens here (delivery decision 2).
- Wave 4: the seven tickets above, each owning its seam.
- Wave 5: the six leaves of the graph.

## Standing rules (repeat these in every prompt)

- TDD via the `tdd` skill, at the two agreed seams, asserting external behaviour only.
- Never touch `~/.config/dida-tui/config.toml`, never print the token, never run a write experiment against
  the live API (it is the user's real account), never `env`/`set -x`/`bash -x`.
- Never merge to `feat/v2-terminal-client` yourself — a merger subagent does it.
- Tickets get a **comment, not a close** (delivery decision 3). Only the user closes issues, and only after
  the branch reaches `main`.
