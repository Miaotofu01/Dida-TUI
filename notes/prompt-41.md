# Prompt — ticket #41 (刷新拿全、不残留：清单索引翻页 + 剪枝)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t41`, branch `ticket/41-refresh-complete-and-prune`. Fetch your acceptance
criteria from the tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/41 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/41/comments --jq '.[].body'
```

Read `/home/tofu/dida-v2-worktrees/notes/cross-ticket-corrections.md` (the `#41` entry) and §4 + §8 of
`codebase-map.md`.

## Ticket-specific instructions

**Your ticket's own note is correct and the mechanism is narrower than it sounds.** The API client *does*
already accept `offset`/`limit` (`api/client.py:67–71`) — what drops them is the refresh: it calls
`list_projects` bare at `sync/engine.py:483`, and `ProjectReader.list_projects` (`sync/engine.py:274`) has no
parameters at all. **Note #32 already split `engine.py`**, so re-locate those two sites before editing —
`refresh.py` is the likely new home. Do not write the fix as "the client lost the parameters".

**Completeness signal — implement the inference, don't invent a total.** From the official doc:
`openapi-dida365.md:986` — the 200 default applies **only** "when either pagination parameter is provided";
there is **no documented maximum**; and the response is a **bare array with no total** (`:991`). So "did I get
everything" can only be inferred as: fetch with a page size, and if `len(page) == limit` there may be more —
fetch the next page; stop on a short or empty page. Your acceptance criterion 「翻页取不全时不假装拿全了，有
明确信号」 means exactly this, so make the inference explicit and named in the code, and prove it in a test.

**Paging test:** the ticket asks you to pin it with a fake backend returning more projects than the server's
default cap. Make the fake return a number that would be truncated by a single un-paged call (so the test
fails against the current implementation), and assert the full set arrives.

**Pruning — the sharper half of the ticket.** Today the refresh only upserts, never deletes, so anything the
user deleted on the server lives forever locally. Two things must be pruned: tasks that no longer exist
remotely, and projects that no longer exist remotely (the latter is also why #42 is blocked on you — without
prune, a deleted list comes back on the next refresh).

Three constraints on prune, the first two of which are acceptance criteria:
- **Never prune a task that has a pending (unpushed) change** — that would silently destroy the user's own
  edit. Your AC 「剪枝不误删待推送改动对应的任务」 is this.
- A refresh that deletes several remote records must not take unrelated local pending changes with it
  (your AC 「一次刷新里删掉多条远端记录时，不会把未受影响的本地改动一起清掉」).
- Prune must be part of the same all-or-nothing refresh the ADR-0001 design already gives you: v1 fetches
  everything and only then calls `apply_refresh`, so a mid-flight failure leaves no half-refresh. Keep that
  property — do not prune incrementally as pages arrive.

**Read `docs/adr/0001-full-refresh-and-local-diff.md` before you start.** Also relevant:
`RefreshReport.written_lists` / `written_tasks` are 0 on a second identical refresh, and that is what keeps
the UI from flickering and the cursor from being lost — **your prune must not break that**, i.e. a no-change
refresh must still report zero writes. Add an assertion for it if one doesn't exist.

## Note

`#33` and `#41` run concurrently off the same integration tip and both touch the sync engine's read/refresh
surface. **You own the refresh path; #33 owns the read model.** Prefer your own seam, keep edits to `#33`'s
files minimal, and expect the merger to reconcile. If you must change a signature #33 is likely also
changing, say so loudly in your report.
