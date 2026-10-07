# Progress log

Kept by the orchestrator so that a dead subagent (one already died holding 188 lines of uncommitted work)
or a fresh orchestrator can resume without re-deriving anything. Append, don't rewrite.

Branch under construction: **`feat/v2-terminal-client`** in `/home/tofu/dida-v2-worktrees/integration`.
Base: `origin/main` = `4548577`. Baseline suite: **521 passed**.
Delivery (user-confirmed): draft PR → `main`; **comment on tickets, never close**; hard checkpoint after #34.

## Wave 1 — #32 清路

Worktree `/home/tofu/dida-v2-worktrees/t32`, branch `ticket/32-clear-path`.

| Commit | What |
|---|---|
| `646a9b9` | Part 1: engine `WriteKind` + storage `ChangeKind` collapsed into `dida.sync.writes.WriteKind`. |
| `9ef14d0` | Part 2a: `sync/engine.py` 1347 → 312 lines + 8 responsibility modules (`refresh`/`push`/`completed`/`schedule`/`create`/`priority`/`subtasks`/`writes`). Arch tests 16/16 green. |
| `5d6e556` | Part 1 finished: `writes._BEHAVIOUR` — one table per write holding local effect, wire call, `marks_completed`, `whole_row`; `Store.enqueue` / `_exempt_fields` / `PushMixin._send` / `_local_effect` read the table. |

**Status 2026-xx:** the first implementer process **died** after finishing the three commits above, leaving
188 lines of *green* uncommitted work. The orchestrator inspected it, confirmed `531 passed`, and committed
it as `5d6e556` without altering its content (noted in the commit body). A replacement implementer was
launched to finish:
- **part 3** (priority 1) — rescue ~66 engine/API assertions out of the 203 TUI-file tests;
- **part 2b** — split `tui/app.py` (812 lines) by responsibility;
- the closing `date_parser` consumer checklist.

**Nothing has been merged to the integration branch yet.** `#33` and `#41` are still blocked.

### #32 — DONE and merged

Replacement implementer finished it: `584 passed`, 53 assertions rescued into 11 non-UI files, `tui/app.py`
812 → 338 lines. The two original commits were **rebased to add the missing `(#32)` reference** (content
verified byte-identical via `git diff backup-t32-pre-32ref HEAD`; backup tag retained). All 12 commits now
name the ticket, so the final code review's `#NN` scraping sees the whole range.

Merged into `feat/v2-terminal-client` as **`4b91b46`** (`--no-ff`, conflict-free: the integration branch had
not moved). Verified in the integration worktree: **584 passed**, `import dida.bootstrap` OK,
`test_architecture.py` 17 passed.

Branch list after the rebase (old SHAs → new): `646a9b9`→`73804c1`, `9ef14d0`→`3ea133f`, `5d6e556`→`733d0aa`,
tip `c60afc2`→`706ba9f`. Worktree `t32` now sits on `706ba9f`.

- Pushed to `origin/feat/v2-terminal-client` — **note the SSH-proxy bypass in `brief.md`**, a plain
  `git push` hangs.
- **Draft PR #50** opened: https://github.com/Miaotofu01/Dida-TUI/pull/50 (body at `/tmp/dida-v2/pr-body.md`;
  it carries a ticket-progress table to update as waves land).
- Ticket comment left on #32; **issue left open** per the delivery decision.

## Wave 2 — #33 + #41 (both DONE and merged)

Worktrees `/home/tofu/dida-v2-worktrees/t33` (branch `ticket/33-engine-read-shapes`) and
`/home/tofu/dida-v2-worktrees/t41` (branch `ticket/41-refresh-complete-and-prune`), both cut from `4b91b46`.
Per-ticket instructions pre-written at `prompt-33.md` and `prompt-41.md`.

| Merge | Ticket | Merge commit | Suite |
|---|---|---|---|
| 1 | #41 刷新拿全 + 剪枝 | `087a354` (clean, tree byte-identical to `baeea93`) | 584 → **593** |
| 2 | #33 引擎三种读形状 | `44d4b75` (clean — **but read the note below**) | 593 → **617** |
| — | orchestrator: collect the account id out | `7e9c38a` | **617**, unchanged |

**#33's merge was textually clean, which is exactly when a wrong result goes unnoticed.** The two intents met
in `refresh()`'s fetch-loop body and in `store.py`'s `_write_list` without git flagging anything. The #33
merger read both sides by hand and found the interaction genuinely load-bearing: **#41's list-prune exemption
keys on `is_inbox`, and #33's synthesized inbox row is the only thing that sets it** — the configured row is
not in the server index, so a naive prune deletes the row #33 just added, and once a refresh can't re-learn it
there is nothing left to restore it. **There was no test for that combination**, so the merger added
`test_the_client_added_inbox_row_survives_a_prune_that_cannot_re_add_it` (mutation-checked: dropping
`and not row["is_inbox"]` turns it red) plus a zero-writes/zero-prunes companion.

### Benign history artifact to clean when the pump-race fix merges

`fix/pump-teardown-race` was branched from `baeea93` and later merged `feat/v2-terminal-client` **while the
integration branch was momentarily at `517c16c`** — the SHA the #33 merger created and then amended twice
(`517c16c` → `d96554b` → `44d4b75`) to set its message and fold in its two tests. The flake agent read the
branch inside that window. Reflog confirms this; **no agent ever wrote to the integration worktree**, which is
clean and on the right branch.

Two consequences, neither harmful:
1. That branch's tree is **missing the #33 merger's two added tests**, so its reported test count runs ~2 low.
   Merging it into the integration tip brings them back, because the merge base is `087a354`.
2. It carries a redundant duplicate merge commit whose subject claims it merged #33 into
   `feat/v2-terminal-client`, which is misleading history for a reviewer.

**So merge it as: `git rebase 7e9c38a fix/pump-teardown-race`** (replays only its real commits, dropping both
merge commits), then `git merge --no-ff`. Verify the count lands at **617 + the fix's own new tests**.

### Landmine for later waves, confirmed by the #33 merger

`tui/escape.py:16` now imports `dida.sync.engine` at module top level. Fine today, but it would bite if
`dida.sync.engine` ever imports the TUI. And **#34 deletes `tests/test_escape.py`**, so #33's deep-link
assertions in **`tests/test_deep_link.py` are the survivors** — #34 must not delete that file.

**Both touch the sync engine's read/refresh surface and run concurrently — expect a real merge conflict.**
#33 owns the read model, #41 owns the refresh path. Merge them **one at a time** (a merger subagent per
branch), and remember that `codebase-map.md` predates #32's engine split, so its file/line pointers into
`engine.py` are stale — `refresh.py` is the likely new home for the paging sites.

### Traps already handed to #32 (do not let a later agent re-derive them)

