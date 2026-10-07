# #34's deletion manifest — exact, computed on the integration tip `7e9c38a`

Ticket #34 says 「v1 的 203 条界面测试已删除」 and tells you to recompute the list with
`grep -lE "dida\.tui|DidaApp" tests/test_*.py`. **That grep is now wrong on both ends**, because #32 added two
rescued non-UI test files that touch TUI-adjacent modules and therefore match it. Use this file instead.

Reproduce any line with `uv run pytest --collect-only -q <file>` (counts include parametrization, which a
`grep -c "def test_"` does not — my first attempt undercounted badly).

## DELETE — 17 files, **187 tests** (these drive the Textual pilot; the rewrite replaces them)

| Tests | File |
|---:|---|
| 22 | `tests/test_reschedule.py` |
| 21 | `tests/test_priority_filter.py` |
| 20 | `tests/test_sync_session.py` |
| 17 | `tests/test_app_responsive.py` |
| 17 | `tests/test_escape.py` |
| 17 | `tests/test_subtasks.py` |
| 16 | `tests/test_quick_add.py` |
| 15 | `tests/test_app_view.py` |
| 10 | `tests/test_delete.py` |
| 8 | `tests/test_app_completed.py` |
| 8 | `tests/test_complete.py` |
| 4 | `tests/test_detail_description.py` |
| 3 | `tests/test_app_defer.py` |
| 3 | `tests/test_overdue_row.py` |
| 2 | `tests/test_app_shell.py` |
| 2 | `tests/test_key_help.py` |
| 2 | `tests/test_space_complete.py` |
| **187** | **total** |

Plus, when you delete the parser: **`tests/test_date_parser.py` — 75 tests.** Total removed by #34: **262**.

## DO NOT DELETE — 4 files that a naive grep will falsely accuse

| Tests | File | Why it survives |
|---:|---|---|
| 17 | `tests/test_architecture.py` | Pure AST module-boundary guards. Edit its `SEVEN_MODULES` list (drop `dida.date_parser`), but **keep the file**. |
| 2 | `tests/test_bootstrap.py` | The `dida` entry point. This is what catches a broken console script. |
| 8 | `tests/test_composition_root.py` | **Created by #32.** Rescued composition-root assertions (config → engine wiring, the entry-point guard). |
| 10 | `tests/test_deep_link.py` | **Created by #32**, extended by #33. Deep-link assembly, which the spec explicitly **keeps** (深链拼装 is on the 可以原样保留 list). #33's reconciliation of the inbox-substring bug lives here — and `test_escape.py` (which dies) is where the old, wrong behaviour was pinned. |

## Why the number is 187 and not 203

The spec's 203 = every test in every file matching that grep, at baseline. Reconcile it like this:

- baseline grep-matching set: **19 files / 203 tests**;
- **#32 deliberately did not modify those 19 files.** It *copied* the engine-level assertions into new
  non-UI files instead — 「a duplicated assertion beats a lost one」. So the old files still contain them and
  #34 still deletes that whole set; the rescued copies are what survive.
- #33 added 2 tests to `test_escape.py`; #32 added 1 to `test_architecture.py` → 203 + 3 = 206 across 19 files;
- of those, the 17 pilot-driving files hold **187** and the 2 non-pilot files hold 19 (`17 + 2`).

**So: deleting 187 is correct, and it is not a shortfall against the spec's 203.** If you find yourself
deleting a file *not* on the DELETE list above, stop and check — the likely mistake is sweeping in one of the
four survivors.

## What must be true when you are done

- `tests/test_date_parser.py` and `src/dida/date_parser.py` are both gone, against the consumer checklist at
  `docs/tickets/32-date-parser-consumers.md` — **8 production call sites, 106 test cases across 5 files**
  (measured empirically, more than the map's earlier estimate of 84/4).
- `tests/test_architecture.py::SEVEN_MODULES` no longer lists `dida.date_parser`, and the file still passes.
- The suite is green and the count has dropped by 262 **minus whatever v2 tests you add** — do not treat a
  large drop as failure, but do account for every file.
