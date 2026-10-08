# v1 codebase map — `feat/v2-terminal-client` @ `4548577`

Worktree: `/home/tofu/dida-v2-worktrees/integration` (clean, at `origin/main` merged with PR #28/#31).
Baseline: **521 tests collected**, `src/dida` = 6447 LOC across 22 files, `tests` = 9241 LOC / 34 files.
All line numbers are from this commit. Sources: spec #30, tickets #32–#48, `docs/architecture.md`,
`GLOSSARY.md`, `docs/adr/0001..0006`.

Notation: `→` = imports. `TOP` = module top level, `DEF` = function-local/deferred.

---

## 1. Module inventory

| Path (LOC) | Single responsibility | Imports (intra-`dida`) | Imported by |
|---|---|---|---|
| `src/dida/__init__.py` (3) | Package docstring + `__version__` | — | — |
| `src/dida/__main__.py` (6) | `python -m dida` → `bootstrap.main` | `bootstrap` TOP:3 | — |
| `src/dida/clock.py` (24) | The clock seam: `Clock` protocol + `SystemClock` | — | `testing` TOP:23, `bootstrap` TOP:28, `sync/engine` TOP:41 |
| `src/dida/logical_day.py` (97) | Pure logical-day arithmetic: `parse_day_end`, `logical_day`, `LogicalDay`, `InvalidDayEnd` | — | `date_parser` TOP:53, `sync/view` TOP:33, `sync/engine` TOP:43 |
| `src/dida/config.py` (206) | `config.toml` read/validate/normalise (0600) + `Credentials.verify_and_store` (verify-before-write) | `api.client` TOP:35, `api.errors` TOP:36, `api.transport` TOP:37 | `bootstrap` TOP:29, `tests/test_config.py` |
| `src/dida/date_parser.py` (399) | v1 one-line shorthand grammar → `ParsedTask` (**deleted wholesale in v2**) | `logical_day` TOP:53 | `sync/engine` TOP:42, `testing` TOP:24 — see §6 |
| `src/dida/bootstrap.py` (218) | Composition root: config → Store + DidaApiClient → SyncEngine → DidaApp; first-run token paste | `api.client` TOP:25, `api.errors` TOP:26, `api.transport` TOP:27, `clock` TOP:28, `config` TOP:29, `storage.store` TOP:30, `sync.engine` TOP:31, **`tui.app` TOP:32**, `tui.escape` TOP:33 | `__main__` TOP:3; entry point `dida` (`pyproject.toml:19`) |
| `src/dida/testing.py` (386) | Test doubles: `ManualClock`, `FakeTransport`, `InMemorySource`, `FakeBackend` — see §9 | `clock` TOP:23, `date_parser` TOP:24, `storage.store` TOP:25, `sync.engine` TOP:26, `sync.view` TOP:27 | every TUI test; `tests/test_fake_backend.py` |
| `src/dida/api/__init__.py` (5) | Re-export `DidaApiClient` | `api.client` TOP:3 | — |
| `src/dida/api/errors.py` (70) | Closed error family: `DidaError` + 4 subclasses + guard errors; all carry `status_code`/`field` | — | `api.client` TOP:25, `api.guards` TOP:20, `config` TOP:36, `sync/engine` TOP:39, `bootstrap` TOP:26 |
| `src/dida/api/transport.py` (37) | Transport seam: `Transport` protocol, `HttpxTransport`, `DEFAULT_TIMEOUT` | — | `api.client` TOP:38, `config` TOP:37, `bootstrap` TOP:27 |
| `src/dida/api/guards.py` (186) | 4 pre-send guards as pure functions: date shape, dateless-repeat, non-writable fields, snapshot merge | `api.errors` TOP:20 | `api.client` TOP:32, `sync/engine` TOP:40 |
| `src/dida/api/client.py` (312) | Thin TickTick Open API wrapper; keeps `_snapshots` for write-back merge | `api.errors` TOP:25, `api.guards` TOP:32, `api.transport` TOP:38 | `api/__init__` TOP:3, `config` TOP:35, `bootstrap` TOP:25 |
| `src/dida/storage/__init__.py` (25) | Re-export `Store`, `ChangeKind`, `RefreshReport`, `ListRecord`, `PendingChange`, `StoredSyncState`, `FieldOverride` | `storage.store` TOP:7 | — |
| `src/dida/storage/store.py` (619) | SQLite local replica: schema, diff-write, conflict arbitration, retry queue, sync state | **`sync.view` TOP:45** (`INBOX_ID`, `ListSnapshot`, `SyncState`, `TaskSnapshot`); `storage.store` DEF in `sync/engine` | `storage/__init__` TOP:7, `bootstrap` TOP:30, `sync/engine` DEF:670/1040/1050, `testing` TOP:25 |
| `src/dida/sync/__init__.py` (5) | Re-export `SyncEngine`, `SyncStatus` | `sync.engine` TOP:3 | — |
| `src/dida/sync/view.py` (528) | Pure view-model dataclasses + grouping/sorting/formatting/subtask functions (no I/O, no clock) | `logical_day` TOP:33 | `sync/engine` TOP:44, `storage/store` TOP:45, `testing` TOP:27 |
| `src/dida/sync/engine.py` (1347) | The only read/write entry point for the TUI: view model, full refresh, optimistic writes + retry queue, completed stream, defer/reschedule/create/priority/subtasks | `api.errors` TOP:39, `api.guards` TOP:40, `clock` TOP:41, `date_parser` TOP:42, `logical_day` TOP:43, `sync.view` TOP:44; `storage.store` **TYPE_CHECKING:74 + DEF:670/1040/1050** | `sync/__init__` TOP:3, `bootstrap` TOP:31, `tui.app` TOP:19, `tui.panes` TOP:33, `testing` TOP:26 |
| `src/dida/tui/__init__.py` (5) | **Imports `DidaApp` at package top level** (`:3`) | `tui.app` TOP:3 | — |
| `src/dida/tui/escape.py` (44) | Escape hatch: `task_url` (pure) + `open_in_browser` | — | `tui.app` TOP:20, `bootstrap` TOP:33 |
| `src/dida/tui/panes.py` (1113) | All widgets/renderers: 3 panes, status bar, 3 input boxes, confirm modal, subtask pane, overlays, `KEY_HELP` | `sync.engine` TOP:33 (26 names) | `tui.app` TOP:21 |
| `src/dida/tui/app.py` (812) | `DidaApp`: widget tree, bindings, actions, sync orchestration, tier/responsive logic | `sync.engine` TOP:19, `tui.escape` TOP:20, `tui.panes` TOP:21 | `tui/__init__` TOP:3, `bootstrap` TOP:32 |

### Import-cycle guardrails (do not "fix")
- `storage/store.py:45` imports `dida.sync.view` at **module top level** — used at `:265` (`ListSnapshot`), `:434` (`SyncState`), `:548/:556` (`TaskSnapshot` annotation + construction), and `INBOX_ID` at `:453, :501, :538, :559`.
- `sync/engine.py:73-79` — `TYPE_CHECKING`-only `storage.store` import with the comment: *"只为了标注：storage 反过来 import dida.sync.view，运行时不能在这里 import"*.
- `sync/engine.py:1033-1042` `_storage_kind()` — deferred import with the full rationale at `:1036-1038`: *"**必须延迟 import**：`dida.storage.store` 在模块级 import 了 `dida.sync.view`，而 import 子模块会先跑父包的 `__init__`（那里 import 了本模块）——顶层互相 import 时总有一方拿到半成品模块"*. Same pattern at `:670` and `:1050`.
- `dida/date_parser.py` is imported by `sync/engine.py:42` TOP — so deleting the file breaks `engine`, `testing`, and `test_architecture.py`'s `SEVEN_MODULES`.

### Multi-responsibility modules (what #32/#34 must split)

**`sync/engine.py` (1347)** — five unrelated reasons to change:

| Lines | Responsibility | Owning ticket |
|---|---|---|
| 1–136 | Module docstring, re-export list, backoff/window constants | #32 |
| 139–151 | `backoff_delay` pure function | — |
| 154–168 | `SyncStatus` | #34 (status bar) |
| 171–191 | `WriteKind` enum | **#32 §2** |
| 194–265 | `Engine` protocol (14 methods, incl. v1-only `cycle_priority`, `toggle_subtask`, `plan`) | #32/#34 |
| 268–406 | 5 collaborator protocols (`ProjectReader`, `TaskWriter`, `TaskReader`, `RefreshTarget`, `WriteTarget`) — the storage/API seam | #32 |
| 408–464 | `SyncEngine.__init__`, `status()`, **`view()`** | **#33** |
| 466–513 | `refresh()` — per-list full pull, no pagination | **#41** |
| 515–554 | `complete`/`defer`/`delete` presets | #38 #40 |
| 558–636 | `write()` optimistic write, `push_pending`, `wait_for_pushes` | #32 |
| 640–760 | Push internals: `_push_now`, `_schedule_push`, `_send` dispatch, `_adopt_created`, target accessors | **#32 §2** |
| 764–841 | Completed stream (`refresh_completed`, window, cursor) | **#37** |
| 845–874 | `plan()` + `reschedule()` — **date-parser call sites** | #44 (replaced) |
| 878–920 | `create()` — **hard-codes inbox** | **#39** |
| 923–939 | `cycle_priority()` | deleted in v2 (#45 replaces with a choice) |
| 943–1022 | `subtasks()` / `toggle_subtask()` | #43 (read-only) |
| 1025–1060 | `_is_due`, `_storage_kind`, `_completed_status` | #32 |
| 1055–1145 | Payload parsers: `_project_index`, `_unfinished_tasks` (**injects literal `inbox`**), `CompletedReader`, `_completed_tasks` (**no status filter**) | #33 #41 #37 |
| 1148–1252 | `_parse_moment`, `_defer_changes`, `_logical_day_after`, `_local_task_id`, `_create_payload` | #39 #40 #44 |
| 1258–1347 | `UnknownTaskError`, `_priority_of`, `SubtaskWrite`, `_has_pending_items`, `_toggled_items` | #32 |

**`tui/app.py` (812)** — three reasons to change: (a) presentation/messages `:40-196` + `KEY_HELP` consumption; (b) sync orchestration `:328-407`, `:736-770`; (c) write-action handlers `:433-735`; (d) responsive/pane-tier layout `:175-197`, `:409-431`, `:772-811` — (d) is deleted by #34, (a)–(c) survive in rewritten form.

**`tui/panes.py` (1113)** — 11 top-level classes, most of them v1-only: `Pane` `:178`, `ListPane` `:244`, `TaskPane` `:266`, `DetailPane` `:417`, `StatusBar` `:453`, `RescheduleInput` `:499`, `QuickAddInput` `:591`, `FilterInput` `:679`, `ConfirmScreen` `:744`, `SubtaskPane` `:817`, `OverlayBox` `:986` / `DetailScreen` `:1040` / `ListsScreen` `:1046` / `HelpScreen` `:1106`. **Only `ConfirmScreen` (#40/#42), `StatusBar`+`format_status` (#34), `KEY_HELP`-as-data (#48) and a task-row renderer (#37) survive in recognisable form.**

**`sync/view.py` (528)** — v2 keeps `format_due` `:336`, `due_day` `:326`, `priority_mark` `:221`, `next_priority` `:230`, `format_tags` `:251`, `subtask_items` `:471`; v2 deletes `GroupKind`/`GROUP_ORDER`/`group_tasks` `:44-63,:289`, `filter_groups`/`fuzzy_match` `:370-399`, `TodayView` `:212`, `CompletedSection` folding semantics `:196`.

---

## 2. The write-type duplication (#32 part 1)

### The two enums — 4 members, exact 1:1 by value

| # | File:lines | Name | Members |
|---|---|---|---|
| A | `src/dida/sync/engine.py:171-191` | `WriteKind(Enum)` | `CREATE="create"` `:181`, `UPDATE="update"` `:184`, `COMPLETE="complete"` `:187`, `DELETE="delete"` `:190` |
| B | `src/dida/storage/store.py:87-93` | `ChangeKind(Enum)` | `CREATE="create"` `:90`, `UPDATE="update"` `:91`, `COMPLETE="complete"` `:92`, `DELETE="delete"` `:93` |

The bridge is value-based (`engine.py:1042` `return ChangeKind(kind.value)`), so A and B can never drift in *value* — only in *membership*. The docstring at `engine.py:172-179` states the mapping and the reason the two exist (*TUI may only import `dida.sync.engine`*).

### Every other place that must be touched for a new write type

| # | Place | Lines | Kind of edit |
|---|---|---|---|
| 1 | `WriteKind` members | `engine.py:181-191` | add member |
| 2 | `ChangeKind` members | `store.py:90-93` | add member (must match value) |
| 3 | `SyncEngine._send` dispatch | `engine.py:668-688` | add `elif` — **else-branch raises `NotImplementedError` `:688`** |
| 4 | `SyncEngine._local_effect` | `engine.py:725-736` | per-kind local field payload (`COMPLETE` is the only special case, `:734-735`) |
| 5 | `Store.enqueue` local effect | `store.py:309-313` | `if kind is ChangeKind.DELETE: DELETE FROM tasks else _apply_locally` |
| 6 | `Store._exempt_fields` | `store.py:460-475` | `DELETE` gets whole-row exemption `:470`; new kinds that are "the task no longer exists" need the same |
| 7 | Serialisation | `store.py:325` (`kind.value` → TEXT), `store.py:577` (`ChangeKind(row["kind"])`) | automatic **iff** the value is shared; a mismatch raises `ValueError` at read time |
| 8 | A public preset/entry point on `Engine` + `SyncEngine` | `engine.py:194-265` (protocol), `:515-554` (`complete`/`delete` patterns), `:878-920` (`create`) | new method |
| 9 | `FakeBackend` | `testing.py:155-386` | new recorder list + method (it is the 19-file injection point) |
| 10 | `Engine` protocol conformance | `engine.py:194` `@runtime_checkable` | a missing method silently fails `isinstance` |
| 11 | Tests naming the enums | `test_write_guard.py:108`, `test_sync_push.py:249,275`, `test_complete.py:114`, `test_delete.py:243,276`, `test_quick_add.py:151`, `test_reschedule.py:195`, `test_priority_filter.py:144`, `test_subtasks.py:251`, `test_store.py:116-437`, `test_sync_refresh.py:323`, `test_sync_defer.py:236` | assertions |

**N (current) = 9 production places (1–9) + 11 test files.** #32's deliverable is to collapse 1,2,3,4,5,6 into "one place" — realistically a single `WriteKind` table with `(member, storage_kind, wire_call, local_effect, exemption)` tuples, since 7,8,9 stay but become mechanical.

---

## 3. The engine's current read path

### Entry point
`src/dida/sync/engine.py:447-464`
```python
def view(self) -> TodayView:            # :447
    if self._source is None: return TodayView(lists=(), groups=())   # :449-450
    lists = tuple(self._source.lists()); tasks = tuple(self._source.tasks())  # :451-452
    now = self._clock.now()                                          # :453
    return TodayView(lists=summarize_lists(lists, tasks),            # :455
                     groups=group_tasks(tasks, lists, now=now, day_end=self._day_end),  # :456
                     completed=completed_section(...))               # :457-463
```
It is the **only** read entry point for content; `status()` `:436-445` and `subtasks(task_id)` `:943-952` are the other two reads.

### Returned type
`TodayView` — `src/dida/sync/view.py:212-218`: `lists: tuple[ListSummary, ...]`, `groups: tuple[TaskGroup, ...]`, `completed: CompletedSection = CompletedSection()`.
Also `SyncStatus` — `engine.py:154-168`: `checked_at`, `pending_count`, `last_refresh_at`, `logical_day`.

### The specific ways it cannot express v2's three read shapes

**List index (needs: built-in views + custom views + real lists, badges, colour, group, `kind`, `permission`, inbox row synthesised)**
- `ListSummary` (`view.py:131-137`) carries only `id`, `name`, `unfinished`. No `color`, `group_id`, `kind`, `permission`, `is_inbox` — the store *has* colour/sort/group/inbox (`store.py:50-58`) but `Store.lists()` (`store.py:263-265`) throws all but id+name away, and `kind`/`permission` are **not stored at all** (no column).
- `summarize_lists` (`view.py:278-286`) can only emit rows for what `Store.lists()` returned. There is no built-in view row and no custom view row anywhere in the read model.
- **Inbox row is not synthesised**: `engine.refresh()` `:486-489` *fetches* `"inbox"` data, but only appends a list row if the payload happens to contain a `project` object (`:496-501`). The API index does not contain the inbox (ADR-0001:11), so on a normal account there is no inbox row and `summarize_lists` produces no badge for it.
- **Inbox identity is broken on the way in**: `_unfinished_tasks` (`engine.py:1070-1095`) rewrites a missing `projectId` to the *fetch container id* (`:1094`), which for the inbox fetch is the literal `"inbox"` — task `list_id` then never equals the server's per-account `inbox1025...`, so `unfinished.get(item.id, 0)` at `view.py:285` yields 0. Same literal fallback at `store.py:538` and `store.py:559`.

**Task list for a container (needs: all unfinished of *that* container, plus the completed slice)**
- The model has **no container concept**: `TaskGroup.kind` is `GroupKind` (`view.py:44-50` = `OVERDUE`/`TODAY`/`INBOX_UNDATED`), not a list id. `TodayView` has no `open_list_id` / `current_container` field at all.
- **Future-dated tasks are dropped**: `view.py:316` `continue  # 未来的任务不属于「今日」`.
- **Undated tasks are merged across lists**: `view.py:307-308` — `if snapshot.due is None: kind = GroupKind.INBOX_UNDATED` regardless of `snapshot.list_id` (documented at `docs/architecture.md:93-94`).
- **Completed tasks never appear in a container**: `view.py:305-306` `if snapshot.completed: continue`; they only surface in `CompletedSection` (`view.py:196-209`) which is a separate top-level field, not per-list.
- Sorting is `_by_due` (`view.py:360-364`) = `due` asc, tiebreak **title** — no priority tiebreak, and no completed-last rule (spec #37 wants due asc → priority desc → undated last → completed last).

**Single-task detail (needs: title, description, note, list, due, priority, tags, + read-only repeatFlag/reminders/items/unknown fields)**
- `TaskItem` (`view.py:141-164`) exposes `task_id,title,list_id,list_name,priority,priority_mark,due,all_day,due_text,desc,content,tags_text`. **No `repeatFlag`, no `reminders`, no `items`, no raw payload, no timeZone.**
- There is **no `engine.task(task_id)`** at all. The detail pane is fed by the currently-selected `TaskItem` plus a separate `subtasks()` call (`engine.py:943-952`, called from `app.py:700`) which reaches into the raw payload via `_payload_of` `:1013-1016`.
- "Which list is open" is unexpressible: `ListPane.selected_list_id` (`panes.py:256-259`) is the only cursor notion and **has no production caller** (only `tests/test_app_view.py:194`); `enter` on the list pane is bound to `toggle_detail`, not to entering a list (`app.py:244`).

### How the engine gets data from storage (every `Store` method it calls)

| Store method | Called at | Role |
|---|---|---|
| `sync_state()` | `engine.py:439` | status bar |
| `lists()` | `engine.py:451` | view |
| `tasks()` | `engine.py:452` | view |
| `apply_refresh(lists=,tasks=)` | `engine.py:503`, `:795`, `:978` | full refresh, completed stream, subtask reread |
| `set_sync_state(...)` | `engine.py:508`, `:797` | refresh/completed |
| `stored_sync_state()` | `engine.py:509`, `:785` | cursor preservation |
| `task_payload(task_id)` | `engine.py:536, 584, 678, 709, 936, 974, 1008` | write-back snapshot source |
| `enqueue(...)` | `engine.py:587`, `:905` | optimistic write + local effect |
| `pending()` | `engine.py:608`, `:1317` | retry pump / pending-`items` check |
| `record_attempt(...)` | `engine.py:618` | backoff bookkeeping |
| `resolve(change_id)` | `engine.py:624` | dequeue |
| `adopt_created(local_id, payload)` | `engine.py:710` | created-task id adoption |
| `pending_count()` | **not called directly** — only via `sync_state()` `store.py:434` / `status()` | — |
| `list_records()`, `list_tags()`, `close()`, `_list_rows()` | **never called from `engine.py`** | see §11 |

---

## 4. Storage schema

`src/dida/storage/store.py:50-84` (`_SCHEMA`, executed at `:182`). No migration framework — `CREATE TABLE IF NOT EXISTS` only, so **adding a column to `lists`/`tasks` needs an explicit `ALTER TABLE` path that does not exist today** (a landmine for #33/#36/#37).

| Table | Column | Type | Null | Default | Notes |
|---|---|---|---|---|---|
| `lists` `:51-58` | `id` | TEXT | NOT NULL | — | PRIMARY KEY |
| | `name` | TEXT | NOT NULL | — | |
| | `color` | TEXT | NULL | — | written from `color` `:498` |
| | `sort_order` | INTEGER | NULL | — | from `sortOrder` `:499`; only used for `ORDER BY` `:442` |
| | `group_id` | TEXT | NULL | — | from `groupId` `:500` |
| | `is_inbox` | INTEGER | NOT NULL | 0 | `1` if `isInbox` or `id == "inbox"` `:501` |
| `tasks` `:60-64` | `id` | TEXT | NOT NULL | — | PRIMARY KEY |
| | `list_id` | TEXT | NOT NULL | — | `projectId or INBOX_ID` `:538` |
| | `raw` | TEXT | NOT NULL | — | canonical JSON, `sort_keys=True` `:543-545`; unknown fields kept verbatim |
| `pending_changes` `:66-76` | `id` | INTEGER | NOT NULL | AUTOINCREMENT | PRIMARY KEY |
| | `task_id` | TEXT | NOT NULL | — | |
| | `list_id` | TEXT | NOT NULL | — | for URL assembly |
| | `kind` | TEXT | NOT NULL | — | `ChangeKind.value` |
| | `payload` | TEXT | NOT NULL | — | JSON of the changed fields |
| | `created_at` | TEXT | NOT NULL | — | ISO |
| | `attempts` | INTEGER | NOT NULL | 0 | |
| | `next_retry_at` | TEXT | NULL | — | ISO |
| | `last_error` | TEXT | NULL | — | |
| `sync_state` `:78-83` | `id` | INTEGER | NOT NULL | — | PRIMARY KEY + `CHECK (id = 1)` |
| | `completed_cursor` | TEXT | NULL | — | ISO |
| | `last_refresh_at` | TEXT | NULL | — | ISO |
| | `logical_day` | TEXT | NULL | — | `date` ISO |

### Method → read/write/upsert/delete

| Method | Lines | R/W | Semantics |
|---|---|---|---|
| `__init__` | 178-183 | W | connect + `executescript(_SCHEMA)`; **no `check_same_thread=False`, no lock** |
| `task_payload` | 254-261 | R | single row, JSON-decoded |
| `lists` | 263-265 | R | **projects away** colour/sort/group/inbox |
| `list_records` | 267-279 | R | full records (no production caller) |
| `tasks` | 281-287 | R | all snapshots incl. completed |
| `apply_refresh` | 197-250 | W | **UPSERT** (`_write_list` `:506-518`, `_write_task` `:533-539`), one transaction `:219`; **never DELETEs** |
| `enqueue` | 291-332 | W | DELETE row (only for `ChangeKind.DELETE` `:310-311`) or `_apply_locally` merge `:313`; then **INSERT** `pending_changes`; one transaction `:309` |
| `adopt_created` | 334-346 | W | UPSERT new id + **DELETE** the temp id, one transaction |
| `pending` | 348-354 | R | `ORDER BY id` |
| `pending_count` | 356-359 | R | `COUNT(*)` |
| `record_attempt` | 361-380 | W | UPDATE (attempts+1, error, next_retry) |
| `resolve` | 382-385 | W | **DELETE** pending row |
| `set_sync_state` | 389-416 | W | UPSERT on `id=1`; `None` **clears** |
| `stored_sync_state` | 418-429 | R | |
| `sync_state` | 431-434 | R | `ViewSource` shape |
| `_list_rows` / `_task_rows` | 438-448 | R | `ORDER BY (sort_order IS NULL), sort_order, id` / `ORDER BY id` |
| `_list_of` | 450-453 | R | inbox fallback `:453` |
| `_exempt_fields` | 460-475 | R | whole-row exemption when a pending `DELETE` exists `:470` |
| `_apply_locally` | 477-485 | W | `dict.update` merge, not replace |
| `_write_list` / `_write_task` | 493-540 | W | UPSERT, **returns `False` when unchanged** — this is the "only write changes" mechanism |

### Where a prune (#41) hooks in
- The only sane site is inside `apply_refresh`'s existing transaction: `store.py:219-243`, between the list loop `:220-221` and the task loop `:222-243` (or after both, still inside `with self._db:`).
- Required inputs that do **not** exist yet: (a) the set of remote list ids in this refresh and (b) the set of remote task ids **per container** — `apply_refresh` currently receives flat `lists`/`tasks` sequences and never learns which containers were successfully fetched, so a partial refresh would prune everything else. `engine.refresh()` `:483-501` knows the fetched set; that information must be threaded through.
- Must consult `_exempt_fields` (`:460-475`) so a task with a pending change (especially a pending `CREATE`, whose id is a local `local-<uuid>` `engine.py:1217-1223`) is not pruned — #41's AC "剪枝不误删待推送改动对应的任务".
- List prune must also not remove the synthesised inbox row (which does not exist yet — #33/#42).

### What a `views` table (#36) needs
New table (nothing like it exists): `id` (TEXT PK or INTEGER AUTOINCREMENT), `name` TEXT NOT NULL, `filter` TEXT NOT NULL (JSON of: list scope[], due-range relative to logical day, priority[], tags[], completion status), `sort` TEXT NULL, plus `created_at`. `Store` needs `views()` / `save_view()` / `delete_view()`; `ViewSource` (`sync/view.py:115-128`) is the protocol the engine reads through and must gain a `views()` member (or a sibling protocol), and **any new type must be re-exported through `dida.sync.engine`** or `tests/test_architecture.py:130-136` fails. Deleting a view must touch only that table (spec #36: "删视图不删除任何任务").

### What storing completed tasks (#37) needs
**Already 90% done.** `tasks.raw` stores whatever `apply_refresh` is handed, and `refresh_completed` hands it completed tasks (`engine.py:795`); `_snapshot` (`store.py:548-568`) already derives `completed` from `status == COMPLETED_STATUS` (`:563`) and `completed_at` from `completedTime` (`:564`). What is actually missing:
1. `completed_window_hours` default `24` → `168` (`config.py:101`, plus the fallback `engine.py:132`).
2. The completed fetch does **not** filter by status: `engine.py:788-792` sends only `startDate`/`endDate`; `client.py:94-100` has no `status` field in the body; `_completed_tasks` (`engine.py:1133-1145`) returns the payload unfiltered. So a task that was un-completed (its `completedTime` is *not* cleared — ADR-0002:18) will be pulled back and, because `_snapshot` reads `status`, it will land as **not completed** and reappear in a container as an unfinished task.
3. Prune (#41) must also apply to completed tasks, otherwise the local table grows without bound.

---

## 5. Test inventory

**34 files, 521 collected tests.** 15 files never touch `dida.tui` (318 tests); **19** files reference `dida.tui`/`DidaApp` (**203 tests** — this is exactly the spec's "203", so the brief's "20 files" is off by one).

| File | Tests | Imports `dida.tui`/`DidaApp` | LOC |
|---|---|---|---|
| `test_api_client.py` | 66 | no | 931 |
| `test_app_completed.py` | 8 | **yes** | 160 |
| `test_app_defer.py` | 3 | **yes** | 64 |
| `test_app_responsive.py` | 17 | **yes** | 307 |
| `test_app_shell.py` | 2 | **yes** | 37 |
| `test_app_view.py` | 15 | **yes** | 249 |
| `test_architecture.py` | 16 | **yes** (scans `src/dida/tui`) | 175 |
| `test_bootstrap.py` | 2 | **yes** | 39 |
| `test_clock_seam.py` | 2 | no | 23 |
| `test_completed_stream.py` | 9 | no | 287 |
| `test_complete.py` | 8 | **yes** | 252 |
| `test_config.py` | 42 | no | 248 |
| `test_date_parser.py` | 75 | no | 641 |
| `test_delete.py` | 10 | **yes** | 348 |
| `test_detail_description.py` | 4 | **yes** | 114 |
| `test_engine_view.py` | 5 | no | 76 |
| `test_escape.py` | 15 | **yes** | 250 |
| `test_fake_backend.py` | 2 | no | 27 |
| `test_first_run_unreachable.py` | 4 | no | 143 |
| `test_key_help.py` | 2 | **yes** | 107 |
| `test_logical_day.py` | 22 | no | 132 |
| `test_overdue_row.py` | 3 | **yes** | 114 |
| `test_priority_filter.py` | 21 | **yes** | 374 |
| `test_quick_add.py` | 16 | **yes** | 428 |
| `test_reschedule.py` | 22 | **yes** | 428 |
| `test_space_complete.py` | 2 | **yes** | 53 |
| `test_store.py` | 27 | no | 452 |
| `test_subtasks.py` | 17 | **yes** | 424 |
| `test_sync_defer.py` | 10 | no | 306 |
| `test_sync_push.py` | 11 | no | 356 |
| `test_sync_refresh.py` | 16 | no | 437 |
| `test_sync_session.py` | 20 | **yes** | 794 |
| `test_view_models.py` | 21 | no | 266 |
| `test_write_guard.py` | 6 | no | 175 |
| | **521** | 19 files / 203 | |

### Rescuable engine/API/storage assertions inside the 19 TUI-importing files

Legend: **R** = clean rescue (no `DidaApp`/`pilot`/`screen_text` needed) · **M** = MIXED (real UI test whose *payload* is an engine/wiring assertion — the engine half must be re-pinned in a non-UI file) · **D** = dies with v1 (no rescue) · **R\*** = rescue but the assertion itself must be **rewritten** first (it pins a v1 conclusion the spec overturns).

| File | Test (line) | Class | External behaviour pinned | Target file |
|---|---|---|---|---|
| `test_architecture.py` | **all 16 tests** `:125-175` | **R** | module importability of the 7 modules + the TUI import allow-list `ALLOWED_IN_TUI` `:31` + the AST scanner's own behaviour (`:139-175`) | **stays put**; `SEVEN_MODULES:27` must drop `dida.date_parser` |
| `test_app_view.py` | `test_the_tui_never_hardcodes_a_hex_colour` `:224-232` | R | no `#rrggbb` literals under `src/dida/tui` | `test_architecture.py` |
| `test_bootstrap.py` | `test_console_script_points_at_an_importable_main` `:33-39` | R | `pyproject` `dida` entry point resolves to a callable | `test_bootstrap.py` (stays) |
| `test_bootstrap.py` | `test_build_app_composes_the_shell_with_a_real_engine` `:16-30` | **R\*** | `build_app()` returns an app whose `.engine` is a real `SyncEngine` | rewrite to "engine wiring", drop the `DidaApp` half |
| `test_complete.py` | `test_completing_through_the_engine_pushes_the_complete_endpoint` `:95-124` | **R\*** | `complete()` → local `status==2`, `pending_count==1`, one `POST .../complete` with **empty body**, one `ChangeKind.COMPLETE` | `test_complete.py` (stays); **`:115` asserts "no uncomplete path" — directly contradicts #38, must be rewritten** |
| `test_complete.py` | `test_the_completion_key_is_not_adjacent_to_the_navigation_keys` `:225-232` | R | no complete-key collides with / neighbours `j k ↑ ↓` | new `test_keymap.py` (#48) |
| `test_complete.py` | `test_the_completion_key_is_not_part_of_the_cursor_cluster` `:235-240` | R | complete key ∉ `TaskPane.BINDINGS` | new `test_keymap.py` (#48) |
| `test_delete.py` | `test_deleting_through_the_engine_enqueues_one_delete_change` `:229-251` | R | `delete()` → local row gone, exactly one `DELETE` change, one `DELETE` request | `test_delete.py` (stays) |
| `test_delete.py` | `test_a_delete_that_cannot_be_pushed_does_not_come_back_on_the_next_refresh` `:257-294` | R | an unpushed `DELETE` is whole-row exempt from `apply_refresh` | `test_sync_refresh.py` |
| `test_delete.py` | `test_the_prompt_names_the_task_and_says_it_cannot_be_undone` `:334-340` | R | delete prompt names the task, says 找不回来 / 没有回收站 | `test_delete.py` (#40 AC) |
| `test_delete.py` | `test_the_prompt_never_implies_the_task_can_be_recovered` `:343-348` | R | prompt contains none of 恢复/撤销/撤回 | `test_delete.py` (#40 AC) |
| `test_detail_description.py` | `test_a_refresh_carries_them_from_the_server_payload_into_the_snapshot` `:88-114` | R | `apply_refresh` maps `desc`/`content`/`tags` from server payload onto `TaskSnapshot` | `test_store.py` |
| `test_escape.py` | `test_task_url_follows_the_vendors_copy_task_link_template` `:71-76` | R | `task_url` == vendor webapp hash route | `test_escape.py` (stays) |
| `test_escape.py` | `test_task_url_substitutes_the_literal_inbox_when_the_project_id_contains_inbox` `:79-84` | R | any inbox-ish projectId → literal `inbox` in the URL | `test_escape.py` |
| `test_escape.py` | `test_task_url_treats_any_inbox_containing_project_id_as_the_literal` `:88-90` (×3 params) | R | same, case/path-insensitive | `test_escape.py` |
| `test_escape.py` | `test_open_in_browser_hands_the_url_to_the_system_browser` `:96-107` | R | `open_in_browser` returns `webbrowser.open`'s truthiness | `test_escape.py` |
| `test_key_help.py` | `test_the_help_table_covers_every_key_the_app_and_the_panes_bind` `:73-81` | **R\*** | every bound key has a `KEY_HELP` row | new `test_keymap.py` (#48: help must derive from the binding table) |
| `test_priority_filter.py` | `test_p_walks_the_wire_values_0_1_3_5` `:106-108` (×4 params) | **R\*** | `next_priority` cycles the wire codes `0/1/3/5` | `test_view_models.py`; the `p` cycle key itself is out of v2 scope (#45) |
| `test_priority_filter.py` | `test_p_pushes_the_wire_value_and_marks_it_with_the_existing_marks` `:111-129` | **R\*** | priority change is locally instant *and* pushed as a wire code | `test_engine_view.py` |
| `test_priority_filter.py` | `test_a_failed_push_keeps_the_priority_change_in_the_retry_queue` `:132-147` | R | failed priority push stays queued, local value not revoked | `test_sync_push.py` |
| `test_quick_add.py` | `test_quick_add_creates_locally_and_pushes_the_whole_line` `:83-130` | **R\*** | `create()` → local-first, `POST /open/v1/task`, `projectId:"inbox"`, adopt server id | `test_sync_push.py`; strip the parser-derived date/priority/tags expectations (#39 is title-only) |
| `test_quick_add.py` | `test_a_failed_create_push_stays_in_the_retry_queue` `:133-162` | R | create push failure → queue, 2 s backoff, local task survives, id adopted on retry | `test_sync_push.py` |
| `test_quick_add.py` | `test_an_all_day_create_writes_the_date_marker_verbatim` `:165-195` | **R\*** | all-day `due` written verbatim (no logical-day shift) + reads as 今天 | `test_view_models.py` + `test_engine_view.py`; drop `engine.plan` |
| `test_quick_add.py` | `test_a_thin_create_response_does_not_drop_what_the_user_wrote` `:198-218` | **R\*** | `_adopt_created` merges instead of replacing | `test_sync_push.py`; drop `engine.plan` |
| `test_reschedule.py` | `test_reschedule_changes_only_the_due_date_and_pushes_at_once` `:104-135` | R | `reschedule` writes only `dueDate`+`isAllDay` and merges the snapshot back | `test_engine_view.py` (#44) |
| `test_reschedule.py` | `test_an_all_day_due_is_written_verbatim_as_a_date_marker` `:138-157` | R | all-day due is a date marker, not shifted into the logical day | `test_engine_view.py` (#44) |
| `test_reschedule.py` | `test_rescheduling_an_all_day_task_to_a_time_clears_the_all_day_flag` `:160-175` | R | all-day→timed must write `isAllDay: false` explicitly | `test_engine_view.py` (#44) |
| `test_reschedule.py` | `test_a_failed_push_keeps_the_reschedule_in_the_retry_queue` `:178-205` | R | pending payload is exactly `{dueDate, isAllDay}`; backoff 2 s | `test_sync_push.py` |
| `test_reschedule.py` | `test_rescheduling_a_task_outside_the_cache_is_refused_not_queued` `:208-223` | R | unknown task → `UnknownTaskError`, nothing queued, no request | `test_write_guard.py` |
| `test_space_complete.py` | `test_space_is_bound_to_the_same_action_as_x` `:49-53` | R | `space` and `x` share the `complete` action | new `test_keymap.py` (#48) |
| `test_subtasks.py` | `test_subtasks_show_title_and_completion_state_even_without_a_due_date` `:101-123` | R | `subtasks()` rows carry title + completed + `—` for dateless | `test_engine_view.py` (#43 read-only display) |
| `test_subtasks.py` | `test_a_task_without_subtasks_reads_as_no_rows` `:126-130` | R | no `items` → `()` | `test_engine_view.py` |
| `test_subtasks.py` | `test_toggling_a_subtask_rereads_then_writes_only_that_change` `:170-204` | **R\*** (decide) | read-before-write ordering; only this one entry flipped; unknown fields carried back | `test_write_guard.py` — **v2 says subtasks are read-only, so `toggle_subtask` becomes dead code; if #34 keeps the method, this must be moved, not deleted** |
| `test_subtasks.py` | `test_the_reread_adopts_the_server_copy_and_says_the_task_changed_elsewhere` `:207-223` | R\* (decide) | server authority lands in the store, not just in the report | same |
| `test_subtasks.py` | `test_a_reread_that_matches_what_we_already_had_is_not_a_change_elsewhere` `:226-236` | R\* (decide) | no false "changed elsewhere" | same |
| `test_subtasks.py` | `test_a_failed_push_stays_in_the_retry_queue` `:239-255` | R\* (decide) | failed subtask push stays queued | same |
| `test_subtasks.py` | `test_a_subtask_the_server_no_longer_has_is_not_written_back` `:258-269` | R\* (decide) | `written=False`, no request | same |
| `test_subtasks.py` | `test_our_own_pending_tick_survives_the_next_reread` `:272-296` | R\* (decide) | per-field ADR-0002 exemption protects a pending `items` change | same |
| `test_sync_session.py` | `test_the_first_run_verifies_the_token_before_storing_it` `:644-660` | R | paste → one `list_projects` → only then write; 0600; trimmed | `test_config.py` |
| `test_sync_session.py` | `test_a_rejected_token_is_not_stored_and_never_printed` `:663-676` | R | failure writes nothing; token never in the message | `test_config.py` |
| `test_sync_session.py` | `test_an_auth_rejection_tells_the_user_to_paste_again` `:679-689` | R | 401/403 → "重新粘贴", not a network error | `test_config.py` |
| `test_sync_session.py` | `test_the_quit_prompt_does_not_claim_the_queue_is_lost` `:432-456` | R | `quit_prompt` says "N 处" + "下次打开", never "丢了" | `test_write_guard.py` / new `test_quit_guard.py` (#47) |
| `test_sync_session.py` | `test_the_composition_root_wires_config_into_the_engine_and_the_app` `:577-638` | **R\*** | `day_end` from config reaches the view; `completed_window_hours=48` window; `push_on_change=false` queues; db path; token never on screen | new `test_bootstrap.py` cases |
| `test_sync_session.py` | `test_a_transport_that_cannot_be_built_does_not_stop_the_app` `:721-750` | **M** | unbuildable transport degrades to a structured `NetworkError`, not a startup crash | `test_first_run_unreachable.py` |
| `test_sync_session.py` | `test_r_calls_all_three_engine_entry_points` `:753-770` | **M** | `r` == `refresh` + `push_pending` + `refresh_completed` | new `test_sync_session`-style engine test |
| `test_sync_session.py` | `test_r_refreshes_pushes_and_pulls_the_completed_stream` `:180-205` | **M** | the three calls happen **in that order**, and the status bar shows the result | engine-level half → `test_sync_session` |
| `test_sync_session.py` | `test_r_pushes_what_is_waiting_in_the_queue` `:208-234` | **M** | `push_on_change=False` + `push_pending()` pushes exactly once, body carries the local value | → `test_sync_push.py` |
| `test_sync_session.py` | `test_offline_still_reads_the_cache_and_queues_writes` `:287-316` | **M** | offline: cache readable, write queued, refresh failure reported honestly | → `test_sync_session` |
| `test_sync_session.py` | `test_the_push_tick_retries_only_once_the_clock_reaches_the_backoff` `:322-353` | **M** | no request before `next_retry_at`; exactly one after | → `test_sync_push.py` |
| `test_sync_session.py` | `test_q_is_interrupted_once_and_says_how_many_are_pending` `:387-429` | **M** | `pending_count != 0` gates quit; exactly one confirm overlay | → new `test_quit_guard.py` (#47) |
| `test_sync_session.py` | `test_a_refresh_that_overwrites_local_changes_says_how_many` `:462-492` | **M** | `RefreshReport.overwritten` count surfaces as「N 处本地改动被覆盖」 | → `test_sync_refresh.py` |
| `test_sync_session.py` | `test_an_unpushed_change_is_not_counted_as_overwritten` `:495-521` | **M** | pending changes are not counted as overwritten | → `test_sync_refresh.py` |
| `test_sync_session.py` | `test_no_startup_request_when_the_config_turns_it_off` `:553-571` | **M** | `refresh_on_start=false` ⇒ zero requests | → `test_bootstrap.py` |
| `test_sync_session.py` | `test_a_completed_stream_failure_does_not_claim_the_whole_sync_failed` `:773-794` | **M** | partial failure is reported as partial | → `test_completed_stream.py` |
| `test_app_shell.py` | `test_shell_renders_three_panes_and_the_status_bar` `:17-28` | **M\*** | `SyncEngine(source=None)` degraded mode yields `已同步 — · 待推送 0 · 逻辑日 03-14` | `format_status` unit test + an engine degraded-mode test |
| `test_app_shell.py` | `test_q_quits_the_app` `:31-37` | **M** | zero pending ⇒ `q` exits immediately | → new `test_quit_guard.py` (#47) |
| `test_app_defer.py` | `test_g_defers…next_logical_day` `:30-39`, `test_shift_g…` `:42-52`, `test_the_defer_keys_do_nothing…` `:55-64` | **M** | `g`/`G` → `defer(id, days=1/7)`; empty screen → no call | engine half already covered by `test_sync_defer.py` (10 tests); keep the key→days mapping in a keymap test (#40) |
| `test_complete.py` | `test_pressing_x_completes_the_task_under_the_cursor` `:130-141`, `…on_an_empty_screen` `:144-155` | **M** | `x` → `complete(cursor task)`; empty → nothing | keymap test (#48) |
| `test_app_completed.py` | `test_completed_rows_are_dim_and_struck_through` `:108-122`, `test_the_completed_header…` `:125-143` | **D** | renderer + folded-section semantics — v2 drops the folding footer | — |
| `test_app_view.py` | `test_the_overdue_group_header_is_red_and_today_is_not` `:215-221` | **D** | v1 group headers | — |
| `test_overdue_row.py` | `test_an_overdue_row_is_red_and_a_today_row_is_not` `:50-56`, `test_the_overdue_row_keeps_its_plain_text` `:59-66` | **D** | v1 row renderer (behaviour re-pinned by #37) | — |
| `test_priority_filter.py` | `test_fuzzy_match_is_an_ordered_subsequence` `:236-237` (×6), `test_filter_groups…` `:240-255`, `:258-263` | **D** | `/` fuzzy filter — out of scope (#34) | — |
| `test_reschedule.py` | `test_the_reschedule_box_understands_the_parser_syntax_table` `:305-326` | **D** | v1 shorthand in the reschedule box — #44 replaces with a structured picker | — |
| `test_app_responsive.py` | all 17 | **D** | three-pane tiers, overlays, narrow-screen fallback | — |
| `test_app_completed.py` / `test_app_view.py` / `test_detail_description.py` / `test_escape.py` / `test_priority_filter.py` / `test_quick_add.py` / `test_reschedule.py` / `test_subtasks.py` / `test_sync_session.py` / `test_complete.py` / `test_delete.py` / `test_app_defer.py` | all `run_test`+`screen_text` tests not listed above | **D** | screen text / key presses / focus | — |

**Counts:** clean **R** = 35 (incl. the 16 in `test_architecture.py`); **R\*** (rescue-with-rewrite) = 15; **M** = 16; **D** = the rest of the 203. `R + R* + M` ≈ 66 individual test functions, i.e. **more than the spec's "约 50"**; the spec's estimate is low, mainly because `test_architecture.py`'s 16 boundary tests were counted as UI tests (they are not) and `test_sync_session.py`'s 17 "UI" tests are mostly engine-level payloads.

---

## 6. Date-parser consumers — #32 closing checklist / #34 deletion list

### Production call sites (every one)

| File:line | What it consumes | Fate |
|---|---|---|
| `src/dida/sync/engine.py:42` | `from dida.date_parser import ParsedTask, parse` (TOP) | delete the import; also breaks the module's `__all__`-adjacent docs at `:845-855` |
| `src/dida/sync/engine.py:234` | `Engine.plan(self, text) -> ParsedTask` protocol member | delete the member (#48/#39 replace with title-only create) |
| `src/dida/sync/engine.py:845-855` | `SyncEngine.plan()` → `parse(text, self._clock.now(), self._day_end)` `:855` | delete the method |
| `src/dida/sync/engine.py:857-874` | `reschedule()` docstring says the date "由 `date_parser.parse` 定" `:860`; body itself is clean (`api_date`) | rewrite docstring; keep the write path (#44) |
| `src/dida/sync/engine.py:898` | `create()` docstring: "拦它的是调用方：`plan()` 的 `diagnostics` 非空" | rewrite (#39 = title-only) |
| `src/dida/testing.py:24` | `from dida.date_parser import ParsedTask` (TOP) | delete; `FakeBackend.plan` is the only user |
| `src/dida/testing.py:291-293` | `FakeBackend.plan()` → `self._engine.plan(text)` | **delete** — this is what makes `FakeBackend` satisfy `Engine`; removing `plan` from both protocols keeps `isinstance` green |
| `src/dida/tui/app.py:513-526` | `action_quick_add`: reads `parsed.diagnostics`, refuses to submit `:525-526` | rewrite for title-only (#39) |
| `src/dida/tui/app.py:542-585` | quick-add entry: types a whole line, calls `engine.plan` → `engine.create(...)`; `diagnostics` gate `:563-564` | rewrite (#39) |
| `src/dida/tui/app.py:496-540` | `action_reschedule` → box → `engine.reschedule` (uses the parser indirectly; `:552-564` diagnostic gate) | rewrite (#44) |
| `src/dida/tui/panes.py:503` | comment on `RescheduleInput`: "语法归 `dida.date_parser`" | comment only |
| `src/dida/tui/panes.py:598` | comment on `QuickAddInput`: same | comment only |

> There is **no** other production consumer: `sync/view.py:235` only mentions `dida.date_parser` in a docstring, and `api/guards.py` has its own `api_date` date validation (unrelated, and it must stay — that is #44's "illegal date blocked locally").

### Test coverage of the parser

| File | Tests exercising the parser | Fate |
|---|---|---|
| `tests/test_date_parser.py` | **75** (whole file, `:24-641`) | delete (#34) |
| `tests/test_architecture.py:27` | `SEVEN_MODULES` entry `"dida.date_parser"` → 1 parametrized case of `test_module_is_importable` | **must be edited**, not deleted (#34) |
| `tests/test_quick_add.py` | 3 engine tests call `engine.plan(...)`: `:98`, `:185`, `:209`; 4 UI tests type shorthand: `:291`, `:319`, `:342` | rewrite (#32 rescues the create mechanics; #39 rewrites the UI half) |
| `tests/test_reschedule.py` | 4 UI tests type shorthand: `:259`, `:282`, `:390`, `:408` | rewrite (#44) |
| `tests/test_quick_add.py:5`, `tests/test_reschedule.py:5` | docstrings naming "`plan()` / `create()`" | docstrings |
| **Total parser-specific tests** | **75** (`test_date_parser.py`) **+ 1** (`test_architecture` param case) **+ 3** engine calls **+ 8** UI shorthand tests | |

Spec's "75 条测试" is exact for `test_date_parser.py`; the real deletion blast radius is 84 test functions/cases across 4 files.

---

## 7. Configuration surface

`src/dida/config.py:93-118` — `@dataclass(frozen=True) Config`; `__post_init__:103-108` validates + normalises.

| Key | Type | Default | Line | Read at | Notes |
|---|---|---|---|---|---|
| `token` | `str \| None` | `None` | `:97` | `bootstrap.py:110` (`config.token or ""`), `config.py:149` (`needs_token`), `config.py:204` | stripped `:106`; empty → `None`; `__repr__` masks it `:110-118` |
| `day_end` | `str` | `"24:00"` | `:98` | `bootstrap.py:133` → `SyncEngine(day_end=…)` | normalised to `"00:00"` `:108` via `_normalise_day_end:80-90` (`24:00`→`00:00` `:88-89`); legal `"00:00"`–`"24:00"` whole minutes; anything else → `InvalidDayEnd` `:55-56` |
| `refresh_on_start` | `bool` | `True` | `:99` | `bootstrap.py:158` → `DidaApp(refresh_on_start=…)` | type-checked `:71-74` |
| `push_on_change` | `bool` | `True` | `:100` | `bootstrap.py:137` → `SyncEngine(push_on_change=…)` | type-checked `:71-74` |
| `completed_window_hours` | `int` | **`24`** | **`:101`** | `bootstrap.py:136` → `SyncEngine(completed_window_hours=…)` | type-checked `:75-77` (bool rejected) |

Dup default: `sync/engine.py:132` `DEFAULT_COMPLETED_WINDOW_HOURS = 24` (used as the `SyncEngine.__init__` default `:418`). **#37 must change both `config.py:101` and `engine.py:132`, or a directly-constructed engine keeps 24 h.** `tests/test_config.py` currently pins nothing about `24` as a default (it only tests explicit values `:79, :148-149, :198-203`), so changing the default breaks no existing test.

### Logical-day boundary — how it reaches the engine, and why a change needs a restart

`config.toml` → `load_config()` (`config.py:129-144`) → `Config.day_end` → `build_engine` `bootstrap.py:133` → `SyncEngine.__init__` `engine.py:415` stored at `engine.py:422` (`self._day_end = day_end`) → used at `:444` (`status()`), `:456` (`group_tasks`), `:461` (`completed_section`), `:511` (`set_sync_state`), `:539` (`_defer_changes`), `:855` (`plan`), `:952` (`subtasks`).

**Restart is required because:**
1. `load_config()` is called exactly twice on the production path — `bootstrap.main():210` and `bootstrap.build_app():154` — both before the app runs; nothing re-reads the file afterwards.
2. `SyncEngine` exposes **no setter** for `day_end`; the only assignment is `engine.py:422`. `_day_end` is read imperatively at every one of the 7 sites above, so the whole logical day is a *construct-time constant* baked into the engine.
3. `DidaApp` (`tui/app.py:199`) has no config reference at all — it only holds `engine`, `_open_url`, `_refresh_on_start`, `_push_tick_seconds` (`:290-295`).
4. The persisted `logical_day` in `sync_state` (`store.py:394`, written at `engine.py:511`) is a *cache of the label*, and the cache-reading paths (`format_due` `view.py:336-357`, `due_day` `view.py:326-333`) take `day_end` as a parameter, not from the store — so a re-derivation must recompute rather than read.
5. #46 therefore needs three things that do not exist: a `SyncEngine.set_day_end()/reload_config()`, a logical-day recompute + `refresh_view()` that preserves the cursor (the cursor-preservation machinery already exists: `TaskPane.render_groups` `panes.py:294-330` keeps the cursor when the task is still present), and a chosen trigger.

---

## 8. API client surface

`src/dida/api/client.py` — `class DidaApiClient` `:43`.

| # | Method | Lines | HTTP + path | v2 status |
|---|---|---|---|---|
| 1 | `list_projects(*, offset=None, limit=None) -> list[dict]` | `:59-73` | `GET /open/v1/project` | keep; **#41**: the client already supports paging (`:67-71`) but the refresh path never passes them (`engine.py:483` calls it bare) |
| 2 | `list_tags() -> list[dict]` | `:75-78` | `GET /open/v1/tag` | **keep for #45** (tags picker) — but note §11: it has **no production caller today** |
| 3 | `list_completed(*, project_ids=None, start_date=None, end_date=None) -> list[dict]` | `:80-108` | `POST /open/v1/task/completed` | keep; **#37** wants a `status` condition — see §11 |
| 4 | `get_project_data(project_id) -> dict` | `:110-131` | `GET /open/v1/project/{id}/data` | keep; also caches snapshots via `_remember` `:129-130` |
| 5 | `get_task(project_id, task_id) -> dict` | `:133-140` | `GET /open/v1/project/{projectId}/task/{taskId}` | keep |
| 6 | `create_task(body) -> dict` | `:142-148` | `POST /open/v1/task` | keep |
| 7 | `update_task(project_id, task_id, changes, *, snapshot=None) -> dict` | `:150-177` | `POST /open/v1/task/{taskId}` (no PATCH — ADR-0002:30) | keep |
| 8 | `complete_task(project_id, task_id) -> None` | `:179-183` | `POST /open/v1/project/{projectId}/task/{taskId}/complete` (no body) | keep (#38) |
| 9 | `delete_task(project_id, task_id) -> None` | `:185-187` | `DELETE /open/v1/project/{projectId}/task/{taskId}` | keep (#40/#42) |

### Methods the spec says must be **added** in v2 — all absent today (verified: no occurrence of `task/batch`, `task/move`, `task/filter`, or any project write in `src/`)

| Needed | Endpoint | Blocks |
|---|---|---|
| list CRUD by id (create / update / delete / get) | `POST /open/v1/project`, `POST /open/v1/project/{id}`, `DELETE /open/v1/project/{id}`, `GET /open/v1/project/{id}` | #42 |
| task move | `POST /open/v1/task/move` | #45 |
| batch update (**the only uncomplete path**) | `POST /open/v1/task/batch` with `{update:[{id, projectId, status: 0}]}` | **#38** |
| task filter | `POST /open/v1/task/filter` (200-cap, no paging — ADR-0001:9) | #36 (not used for view evaluation) |
| search | `POST /open/v1/task/filter` w/ `search` or equivalent | no v2 ticket references search explicitly; §30 line 187 lists it |

**A blocker for batch-update that no ticket mentions:** `guard_writable` (`api/guards.py:157-169`) raises `FieldIgnoredError` for **any** body containing `status` (`NON_WRITABLE_FIELDS = ("status",)` `:49`), and `merge_snapshot` `:180-184` *strips* `status` from the snapshot. Any new `batch_update` method must bypass both or it can never send `status: 0`. The engine's own local-effect path deliberately keeps `status` out of the request body (`engine.py:726-736`) and relies on `complete_task` having no body (`:680-681`).

### Transport injection (the "narrow seam")
- Constructor: `DidaApiClient(*, token, transport: Transport, base_url=DEFAULT_BASE_URL)` `:46-57`; `Transport` is a `Protocol` with one `async send(request) -> response` (`api/transport.py:17-22`).
- Every request is built by `_request()` `:273-288` (Bearer header `:285`) and sent by `_send()` `:290-309`, which classifies failures **by status code only** (`:301-308`) — 401/403 → `AuthError`, other ≥400 → `ServerRejectionError`, `httpx.TransportError` → `NetworkError`.
- Tests pin request shape with `dida.testing.FakeTransport` (`testing.py:53-82`; `last_request`/`last_json` `:67-75`).

### The inbox literal
- The client itself has **no** inbox special-case: `project_id` is interpolated verbatim in methods 4, 5, 8, 9.
- The literal lives in **four** other places: `sync/view.py:38` `INBOX_ID = "inbox"`, `tui/escape.py:21` `INBOX_ID = "inbox"` (a **second, duplicated constant**, used to rewrite the deep-link `:32-34`), `storage/store.py:538/:559` (`projectId or INBOX_ID`), `sync/engine.py:486-489` (fetch the inbox when the index omits it) and `:914/:917` (`create()` always targets the inbox).
- `engine.py:1094` (`_unfinished_tasks`) is the one place where the literal is *written into a task payload* as a substitute for a missing server id — the trap #33 names.

---

## 9. Test fakes and seams

`src/dida/testing.py` (1021 lines, `wc -l`). **#86 之后这里没有第二份实现**：读、写、字段翻译、守卫、发号、队列记账全在真货（`Store` / `SyncEngine`）里，替身只剩「摆数据 + 记下调用」。下面的行号按 #86 之后的文件重数过。

| Class | Lines | Role |
|---|---|---|
| `ManualClock` | `:59-72` | `now()` `:65`, `advance(delta)` `:68`, `set(now)` `:71` — the clock seam implementation |
| `CreatedTask` | `:76-89` | one recorded create, **including its landing list** (`list_id`, #39) |
| `FakeTransport` | `:92-121` | records `requests`, replays a queue or a default `json=` response `:98-101`; `enqueue` `:103`; `last_request` `:107`; `last_json` `:112`; `send` `:116` (re-raises enqueued exceptions) |
| `InMemorySource` | `:183-354` | read-only double: `add_list` `:212`, `add_view` `:241`, `add_task` `:254`, `lists`/`tasks` `:336-339`, `resolve_id` `:342`; **the view + sync-state half is a real `Store(":memory:")`** `:201` — `set_sync_state(last_refresh_at=)` `:204`, `sync_state()` `:352` |
| `_FakeBackendServer` | `:357-437` | the fake *server*: records requests, answers per endpoint `:409`; `enqueue_for(method=, path=, response=)` `:389` pins a failure to **one endpoint** (order-independent); the inherited `enqueue` is **overridden to raise** `:382` (order-dependent injection was a real footgun) |
| `FakeBackend` | `:440-1021` | the seam-one fake |

### `FakeBackend` details
- Constructor: `FakeBackend(*, clock: Clock, day_end: str = "24:00")` `:460-575`. It builds `self.source = Store(":memory:")` `:462`, a real `DidaApiClient(token="tok", transport=self.transport)` `:466` and a **real** `SyncEngine(clock=clock, day_end=day_end, source=self.source, client=self.client)` `:568` — reads **and** writes both run the production path.
  - ⚠️ default `day_end="24:00"` is the **non-canonical** spelling; `logical_day.parse_day_end` accepts it (`logical_day.py:70` folds it to zero), so it works, but `Config` would have normalised it to `"00:00"` (`config.py:88-89`).
- Recorded attributes — **each one has a reader** (#86 deleted the three nobody read: `created_due` / `created_all_day` / `created_priority`; `reschedule_error` / `list_error` / `view_error` / `tag_loads` went the same way): `refreshes` `:467`, `pushes` `:470`, `manual_pushes` `:473`, `completed_pulls` `:476`, `completed` `:479`, `uncompleted` `:482`, `deferred` `:485`, `deferred_days` `:488`, `rescheduled`/`rescheduled_due`/`rescheduled_all_day` `:491-497`, `created` `:500`, `created_tags` `:503`, `created_tasks` `:506`, `deleted` `:509`, `moved` `:512`, `writes` `:517`, `write_error` `:524`, `delete_error` `:538`, `created_lists`/`updated_lists`/`deleted_lists` `:545-551`, `created_views`/`updated_views`/`deleted_views` `:554-560`.
- **Error injection**: `write_error` `:524` / `delete_error` `:538` are **method-level on purpose** — they simulate a *synchronous engine refusal* (no local payload), which a transport-level failure cannot reproduce (the optimistic write would land first). Everything else (tag list, pushes) is injected at the transport with `_FakeBackendServer.enqueue_for` `:389`.
- Methods (33 public: the 28 `Engine` protocol members + 5 setup extras `add_list` `:608` / `add_view` `:641` / `add_task` `:654` / `set_sync_state` `:707` / `set_tags` `:780`): `refresh` `:577`, `push_pending` `:586`, `refresh_completed` `:596`, `read_model` `:758`, `list_index` `:765`, `tasks_in` `:769`, `move_targets` `:773`, `load_tags` `:790`, `tags` `:794`, `task_detail` `:798`, `set_day_end` `:802`, `status` `:810`, `logical_day` `:814`, `complete` `:820`, `uncomplete` `:831`, `defer` `:838`, `reschedule` `:850`, `create` `:870`, `delete` `:906`, `move_task` `:919`, `write` `:935`, `create_list` `:965`, `update_list` `:973`, `delete_list` `:990`, `create_view` `:997`, `update_view` `:1002`, `delete_view` `:1014`, `view_definition` `:1019`.
- **Id generation**: `add_task` has none of its own — `InMemorySource._seq` `:200` increments per call and the id is `f"t{self._seq}"` unless `id=` is given. Custom ids in tests are always passed explicitly (e.g. `id="t1"`).
- **Timestamps**: only via the injected `clock` — `refresh_completed` uses `self.clock.now()` `:596`; `set_sync_state(pending_count=)` plants queue rows stamped with `self.clock.now()` `:707`.
- **Does it support creating a task in a named list? → YES** (fixed by #39). `FakeBackend.create(title, list_id=INBOX_ID, *, due, all_day, priority, tags)` `:870` hands the landing list to the real engine, so `tasks_in(<that list>)` really shows it and `created_tasks[].list_id` records it (`tests/test_fake_backend.py`).
- **`set_sync_state(pending_count=N)`** `:707` no longer overlays a number on `status()`: it enqueues N **real** changes into the real queue (`_enqueue_planted_change` `:728`), touching only a field the UI never draws (task `etag`, or list `color` when there is no task). `status()` `:810` is a pure delegation — the count is always what `Store.pending_count()` derives.


### `Engine` protocol
- `src/dida/sync/engine.py:333-528`, decorated `@runtime_checkable` (`:332`). 28 members: `set_day_end` `:336`, `status` `:345`, `logical_day` `:349`, `read_model` `:358`, `list_index` `:366`, `tasks_in` `:370`, `move_targets` `:374`, `task_detail` `:382`, `tags` `:386`, `load_tags` `:390`, `refresh` `:394`, `push_pending` `:398`, `refresh_completed` `:409`, `write` `:413`, `complete` `:432`, `uncomplete` `:436`, `defer` `:444`, `delete` `:451`, `move_task` `:455`, `reschedule` `:462`, `create` `:473`, `create_list` `:489`, `update_list` `:493`, `delete_list` `:502`, `create_view` `:506`, `update_view` `:514`, `delete_view` `:522`, `view_definition` `:526`. (#58 删掉的 `view` / `plan` / `cycle_priority` / `subtasks` / `toggle_subtask` 不在里面了。)
- `runtime_checkable` matters: `DidaApp(engine: Engine)` (`app.py:270`) is the single injection point, and **`isinstance`-style conformance is what keeps `FakeBackend` honest** — adding a protocol member without adding it to `FakeBackend` breaks `DidaApp(FakeBackend(...))` in every test file that injects the fake.

- 能力协议**不在 engine.py**：`ProjectReader` `refresh.py:36`、`RefreshTarget` `refresh.py:61`、`TaskWriter` `push.py:145`、`WriteTarget` `writes.py:132`、`CompletedReader` `completed.py:38`（五个都 `@runtime_checkable`；`TaskReader` 已全仓库不存在）。engine.py 里只剩 `SyncStatus` `:309` / `Engine` `:333` / `SyncEngine` `:531`，**一处 `isinstance(` 都没有**。
- 探测只有一处：`capabilities.py` 的 `_capability` `:54-56`，由 `Capabilities.of` `:101-116` 在引擎构造时问一遍；「缺这一件」怎么说是那几个访问器 `:119-180`（`write_target` `:119` … `completed_reader` `:175`）的事。


### `tests/support.py::screen_text`
`:11-14` — `app.screen._compositor.render_strips()` then `"\n".join(strip.text.rstrip())`. A second helper `screen_styled_text` `:17-24` renders each strip with ANSI so tests can assert "this row is styled differently" without hard-coding colours (used by `test_overdue_row.py` and `test_complete.py`). `docs/architecture.md:120` states this is the **only** place that touches Textual's compositor.

---

## 10. TUI architecture as it stands

### Widget tree (`tui/app.py:299-312 compose()`)
```
DidaApp (App[None])                                    :199
├── QuickAddInput(id="quick-add-input")   Vertical     :302   (panes.py:591)
├── Horizontal(id="panes")                             :303
│   ├── ListPane(id="list-pane")          VerticalScroll :304 (panes.py:244)
│   ├── TaskPane(id="task-pane")          VerticalScroll :305 (panes.py:266)
│   └── DetailPane(id="detail-pane")      VerticalScroll :306 (panes.py:417)
├── RescheduleInput(id="reschedule-input") Vertical      :308   (panes.py:499)
├── FilterInput(id="filter-input")         Vertical      :310   (panes.py:679)
├── Footer()                                             :311
└── StatusBar(id="status-bar")            Static         :312   (panes.py:453)
```
`Pane` base = `VerticalScroll` with `can_focus = True` (`panes.py:187`), j/k/↑/↓ bindings `:189-194`, a `_cursor` `:202`, `set_body(Text)` `:207-209`, and border-aware scrolling `:234-241`. The three panes are **decorative in v1**: `ListPane.selected_list_id` `:256-259` exists but nothing consumes it; `TaskPane` is the only interactive one.

Also present but not in `compose`: `SubtaskPane` `:817` (mounted by `DetailPane`, `can_focus=True` `:832` with an `allow_focus()` override `:866-874` that keeps it out of the Tab ring unless armed), `ConfirmScreen` `:744`, `OverlayBox` `:986`, `DetailScreen` `:1040`, `ListsScreen` `:1046`, `HelpScreen` `:1106`. CSS for the three-pane widths is `app.py:262-283` (`#list-pane width:20`, `#task-pane 1fr`, `#detail-pane width:34`).

### Bindings

**App-level — `tui/app.py:177-253`, `BINDINGS:203-249`** (all non-`priority` — deliberately, so `q` is a literal character inside inputs, comment `:205`):

| Key | Action | Label | Line |
|---|---|---|---|
| `q` | `quit` | 退出 | `:206` |
| `x` | `complete` | 完成 | `:210` |
| `space` | `complete` | 完成 (`show=False`) | `:215` |
| `g` | `defer` | 顺延 | `:216` |
| `G` | `defer_week` | 顺延一周 | `:217` |
| `e` | `reschedule` | 改期 | `:218` |
| `a` | `quick_add` | 新建 | `:219` |
| `d` | `delete` | 删除 | `:222` |
| `p` | `priority` | 优先级 | `:223` |
| `/` | `filter` | 过滤 | `:224` |
| `o` | `open` | 浏览器 | `:228` |
| `r` | `refresh` | 同步 | `:232` |
| `s` | `subtasks` | 子任务 | `:234` |
| `enter` | `toggle_detail` | 详情 | `:238` |
| `l` | `lists` | 清单 | `:242` |
| `question_mark` | `help` | 帮助 | `:245` |

**Widget-level**: `Pane.BINDINGS` `panes.py:189-194` (`j/down/k/up`); `TaskPane.BINDINGS` `panes.py:277-283` **re-lists all four cursor keys** plus `c` (`toggle_completed`) — the comment at `:275-276` explains the trap: *"Textual 里子类的 BINDINGS 是覆盖而不是追加，只写 `c` 会把继承来的 j/k/↑/↓ 一起屏蔽掉"*; `SubtaskPane` `panes.py` (j/k + `t`); `OverlayBox.BINDINGS` `:1020-1023` (`escape`/`enter` → `close`); `ConfirmScreen` `:744-800`, bindings `:776-780` (`y` confirm / `n` cancel / `escape` cancel).

**There is no explicit `tab` binding anywhere** — Tab switching is Textual's built-in focus ring over the widgets with `can_focus = True`: `Pane` (`panes.py:187`) and, when armed, `SubtaskPane` (`:832`, `:866-874`). `DetailPane.can_focus = False` `:429` (*"Tab 只在左栏与中栏之间切换"*), and the three input boxes set `can_focus = False` (`:525`, `:620`, `:699`) then focus their inner `Input` (`:553`, `:642`, `:719`). Focus is moved imperatively at `app.py:317, 537, 583, 650, 728-734, 769`. Tab appears in only two places outside Textual: the KEY_HELP row `panes.py:1076` and `tests/test_key_help.py:60`.

### Status bar
- `StatusBar` — `panes.py:453-454`, a bare `Static`.
- **`format_status(status: SyncStatus) -> str` — `panes.py:457-461`**, current format string (`:461`):
  `f"已同步 {last_refresh} · 待推送 {status.pending_count} · 逻辑日 {logical_day}"`
  with `last_refresh = strftime("%H:%M")` or `"—"` (`:460`), `logical_day = strftime("%m-%d")` or `"—"` (`:459`). **Plain text, no styling** — #34's remaining work is *highlighting when `pending_count != 0`*; the string is already correct per GLOSSARY.
- Written by `DidaApp.update_status()` `app.py:470-472`; also overwritten ad-hoc for transient messages (`app.py:334` 同步中, `:357` refresh failure, `:371` overwritten count, `:373` completed-stream failure).

### Where `TEXTUAL_DISABLE_KITTY_KEY` is (or isn't) set
**It is set nowhere.** Verified by `grep -rn 'kitty|TEXTUAL_|environ|setenv|putenv'` over `src/`, `tests/`, `pyproject.toml`: the only hits are the ADR document `docs/adr/0006-disable-kitty-keyboard-protocol.md:3,5` and unrelated `monkeypatch.setenv("HOME", …)` in `tests/test_config.py:33`. `dida/bootstrap.py:202-218` (`main`) never touches `os.environ`, and `pyproject.toml` has no `[tool.*]` env section. So spec #30 §"终端能力" and ADR-0006 describe an intention that **v1 never implemented**; #34 must add it (before `App.run()`, i.e. in `bootstrap.main` or `DidaApp.on_mount`/`__init__`).

### What ticket #34 must delete
1. **Three-pane layout** — `app.py:262-283` (CSS), `:303-306` (compose), `panes.py:178-451` (`Pane`/`ListPane`/`TaskPane`/`DetailPane`); `#list-pane width:20` etc.
2. **Tab focus switching** — the built-in ring over `Pane.can_focus` (`panes.py:187`), `DetailPane.can_focus = False` (`:429`), `SubtaskPane.allow_focus()` (`:866-874`), the `Tab` help row (`panes.py:1076`), `BUILT_IN_KEYS = {"Tab"}` (`test_key_help.py:60`).
3. **List overlay** — `app.action_lists` `:795-804`, `ListsScreen` `panes.py:1046-1051`, `Binding("l", …)` `app.py:242`.
4. **Narrow-screen responsive fallback** — `WIDE_MIN_WIDTH`/`MEDIUM_MIN_WIDTH`/`pane_tier()` `app.py:175-197`, `on_resize` `:411-420`, `_apply_tier` `:422-431`, `_tier` `:255-257`, `_detail_open` `:259-261`, `action_toggle_detail` `:772-793`, `DetailScreen` `panes.py:1040-1043`, `OverlayBox` `:986-1037`, `tests/test_app_responsive.py` (17 tests).
5. **Kitty-protocol handling** — nothing to delete; it must be **added** (see above).
6. Also necessarily gone with the above: `a`/`e`/`/`/`p`/`s`/`t`/`c`/`enter`(detail)/`l` bindings, `QuickAddInput`/`RescheduleInput`/`FilterInput`, `SubtaskPane` as a focusable widget, `KEY_HELP` in its current all-layers form, `tests/test_app_*.py`, `test_overdue_row.py`, `test_detail_description.py`, `test_key_help.py`, `test_priority_filter.py`(UI half), `test_quick_add.py`(UI half), `test_reschedule.py`(UI half), `test_space_complete.py`(UI half), `test_subtasks.py`(UI half), `test_complete.py`(UI half), `test_delete.py`(UI half), `test_escape.py`(UI half).

---

## 11. Surprises

1. **`TEXTUAL_DISABLE_KITTY_KEY` is not set anywhere in the codebase.** ADR-0006:5 and spec #30:261 both state it is set at startup; grep over `src/`, `tests/`, `pyproject.toml` finds only the ADR text. #34's AC "启动时 kitty 键盘协议推送已关闭" is currently **unimplemented**, and the Chinese-IME acceptance item (#43) depends on it. Also note the ADR's second claim ("`constants.py` 与 `_xterm_parser.py` 都会认它") is about Textual internals, not this repo.

2. **The `desc`/`content` mapping is backwards relative to the v2 glossary, and a test pins the wrong one.** `GLOSSARY.md:31-37` (already merged on this branch) says **描述 = `content`, 备注 = `desc`**. The code and tests say the opposite: `sync/view.py:93-101` labels `desc` as "描述" and `content` as "备注/正文"; `detail_body()` renders them in that order — `panes.py:160` `for label, value in (("描述", item.desc), ("标签", item.tags_text), ("备注", item.content))`; and `tests/test_detail_description.py:53-59` **asserts** `"描述  本周的三件事"` for `desc="本周的三件事"`. Spec #43 says *"v1 标反了，这份 spec 纠正它"*. So #32 must rescue that test **with the field mapping flipped**, and `tests/test_detail_description.py:8-9`'s justification (*"字段映射按 api-contracts.md 的 Task 字段表"*) is an over-claim: `api-contracts.md:58` only lists the field names, it never says which is the description.

3. **A rescued test asserts the exact opposite of ticket #38.** `tests/test_complete.py:115` — `"完成只有一个方向：一条 COMPLETE 改动，没有本地「取消完成」这第二条路"` — and its docstring `:96-99` repeats it. ADR-0002:13 was already amended to say uncomplete is real, and #38 exists to build it. Moving this test verbatim into a non-UI file would **enshrine a conclusion the spec overturned**. It is the single highest-risk item in the ~50-test rescue list.

4. **`guard_writable` forbids `status` on every write body, which is the only way #38 can work.** `api/guards.py:49` `NON_WRITABLE_FIELDS = ("status",)`, enforced at `:157-169`, plus `merge_snapshot` strips `status` from the snapshot `:180-184`. The new `batch_update(status=0)` path must bypass both. No ticket mentions this coupling.

5. **`test_detail_description.py`'s "no empty rows" test and `tests/test_app_view.py`'s hex-colour test are the only style-level guards; the latter is the one thing in `test_app_view.py` worth rescuing.** `test_the_tui_never_hardcodes_a_hex_colour` (`:224-232`) is an architecture rule from `docs/architecture.md:33` and has nothing to do with v1's UI; it should live in `test_architecture.py`.

6. **`test_architecture.py` is counted as one of the "203 UI tests" but is not a UI test at all** — all 16 cases are module-boundary AST assertions (`:125-175`). #34 deleting it would remove the only guard that keeps new view types flowing through `dida.sync.engine` (`ALLOWED_IN_TUI:31`). Conversely it **will fail the moment `date_parser.py` is deleted**, because `SEVEN_MODULES:27` names it — so #34 must edit that list (and #32 must not "rescue" the file into a new name without keeping the allow-list).

7. **`FakeBackend.create()` cannot target a list, and `InMemorySource.add_task` silently invents the list row.** `testing.py:307-333` hard-codes `list_name=INBOX_NAME` `:329`; `InMemorySource.add_task:141` does `self._lists.setdefault(...)`, so a test that creates "into 工作" would show a 工作 row that the fake invented. This is precisely #39's AC and it is a **silent-pass** trap, not a crash.

8. **`FakeBackend.create()` records `tags` but never puts them on the task** — `testing.py:326` appends to `created_tags`, `:327-333` omits `tags=` in the `add_task` call. So `backend.created_tags` and what the view shows can disagree.

9. **`Store.list_records()`, `DidaApiClient.list_tags()`, `ListPane.selected_list_id` have no production caller.** `list_records` `store.py:267-279` (only `tests/test_store.py:49-51,377-380`); `list_tags` `client.py:75-78` (only `tests/test_api_client.py:122-127, 675, 850, 902-915`) — **but #45's tag picker will need it**, so do not delete it; `selected_list_id` `panes.py:256-259` (only `tests/test_app_view.py:194`). The brief lists all three as cut candidates; only the third is genuinely dead in v2.

10. **`bootstrap.py:190`'s docstring names a class that does not exist.** It says the degraded transport "抛的是 :class:`_TransportUnavailable`", but the class is `_UnusableTransport` (`:42-58`) and it raises `NetworkError` (`:54`). The behaviour is right; the docstring is stale. `tests/test_first_run_unreachable.py` covers the real behaviour.

11. **`engine.py:965` attributes `changed_elsewhere` to the wrong type.** The docstring says *"由 `RefreshReport` 如实记下，其中 `changed_elsewhere` 报给用户"*, but `RefreshReport` (`store.py:160-172`) has only `written_lists`/`written_tasks`/`overwritten`/`suppressed`; `changed_elsewhere` is a field of `SubtaskWrite` (`engine.py:1299-1313`, field at `:1310`) and is computed locally at `:979`.

12. **"Completed tasks are not stored" is wrong — they already are; the real #37 defects are elsewhere.** `refresh_completed` writes fetched tasks through the same `apply_refresh` (`engine.py:795`) and `_snapshot` already reads `status`/`completedTime` (`store.py:563-564`). What is actually missing: (a) the window default 24 h (`config.py:101` + `engine.py:132`), (b) **no `status` filter on the completed fetch** — neither the request (`engine.py:788-792`, `client.py:94-100`, and `api-contracts.md:50` confirms the body is `{projectIds[], startDate, endDate}` with **no status field**) nor the response (`_completed_tasks` `engine.py:1133-1145`). Because un-completing does not clear `completedTime` (ADR-0002:18), **an un-completed task is pulled back by the window and lands as unfinished**, i.e. it reappears in the list. #37's AC "拉取已完成流时同时按完成时间与状态过滤" is only implementable as a **local** status filter — the ticket's phrasing implies a server-side parameter that the documented contract does not have.

13. **#41's premise as stated in the ticket is exactly right, and the code confirms it:** `api/client.py:59-73` *does* accept `offset`/`limit` (`:67-71`), and `engine.refresh()` calls `reader.list_projects()` with **no arguments** (`engine.py:483`); `ProjectReader.list_projects` is declared with no parameters at all (`engine.py:274`). So the fix is in the refresh path **and** the protocol signature, not in the client. Beyond 200 lists, `_project_index` (`engine.py:1055-1068`) would silently accept a truncated page — there is no "did I get them all" signal anywhere.

14. **`GLOSSARY.md:70` contradicts ADR-0005 and spec #36 about where views live.** The glossary says a custom view *"只存在本地配置文件里"* (the config **file**); ADR-0005:5 and #36 say the local **SQLite DB**, explicitly *not* `config.toml` (because that file holds the token). The ADR's own filename is stale too: `docs/adr/0005-custom-views-live-in-local-config.md`, while its body says the opposite. Fix the glossary + filename alongside #36, or implementers will write view definitions into the credential file.

15. **Duplicate `INBOX_ID = "inbox"` constant.** `sync/view.py:38` and `tui/escape.py:21` define it independently, and `escape.py:32` does a *substring* match (`if INBOX_ID in project_id.lower()`), so a list whose id merely contains "inbox" gets rewritten to the literal in deep links. #33 changes inbox identity everywhere else; these two constants must be unified or the escape hatch will silently disagree with the categorisation.

16. **`FakeBackend`'s `day_end` default is `"24:00"` (`testing.py:169`), a spelling `Config` normalises away** (`config.py:88-89` folds it to `"00:00"`). Harmless today because `parse_day_end` accepts both (`logical_day.py:58-70`), but any test or code that keys on the canonical string ("is it `00:00`?") will disagree with the fake.

17. **`_unfinished_tasks` manufactures the literal `"inbox"`** (`engine.py:1094`): a task the server returned without `projectId`, fetched via the inbox request, is stored with `projectId: "inbox"`. `store.py:538`/`:559` do the same on the write side. Together they are why #33's AC "缺失的 projectId 的任务不再被写成本地字面量 `inbox`" has **three** code sites, not one — and why `summarize_lists` (`view.py:278-286`) can never match those rows to the real per-account inbox id.

18. **`create()` hard-codes the inbox in two places** (`engine.py:914` payload `project_id=INBOX_ID`, `:917` `list_id=INBOX_ID`) and the `Engine` protocol's `create` signature has **no target-list parameter** (`engine.py:242-251`). #39 has to change the protocol, `SyncEngine`, and `FakeBackend` together or `isinstance` conformance silently breaks.

19. **`_by_due` (`view.py:360-364`) has no priority tiebreak**, and both `TaskGroup.items` orderings come from it. Spec #37's "截止时间升序 → 优先级从高到低 → 无日期在后 → 已完成沉底" is a new comparator; the v1 one is `(due, title)` for dated and `(title, task_id)` for undated. `tests/test_view_models.py` and `test_engine_view.py` (26 tests, non-UI) currently pin the v1 ordering, so #37 will have to change them.

20. **The inbox list row is missing on a normal account, and the fetch that would supply it never creates it.** `engine.py:486-489` appends `"inbox"` to the fetch list only when the id is *not* in the index — which is always, since the server index excludes the inbox (ADR-0001:11) — but the row is only appended to `lists` if the payload happens to carry a `project` object with an id (`engine.py:496-501`). So the inbox row is absent from `ListPane` and from `view().lists`, `summarize_lists` (`view.py:278-286`) reports no badge for the inbox, and any task whose `projectId` is the real per-account id has no matching `ListSummary` — `task_item` then falls back to printing the raw id as the list name (`view.py:266` `names.get(snapshot.list_id, snapshot.list_id)`). This is exactly story 112 / #33's "客户端自己补上这一行".
