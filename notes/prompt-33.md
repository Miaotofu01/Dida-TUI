# Prompt — ticket #33 (引擎按三种形状读)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t33`, branch `ticket/33-engine-read-shapes`. Fetch your acceptance criteria
from the tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/33 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/33/comments --jq '.[].body'
```

Read `/home/tofu/dida-v2-worktrees/notes/cross-ticket-corrections.md` (especially any `#33` entry and the
repo-bugs section) and §3 + §4 + §9 of `codebase-map.md` before designing.

## Ticket-specific instructions

**This is the ticket that makes v2's read model possible.** The engine today has one read entry returning a
type hard-coded for "today" with three sections; v2's three page layers need three read shapes. §3 of the
codebase map documents the current entry point, the returned type, and precisely why it cannot express the
three shapes — start there.

**The inbox identity is the core of this ticket, and it interacts with an existing test.**
- The server's project index does **not** contain the inbox; the client must add that row itself.
- The request side may keep using the literal `inbox`, but the `projectId` the **server returns for tasks in
  the inbox is a per-account string** (like `inbox` plus digits). Classification, grouping and counting must
  use the server-returned id.
- **A missing `projectId` must no longer be guessed as the literal `inbox`.** That guess is why a fallback
  branch is unreachable and why classification matches nothing.
- **⚠ Reconcile deliberately with `tests/test_escape.py`:** it has three `test_task_url_*` tests pinning deep
  link assembly, which the spec explicitly **keeps** (深链拼装 is on the 可以原样保留 list). One of them
  asserts that a project id *containing* `inbox` is substituted with the literal `inbox`. There is also a
  duplicated `INBOX_ID = "inbox"` in `sync/view.py:38` and `tui/escape.py:21`, and `escape.py:32` matches it
  as a **substring** — so a list id merely containing "inbox" gets rewritten in deep links. **You own this
  reconciliation.** The deep-link behaviour must keep working; the guess-as-inbox must go. Decide, implement,
  and say in your report exactly what you changed and which test you touched.

**Architecture constraint that will bite you:** the TUI may only import `dida.sync.engine` and `dida.tui.*`
(`tests/test_architecture.py` enforces it with an AST scan). Your new read-model types must therefore be
**re-exported through `dida.sync.engine`** for the UI layer to see them. Do not ask the TUI to import your
new module directly.

**Scope discipline:** this ticket does **not** touch the UI. No Textual, no screens, no keybindings. Pure
functions get tested directly; engine tests go through the existing in-memory `FakeBackend` / `InMemorySource`
seams described in `docs/architecture.md`.

**Your two named acceptance tests are the point of the ticket, so write them first:**
- a fake backend returning a `projectId` shaped like `inbox` + digits, asserting tasks classify under the
  inbox row;
- a companion asserting that comparing against the **literal** `"inbox"` is **not** accepted as a correct
  implementation — i.e. the test must fail against a literal-comparison implementation. Make that explicit
  rather than implied; a test that passes either way is worthless here.

Also: the project index rows need colour, project group, `kind` and `permission`, and each row needs its
unfinished count. `codebase-map.md` §4 records which of those v1 already stores but never exposes.

## Note

`#33` and `#41` run concurrently off the same integration tip, and both touch `sync/engine.py`'s read/refresh
surface — #32 split that file into `refresh.py`, `completed.py`, `push.py` etc. **Prefer your own seam; keep
edits to files #41 clearly owns (the refresh path) as small as you can**, and expect the merger to reconcile.
