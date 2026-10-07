# Prompt — ticket #35 (内置视图：今天 / 最近七天 / 所有)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t35`, branch `ticket/35-builtin-views`. Fetch your acceptance criteria from the
tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/35 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/35/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`, `wave-plan-and-seams.md` (**you own the view-evaluation
seam — `#36` extends the same file and must not be forced to rewrite it**), and §3 + §4 of `codebase-map.md`.

## What already exists (do not rebuild it)

Ticket **#33** landed the read model and already implemented **the identity-and-membership half** of the
built-in views, so their counts are honest: `sync/read.py` has `builtin_view_rows`, `_in_builtin_view`,
`_view_members` and the three fixed definitions (今天 = overdue ∪ due today, 最近七天, 所有). It deliberately
stopped there.

**So your job is the evaluation half that #33 left:** 逾期置顶标红, the full ordering, and the per-row list name
in view mode. Build it as **pure functions** — 「给定视图定义、缓存与逻辑日，输出确定的、排好序的任务列表」 —
that touch neither network nor storage, because that is what makes them directly testable and is an explicit AC.

## The one definition that is not a clean interval

**「今天」 = 逾期 ∪ 截止时间落在当前逻辑日内的任务.** It is a union, not a range: overdue tasks
「置顶并标红」, and the today-due ones follow. A naive implementation as a single due-date window silently drops
every overdue task — which is the single most likely way to get this ticket wrong.

「最近七天」 = due within the **seven logical days starting today**. 「所有」 = every unfinished task — the view
whose absence made v1 unusable.

## Every date judgement goes through the logical day, not the calendar day

Your AC calls out the `04:00` boundary explicitly: at 02:00 with the boundary set to `04:00`, a task due
**yesterday 23:00** must be classified as **today**, not overdue. That is the whole point of the logical day
(`logical_day.py`, and `GLOSSARY.md`'s terms).

⚠ **All-day tasks are the trap here.** An all-day due is a **date marker** (that day at 00:00), not an instant.
Compare it as an instant and an all-day task due 「今天」 counts as yesterday once the boundary is `04:00`. #33's
read model and `view.py`'s helpers already know about this — reuse the existing handling rather than writing a
second date comparison.

## The list name in view mode

In a **view**, every row must show which list the task belongs to — a view is not a container, so the repeated
name is real information. In a **list** view it must **not** be shown. #33's read model already distinguishes
the two cases; use it. (This overlaps ticket #37, which owns row *rendering*; #35 owns view *evaluation*.
Coordinate through the seam rather than both editing the same renderer — if you need a row field that #37 also
touches, add it to the read model / row data and say so in your report.)

## Built-in and custom views share ONE evaluation path

Your AC: 「内置视图与自定义视图共用同一条求值路径」. The three built-ins are simply three hard-coded view
definitions fed through the same evaluator that #36's user-created views will use. **Do not special-case the
built-ins** — if the evaluator only works for hard-coded definitions, #36 cannot extend it, and #36 is blocked
on you precisely because it reuses this.

#33 already left the seam for the other direction: `ViewReader.views()` returns `ViewRow(id, name, task_ids)` and
**has no production producer yet** — #36 supplies the storage table. Don't implement that table; just make your
evaluator accept a definition rather than assuming a built-in.

## Verifying

Pure functions get tested directly, no UI needed — that is the point. Cover at minimum: overdue ∪ today as a
union; the `04:00` boundary case from your AC; the all-day case; the seven-day window; 「所有」 = all unfinished;
and determinism/ordering for a fixed (definition, cache, logical day) input.

Then, at the pilot seam, assert the cross-layer behaviours your AC names: the three built-ins appear as rows in
the list-index page and are distinguishable from real lists by **prefix character, not colour alone**; entering
one shows the filtered tasks; and rows in a view show their list name.

## Scope discipline

You own **view evaluation** (pure) plus the three view rows' wiring in the list-index page. Do **not** implement
custom-view CRUD or its storage (#36), the overlay (#42/#36), the detail page (#43), or row *rendering* (#37).
Keep edits to `app.py` small — seven tickets run concurrently and the seam plan exists to keep them apart.
