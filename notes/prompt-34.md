# Prompt — ticket #34 (界面重写：清单列表页 ↔ 任务列表页)

> **This is the checkpoint ticket.** When it lands, the user takes over and runs `dida` himself before the
> remaining 13 tickets start. Its quality decides whether the rest of the plan survives contact with him.

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t34`, branch `ticket/34-ui-rewrite`. Fetch your acceptance criteria from the
tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/34 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/34/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md`; `wave-plan-and-seams.md` **in full** (it specifies
the file seams six later tickets depend on); `kitty-flag-import-order.md`; §10 of `codebase-map.md`;
`docs/adr/0004-terminal-client-not-today-console.md` and `docs/adr/0006-disable-kitty-keyboard-protocol.md`.

## The one instruction that outranks the rest

**Cut the file seams named in `wave-plan-and-seams.md`, exactly.** Seven tickets in the next wave run
concurrently, and every one of them needs to own a file rather than fight over one 800-line `DidaApp` class.
v1 is one class with a flat 20-entry `BINDINGS` list (`tui/app.py:205–240`) and every action as a method on
the app; that shape is what makes the next wave impossible. You may rename, but **do not collapse two seams
back together.** Specifically:

| Seam | File |
|---|---|
| List-index page (layer 1) | `dida/tui/pages/index.py` |
| Task-list page (layer 2) | `dida/tui/pages/tasks.py` |
| Detail page (layer 3, owned by #43 later — leave the seam) | `dida/tui/pages/detail.py` |
| **The single bindings table**, keyed by layer, plus the help text derived from it | `dida/tui/keys.py` |
| Shared form overlay (list name+colour vs. view filter conditions) | `dida/tui/overlays.py` |
| Thin app: composition, status bar, sync pump, quit flow | `dida/tui/app.py` |

`keys.py` is load-bearing beyond this ticket: #48 requires 「帮助里列出的键与真实绑定一致（跟着绑定表走，不是
手抄一份）」 and a guard test that certain keys never appear. So the table must be **one data structure** that
both the app's bindings and the `?` help read — not a hand-maintained help list beside a bindings list.

## Non-obvious traps, all verified — do not rediscover these

**`TEXTUAL_DISABLE_KITTY_KEY` (your AC 「启动时 kitty 键盘协议推送已关闭」).** It is currently set **nowhere**
in `src/`; ADR-0006 describes it as done. It is also a `Final[bool]` read **once at import time**
(`textual/constants.py:116`), so a `os.environ.setdefault(...)` inside `main()` is **too late**. Set it
**above `bootstrap.py`'s import block** — `bootstrap.py:32` (`from dida.tui.app import ...`) is the first
import in the process that pulls `textual`. Use `setdefault` so an explicit setting still wins. **And read the
warning in `kitty-flag-import-order.md` §3 before you write the acceptance test**: feeding a kitty CSI-u
sequence to the parser asserts the *opposite* of what it looks like, because the whole point is that the
terminal is never asked to send one. The honest assertion is a clean-subprocess check that the constant is
`True` after importing `dida.bootstrap`. Say in your report that a real-IME pass is still manual.

**`PUSH_TICK_SECONDS` (this will stop `dida` starting if you miss it).** `bootstrap.py:32` imports it *from the
TUI* and passes it to the engine at `:159`. You are deleting `tui/app.py`. **Move the constant before you
delete its home**, and keep `bootstrap.py` importable — `uv run python -c "import dida.bootstrap"` must pass.

**The status bar is modify, not create.** `format_status` already renders 「已同步 HH:MM · 待推送 N · 逻辑日 D」.
What's missing is highlighting when 待推送 is non-zero. Don't rewrite the wording — check `GLOSSARY.md` for it.

**`dida/tui/__init__.py` imports `DidaApp` at package top level.** Deleting/renaming `app.py` must handle it.

**`tests/test_architecture.py` guards you.** Three rules: a `SEVEN_MODULES` import whitelist; the TUI may only
import `dida.sync.engine` and `dida.tui.*` (AST-enforced); and a scanner self-check that must not be deleted.
New view types must be re-exported **through `dida.sync.engine`**. **You remove `dida.date_parser` from
`SEVEN_MODULES` when you delete the parser** — it is in that whitelist today (`:27`), and leaving it means a
red suite.

## The deletions, and the one thing you must not over-delete

Delete: the three-pane layout, `Tab` focus switching, the list overlay, the narrow-screen responsive
fallback, `dida/date_parser.py`, and the TUI test files that die with the rewrite.

**`date_parser` — delete against ticket #32's closing checklist**, published on
`gh api repos/Miaotofu01/Dida-TUI/issues/32/comments` and in-repo at
`docs/tickets/32-date-parser-consumers.md`. Every production call site and every covering test: **8 production
call sites, 106 test cases across 5 files** (measured empirically — more than the earlier estimate of 84/4, and
the difference matters, because a missed consumer means `dida` stops starting).

**⚠ The test deletion list: it is NOT 203 tests in 19 files, and the grep the ticket suggests is now wrong.**
Use **`notes/deletion-manifest-34.md`** — computed on the integration tip with `pytest --collect-only`, so it
accounts for parametrization (a `grep -c "def test_"` does not, and undercounted badly on my first attempt).
In short:

- **DELETE 17 files, 187 tests** (the ones that actually drive the Textual pilot). Plus
  `tests/test_date_parser.py`'s 75 when the parser goes. **262 total.**
- **DO NOT DELETE 4 files** that a naive `grep -lE "dida\.tui|DidaApp"` falsely accuses:
  `test_architecture.py` (17), `test_bootstrap.py` (2), and — **both created by #32 as rescued non-UI files** —
  `test_composition_root.py` (8) and `test_deep_link.py` (10). `test_deep_link.py` is where #33's corrected
  deep-link assertions live, and the spec **keeps** deep-link assembly.
- 187 is not a shortfall against the spec's 203: #32 deliberately left those 19 files untouched and *copied*
  the engine-level assertions into new files, so the old files still hold them and all the pilot-driven ones
  still go. The manifest reconciles the arithmetic line by line.

**If you find a TUI test file whose conclusions are *not* preserved anywhere and which is not obviously pure
rendering, stop and report it rather than deleting it.** Silent loss is the one failure this ticket can cause
that nothing downstream will catch.

## Preserve the pump-teardown guard (landed just before you)

A defect was fixed on the integration branch immediately before your ticket: the retry-queue pump kept no
timer handle and had no `on_unmount`, and `update_status`/`refresh_view` did `query_one(...)` on a screen that
could already be torn down, so a tick landing after an await raised `NoMatches: StatusBar` (~1 run in 35 — a
real shutdown bug, since production pumps at 1s). See `notes/progress.md`'s "pump teardown race" section.

**Your rewrite must keep both halves of that fix, because you are rewriting exactly the code that carries it:**

1. **Stop the pump's timer on unmount** — don't discard the handle.
2. **Tolerate a torn-down screen on any path that resumes after an `await`** before touching widgets. Scope it
   to *shutting down*: a missing status bar during normal operation is still a bug, so **do not** turn it into a
   blanket `NoMatches` swallow.
3. **Carry the regression test forward into a file that survives your deletions.** The fix pinned this in
   `tests/test_subtasks.py::test_a_toggle_that_lands_after_the_ui_is_gone_does_not_raise`, and that file is on
   your DELETE list. Note the subtlety it got right and keep it: **the write must still land** even though the
   screen is gone — the UI disappearing does not mean the user's action didn't happen. (That particular test
   covers v1's subtask *toggle*, which v2 makes read-only and you are deleting, so re-express the property for
   a path that survives: the sync pump.) If you cannot see how to keep it, say so in your report rather than
   dropping it silently — a silently lost regression guard is worse than a failing one.

## Testing your own work

The main seam is the in-memory `FakeBackend` + Textual's `run_test()` pilot, per `docs/architecture.md`.
Your acceptance criteria are mostly cross-layer key presses, which is exactly what that seam is for. Worth
explicit assertions:
- launch lands on the list-index page with the inbox as the top row;
- `enter` into a list, `esc` back, **cursor restored to the row you entered from**;
- **a background refresh does not move the cursor on either layer** (AC calls this out twice — test it on
  both layers);
- the three row kinds are distinguished by **prefix characters**, not colour alone;
- a NOTE-kind or non-write-permission list is marked and cannot be entered;
- empty list shows explicit empty-state text;
- the status bar highlights when pending count is non-zero.

Assert external behaviour only — no widget trees, no internal state objects, no whitespace or colour codes
in rendered strings. Use `tests/support.py::screen_text` (the repo's only compositor-touching helper) if you
need to read the screen.

## Note

`#34` is also where the **draft PR** opens (the orchestrator does that, after the merge). Your job ends with
a green suite and a merge of the integration tip into your branch. The user will then run
`uv run dida` against his own account — so **the app must actually start**, not merely pass tests. Prove
it: `uv run python -c "import dida.bootstrap"`, and state in your report that the real-terminal run is his
to do.
