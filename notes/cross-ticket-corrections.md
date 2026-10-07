# Cross-ticket corrections: where a ticket's premise is wrong or incomplete

Consolidated by the orchestrator from two independent exploration passes
(`codebase-map.md` — the repo, `api-shapes.md` — the official OpenAPI doc). Every entry below was
independently corroborated by **both** passes or verified directly by the orchestrator, and citations
point at the evidence. **Read the entries for your ticket before you design anything.** Where an entry
conflicts with the ticket text, this file wins on *fact* and the ticket wins on *scope* — and if the two
cannot be reconciled, stop and report rather than inventing a resolution.

Reproduce the doc citations at `notes/openapi-dida365.md:<line>`; the repo citations are relative to the
integration worktree.

---

## Blocking traps — a ticket as written cannot be implemented without handling these

### #32 (清路) — a rescued assertion carries a stale rationale, and two production comments do too
`tests/test_complete.py:115` asserts that completing enqueues exactly one `ChangeKind.COMPLETE` change.
**That assertion remains true in v2 — rescue it.** But its trailing message (「完成只有一个方向……没有本地
『取消完成』这第二条路」) encodes the conclusion ADR-0002 overturned. Rescue the assertion, **rewrite the
message**; do not drop the test, and do not fix the production side (#38's job). The same stale rationale
sits in production at `sync/engine.py:519` and `tui/app.py:436` — **#38 owns those two**, because #38 is the
ticket that adds the second path.

### #38 (完成 ↔ 取消完成) — a local guard forbids the only field the ticket needs
`guard_writable` (`api/guards.py:49`, `:157–169`) **rejects `status` in every write body**, and
`merge_snapshot` (`:180–184`) strips it. `status: 0` on the batch endpoint is the *entire* mechanism #38 is
built on. So #38 must consciously relax the guard **for the batch-update path only** — the guard exists for
a reason (a `status` sent to the ordinary update endpoint is what the spec says is not writable), so the
relaxation has to be scoped to the one documented-by-experiment path, not removed globally. Keep the guard
test that pins "ordinary writes still refuse `status`" alive while you do it.

Second trap: `/task/batch` reports **per-task failures inside a `200 OK`** via `id2error`
(`openapi-dida365.md:567`), and `api/client.py:290–309` treats any 200 as total success. A fully failed
un-complete would be reported to the user as done. #38 must read `id2error`.

Corroborating API facts: batch has **no `status` anywhere** in `:551–590` and **no `delete` array** — so the
spec's "the docs mention it zero times" is literally true. Do not "fix" this into a documented feature.

### #37 (任务行的样子与顺序) — the "dual filter" half that must happen on the server does not exist
`POST /open/v1/task/completed` accepts **only `projectIds`, `startDate`, `endDate`** (`:642–646`) — there is
**no `status` parameter**. Its dates filter `completedTime` (`:645–646`). The repo agrees: the client's body
is `{projectIds[], startDate, endDate}` (`api/client.py:94–100`) and `_completed_tasks` (`sync/engine.py:1133–1145`)
does no status filtering.

So the ticket's 「拉取要**同时**按完成时间窗口与完成状态过滤」 is achievable **only as**: server-side
completion-time window **+ client-side filter on the `status` returned in each payload object**
(`:688` shows `status` in the response). **Do not write a test asserting `status` in the request body** — it
would pin invented surface and pass against a request the server never sees.

Also: storing completed tasks is **already done** (`refresh_completed` `store.py:795`; `_snapshot:563–564`
already keys on `status`). #37's real gaps are (a) the window default in **two** files — `config.py:101`
*and* `sync/engine.py:132` — and (b) the missing status filter. And `_by_due` (`sync/view.py:360–364`) has
**no priority tiebreak**, while the v1 order is pinned by *non-UI* tests (`test_view_models.py:79,149`,
`test_engine_view.py:34–40`) that #37 must edit — those survive #34, so they are #37's to change.

### #41 (清单索引翻页 + 剪枝) — the client already pages; the refresh throws the parameters away
The ticket's own note is right and the map confirms the *mechanism*: `DidaApiClient.list_projects` already
accepts `offset`/`limit` (`api/client.py:67–71`), but the refresh calls it bare (`sync/engine.py:483`) and
`ProjectReader.list_projects` (`sync/engine.py:274`) has **no parameters at all**. Fix in those two places.

Completeness signal: the 200 default is **conditional** — 「When either pagination parameter is provided」
(`:986`); there is **no documented maximum** and the response is a **bare array with no total** (`:991`).
So "did I get everything" can only be inferred as `len(page) == limit` → fetch one more page, and stop on a
short or empty page. #41's AC 「翻页取不全时不假装拿全了，有明确信号」 means exactly this inference — implement
it explicitly and name it in the report.

### #42 (清单的建 / 改 / 删) — two documented traps in the list-write responses
1. **`POST /open/v1/project` and the update both have two success shapes: `200 → Task` and `201 → No
   Content`** (`:247`, `:339`) with no documented rule for which you get. `api/client.py:199–212`
   (`_payload_object`) parses the body unconditionally, so **a 201 with an empty body raises
   `MalformedResponseError` on a success**. #42 must handle the 201/no-body case.
2. **Update-project `sortOrder` is documented with "default 0"** (`:1237`) and replace-vs-merge is doc
   silent — omitting it may silently reset the user's list order. **Echo the current `sortOrder` back** on
   rename. (This is the same shape of bug as the unknown-field rule the spec already states, just for a
   field the doc gives a default for.)

Confirmed good news for #42: `groupId` appears only in Project **responses** (`:1011`, `:1052`, `:1095`,
`:2302`) — no request accepts it, so 「不写项目组字段」 is right. And the delete-confirmation copy is
**verified**: `:1278–1302` says nothing about the tasks inside a deleted project, and there is no
trash/undelete/restore endpoint anywhere (the only `DELETED` hit is a batch error code, `:567`). So 「文档没写、
不承诺恢复」 is accurate wording — use it.

