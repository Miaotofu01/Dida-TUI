# Merger prompt — ticket #33 (引擎按三种形状读)

Use `/home/tofu/dida-v2-worktrees/notes/merger-template.md` as the procedure. Read
`/home/tofu/dida-v2-worktrees/notes/brief.md` first — **including its network section**: a plain `git push`
hangs because the user's SSH proxy is down, so pushes must be prefixed with
`GIT_SSH_COMMAND="ssh -o ProxyCommand=none"`.

## This merge is expected to CONFLICT — resolve by intent, both sides must survive

- Ticket: **#33**, branch **`ticket/33-engine-read-shapes`**, tip **`3f98743`** (9 commits, each naming #33).
- Implementer reported **606 passed** (baseline 584).
- **#41 has already landed on the integration branch.** #33 and #41 were built concurrently and both touch
  the sync engine's read/refresh surface. The #33 implementer predicted the exact collision sites:
  - `sync/refresh.py`'s **fetch-loop body** (`refresh()` around lines 105–116),
  - `storage/store.py`'s `__init__` / `_write_list` / `lists()`.

  Its own claim is that it left `apply_refresh` (#41's prune site) and `ProjectReader.list_projects`
  (#41's pagination signature) untouched, so the conflict should be **textual, not semantic**. Verify that
  claim rather than trusting it — read both intents in every conflicted hunk.

**Do not resolve by discarding either side**, and never with `-X ours` / `-X theirs`. In particular these
must all survive together:
- #41's paging (`PROJECT_PAGE_SIZE`, the `len(page) == limit` completeness inference, the repeated-page-id
  guard) **and** #41's prune (`prune_lists` / `prune_unfinished_tasks` only after every fetch succeeded,
  inside the single transaction, exempting pending-change tasks, locally-completed rows and `is_inbox` lists).
- #33's `_container_id` (the server-returned id, learned from tasks when the index has no inbox row) and
  `_missing_project_row` (the client adds the inbox row, tagged `isInbox`).
- **These two interact and it matters:** #41's list-prune exemption keys on `is_inbox`, and #33's
  synthesized inbox row is what sets it. The inbox row is *not* in the server index, so a naive prune would
  delete the very row #33 added. **Prove the inbox row survives a prune** — if no test covers the
  combination, add one, and say so.

## Also verify after merging

- #33's migration: `store.py` gained `lists.kind`/`lists.permission` behind an idempotent `ALTER TABLE` in
  `__init__`. Make sure that still runs on an already-migrated database and that #41's prune did not disturb it.
- `RefreshReport` now carries both #41's `pruned_lists`/`pruned_tasks` and whatever #33 added. Check the
  no-change property still holds: a second identical refresh reports zero writes **and zero prunes**.
- #33 changed the deep-link behaviour in **both** `tests/test_deep_link.py` and `tests/test_escape.py`.
  `test_escape.py` is one of the 19 v1 interface test files that ticket #34 will delete, so the surviving
  assertion is the one in `test_deep_link.py`. Keep it green and don't let the merge resurrect the old
  substring behaviour in `tui/escape.py`.

## Known-flaky test — read before judging the suite

`tests/test_sync_session.py::test_the_periodic_timer_pumps_the_queue_without_any_keypress` flakes roughly
1 run in 35 with `NoMatches: StatusBar`. **A separate agent is fixing the underlying defect right now, and
its fix may or may not have landed on the integration branch before you merge** — if it has, the flake should
be gone. Either way: if you see exactly that failure, re-run before treating it as a merge problem, and note
it in your report. **Do not** fix it by weakening an assertion, skipping, or xfail. Any other failure is yours.

## Before you report

Whole suite in the integration worktree, ≥ 606 passing expected (plus #41's 9, so expect roughly 615 —
report the real number). Then `uv run python -c "import dida.bootstrap"` and
`uv run pytest tests/test_architecture.py -q`. Push with the `GIT_SSH_COMMAND` bypass. Leave a **comment —
never a close** — on ticket #33 in Chinese, pointing at the merge commit. **Never `gh issue close`.**

Report in ≤25 lines per the template's numbered format, and include: the merge SHA, the new integration tip,
the exact suite summary line, **each conflicted file with one line on how you preserved both intents**, and an
explicit statement of whether the inbox-survives-prune case was already covered or whether you had to add it.
