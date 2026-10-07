# Prompt — ticket #37 (任务行的样子与顺序)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t37`, branch `ticket/37-task-rows-and-order`. Fetch your acceptance criteria
from the tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/37 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/37/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md` (the `#37` entry — your ticket's premise is partly
wrong), `wave-plan-and-seams.md` (you own the row-rendering seam), and §4 + §7 of `codebase-map.md`.

## The one thing in your ticket that is factually wrong

Your ticket says 「拉取要**同时**按完成时间窗口与完成状态过滤」. **`POST /open/v1/task/completed` has no
`status` parameter at all** — its only fields are `projectIds`, `startDate`, `endDate`
(`notes/openapi-dida365.md:642–646`), and its dates filter **`completedTime`** (`:645–646`). The client's
request body agrees (`api/client.py:94–100`).

So the dual filter is achievable **only** as: **server-side completion-time window + client-side filter on the
`status` returned in each payload object** (`:688` shows `status` in the response).

**Do not write a test asserting `status` in the request body.** It would pin a field the server never receives
and pass against an implementation that does nothing. Your acceptance criterion 「已取消完成的任务不会被捞回来」
is still meetable — prove it the client-side way, because the reason it matters is real: **un-completing a task
does not clear its `completedTime`**, so a window-only filter resurrects it.

## What is already done (don't re-do it)

Storing completed tasks is **already implemented** (`store.py:795`'s `refresh_completed`; `_snapshot:563–564`
already keys on `status`). Your real gaps are:
1. the completed-window default, which is 24h in **two** places — `config.py:101` **and** `sync/engine.py:132`
   — and the spec wants **168h**. Missing the second one is the easy mistake.
2. the missing client-side status filter.

## Ordering: the tiebreak is missing and a *surviving* test pins the old order

`sync/view.py`'s `_by_due` (has no priority tiebreak; #33 renamed it to the public `by_due`) is pinned by
**non-UI** tests that #34 does *not* delete: `tests/test_view_models.py:79,149` and
`tests/test_engine_view.py:34–40`. So adding the priority tiebreak means **editing those tests** — which is
correct and expected here, not a violation. Do it deliberately, state it in your report, and don't let a
merger mistake it for weakening.

Target order: **due ascending → priority descending → undated after dated → completed always last.** The
client re-sorts unconditionally; the server's `sortOrder` is ignored (that is a deliberate spec trade-off, not
an oversight — the spec's 「一个已知的、故意的取舍」 section).

## Narrow terminals: the discard order is part of your ticket

The user found this on the visual prototype at 30 columns: the **title** got cut to four characters while the
due time kept its full width. That is backwards — the title is the content, the due time is secondary. See
your ticket's comment for the measured example.

When width runs out, discard in this order: **tags → repeat/reminder marks → due time → and only then clip the
title** (by *cells*, with `…`; `rich.cells.cell_len`/`set_cell_size`, never `len()` — his locale is
`zh_CN.UTF-8` and a CJK glyph is 2 cells).

**One row stays one task.** Narrow terminals are handled by dropping columns, **not** by wrapping — wrapping is
the **detail page's** privilege, by an explicit rule from the user (「进入任务详细页的内容需要自动换行，其他的部分
保留截断」, recorded on #43 and in ADR-0007). Wrapping the list would break both scannability and the cursor
arithmetic, which assumes one row = one screen line.

**Also watch the `☰` trap:** rich measures it as 2 cells while it is East-Asian-Neutral, so the moment you
introduce a column-aligned due/tag field, every such row is laid out one cell wider than terminals draw it.

## Row rendering — three traps

- **All-day tasks.** An all-day due is a **date marker** (that day at 00:00), not an instant. Rendering it by
  instant makes a 「今天」 all-day task read as yesterday once the boundary is `04:00`. Render it as 「今天」,
  never 「今天 00:00」.
- **「没有截止时间」 and 「今天截止」 must be visually distinct** at a glance, and a missing date reads 「—」.
- **The list name.** In a *list* view the row must **not** repeat the list name (one list name a hundred times
  is noise); in a *view* the row **must** show it (a view is not a container, so the name is real information).
  #33's read model already distinguishes the two cases — use it rather than re-deriving it.

**Colour:** overdue is red. Assert it through `tests/support.py::screen_styled_text` (the repo's helper for
"which line carries this style"), never by asserting a hex code — `tests/test_architecture.py` has a guard
against hardcoded colours in the TUI and the repo's CSS uses `ansi_*` only.

## Cursor survives refresh

Your AC 「后台刷新后光标仍在原来那一条上」 is a real cross-layer behaviour, not a formality. A refresh that
rewrites the rows must not reset the cursor. Test it at the pilot seam: move the cursor, trigger a refresh,
assert the cursor is still on the same task.

## Scope discipline

You own the **row rendering + ordering** seam and the completed-window default. Do **not** implement view
evaluation (#35 owns built-in views), the detail page (#43), or the un-complete write path (#38). Do not touch
`tui/index`-side list rows. Prefer your own file; keep edits to `app.py` small — seven tickets are running
concurrently and the seam plan exists to keep them apart.