### #45 (挑选型字段) — one ticket claim is false, and the move endpoint has two shape traps
**`POST /open/v1/tag` IS documented** (`:1612–1654`) — `name` and `label` both required, max 64, lowercase,
`label` must equal `name` lowercased (`:1620–1621`). So 「不能在客户端新建标签」 is **not** an API limitation.
Tag *deletion* really is absent (`:1576–1654`), so that half stands.

**Scope call — do not expand it yourself:** the spec's own Out of Scope section decides that creating tags
is out of scope for v2, so #45 must still **not** implement tag creation. But the UI copy and the ticket's
justification must not claim the API can't do it. Reword to 「这个客户端不做」 rather than 「做不到」, and flag
the discrepancy in your report — the user decides whether to revisit it.

**Move (`POST /open/v1/task/move`, `:497–548`)** — the doc is **silent** on inbox↔list in both directions,
but the *shapes* are documented and easy to get wrong: the **request body is a JSON array** (`:504`) and the
**response is an array of `{id, etag}`** (`:516`) — *not* the moved Task. A request-shape test must pin the
array body; a response parser that expects a Task object will break.

Also: on the ordinary update endpoint, `status` cannot be carried (`:309–333`, replace-vs-merge doc silent)
— so **editing the title of a completed task cannot re-assert `status: 2`, and whether it survives is
undocumented.** Don't promise it either way in the UI.

### #43 (任务详细页) — 描述/备注 must be **flipped**, not just re-rendered
`GLOSSARY.md:31–37` (already merged, authoritative) says **描述 = `content`**, **备注 = `desc`**. v1 is
backwards in three places — `sync/view.py:93–101`, `tui/panes.py:160`, and
`tests/test_detail_description.py:53–59`. The spec calls this out (「v1 标反了，这份 spec 纠正它」). #43 must
flip the production mapping; the rescued test must be flipped with it. Don't let the old assertion ride
along unexamined.

### #39 (新建任务) — the API is fine; the *fakes* are what would make your tests lie
`projectId` is **required** on create (`:222`), so the API has always supported a named target list — the
hard-wiring is repo-side at `sync/engine.py:914` (`INBOX_ID`). Good.

