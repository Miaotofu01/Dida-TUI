# Prompt — ticket #43 (任务详细页：字段列表、文本编辑、只读字段)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t43`, branch `ticket/43-task-detail-page`. Fetch your acceptance criteria from
the tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/43 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/43/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md` (the `#43` and `#45` entries),
**`kitty-flag-import-order.md` in full**, `terminal-input-evidence.md`, `wave-plan-and-seams.md` (you own the
detail-page seam), and §10 of `codebase-map.md`.

## 描述 / 备注 are INVERTED in v1 — you must FLIP, not just re-render

`GLOSSARY.md:31–37` (already on `main`, authoritative) fixes the vocabulary: **描述 = `content`**,
**备注 = `desc`**. v1 has it backwards in three places:

- `src/dida/sync/view.py:93–101` (the mapping),
- `src/dida/tui/panes.py:160` (the rendering),
- `tests/test_detail_description.py:53–59` (the assertion) — **and note that file is on #34's DELETE list**, so
  if the mapping assertion still lives only there, it is about to vanish.

The spec calls this out explicitly: 「v1 标反了，这份 spec 纠正它」, with the field evidence (a list whose seven
tasks all had non-empty `content` and empty `desc`, yet the web client showed that content as the body).
`tests/test_deep_link.py`-style rescued copies may assert the mapping — check
`tests/test_detail_description.py`'s rescued counterpart and flip it too if it exists. Ticket #45 also touches
this area, so keep the mapping in one place.

Do not cite `api-contracts.md` as authority for the mapping: `api-contracts.md:58` only *lists* the field
names, it never assigns 描述/备注. `test_detail_description.py:8`'s comment over-claims that.

## The Chinese-input acceptance criterion — and why the obvious test is wrong

Your AC: 「**中文输入**：用输入法一次上屏超过四个汉字，落进字段的是原文而不是转义序列」.

Measured facts, in `kitty-flag-import-order.md` and `terminal-input-evidence.md`:

- An IME commit of **11 CJK characters** arrives as one 78-byte kitty CSI-u event; Textual's **32-character**
  parse cap makes it give up and re-emit the sequence character-by-character, so the field receives the literal
  escape string. **4 characters (30 bytes) decode fine.** That is why self-testing with 「你好」 proves nothing.
- `TEXTUAL_DISABLE_KITTY_KEY` is currently set **nowhere** in `src/`. It is a `Final[bool]` read **once at
  import** (`textual/constants.py:116`), so setting it inside `main()` is **too late**. Ticket #34 sets it
  above `bootstrap.py`'s import block — **check whether #34 has landed that before you rely on it**, and if it
  hasn't, coordinate rather than duplicating the mechanism.
- ⚠ **Do NOT write the acceptance test by feeding a CSI-u sequence to the parser.** I measured this both ways:
  with the protocol **enabled** a short sequence decodes correctly; with it **disabled** the same sequence
  produces garbage. So that test **passes when the guard is broken and fails when it works.** The protection is
  that the terminal is never *asked* to send the sequence, because the driver never writes `\x1b[>25u`
  (`drivers/linux_driver.py:285–292`).
- The honest automated assertions are therefore: (a) the flag is set in the process before `textual` is
  imported (a clean-subprocess check), and (b) a multi-character CJK string lands verbatim in the field
  through the normal input path. **A real IME in a real terminal is a manual step** — say so in your report
  rather than implying end-to-end coverage.

## Read-only fields — and a status-code trap

You must display subtasks + their completion state, reminders + trigger times, and "this is a repeating task",
all **read-only**, with explicit copy saying the client can't change them (that candour is itself an AC).

⚠ **Never share one `is_completed` predicate between subtasks and tasks.** Subtask `status == 1` means
completed (`notes/openapi-dida365.md:2258`); task `status == 2` does (`:2286`). Also `items.status` **is**
writable on create/update (`:240`, `:332`) while the task's own `status` is not — so "read-only" here is a
product decision (#34/#43's scope), not an API limitation. Don't write copy claiming otherwise.

## Per-field save, and the status line

Your ACs: every field change **pushes immediately** (not batched until you leave the page), and the bottom of
the page permanently shows 「已保存」 or 「待推送（N）」 so the user knows whether it went out. Save failures must
show a **specific** error, not 「保存失败」.

Route every status-bar write through the guard that landed just before your ticket (`_write_status` — see
`progress.md`'s pump-teardown section). **The general rule that fix established: after every `await`, ask
`self.is_running` before touching the DOM**, because `Timer._tick` swallows callback exceptions and this class
of bug otherwise surfaces as a teardown error far from its cause. Your per-field push paths await, so they are
exactly the shape that races.

## Scope discipline

You own **layer 3**: the field list, per-field editing, text fields, and the read-only sections. Do **not**
implement the due-date editor (#44 — it is blocked by you, deliberately) or the picker fields
(list/priority/tags, #45). Leave the seam for them: they extend your page, they don't replace it. Don't touch
the list-index or task-list pages.