- `test_complete.py:115` — rescue the assertion, rewrite its stale rationale message. **Do not** touch
  production (`engine.py:519`, `app.py:436` are #38's).
- `test_detail_description.py:53–59` — v1's 描述/备注 mapping is backwards; the flip is #43's. Rescue the
  mapping-neutral half only.
- `test_architecture.py` (16 AST boundary cases) must survive as a file; `SEVEN_MODULES:27` keeps the
  `dida.date_parser` entry until #34 deletes the parser.
- `tui/app.py` split must match the seam names in `wave-plan-and-seams.md` — seven wave-4 tickets each need
  to own a file.

## Unplanned work: the pump teardown race (diagnosed by the orchestrator)

**Not a ticket.** Found while verifying #41, and it had to be fixed because it reddens ~1 run in 35 of *every*
full-suite verification from here on — which would have corrupted the check for each of the 16 remaining
merges, and tempted a merger to "fix" it by weakening an assertion.

**Diagnosis (read from the code, not guessed):** `tui/app.py`'s `on_mount` (~`:202`) starts the retry-queue
pump with `self.set_interval(self._push_tick_seconds, self.push_tick)` and **discards the timer handle — there
is no `on_unmount`**. `push_tick` (~`:277`) is `await self.engine.push_pending()` **then**
`self.update_status()`, and `update_status` (~`:226`) is `self.query_one(StatusBar).update(...)`. If shutdown
begins while `push_pending()` is awaiting, the widgets unmount, the await resumes, and `query_one` raises
`NoMatches: StatusBar`. Every path that resumes after an await into a widget touch has the same hazard.

**This is a production defect, not only a test flake** — production runs the pump at a 1-second interval
(`PUSH_TICK_SECONDS`), so quitting mid-push can hit the same window.

Symptom: `tests/test_sync_session.py::test_the_periodic_timer_pumps_the_queue_without_any_keypress` (the only
test using `push_tick_seconds=0.05`, `:366`) fails ~1/35. The repo's own comment at `:480` already calls
real-time-waiting tests 「看运气」.

Fix delegated to worktree `/home/tofu/dida-v2-worktrees/t-flake`, branch `fix/pump-teardown-race` (based on
`baeea93`, #41's tip): keep the timer handle and stop it in `on_unmount`, **plus** a guard for a tick already
in flight — step one alone cannot close that window. Not a blanket `NoMatches` swallow: a missing status bar
during normal operation is still a bug. Commits name `#41` and `#34` (the ticket that observed it, and the
ticket that owns the file).

**Fix complete and MERGED** — branch tip rebased `7a8805a` → `4448143`, merged as **`f08b32d`** (integration tip).
Suite **622 passed**; the flake test **20/20 clean on the merged tip** (implementer's measurement: 5/100 red on
base `baeea93`, 0/100 on the fixed tip). Pushed. The mechanism was confirmed from the traceback, not
assumed: `App._shutdown` sets `_running = False` **first**, then awaits `_close_all()` while unmounting, so an
in-flight tick resumes inside that window; `Timer._tick` routes the exception to `app._handle_exception`, and
`run_test` re-raises it at teardown — which is why it surfaced as a teardown error far from the failing line.

The merge also cleaned up the history artifact recorded above: rebasing dropped **3** merge commits including
the misleading `517c16c`, proved topology-only by an empty `git diff 7a8805a HEAD` and an identical tree object
(`4eb63e89beef6ecd96a5384c215390101dbd064e`). No force-push anywhere — the fix branch was never pushed to origin.

**Four landmines for #34 (all relayed to it):**
1. **The status bar now has exactly ONE write entrance: `DidaApp._write_status`.** Writing
   `query_one(StatusBar).update(...)` directly — which v1 did in about a dozen places, and which is the obvious
   thing to write — **re-opens the window this fix closed.**
2. The guard's correctness depends on a **Textual internal**: `App._shutdown` sets `_running = False` before
   awaiting `_close_all()`. Not a documented contract — a Textual upgrade needs this re-verified.
3. **Textual 8.2.8 has no `Shutdown` event**; `on_unmount` is the only teardown hook. The timer *stop* is
   lifecycle hygiene (Textual's own `_close_messages` also cancels app timers) — the **guard** is what closes the
   race, but the stop is still part of the contract and is pinned.
4. `test_the_pump_timer_is_stopped_when_the_app_unmounts` is **white-box on purpose** (it asserts our own
   `_push_timer`; no external assertion distinguishes a stopped timer). **Move it with the pump, don't delete it.**

What landed: `_write_status()` as the single status-bar write entry, dropped only when `not self.is_running`
(a missing bar while running still raises — deliberately not a blanket swallow); the pump's `Timer` handle kept
and stopped in `on_unmount`; `refresh_view()` no-ops when the screen is gone; the `FLASH_SECONDS` callback
no-ops when the app is gone; and an `is_running` return after the `await` in `on_subtask_pane_toggled`, **a
second instance of the same hazard that the orchestrator had not spotted** (that path also did
`query_one(DetailPane)`).

Evidence: the flaky test 100 runs → **5 failures on base `baeea93`, 0 on the fixed tip**. The reproduction is
**deterministic, no sleeps** — it gates the fake server, blocks a real `asyncio.create_task(app.push_tick())`,
tears the app down, then releases the gate.

**Honest caveats (do not "improve" these):** the `on_unmount` stop is lifecycle hygiene — Textual's own
`_close_messages` also cancels app timers, so the *guard* is what closes the flake; Textual 8.2.8 has no
`Shutdown` event, so `on_unmount` is the only teardown hook; and the "timer is stopped" test asserts our own
`_push_timer` (white-box on our lifecycle contract) because no external assertion distinguishes it.

**General pattern for later tickets:** after every `await`, ask `self.is_running` before touching the DOM.
`Timer._tick` swallows callback exceptions, so this class of bug surfaces as a *test-teardown* error far from
its cause.

**#34 must carry two things forward** (it is rewriting exactly these files): both halves of this guard, and the
regression test — which currently sits in `tests/test_subtasks.py`, a file #34 deletes. The property to keep is
that **the write still lands** even though the screen is gone. See `prompt-34.md`'s "Preserve the pump-teardown
guard" section.

## Wave 3 — #34 (in flight) — the user's acceptance checkpoint

Worktree `/home/tofu/dida-v2-worktrees/t34`, branch `ticket/34-ui-rewrite`, cut from `7e9c38a`. It merged the
integration tip (`f08b32d`) **before** starting real work, so it inherits the teardown fix as its base rather
than having to reconcile it — the conflict risk that motivated the merge-order worry never materialised.

Instructions: `prompt-34.md` (seams, traps, the teardown guard, the deletion manifest) plus two follow-ups sent
while it ran: the four `_write_status` landmines from the fix's merger, and the documentation scope addition
below.

### Scope addition the user approved: v1 documentation that #34 invalidates

**Not in any ticket — a real gap in the ticket set, found by the orchestrator.** The user chose to fold it into
#34 rather than open a new ticket (his words: the ticket that makes a doc wrong is the one that should fix it),
and to **leave the example list/task names alone**.

- **`README.md` is entirely v1**, and `AGENTS.md` calls it 「面向使用者的入口」. It is *accurate today* (the code
  still is v1's three panes) and **#34 is what invalidates it**. Wrong parts: the opening positioning
  (「把**「今天该做什么」这一屏做到极致**」 — the replaced v1 position, ADR-0004); 「v1 已经能用」 + the three-pane
  screenshot + the two narrow-screen degradations; the whole `## 一行语法（a 新建与 e 改期共用）` section
  (documents the date parser #34 deletes — v2 新建 takes only a title); and the key table (`c 已完成`,
  `/ 过滤`, `s 子任务`, `t 勾选`, `e 改期`, `l 清单`, `Tab` 切栏 are all gone). #34 should **regenerate the
  screenshot from the real app** via `FakeBackend` if that path survives — a hand-drawn render is how a README
  starts lying again — and otherwise describe the layout in words rather than inventing a picture.
- **`docs/ui-mockups.md`** still opens with 「**已选定 C（三栏：清单 + 今日 + 详情）**」, the decision ADR-0004
  overturned. It must be **marked superseded, not deleted**: it is a deliberate design record of four candidate
  layouts, and nothing links to it, so only the *decision* statement needs to stop reading as current.

Nothing to do about the example names (`工作`, `生活`, `学习`, `交季度报告`, `研究 v2ray 分流规则`, …) — the file
declares them 「示例数据」 and the user was shown them and said to leave them.

## Not yet started

Waves 2–5. Wave 2 = #33 + #41 (both unblock when #32 merges, and they can run concurrently). Wave 3 = #34
(also needs #33). **Stop there for the user's acceptance.** Wave 4 = #35, #37, #42, #43, #46, #47, #48.
Wave 5 = #36, #38, #39, #40, #44, #45.

Draft PR opens **after the first merge into the integration branch** (a branch with no commits ahead of
`main` cannot open one). `git push --dry-run` to `origin/feat/v2-terminal-client` already verified working.

## Wave 4 — #47 退出拦截 (DONE on its branch, not yet merged)

Worktree `/home/tofu/dida-v2-worktrees/t47`, branch `ticket/47-quit-interception`, cut from `3b2a607`.
Tip `bbb3b3e`. Full suite on the branch: **466 passed** (454 baseline + 12 new in
`tests/test_quit_interception.py`). The integration tip had not moved, so "merge
`feat/v2-terminal-client`" was a no-op.

**The ticket's premise is factually wrong in a way that matters, and the fix is bigger than it says.**
`Ctrl+C` did **not** take "another default exit path" — in Textual **8.2.8** it quits *nothing*:
`App.BINDINGS:463` binds `ctrl+c → action_help_quit`, which only fires a toast 「Press q to quit the app」
and returns (`app.py:3990-4004`). The direction of the bug is the opposite of the ticket's story: the
back door was there in reverse — **pressing Ctrl+C did nothing, said nothing useful, and there was no
judgement to bypass.** AC items 2 and 4 are still the right thing to ask for (same judgement; direct
exit when idle), and both are now true, so the ticket's *intent* holds even though its *diagnosis* does
not. Recorded rather than silently reinterpreted.

**The real defect the ticket did not know about, and the reason the change is in three files.** Textual
resolves keys through `Screen._modal_binding_chain`, which **truncates at the last modal widget** — so
for a `ModalScreen` the `app._bindings` are gone. Consequences, both measured in a real pty:
- `Ctrl+C` on **any** overlay: `Screen.BINDINGS:272` binds `ctrl+c → screen.copy_text`, and `Screen`
  is not in the modal chain — so the key hit an invisible "Copy selected text" action. Nothing on
  screen, nothing said.
- `q` on the **`?` help overlay**: not bound anywhere reachable either (`q` lives on the pages and on
  `Screen`), so the help overlay had **no working quit key at all**.

So a single binding in `keys.py` is not enough: the overlays need their own. They use Textual's action
namespace (`Binding(key, "app.quit")` — `app` is in `App._action_targets`) so both keys and all three
layers land on the *same* `DidaApp.action_quit`. A plain `"quit"` there fails silently: it resolves to
the overlay, which has no `action_quit`, and the binding "matches but does nothing" — the key gets eaten.

**Landmines for later tickets:**
1. **`DidaApp.layer` shadows Textual's `Widget.layer`** (`widget.py:2605`, returns the CSS layer name).
   The app returns `"index"`/`"tasks"`/`"detail"` there. Pre-existing, not introduced by #47, benign in
   8.2.8 (nothing on the App path reads it — verified), and the same class of bug as `_animate` and
   `TasksPage._name`. It wants deciding on by whoever owns the navigation stack, not by a quit ticket.
   My AST scan found **no other** class-level or instance shadow in `src/dida/tui/` beyond the
   intentional framework overrides (`BINDINGS`, `DEFAULT_CSS`, `compose`, `__init__`, `action_quit`,
   `can_focus`, `TITLE`).
2. **Any ticket that adds an `Input`/`TextArea` must re-decide `Ctrl+C`.** `Screen.BINDINGS` binds it to
   copy precisely so that Ctrl+C in a text box does not quit. #47 has made `ctrl+c` a GLOBAL binding
   (via `keys.py`'s `QUIT_ACTION`/`QUIT_KEYS`), and a focused `Input` **consumes** keys via
   `check_consume_key` — so #42/#39 must confirm the copy path still wins inside a text field, or the
   quit prompt fires while someone is pasting a token.
3. **A quit key over an overlay stacks a confirm box on top of it** (measured: `Screen, MessageOverlay,
   ConfirmOverlay`). Cancel returns to the help overlay rather than closing it. That is coherent but
   worth a look when #42's form shell lands.
4. The `_finish_quit` guard is `confirmed and self.is_running`; **the method touches no DOM**, so the
   "after every await ask `is_running`" rule does not apply to it — the rule is about DOM touches, and
   the reason is written in the docstring so the next editor does not mistake its absence for an
   oversight. `action_quit` is the same: `push_screen` and `App.exit()` are both synchronous, and the
   only real `await` is the caller's `_dispatch_action`.

**Verification beyond the suite:** all six cases (pending × {`q`, `Ctrl+C`} plus confirm/cancel) were
run in a **real pty** under `env -u NO_COLOR TERM=xterm-kitty COLORTERM=truecolor`, checking both that
the child process stays alive and that the prompt text actually reaches the terminal. All six correct.
`q / ctrl+c` now renders in `?`'s help (the binding table is still the single source).

**Kept alive, not re-expressed:** the files holding the wording assertions were **not** deleted by #34,
so `tests/test_app_actions.py:276` (`q`) and `test_sync_push.py::test_a_restart_still_has_the_queue_and_pushes_it`
(the factual basis) both survive; #47 added the `Ctrl+C` half of the same assertion. The `quit_prompt`
wording in `messages.py` is unchanged.

## Wave 4 — the graph had a missing edge, found during implementation

**#35 and #37 are siblings in the task graph** (both blocked only by #34), but #37's acceptance criteria need a
read-model field that **#35 owns**: `TaskItem.overdue`. #35's evaluator decides it (`sync/views.py::evaluate_view`
via `is_overdue`, the logical-day judgement the TUI is architecturally forbidden from making), `task_item()` fills
it, `read.py` passes it through.

This is exactly the kind of dependency the graph cannot see: both tickets are *independently implementable* on
paper, but if both add the field the merger gets a duplicate-field conflict with no authority to pick between them.
Handled by handoff rather than by re-cutting the graph:

- #35 sent an **early seam handoff** rather than waiting to finish, which is what made this catchable.
- #37 was told to **consume** `TaskItem.overdue`, never re-add it, and to stop and report if the field is missing
  after merging the integration tip rather than defining its own.
- **#35 must land before #37.** If #35 stalls, #37's overdue AC cannot be met.
- #35 also had to rewrite another ticket's guard: `tests/test_theme.py`'s
  `test_the_overdue_token_is_a_token_and_nothing_more` asserted `not hasattr(TaskItem, "overdue")` with the message
  「那是 #37 的工单」. That "not yet" assertion expired the moment #35's AC needed the bit. **#37 was told not to
  edit that test**, or the two branches collide on the same one-line change.

Two more handoffs from #35 worth carrying:

- `TaskList.shows_list_name` (True for views, False for real lists) is the field #37's prompt calls "the read model"
  — keep the wiring into `task_line(item, list_name=...)`.
- **A measured trap for any colour assertion:** `CursorPage._redraw` does `line.stylize(theme.SELECTED)` on the
  cursor row, which **overrides span colours in that row** — so **an overdue row under the cursor renders in the
  accent colour, not red.** Any "the selected overdue row is red" test fails for the wrong reason. #35 moved the
  cursor off the row; #37 was told to do the same or raise the design question.

## From #47 (quit interception) — a factual correction and a silent-key trap

- **The ticket's diagnosis was wrong in direction.** `Ctrl+C` in Textual 8.2.8 did not take "another default exit
  path" — it quit **nothing**: `App.BINDINGS` binds it to `action_help_quit`, which fires a toast
  「Press q to quit the app」 and returns. The real defect was that **no judgement ran at all**, and that overlays
  had no reachable quit key. The AC was still right and is now true; the implementer kept the real diagnosis in
  code comments rather than silently rewriting the ticket.
- **⚠ A modal's bindings cannot reach the app's.** `Screen._modal_binding_chain` truncates at the last modal widget,
  so while an overlay is open **`app._bindings` are unreachable**. Measured: `q` on the `?` help overlay was bound
  **nowhere reachable at all** (the help overlay had no working quit key), and `Ctrl+C` on any overlay silently hit
  `Screen.BINDINGS`'s invisible `screen.copy_text`. Keys a modal needs must be bound **on the modal** as
  `Binding(key, "app.quit")` — a plain `"quit"` resolves to the overlay, which has no `action_quit`, and the key is
  **eaten silently**. `overlays.py` now carries `QUIT_BINDINGS`; #42 (and #36 reusing its shell) were told to follow
  it and to test with the modal actually mounted.
- **`Ctrl+C` is now a global quit binding**, so every ticket adding an `Input`/`TextArea` (#42, #39, #44, #45, #36)
  must **re-decide** it — Textual binds `Ctrl+C` to *copy* precisely so Ctrl+C in a text box doesn't quit, and a
  focused `Input` consumes keys. Undecided means the behaviour depends on focus, which reads as a bug.
- **`DidaApp.layer` shadows `Widget.layer`** — pre-existing, benign in 8.2.8, same class as `_animate` /
  `TasksPage._name`. A shadow-scanning guard must not assume it is clean.

## Orchestrator verification: #35's overdue semantics (checked against merged code, not the report)

I asked #35's merger to independently verify two semantic points. I then checked them myself by reading
`3f4374e`'s `src/dida/sync/view.py` — **both hold, and the structural outcome is the one that matters**:

```python
def is_overdue(snapshot, *, today, day_end) -> bool:
    if snapshot.completed or snapshot.due is None:
        return False
    return due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end) < today
```

- **Completed is never overdue** — guarded explicitly, with the reason in the docstring ("它压根不该在那个视图里
  ——三个内置视图都只收未完成的").
- **All-day due is compared by logical day, not instant** — it delegates to `due_day(...)`, so a `04:00` boundary
  does not retroactively make today's all-day marker overdue. No second date comparison was written.
- `git log -S "def is_overdue"` → added once, by #35's `9d47c7f`. **There is exactly one copy in the tree.**
- **#37 adopted it rather than re-adding it** (as instructed): its worktree stages #35's `views.py`/`view.py`
  through an in-progress merge. So the two tickets cannot drift apart on this judgement.

**The conflict site for #37 is `src/dida/sync/read.py`** (`UU` — both tickets changed the read model): #35 added
`shows_list_name`, #37 added the client-side completed-status filter (`d4b0f88`). The resolution must keep both;
#37's merger should confirm that, and confirm no second `is_overdue` appeared.

## Orchestrator check: the ambiguous-width discipline (scanned the whole tree, found no new bug)

The user's locale is **`zh_CN.UTF-8`** (`locale.getlocale()`), i.e. the CJK condition under which terminals draw
East-Asian-**Ambiguous** characters as **2 cells** while `rich` measures **1**. I scanned all of `src/` for
EAW=A: **21 distinct characters, 779 occurrences** — but the ones that matter are concentrated in prose
(docstrings/comments), and the design already handles the rest deliberately:

- `tests/test_theme.py::test_structural_glyphs_are_one_cell_and_never_ambiguous` asserts every
  `STRUCTURAL_GLYPHS` entry is rich-1 **and** not EAW `A`/`W`/`F`, and its docstring **names
  `LANG=zh_CN.UTF-8` as the reason**. `▣ ★ · ─ ╭╮╰╯ ↑↓` are named there as the rejected set — and every
  occurrence of those in `theme.py` is inside a docstring explaining *why* they were rejected. No glyph
  constant uses one.
- `test_only_end_of_line_glyphs_may_be_ambiguous` makes the allowance explicit: ambiguous width is permitted
  **only as the last glyph on a line** (the truncation mark), where nothing after it aligns. It asserts
  `east_asian_width(ELLIPSIS) == "A"` as the **premise** of that allowance, so if the premise ever changes the
  test reds and forces a re-decision. That is the right shape.
- Truncation is Textual's own CSS (`#page-body { text-wrap: nowrap; text-overflow: ellipsis }`), so the `…`
  is appended at the end of the clipped line — the allowance holds. Render-level test:
  `test_visual_identity.py::test_a_long_title_is_clipped_with_an_ellipsis_and_never_wraps`.
- Only weak spot is the **top bar**, where `WORDMARK_ICON` (Nerd Font, EAW=A) sits mid-line ahead of the
  breadcrumb. Already recorded on **#52**; nothing new.

**New finding, small: `theme.clip()` is dead code.** `clip(text, width)` has **no production callers and no
tests** — `grep` over `src/` finds only its definition, and the only truncation test exercises the CSS path.
It is still exported in `theme.__all__`. Landmine: a wave-5 ticket reaching for `theme.clip()` would get a
**second** truncation path that bypasses the CSS one (double-clipping, or a `…` where Textual had already
clipped). Truncation is the CSS path; either delete `clip()` or say why it stays. Owner: #52.

### Two corrections to my own entries above (found by checking, not by assuming)

1. **The help-width guard is at `tests/test_keymap.py:108`, NOT `tests/test_theme.py`.** I wrote the wrong file into
   `brief.md:245` and told #42 and #46 the wrong path; #47's merger caught it. `brief.md` is fixed. The guards are
   split: `test_keymap.py` owns the *help body* width, `test_theme.py` owns the structural-glyph rules,
   `test_visual_identity.py` owns the render-level SGR/clip assertions.
2. **`theme.clip()` is NOT dead code** — my scan was correct for integration at that moment, but **#37's rewrite
   makes it live** at `src/dida/tui/pages/tasks.py:125` (the title is clipped to the remaining cells after the
   discard ladder). So the #52 item is withdrawn: `clip()` stays, and its CJK behaviour (a 1-cell remainder is
   padded with a space) is real and load-bearing. **Lesson recorded: a "dead code" finding against a branch must be
   re-checked against the in-flight ticket branches before it is reported.**

Also verified while checking #37's glyph choices: `↻ ⚑ ☑ ☐ ▪ ▸ ✦ ⋮ ⚠ ❯` are **all** rich-1 **and** EAW=N, so
`STRUCTURAL_GLYPHS`' guard passes on the real reason and not by accident.

## Orchestrator check: #37's client-side filter cannot cause data loss (verified independently of the merger)

I flagged this as #37's highest-risk point, then read it myself rather than waiting for the merge report.

- `completed.py:121` calls `target.apply_refresh(tasks=tasks)` — **no `prune_*` arguments**, so both default to
  `False` = "no completeness asserted". **The completed path cannot prune anything.**
- The only pruning call in the tree is `refresh.py:146`, on the **per-list full refresh**, which passes
  `prune_lists=True, prune_unfinished_tasks=True` — the one path where the caller genuinely asserts completeness.
  `subtasks.py:100` also passes no `prune_*`. So a row dropped by #37's client-side status filter can never be
  treated as absent.
- `truncated` is counted on the **received** batch (`len(received) >= COMPLETED_PAGE_LIMIT`), not the filtered one,
  and the cursor advances on `received` too. That is the conservative direction on both: counting on the filtered
  set would let a truncated batch look complete, and advancing the cursor on the filtered set would re-fetch the
  dropped rows forever.
- The comment states the measured reason the filter must be client-side at all: the endpoint has no `status`
  parameter (only `projectIds`/`startDate`/`endDate`, and the dates filter `completedTime`), and **cancelling a
  completion does not clear `completedTime`** — so a time-only filter pulls cancelled tasks back and they then
  appear as unfinished.

**One honest observation:** `CompletedReport.truncated` has **no consumer anywhere in `src/`** (grep over
`src/dida/` finds only its definition). So the careful choice above is latent correctness today, not an active
behaviour. It is the right value and must stay; a future ticket that starts consuming it must consume **this**
one, not a re-derived filtered count.

## #46 finished the rollover fix — and the correction paid for itself

New tip `4b1ffbd` (merged `d276aa3`), **514 passed** on its own base (492 + 22 in `test_live_day_boundary.py`,
up from 17). The check now lives **inside `reload_day_boundary()`**, ahead of any network call, on both triggers
(`push_tick`, `action_refresh`), and **not** behind the `_day_boundary is None` early return — the config read and
the rollover check are separate blocks, so an app with no config reader still notices the boundary moving.

It wrote exactly the test my correction implied: `test_r_follows_the_clock_even_when_that_refresh_fails` uses a
`FailingRefresh` backend to stage "pressed `r`, sync failed, `_sync()`'s `refresh_view()` never ran" and asserts the
index count still moves 2 → 3. **Had I not corrected my own wrong claim, that path would have stayed broken and
untested.** Worth remembering: my "`r` is already covered" was a plausible reading of the code that only reading
`_sync()`'s `else:` branch disproved.

Two implementation details worth carrying to later tickets:

- **`_view_day` is recorded in `refresh_view()` *before* the rows are drawn.** Recording it after would let a
  boundary crossed inside those few statements store a day *newer* than the rows, and that screen would then never
  be claimed by the rollover check again. Ordering trap, not a logic trap.
- **New cheap seam, cost measured:** `SyncEngine.logical_day() -> date` is pure arithmetic over the injected clock
  and the current boundary — **5.5 µs, zero I/O** — versus `status()` at **13.7 µs including two `sync_state()` SQL
  reads**. The heartbeat uses the former, so the 1-second tick does **not** add a database read.

**Landmine for wave 5:** the `@runtime_checkable` `Engine` protocol now has **two** new methods — `set_day_end` and
`logical_day`. **⚠ Corrected at the #46 merge: only `logical_day` belongs in `engine.__all__`.** `set_day_end` is a
*method*, so there is no module-level object — `hasattr(engine, "set_day_end")` is False, and listing it makes
`from dida.sync.engine import *` raise `AttributeError` (probed by the merger, and re-probed by me). The protocol is
what carries it, and `Engine` **is** in `__all__`. **The original wording of this note was half wrong.** `tests/test_fake_backend.py` asserts `isinstance(FakeBackend, Engine)`, so a hand-written fake engine
that omits either fails there, and the failure points at `isinstance` rather than at the missing method.
`FakeBackend` **delegates** both to the real engine rather than storing its own day boundary — a fake that records
its own boundary would make the screen disagree with the engine.

## Orchestrator smoke test: the integration tip comes up on a real tty (8 merges in)

Independent of the mergers' checks, I ran the **real entry point** (`uv run dida`) on a **real tty**
(`pty.openpty()` + `TIOCSWINSZ` 30×100, `TERM=xterm-kitty`, `COLORTERM=truecolor`, `NO_COLOR` cleared,
`LANG=zh_CN.UTF-8`, temp `HOME` with a **dummy** token and `refresh_on_start = false` — zero network,
the user's real config and account untouched). Script: `/tmp/smoke_tip.py`.

Result at tip `d276aa3`:

```
came up on a real tty : True
exit code after 'q'   : 0
traceback in output   : False
```

It paints the list-index page correctly: `❯ ▪ 收集箱 0`, then `▸ 今天 0` / `▸ 最近七天 0` / `▸ 所有 0`
(inbox pinned, built-in views marked), the top bar `dida ▸ 清单列表页`, and the status bar
`已同步 — · 待推送 0 · 逻辑日 10-06`. `q` exits **cleanly with code 0** — which also exercises #47's
quit path end-to-end on a real tty rather than under `run_test()`.

**Two incidental findings, both already recorded on #52:**

1. **The first-run token prompt is a plain prompt, not a Textual widget** — and `q` types a literal `q` into it,
   so `q` does **not** quit there. My first run therefore ended at exit code 143 (my own SIGTERM) with the screen
   reading `粘贴滴答清单的 API Token（输入不回显…）：`. That is a **live instance of exactly the Input/`q` concern**
   I raised with #42: inside a text input `q` is text, so the only way out is `Ctrl+C` (which works — with nothing
   selected, `screen.copy_text` raises `SkipAction` and our `quit` runs). Worth a line of help text at that prompt.
2. **Both chrome rows lack `nowrap`/`text-overflow`.** `#top-bar` and `#status-bar` are `height: 1; width: 100%`
   with neither property, while `#page-body` has both — so the status bar can lose its tail (`逻辑日 …`) at narrow
   widths, the same class as the top bar's dangling `▸`. Folded into #52 item 1.
   The status bar's `·`/`—` **are** mid-line ambiguous-width characters (the one place I found that violates the
   "end-of-line only" allowance) but `status_line()` does no column math and nothing right-aligns after them, so
   it cannot misalign anything — recorded as an observation, **not** as a defect.

## #42 landed the shared form shell — and found a real bug on the task path (now issue #53)

#42 tip `783b048`, **566 passed** on its own base (454 baseline; +31 of its own). Two things need to travel:

**1. Issue #53 — the task write path has a stuck-queue bug that #42 fixed for lists and did not fix for tasks.**
I verified the asymmetry myself rather than taking the report's word; it is two adjacent functions in
`src/dida/storage/store.py`:

- `adopt_created_list()` re-points queued changes onto the server id —
  `UPDATE pending_list_changes SET list_id = ? WHERE list_id = ?` — with the reason in the docstring.
- `adopt_created()` (task) does only `_write_task(payload)` + `DELETE FROM tasks WHERE id = ?` — **there is no
  matching `UPDATE pending_changes SET task_id = ?`.**

So: create a task offline → edit it offline → push. The create succeeds and the local row is adopted, but the
queued **edit** still carries the temp id, POSTs to `/open/v1/task/local-0d0e…`, gets a 404 from the real server,
backs off, and **never leaves the queue**. The status bar stays non-zero forever and the edit never reaches the
server — the same "quiet error" that `UnknownTaskError` exists to eliminate, in its own words.
Fix shape is #42's list version (re-point **and** re-read the queue each push iteration) + two tests.
**Folded into `prompt-39.md`** (it owns the task create/adopt path; lowest marginal cost), with a
"don't reintroduce it" line in `prompt-40.md`.

**2. A record-keeping flag for the final Spec-axis review:** #42's two **merge** commits carry git's automatic
subject ("into ticket/42-list-crud") rather than the `#42` form, because the ticket number is not in the `#NN`
shape the review scrapes. #42 flagged this itself. Any Spec-axis pass that maps commits → tickets must read the
**branch name** or the work commits, not only `#NN` in subjects. Same is likely true of other tickets' merge
commits.

**3. `#36` inherits a shell, it does not build one.** `tui/overlays.py` is now
`FormOverlay(title=, fields=)` with `FormField`/`FormOption`/`ChoiceField`; the field set is a parameter and a test
proves the shell is list-agnostic by driving it with a view-condition-shaped field set. The **Ctrl+C rule is already
implemented**: a modal-local `Binding("ctrl+c", "app.quit", priority=True)` built from #47's
`QUIT_ACTION`/`QUIT_KEYS`. **`priority=True` is load-bearing** — without it `screen.copy_text` sits earlier in the
binding chain and the key's meaning follows "did the user select text". `q` is deliberately unbound in the form (a
letter belongs to the field), so **`Esc` is the exit**. Cost is documented: no Ctrl+C copy inside the form.
Also inherited: view rows refuse `e`/`d` with an honest message and write nothing; the inbox row refuses too
(it is client-synthesised, so a rename would be reverted by the next refresh).
**Layering precedent:** `LIST_COLORS` (API data) lives in `sync/lists.py`, **not** `tui/` — `tui/` holds only visual
constants.

**4. Disclosed, not silent:** #42 added one "has a pending list change" exemption each to #41's `_prune_lists` and
to `apply_refresh`'s list loop, so that a just-created list survives a refresh (ADR-0002's rule). Both tested, both
in the commit message and the report.

## ENDGAME STATE (orchestrator, written when the subagent limit was hit at 8 active children)

### Merge ledger — `feat/v2-terminal-client`

| # | merge commit | suite after |
|---|---|---|
| #32 | `4b91b46` | 584 |
| #41 | `087a354` | 593 |
| #33 | `44d4b75` | 617 |
| pump-race fix | `f08b32d` | 622 |
| #34 (user checkpoint) | `062042c` | 407 (deletions) |
| ADR-0007 + #51 | `51e5060` `86611e5` `045d010` `45c8faa` | 454 |
| #51 follow-ups | `3b2a607` | 460 |
| #48 | `9b45dfc` | 460 |
| #35 | `3f4374e` | 480 |
| my doc fix (`views.py:15`) | `fdbd8a4` | — |
| #47 | `d276aa3` | 492 |
| #37 | `2f5d92a` | 535 |
| my truthfulness fix (`__all__` + `TaskItem` sketch) | `aee4850` | — |
| #42 | `3919583` | 566 |
| #43 | `e62ef13` | 594 |

**Still to merge: #46** (branch tip `4b1ffbd`, 514 on its own base — it must re-merge integration first, since it is based on `d276aa3` and #37/#42/#43 have landed since).

### In flight at the time of writing (8 children = the limit)

`#36` custom views · `#38` complete↔uncomplete · `#39` create task (incl. the **#53** fix) · `#40` delete+defer ·
`#42`'s author on **#54** (list-path no-id adoption) · `#43`'s author on **#55** (humanise reminders) ·
`#43`'s merger · `#44` due date.

**Waiting for a slot: `#45` picker fields** — the last spec ticket. Its prompt is written and carries the
「`POST /open/v1/tag` is a documented endpoint」 correction plus the `move` shape traps.

### Issues opened during wave 4/5 (all outside the spec's ACs, all with measured evidence)

- **#53** — task path: a queued change keeps the temp id, POSTs to `/open/v1/task/local-…`, 404s forever.
  **Amended**: the original fix (re-point on adoption) is **insufficient**, because with the documented
  `201 Created → No Content` **adoption never happens**. #39 is implementing both outcomes.
- **#54** — list path, same class but a different root cause: `201 No Content` ⇒ `_adopt_created_list` returns
  early ⇒ every later rename/delete is permanently unpushable, the list renders twice, and **a deleted list comes
  back** (= #42's AC7 failing). Assigned to #42's author; **it defines the mechanism**, #39 mirrors it.
- **#55** — the detail page renders `"  ".join(detail.reminders)`, i.e. raw `TRIGGER:P0DT9H0M0S` to the user.
  Judged to be #43's AC ("read-only reminders") not fully met, so assigned to #43's author. The hard constraint:
  **the sign convention for negative durations is undocumented — do not guess.**

### Remaining endgame

1. Launch **#45** when a slot frees; launch **#46**'s merger when another does.
2. Merge #36/#38/#39/#40/#44/#45/#54/#55 as they land, serially in the integration worktree.
3. Final **`code-review`** pass on `feat/v2-terminal-client` vs `origin/main` (`4548577`) — two axes, Standards and
   Spec. **Note for the Spec axis: it keys on `#NN` in commit subjects, so it must also read branch names and work
   commits** — several tickets' merge commits carried git's automatic subject before being amended.
4. Mark **PR #50** ready (it is a draft).
5. Clean up the ticket worktrees.
6. **The user still has one manual step**: type Chinese with a real IME into the detail page — no test can do it.

## #54 landed — and the unification question it raised, with my ruling

`ticket/54-no-id-adoption` @ `2509e0cd`, based on `e62ef13`, baseline 594 → **604 passed** (+10 in
`tests/test_list_identification.py`). Mechanism **A**, with four invariants documented in `dida/sync/lists.py`:

1. `is_addressable(change)` — an id the server has never seen is never sent to; **the single decision point**.
2. An unconfirmed create owns the **only** queue entry for that row; later renames **fold into it**.
3. **Identification is its own step** — a body-less create turns the entry into an identification record (the name
   actually sent + the ids known before the create); the next full refresh matches **by name, only when unique**,
   against the server set it just wrote — **no extra network request**.
4. A delete of an unconfirmed list is never dropped: it waits for the id, then deletes the real row (and
   identification removes the row the refresh had just written, so nothing "comes back").

Two deliberate behaviour changes: `pending_count()` **ignores clean identification records** (a 201-created list owes
the server nothing, so the status bar must not show a number that never moves), and the prune exemption is
**narrowed to dirty changes** (a pure identification record that is absent from the server index is a shadow and
should be pruned). It also **changed one of #42's existing test expectations on purpose**
(`..._renamed_still_gets_both_changes_out` → `..._goes_out_as_one_change`, 2 entries → 1) and said so — the old
expectation pinned exactly the shape #54 exists to remove.

**It asked whether the mechanism should be unified across both queues. My ruling: unify the *judgement*, not the
*machinery*.**

- **Unify:** the "is this id addressable?" decision must have **one** home. Two queues each growing their own
  `if x.startswith("local-")` is precisely the "one judgement, two implementations" failure this repo already paid
  for with `is_overdue` (where a merge produced two copies). So #39 is instructed to reuse `is_addressable`'s
  **shape** — or, if the task path's criterion genuinely differs (task addressability = `projectId` **and** `taskId`
  both confirmed), to put it in the same module under the same name with a comment saying why the criteria differ.
- **Do not unify:** steps 2–4. #54 says it itself: **a task's title is not an identity**, so matching a task back by
  title is far weaker than matching a list. The task path **self-heals** — the next full refresh writes the real-id
  row and prunes the temp one — so #39's minimal honest version (refuse to queue against an unconfirmed create,
  and say "wait for this sync to finish") is the right size. Renaming `amend_list_change`/`identify_list` into
  queue-generic names is a **refactor of `store.py`'s two queues at the end of a large wave** — not worth the risk
  now, and the list-specific names are at least honest about not being unified.

Recorded on #53 so the two tickets cannot drift.

## Merge queue (7 left), order chosen by file overlap, and one conflict instruction not to lose

Integration tip **`d54ea3a`** (#54 in). Mergers are **serial** — only one may work in the integration worktree at
a time. Order and expected conflicts:

| order | ticket | branch tip | own suite | shares files with |
|---|---|---|---|---|
| 1 | #55 reminders | `127e67b` | 642 | `detail.py`, `test_detail_page.py` — already contains `d54ea3a`, expect clean |
| 2 | #44 due date | `9ee8228` | 642 | `engine.py`, `testing.py`, `detail.py`, `app.py`, `theme.py`, `messages.py`, `schedule.py`, new `pages/due.py` |
| 3 | #45 pickers | `5922e2b` | 628 | same as #44 **plus** `client.py`, `writes.py`, `push.py`, `overlays.py`, new `sync/tags.py` |
| 4 | #38 complete↔uncomplete | `82e2c81` | 615 | `writes.py`, `push.py`, `guards.py`, `client.py`, `errors.py`, `store.py`, `engine.py`, `testing.py`, `keys.py`, `tasks.py`, `app.py`, `messages.py`, `base.py` |
| 5 | #39 create task | `2ce6cf4` | 622 | nearly the same set as #38 **plus** `create.py`, `views.py`, `read.py` |
| 6 | #36 custom views | `f0da8ce` | 651 | `views.py`, `store.py`, `read.py`, `engine.py`, `testing.py`, `app.py`, `keys.py`, `messages.py`, `theme.py`, `index.py` |
| 7 | #46 logical day | `4b1ffbd` | 514 on an old base | `config.py`, `engine.py`, `testing.py`, `app.py`, `bootstrap.py` — **oldest base (`d276aa3`), must absorb four landings**; its merger should expect real conflicts in `engine.py` (`Engine` protocol + `__all__`) and `testing.py` |

### ⚠ The #44 ↔ #45 conflict, and how it must be resolved

#45 warned about this explicitly, and it is a **behavioural** conflict, not a textual one: in
`pages/detail.py`, `action_enter` now branches on **`field.picker` before `field.wire`** (#45's addition). #44 wired
the 截止 field through **`wire`**. So a careless resolution of that one function sends `enter` on 截止 into the
free-text `Input` instead of opening #44's due editor — **a silently wrong behaviour that a green suite might not
catch** (each ticket's own tests pass; only the *combination* breaks).

Whichever of the two merges second must therefore, after resolving, **prove by probe** that pressing `enter` on
each of the fields does the right thing: 截止 opens #44's editor, 所属清单/优先级/标签 open #45's picker, and the
plain-text fields (标题/描述/备注) open their `Input`/`TextArea`. Not "the suite is green" — the specific per-field
dispatch.

### Also carried into the queue

- **#46 must add `set_day_end` and `logical_day` to both the `Engine` protocol and `engine.__all__`** — verified
  absent as of `e62ef13`, and #43's merger listed the protocol's 20 members so a later merger can diff against it.
- **#38's own reported rough edge** (already on #52): completing with `space` removes the row immediately, so an
  immediate second `space` cannot undo — undo needs the completed stream (`r`).
- **#44's judgement call** (already on #52): clearing a repeating task's due date is refused rather than letting the
  server silently clear the repeat rule. Refusing is right; the cost is that this client cannot do it.

## #54's merger: the addressability landscape, and what it means for #39

Integration (with #54) has **exactly one home** for "has the server seen this id": `lists.py:99-101 is_local_list_id`
(shape) and `lists.py:199-215 is_addressable(change)` (sendability), with the prefix constant at `lists.py:90`.
Call sites of the decision — `:472` (parked set), `:566` (push gate), `:643` (superseded lookup), `:485`/`:745`
(candidate/known-id filters), `:348`/`:400` (route to fold/park) — are **uses**, not second homes. Near-misses that
are *not* homes: `store.py:566-576` (allocates ids using the prefix), `testing.py:555` (a copied literal),
`read.py:97/110` (`is_inbox_id` — an id-*shape* question, not addressability), and nothing in the TUI.
The task side has one place that **judges without being written as a judgement**: `push.py:281`
`if not isinstance(created, Mapping) or not created.get("id"): return` — the hole #39 fills.

**#39's branch turns one home into two.** It adds `writes.py:59 LOCAL_TASK_PREFIX="local-"`, `:73-75
is_local_task_id`, `:232-259 is_addressable(PendingChange)`, `:262 is_addressable_task(...)` — same shape, same
name, isomorphic criteria, but **one home per namespace**, which does not satisfy the ruling's strict reading
("unify the judgement, not the machinery"). Asked #39's author to: put the **id-shape predicate in one shared
place** (or a prefix registry) so the two `is_addressable`s are merely machines over their own kind tables; delete
its duplicate `LOCAL_LIST_PREFIX` at `store.py:70` (a guaranteed conflict, #54 moved it to `lists.py:55`); and
**decide the prefix collision**.

**⚠ The prefix collision is a real latent trap:** `"local-"` is a **prefix of** `"local-list-"`, so
`is_local_task_id("local-list-1")` is **True** today. It is harmless only because each predicate is fed its own
family. Any future "OR them into one `is_local_id`" is **ambiguous** unless it compares the longer prefix first —
and order-dependent prefix matching is exactly the kind of thing that is right today and wrong the moment a prefix
is added. Recommended: make the task prefix **non-overlapping** (`local-task-`). Ids are opaque, so the rename
leaks nowhere.

### Revised merge order (the correctness fix goes before the three that touch `store.py`)

`#55` → `#44` → `#45` → **`#57`** → `#38` → `#39` → `#36` → `#46`

### #57's evidence is preserved in the issue, not in /tmp

The probe (`/tmp/probe54h.py`, 97 lines) is pasted **in full** into #57's comments, with a step-by-step
expected-vs-actual table, the run recipe (real `Store` + real engine + `FakeTransport`, `push_on_change=False`
driven step by step, never touching the real account), and **a trap for whoever writes the test**: step 5's index
must include the real row of the list *we* created (`p3` 盐) — feeding only `p1` is a fabricated omission and not a
faithful reproduction. The same-family case (a stale parked DELETE + a reused id turning a later rename into an
UPDATE against the wrong real id) is recorded there too.

## Expected suite counts for the rest of the merge queue (so a wrong number is visible)

Integration is at `6cb6b45` = **642**. Each branch's own new-test count, and the expected post-merge total:

| ticket | own base | own suite | new tests | expected after merge |
|---|---|---|---|---|
| #44 due date | **603** (not 594) | 642 | **+39** | **681** ✅ |
| #45 pickers | 594 | 628 | +34 | **~724** |
| #57 id reuse | 613 | ? | ? | — (fix; will add the 6-step chain tests) |
| #38 complete↔uncomplete | 566 | 615 | +49 | **~773** |
| #39 create task | 566 | 622 | +56 | **~829** |
| #36 custom views | 566 | 651 | +85 | **~914** |
| #46 logical day | 492 | 514 | +22 | **~936** |

These are approximations — a merge can legitimately change the count if a test is renamed or a parametrisation
collapses — but a number far off these means something was dropped, so each merger should be asked to reconcile
the arithmetic explicitly.

## Landmine for any future ticket that WRITES reminders

v2's scope is **read-only** reminder display (spec :305), so no queued ticket writes them. But #55's merger recorded
the trap for whoever does: **the sign convention is now a user-verified fact — positive = 提前.** So "30 分钟前"
must serialise to **`TRIGGER:PT30M`**, and RFC 5545 intuition would write **`-PT30M`**, which is **wrong for
TickTick** (its syntax is iCalendar-shaped but the sign is inverted). Related: `_DURATION` **deliberately** rejects
`P1Y`, `P1M` and fractional durations — widening it means choosing a rounding rule, so it must not be widened
silently.

### ⚠ Correction to the table above — my arithmetic was wrong, and the error mode is worth keeping

The #44 merger measured the truth: the branch's real base was **`98de542` = 603** (verified by `git archive 98de542
tests` → 603 collected), not the 594 its own report quoted (that was its *branch-creation* baseline, before #40
landed mid-flight). So #44 contributes **+39**, and **642 + 39 = 681 exactly** — which is what the suite reported.
**No test was lost; my "~690" was off by exactly 9.**

**The error mode:** I subtracted each ticket's *own reported* new-test count from a base that had already moved.
Several implementers quoted a baseline measured at branch creation, then merged integration mid-flight, so their
"baseline → final" pair spans a different interval than "integration tip → post-merge". **For the remaining rows,
treat the numbers as a sanity band only, and always ask the merger to reconcile the arithmetic against a
*measured* base** — which is exactly how this was caught. The remaining expectations should therefore be read as
"roughly, and reconcile or explain".

## `action_enter` as merged at `3111197` — the exact order #45's merger must preserve

`detail.py:512`. **There is no `picker` branch yet** (that is #45's addition). Order:

1. `if self._editing is not None or self._due_editing: return` — an open editor owns `enter`
2. `field = self._current_field(); if field is None or field.wire is None: return` — **the wire gate comes first**
3. `if field.key == DUE_FIELD_KEY and self._detail is not None: self._begin_due_edit(...); return` — **#44's branch**
4. `self._begin_edit(field)` — free text (`Input` / `TextArea`)

`fields_of` order: title, content, desc, list, due, priority, tags. `wire` is set for the three text fields and for
**截止 = `"dueDate"`**; it is **None** for 清单/优先级/标签. Per-field probe: 标题 → `#detail-input`; 描述/备注 →
`#detail-text`; 截止 → `#due-edit` (focus `#due-date`); 清单/优先级/标签 → **nothing**.

**Two consequences for #45, both load-bearing:**

- A picker branch placed **below gate 2** would be **dead code**, because #45's picker fields are `wire=None` today.
  So #45's `picker` check must come **above** the wire gate — which is what #45 said it did.
- **#44's branch 3 must survive** after #45's picker check and **before** `_begin_edit`. Dropping it sends `enter` on
  截止 into the free-text `Input` pre-filled with 「今天 18:00」 and writes `dueDate` as free text — a silently
  wrong write that **each ticket's own tests would still pass**.

**Other measured facts from #44's merge:**

- The truecolor figure is **24**, not the 40 the implementer reported (session-dependent; it stayed 24 while total
  `38;2;` rose 69 → 114 when `x` was also pressed). **Direction and mutation-sensitivity reproduce exactly**, and the
  committed test goes red under the mutation — so the assertion has teeth even though the headline number was off.
  Recorded as a correction, not a defect.
- #44 added **no** docked widget; `#detail-save` (from #43) is still the only one, and the cursor stays above it at
  100×30/12/8/6.
- `pages/due.py` and the `DueInput` name collision → added to #52 as items 14–15.

## Two corrections to the orchestrator's own claims, both from #45's merger

**1. I over-claimed the `action_enter` danger.** I wrote that dropping #44's due branch would be "a silently
wrong write that **both tickets' own test suites would still pass**". That is **wrong**: the merger mutated the
merged file and **7 tests in #44's own `tests/test_detail_page.py` go red** — #44's suite does catch it. #45's own
file does not, so the combination is still the interesting case, but my statement was stronger than the facts.
**Corrected version: #44's suite catches it; #45's does not; the risk was real but not invisible.**

The seven-field dispatch table on the merged tree (`enter` → focus):

| field | focus |
|---|---|
| 标题 | `#detail-input` |
| 描述 / 备注 | `#detail-text` |
| 截止 | `#due-date` (inside `#due-edit`) |
| 所属清单 | picker `#field-list` |
| 优先级 | picker `#field-priority` |
| 标签 | picker `#field-tags` (`MultiChoiceField`) |

Non-vacuity proven by mutation: moving the picker branch **below** the wire gate leaves the three picker fields'
focus on the page (**dead code** — exactly as predicted); deleting #44's due branch sends 截止 to `#detail-input`.

**2. The colour guard's real rule — my phrasing was imprecise, and the practical rule is narrower.**
`tests/test_architecture.py`'s scan is **AST-based over non-docstring string constants**: Python docstrings are
excluded, and Python `#` comments are invisible to it (the AST drops them). **But the whole CSS string is scanned**,
so a `$` written *inside the CSS text* — **including inside a CSS comment in that string** — is an offence. That
reconciles #44's experience ("I wrote `$surface` in a comment and it reddened": it was inside `_APP_CSS`) with the
merger's finding (the three `$` in `theme.py` are all in Python **docstrings**, which are excluded).
**Practical rule for later tickets: never write `$` anywhere inside the CSS string, CSS comments included.**

## A pre-existing hole #45's merger measured — handed to #39

A task created into a **locally-created list that has not been pushed yet** makes every later write address a
`projectId` the server has never seen: `MOVE`'s `fromProjectId` is `change.list_id` with **no local-id guard**, so it
404s, backs off, and **the queue grows forever** (probe: `kind=move record list_id='local-list-1'` … still queued
after the 404). Same class as #53/#57 — a change parked on an id that can never become real — except the id is the
**list's**. Two things make it #39's rather than #45's: `is_addressable` takes a `PendingListChange` (a **category
error** for task writes, which have **no** addressability judgement at all), and the path is **pre-existing for
UPDATE/COMPLETE/DELETE**, not introduced by #45. Not reachable through the UI today.
**#39 is instructed that task addressability must mean `projectId` **and** `taskId` are both confirmed** — that
phrase was already in #53's amendment, and this probe is why the first half is load-bearing rather than decorative.

## I checked a contradiction between two mergers — and it refined a rule I had been giving everyone

#36's merger reported (check 6) that removing the view form's `Input` colour overrides leaves truecolor at **0 both
ways**, "because `DidaApp` sets `ansi_color=True`". That contradicts #44's merger, which measured **24**
`38;2;25;25;25` sequences appearing when it removed *its* `Input` overrides. Same mechanism, opposite results — so I
probed it myself (`tests/test__zz_probe_form_truecolor.py`, temporary, deleted; worktree verified clean).

| variant | `38;2;` | `48;2;` | accent (`46`) present |
|---|---|---|---|
| A — form as shipped | 0 | 0 | **True** |
| B — `Input`/`Input:focus` `background`/`color` removed | 0 | 0 | **False** |

**#36's merger was right; my suspicion was unfounded.** But the reason matters more than the verdict:

- **Inside the form overlay, the per-widget colour override is NOT load-bearing for truecolor** — the overlay's own
  ANSI surface covers it. A `38;2;` assertion there would be **vacuous** (0 with the override and 0 without).
- **It IS load-bearing for the focus signal**: removing it **loses the accent** (`46`) on the focused cell. That is
  the assertion worth writing — and it is exactly the non-vacuous alternative the merger proposed.
- **On a page (not in an overlay), the override IS load-bearing for truecolor** — #44's 24 sequences, measured on the
  detail page's `Input`s.

**So the blanket rule I have been giving agents — "any new Textual widget needs an override AND a `38;2;`
assertion" — is too coarse, and inside an overlay it would produce a test that can never fail.** The refined rule:

> **In an overlay: assert the signal the override carries (the accent/ANSI code), not `38;2;` — a truecolor
> assertion there is vacuous. On a page: assert `38;2;`, because there the override is what keeps truecolor out.**

**Gap to close:** `38;2;` guards exist for the detail page (#44) and the picker overlay (#45), but the **shared form
overlay has none** — and that overlay is used by #42's list form, #39's new-task form and #36's view form. Recorded on
#52 with the measured table and the exact assertion to write.

## #46 merged — the last ticket branch. The spec is complete on the branch.

`e1e1798`, **831 passed** (809 + the branch's 22, exact). Conflicts were 3 and all trivial (two import-line clashes plus
`docs/architecture.md`). `Engine` went 29 → **31** members. The merger corrected my note about `__all__` (above) and
re-derived the pty evidence: an **outside** config edit with **no keypress** flips the status bar's logical day in
**0.25 s** (≤1 pump tick).

**The residual, stated precisely — the self-contradiction I set out to remove is now *bounded*, not gone.** The repaint
fires iff the configured **value** changed *or* the logical-day **label** the screen was drawn for moved. Detection is
**sampled, not event-driven**, so:

- the status bar is rewritten by paths that **skip** the check (`_sync`'s `finally`, the spinner), so it **can sit up to
  one tick ahead of the rows** — the exact inconsistency the ticket existed to kill, now capped at ≤1 s;
- row content that changes without moving either input (another writer, or a server-side change) is **not** detected —
  that needs `r`.

**Two leftovers for the review / #52:** (i) **nothing pins the check's ordering** — `_view_day` is recorded above the draw
by reading, and the merger's probe (moving it after the draw) left all 22 tests green, because the window is a few
statements and `ManualClock` cannot be advanced inside them; (ii) an app constructed **directly** rather than via
`build_app` gets `push_tick_seconds=None`, i.e. **`r`-only** rollover detection.

## Three residuals from #58c's merge — two of them are the same class as the bugs just fixed

**1. A THIRD copy of the "same list" judgement lives in the fake.** `testing.py:762 FakeBackend.move_task`
re-expresses `current == to_list_id` (minus the empty-target half) — **even though it already imports
`dida.sync.writes`**, where the single home `is_a_move` now lives. #58c's new guard
(`test_the_same_list_judgement_is_asked_of_the_engine_not_written_twice`) scans `app.py` and did **not** cover the
fake. So the fix for T6 has a hole in exactly the place this project has been bitten twice: **a double that
re-decides a judgement the production code owns.** Left unguarded, reported by the merger.

**2. `ALLOWED_IN_TUI` is ONE-DIRECTIONAL — there is no mechanical guard against `sync/` importing `tui/`.** The
merger probed it: `_violations('from dida.tui import messages', package='dida.sync')` returns `()`. So when I told
the user that "the layering forces the priority table onto the sync side", **the placement is right but the cited
guard is only half the reason**. The other half is real and fatal: `messages.py:30` imports `dida.sync.engine`, and
`engine.py:142` imports `sync.views` before engine's names exist — an actual cycle. **Worth a guard**: the documented
direction has no enforcement in that direction today.

**3. Stale references that a `grep` will still find.** `docs/tickets/32-date-parser-consumers.md:44,83` still quotes
`SEVEN_MODULES` and a line number `:31` with no 「当时」 marker — defensible as a historical snapshot, but **one word
would make it match what `docs/architecture.md` did**. The same stale name also lives in the **orchestrator's own
notes** (`brief.md:100`, `merger-template.md`, `codebase-map.md`) — outside the repo, so they cannot mislead a
reader of the code, but they will mislead the next agent that greps them.

Also confirmed by the merger, and worth keeping: `is_a_move` and `PRIORITY_NAMES` are **module-level names on the
engine's public face, NOT `Engine` Protocol members** — so `FakeBackend`'s `isinstance` does not cover them, and a
later rework of the public face must not drop them. And the new duplicate-body guard scans **all** of `src/dida`, so
two legitimately identical module-level helpers will red it — that is deliberate; do not add a skip.

## #58d's merger corrected a rule I had recorded — the overlay/truecolor rule was too broad

I wrote earlier: *"In an overlay: assert the signal the override carries (the accent/ANSI code), not `38;2;` — a
truecolor assertion there is vacuous. On a page: assert `38;2;`."* **That is true for the CARET and FALSE for the
SELECTION.** The merger measured it on the real pre-fix tree:

- the **cursor** goes through Textual's `:ansi` block, which emits ANSI `color(0)`/`color(7)` — **so a `38;2;`
  assertion about the caret is indeed vacuous**;
- the **selection has no `:ansi` variant** and **does emit truecolor** — `\e[38;2;224;224;224;48;2;21;132;170m`
  on the very form the user complained about.

**That is exactly why the leak survived**: the shared form overlay had no truecolor assertion, and the rule I had
been handing out would have told an implementer not to bother writing one. The amended rule:

> **A `38;2;` assertion is vacuous for widgets whose colours Textual maps through `:ansi` (the caret), and
> mandatory for anything Textual does not map (a selection block).** Check which applies before deciding the
> assertion is unnecessary — "it's in an overlay" is not the test.

Also from that merge, two smaller facts worth keeping:

- **`theme.BAR = "on cyan"` is the one remaining accent-as-ground, and it is deliberate** — it renders two blank
  spaces, so it carries no text and cannot be unreadable. **But both new guards are blind to it** (they look for
  text-bearing fills), so it is an accepted exception, not an oversight. Say so if a later ticket touches it.
- **The load-bearing `:focus` in the new form selectors is UNPINNED.** Dropping it reverts the **caret** to
  Textual's `ansi_white`/`ansi_black` (i.e. back to the leak's caret half) and **no test reds**; the **selection**
  keeps the app's block either way, so the specificity tie is real for the caret and moot for the selection.
  A one-line test could pin it — recorded for #52.
- The branch's **"before" pty capture was a reconstruction** (merged CSS with the old colour values), not a run on
  the pre-fix tree: symptom-faithful for the text, but it showed `46` where the real tree shows `30;47` for the
  caret, and it was structurally unable to show the truecolor leak. **The merger re-ran it on the real pre-fix tree
  and its capture is the trustworthy one.** Worth remembering: a "before" capture must come from the old tree, or it
  is not evidence about the old tree.

## #59/#60 合并（tip `7e018c6`，862）——三处更正，其中一处是**我转述错了**

**1. 我告诉用户的那条「顺序依赖」不存在。** 分支说 `_show` 必须「先换层、再 `focus(scroll_visible=False)`」，
否则程序收不到键。merger **实测推翻了它**：Textual 8.2.8 里 `Widget.focus()` 是 `app.call_later` **延迟**执行的，
所以把两条语句**对调**之后 `tests/test_layer_focus.py` 仍然 **8 passed**——**只有一句 docstring 在钉它，没有任何测试**。
我在给用户的报告里把这条当成已验证的事实转述了。**这是我的错：我把实现者的自述当成测量结果转述了。**
（成因值得记：`allow_focus()` 是**现算**的，所以「先换层」这个顺序本来就无关紧要——那句 docstring 的**意图**在
`allow_focus()` 这一版里已经自动满足了，它描述的是**上一版**的必要性。）

**2. 分支给「没有内容拖动手势」的理由不准确。** 它说 Textual 8.2.8 没有这个手势；merger 查明**有**（选中拖动），
只是 `Screen._start_auto_scroll` 的 `_auto_scroll_y` **只动 `scroll_y`**——merger 直接调 `_start_auto_scroll(stage,+1)`
实测 x 仍是 100。**结论（无残留路径）成立，理由不成立。**

**3. `focus(scroll_visible=False)` 不再是承重的——承重的是那道闸。** 我笔记里先前写着它承重。
merger 做了 2×2 实测：**闸开着**时 `scroll_visible=True` 照样有动画（9 帧）；把 `overflow-x` 改回 `scroll` 时
**一帧就跳过去**。所以：**把闸拧回去会同时弄坏动画和输入闸**；而 `scroll_visible` 那个参数现在已经无关紧要。

**4. 一条给后来写测试的人的坑（与本分支无关，是仓库既有的）**：`sample_while(...)` 之后的**第一次 `pilot.press`
可能被吞掉**（merger 的 `enter#2` 没能导航）。本分支的测试没暴露它，但**将来任何「采样之后还要按键」的测试会得到一个幽灵失败**。

**5. 一处未钉住的假不变量（待直接修）**：`_show` 的 docstring 声称那条顺序依赖。按新分流规矩这是**纯文案**，
走直接改——**要么把话改准，要么用一条 await 回调的测试把它钉住**（`Widget.focus()` 延迟，所以钉它必须 await）。