**But the silent-pass trap is in the test doubles:** `FakeBackend.create()` cannot target a list
(`testing.py:307–333`, inbox hard-coded at `:329`) and `InMemorySource.add_task` (`:141`) **invents** the
list row. So a test named "creating into 工作 lands in 工作" would pass while actually asserting inbox —
which is precisely #39's AC 「假后端支持按清单新建，否则测试会静默断言成收集箱」. Fix the fake **first**, then
the behaviour. (Bonus bug in the same area: `FakeBackend` records `tags` at `:326` but never puts them on
the task at `:327–333`.)

---

## Global corrections to the ticket/estimate numbers

- **A new write type currently touches 9 production places, not 4** (`codebase-map.md` §2 enumerates them),
  plus 11 test files. So #32's "one place" is a bigger reduction than the ticket implies — and #32's
  before/after list is the evidence to check it with.
- **Rescuable assertions ≈ 66, not ~50**: 35 clean + 15 needing a rewrite + 16 MIXED. The spec's estimate
  **mis-counted `test_architecture.py`'s 16** (they are pure AST boundary tests, not UI) and treated
  `test_sync_session.py`'s 17 as pure UI. Being *above* the estimate is correct; dropping to fit it is not.
- **Date-parser blast radius is 84 test functions/cases across 4 files, not 75.** #34 deletes against this.
- **The "20 TUI-importing files" is 19.** The 203 count is exact, and `521 − 203 = 318` matches the spec.
- **`TEXTUAL_DISABLE_KITTY_KEY` is currently set nowhere in `src/`, `tests/`, or `pyproject.toml`** — only in
  ADR-0006's prose, which describes it as done. See `kitty-flag-import-order.md`: the flag is frozen at
  import time, so `setdefault` inside `main()` is too late, **and the obvious CSI-u acceptance test asserts
  the opposite of what it looks like.**

## Domain-doc inconsistencies to resolve rather than silently pick a side of

- **`GLOSSARY.md:70` says custom views live in the config *file*; ADR-0005:5 and ticket #36 say the local
  SQLite DB.** ADR-0005 is the decision, and its own filename is stale
  (`0005-custom-views-live-in-local-config.md`). #36 owns views; **fix the glossary line and, if you rename
  the ADR file, keep the link in the spec working.** Do not put views in `config.toml` — that is the file
  holding the user's token.
- **Test comment over-claim:** `tests/test_detail_description.py:8` cites `api-contracts.md` for the field
  mapping, but `api-contracts.md:58` only lists field names — it never assigns 描述/备注. Don't cite it as
  authority for the mapping.

## Repo bugs found in passing — fix only if your ticket touches the file

- **`INBOX_ID = "inbox"` is duplicated** in `sync/view.py:38` and `tui/escape.py:21`, and `escape.py:32`
  matches it as a **substring** — so a list id merely *containing* "inbox" gets rewritten in deep links.
  This is directly adjacent to #33's 「缺失的 projectId 不许再猜成字面量 `inbox`」 and to the rescued
  `test_escape.py::test_task_url_*` tests. **#33 owns the reconciliation**; if you are not #33, leave it.
- `sync/engine.py:965` attributes `changed_elsewhere` to `RefreshReport`; it actually lives on
  `SubtaskWrite` (`:1310`).
- `bootstrap.py:190` names a class that does not exist (`_TransportUnavailable` → really `_UnusableTransport`
  at `:42`).
- `FakeBackend`'s `day_end` default is `"24:00"` (`testing.py:169`), a spelling `Config` normalises away.
- **No rate-limit material exists in the doc at all** — no 429, no `Retry-After`, no quota threshold (only
  the batch error code `EXCEED_QUOTA`, `:567`) — and **no error-body shape is documented anywhere** (every
  failure row is "No Content"). Status-code-only classification is therefore safe, **except** for the batch
  `id2error` case above.
- **Subtask vs task completion codes differ: subtask `status == 1` means completed (`:2258`), task
  `status == 2` (`:2286`)** — and `items.status` **is** writable on create/update (`:240`, `:332`) while the
  task's `status` is not. **Never share one `is_completed` predicate between them.** Relevant to #43's
  read-only subtask display and to #37's local completed predicate.
