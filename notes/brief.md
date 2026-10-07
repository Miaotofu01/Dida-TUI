# v2 implementation — orchestration brief (read this first, every subagent)

## 先读这一节：按后果分流（决定整轮的形态）

起因：用户报「按 `←`/`→` 会穿帮」，那一行修复本身不到一分钟（`overflow-x: hidden`），却走了三轮实现 + 一轮独立复核 +
一次合并器。用户问「为什么能修这么久」，然后选了**按后果分流**。

**判据不是「看起来像不像视觉改动」，而是：这次改动能不能改变「哪一笔写落到哪个对象上」或「写出什么」。**

**走全套流水线**（独立分支 + 实现者 + 独立复核 + 变异验证 + 合并器）——只要它**可能**：

- 决定写什么、写给谁、写不写（`writes` / `push` / `create` / `schedule` / `completed` / `priority` / `subtasks` / `lists` / `tags`）；
- 删除、收养、复用 id（`store` / `refresh` 的剪枝 / 本地 id 分配）；
- 改本地库的表结构或队列（`storage/`）；
- 改发出去的报文（`api/`）；
- **改变某个动作落到哪个对象上**——**包括焦点与选中**：`#60` 的 `tab` 把焦点交给屏外那一页，
  于是接着按的键落在**看不见的那一页**上，**这就是「哪个对象」的问题，不是外观问题**。这一条是判据里最容易看错的一半。

**直接改**（在集成分支上改、跑全量、告诉用户改了什么；不派分支/复核/合并器）：

- 纯渲染与样式（`theme.py` 的 CSS、Rich span、宽度与换行）；
- 键位绑定本身、焦点可见性（**只要不改变动作落到哪个对象上**，见上）；
- 界面文案。

**两条不因分流而放松的**：① 落地前**全量必须绿**（预算见「测试预算」一节）；② 修完**如实说明改了什么、验了什么**。
小改动只要测试是几行的事，就顺手写一条——**没有测试的回归只有用户看得见**。

---

## What is being built

`dida` — a Textual terminal client for 滴答清单 (TickTick). The repo is **mid-pivot from v1 to v2**:
v1 was a three-pane "today console", judged unusable; v2 is a general client — **one column, three
page layers** (list index → task list → task detail), with 今天 demoted to one of three built-in views.

**The requirements live in GitHub issue #30 (spec) and #32–#48 (tickets). Do not re-derive them from
this file.** Read them:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/30 --jq .body          # the spec
gh api repos/Miaotofu01/Dida-TUI/issues/<N> --jq .body         # your ticket
gh api repos/Miaotofu01/Dida-TUI/issues/<N>/comments --jq '.[].body'
```

Local copies of the ticket bodies are also at `/tmp/dida-v2/tickets/<N>.md` (snapshot, may go stale —
the issue tracker is the source of truth).

⚠️ **`gh issue view <n>`（**不带 `--json`**）与 `gh pr edit` 在这个仓库里会崩** —— GraphQL 的
`Projects (classic)` 弃用报错（`repository.issue.projectCards` / `repository.pullRequest.projectCards`）。
**但 `gh issue view <n> --json …` 是好用的**（#62–#66 那一轮整轮都靠它读票），所以别因为这句话绕开它；
真正要绕的是「写」那一半。可用的几条：

```bash
gh issue view <n> --json number,title,body,labels,state --jq '…'   # 读一张票：好用
gh api repos/Miaotofu01/Dida-TUI/issues/<n>                          # 读一张票：永远好用
gh api repos/Miaotofu01/Dida-TUI/issues/<n>/comments                 # read its comments
gh api repos/Miaotofu01/Dida-TUI/issues/<n> --jq '.id'               # db id (blocking edges need this)
gh api --method PATCH repos/Miaotofu01/Dida-TUI/issues/<n> -F body=@/tmp/body.md   # edit a body
gh api --method PATCH repos/Miaotofu01/Dida-TUI/pulls/50  -F body=@/tmp/body.md    # edit a PR body
```

`gh api`, `gh issue comment`, `gh issue list`, `gh pr create` and `gh pr view` all work normally. `-f` does not
accept `@file`; that needs `-F`.

## Shared notes you should read (in `/home/tofu/dida-v2-worktrees/notes/`)

| File | What it gives you |
|---|---|
| `cross-ticket-corrections.md` | **Read before designing anything.** Where a ticket's premise is factually wrong, and where the ticket authors' counts were too low. Fact corrections; scope stays with the ticket. |
| `codebase-map.md` | 599-line line-referenced map of the repo: module inventory, the write-type duplication (9 production places), storage schema, test inventory, date-parser consumers, config surface, API client surface, fakes/seams, TUI layout, surprises. |
| `api-shapes.md` | 684-line extraction of the official OpenAPI doc: exact request/response shapes per endpoint, cited by doc line, with a spec-vs-doc agreement table. |
| `wave-plan-and-seams.md` | The wave order and the file seams that let concurrent implementers avoid colliding. |
| `kitty-flag-import-order.md` | Why `TEXTUAL_DISABLE_KITTY_KEY` must be set before the import block, and why the obvious acceptance test asserts the opposite of what it looks like. |
| `terminal-input-evidence.md` | Measured terminal-input facts: which keys survive, the `space`-vs-focused-input trap, the reproduction harnesses. |
| `orchestrator-triage-of-ui-tests.md` | Per-test rescue/discard judgement for the tests in the TUI-importing files. |
| `deletion-manifest-34.md` | **#34's exact deletion list**: 17 files / 187 tests to delete, 4 files a naive grep falsely accuses, and the line-by-line arithmetic reconciling it against the spec's 「203」. |
| `progress.md` | What has landed, what is in flight, and the benign history artifacts to clean up. Append-only. |
| `openapi-dida365.md` | The official doc itself (2497 lines), so nothing depends on `/tmp` surviving. |

## Delivery decisions (confirmed with the user, 2026-xx)

1. **There is a hard checkpoint after ticket #34.** The order is #32 → #33+#41 → #34, then **stop and hand the
   branch to the user for acceptance** before starting the remaining 13 tickets. He wants to feel the
   one-column three-layer app before 13 more tickets build on it.
2. **Delivery is a draft PR** `feat/v2-terminal-client` → `main`, opened after the first merge into the
   integration branch, marked ready for review at the end.
3. **Tickets get a comment, not a close.** Each landed ticket gets a `gh issue comment` pointing at the
   commits that resolved it. **Nobody closes an issue** until the integration branch actually reaches
   `main` — the user closes them himself.

## ⚠ Network: the SSH proxy is broken — every `git push` needs a bypass

`~/.ssh/config` sends `github.com` through a SOCKS proxy (`ProxyCommand … 127.0.0.1:2080`) which is
**currently down** — a plain `git push` hangs on "Connection timed out during banner exchange", and an HTTPS
`git push` fails with a gnutls handshake error. Direct SSH works fine. **Prefix every push:**

```bash
GIT_SSH_COMMAND="ssh -o ProxyCommand=none" git push origin <branch>
```

`gh` (api.github.com) is unaffected — issue and PR operations work normally. Don't edit the remote URL or the
user's `~/.ssh/config`; just bypass it per command.

## Where things are

| Thing | Path |
|---|---|
| **Integration branch (the deliverable)** | `feat/v2-terminal-client` |
| **Integration worktree** | `/home/tofu/dida-v2-worktrees/integration` |
| Your ticket worktree | `/home/tofu/dida-v2-worktrees/t<NN>` |
| Shared notes (exploration, decisions) | `/home/tofu/dida-v2-worktrees/notes/` |
| The user's own checkout — **do not touch** | `/home/tofu/我的项目/Tips` (sitting on `docs/v2-domain-model`) |

Every worktree is a worktree of the *same* clone, so `gh` works from any of them (the remote is
`Miaotofu01/Dida-TUI`, inferred from `git remote -v`).

## Repo conventions

- Python 3.12 + Textual, `uv` for everything: `uv run pytest`, `uv run dida`. **Run `uv sync` once
  in a fresh worktree** before the first `uv run`.
- `uv run pytest` 全绿时是**近千条、约 4 分钟**（2026-10 实测 886 条 / 4 分 21 秒）。
  **跑得久不等于挂了**——上一轮就是因为把 4 分钟当成了挂，才在每个提示里塞一遍「别以为它卡了」。
- `tests/` has **no `__init__.py` and no `conftest.py`** — tests do bare `from support import screen_text`.
  New shared test helpers must follow that convention.
- `asyncio_mode="auto"`. TUI tests are `async with app.run_test(size=(...)) as pilot:` + `await pilot.press(...)`.
- `tests/support.py::screen_text` is the only place in the repo that touches Textual's compositor.
  Reuse it; do not open a second one.
- `docs/architecture.md` holds module boundaries and the two test seams. `GLOSSARY.md` holds domain
  terms. `docs/adr/` holds decisions — read the ADRs before contradicting one.
- `uv run pytest tests/test_architecture.py` guards module boundaries: a `SEVEN_MODULES` import
  whitelist, and a rule that the TUI may only import from `dida.sync.engine` and `dida.tui`.
  **New view types must be re-exported through `dida.sync.engine`.**
- **Never `cat`, print, or echo `~/.config/dida-tui/config.toml`** — it holds the user's personal API
  token (0600). Don't run `env`, `set -x`, `bash -x`. Never ask for the token in chat.
- **Never run write experiments against the live TickTick API.** The data is the user's real account.

## How to report back (keep it short — the orchestrator reads many of these)

Report, in ≤30 lines:
1. ticket number, branch name, worktree path;
2. the tip commit SHA on your branch (`git rev-parse HEAD`);
3. what you changed, file by file, one line each;
4. the exact `uv run pytest` result line (counts);
5. anything the ticket asked for that you could **not** do, and why;
6. any landmine you hit that a later ticket will also hit (this is valuable — write it down).

Do **not** paste diffs, file contents, or test bodies. The orchestrator will read the branch.

## Landmines carried over from the v1 audit (all verified — don't rediscover them)

- `bootstrap.py` imports `PUSH_TICK_SECONDS` **from the TUI** (`bootstrap.py:32`, consumed at `:159`).
  Ticket #34 deletes `tui/app.py`; that constant must move **before** it does, or `dida` won't start.
- Import-cycle guardrails, don't "fix" them: `store.py:44` imports `dida.sync.view` (and `:45`
  `dida.sync.writes`) at **module top level**. The `engine → storage` deferred import is **function-local**
  and must stay that way — but **note where it lives moved**: after #32 it is no longer in `engine.py`
  (`engine.py:101` is `TYPE_CHECKING`-only), it is `push.py:64` (`_completed_status`). Hoisting it breaks
  import order; renaming/removing `dida.sync.view` breaks `storage`, `testing`, `bootstrap`. Verified in both
  import orders: `uv run python -c "import dida.bootstrap"` and importing `dida.storage.store` first.
- **`apply_refresh(prune_lists=, prune_unfinished_tasks=)` defaults to `False`, meaning "no completeness
  asserted"** (added by #41). `completed.py:108` and `subtasks.py:99` **depend on that default** — they fetch
  a window/slice, where a row's absence is not evidence of deletion. A future scoped refresh must **not** pass
  `True`. Pass it only from a refresh that fetched everything.
- #41's list prune deletes the list row but deliberately keeps tasks that have a pending change, which leaves
  **orphan tasks whose list no longer exists**; `view.py` then falls back to `list_name = list_id`. The
  list-deletion and badge tickets will meet this — it is a known consequence, not a bug to silently patch.
- The sqlite connection is **thread-affine** (no `check_same_thread=False`, no lock). A refresh must
  be a coroutine on the event loop — **never** `threading.Thread`.
- `SyncEngine(source=None)` is a **deliberate degraded mode**, not a half-finished one: empty views,
  `subtasks()` returns `()`, write paths raise `RuntimeError`. Some tests are built on it
  (`tests/test_app_shell.py`). Don't "fix" it into a hard error.
- `FakeBackend` must keep satisfying the `@runtime_checkable` `Engine` protocol — `DidaApp(engine)` is
  the single injection point, and renaming it or changing its constructor breaks ~20 test files.
- `format_status` already renders 「已同步 HH:MM · 待推送 N · 逻辑日 D」. #34's status-bar work is
  **modify, not create**: the missing piece is highlighting when 待推送 is non-zero.
- Three things in v1 that look useful but have **no production caller** — good places to cut:
  `ListPane.selected_list_id` (only tests read it), `DidaApiClient.list_tags`, `Store.list_records`.
- `dida/tui/__init__.py` imports `DidaApp` at package top level; deleting `app.py` must handle it.
- The completed-time window default is hard-coded to **24 hours** (`config.py:101`); the spec wants
  7 days → ticket #37.
- `dida/tui/app.py` (812 lines) and `dida/sync/engine.py` (1347 lines) are the two "big modules" #32
  splits. `dida/tui/panes.py` is 1113 lines. `dida/sync/view.py` is 528 lines.

## Landmines from the visual foundation (#51) — the next seven tickets all hit these

Found the hard way, all in a real pty, none of them error where the cause is:

- **`self._animate` shadows `Widget._animate`** (Textual's own animator). Name your helper anything else, or
  `self.animate(...)` raises `TypeError: 'bool' object is not callable` with the traceback pointing *inside
  `widget.py`*, far from your code.
- **`page.focus()` scroll-reveals its container by default** (`scroll_visible=True`) and **kills a horizontal
  pan instantly** (measured: `scroll_x` jumps straight to 100). Use `focus(scroll_visible=False)`.
- **`pilot.press` waits for animations to go idle**, so `press` then immediately capturing a frame only ever
  shows the settled state. Motion must be observed with a **concurrent sampler**.
- **Two `dock: bottom` widgets overlap and eat CJK cells** — with the Footer and the status bar both docked,
  「待推送」 rendered as 「待推 」. Removing the Footer fixed it; don't re-add a second bottom-docked widget.
- **ANSI mode is asserted by SGR *parameters*, not by the whole sequence.** Textual merges fg+bg into one
  sequence, so the accent is `\x1b[36;49m`, never `\x1b[36m`. A literal-string assertion goes falsely red.
- **`sync/view.py`'s low-priority mark is `·` (U+00B7, East-Asian-Ambiguous)** and is not in the TUI's glyph
  table. **#37 must map or replace it** before putting priority into an aligned column, or every such row is
  laid out a cell wider than terminals draw it.

Also relevant to any later styling: the visual constants live **only** in `src/dida/tui/theme.py` (colour roles
in both vocabularies — Rich `"cyan"` = ANSI 6 vs CSS `"ansi_cyan"` — plus glyphs, rules, spacing, motion
durations, `app_css()`, `overlay_css()`, `clip()`, `pad()`). If you need a new visual value, add it **there**,
not in a page. `notes/wave-plan-and-seams.md` names which file each wave-4 ticket owns.

### More from the foundation follow-ups — all measured, all heading for a specific ticket

- **`TasksPage._name` shadows `DOMNode._name`** (`textual/dom.py:196/836`) — the same shape as the `_animate`
  bug that already bit once. Benign today because Textual only reads it via `.name`/`__rich_repr__`, and a
  public `container_name` property already exists. **#37 should rename the field when it rewrites `tasks.py`.**
  An AST scan of every `self.X =` in `src/dida/tui/` against the installed Textual found **no other real
  shadow**, so this is the last one — but the scan is worth repeating after your own change.
- **Two more East-Asian-Ambiguous glyphs are on their way into a column:** `sync/view.py:35`
  `NO_DUE_TEXT = "—"` (U+2014) and `:234`'s low-priority `·` (U+00B7). Both already reach the detail page's
  截止 field, and both will reach **#37/#44's due column**. Map or replace them before aligning, or every such
  row is laid out a cell off.
- **`keys.py:179`'s help heading `── X ──` is 4 cells wider than the box it sits in on a CJK font** (measured:
  rich sizes the overlay at 30/33 cells, the heading renders 34/37) — so the right border gets pushed in his
  locale. **#48's file**; it is an inline heading, not a column, and it is *not* in `STRUCTURAL_GLYPHS`, so the
  width guard does not cover it.
- **`#top-bar` has no `nowrap`+`ellipsis`** (only `#page-body` does), so at ~40 columns the third breadcrumb
  segment vanishes behind a **dangling `▸`** with no ellipsis. Tracked on #52.
- **A shadow guard must be class-level.** `"_animate" not in vars(app)` is **useless** — Textual assigns it on
  the *instance*, so it is present with and without the bug. Use `DidaApp.__dict__` plus `callable(app._animate)`.
- **Do not `git fetch` in these worktrees** — the user's dead SOCKS proxy hangs it (a job had to be killed).

### From #47 (quit interception) — the modal-binding one will silently eat your keys

- **⚠ A modal's bindings cannot reach the app's.** Textual resolves keys through `Screen._modal_binding_chain`,
  which **truncates at the last modal widget**, so while an overlay is open **`app._bindings` are unreachable**.
  Measured consequences: `q` on the `?` help overlay was bound **nowhere reachable at all** (the help overlay had no
  working quit key), and `Ctrl+C` on any overlay silently hit `Screen.BINDINGS`'s invisible `screen.copy_text`.
  **Any key a modal needs must be bound on the modal, using Textual's action namespace: `Binding(key, "app.quit")`,
  never `Binding(key, "quit")`** — the latter resolves to the overlay, which has no `action_quit`, and the key is
  **eaten silently**. `overlays.py` now carries `QUIT_BINDINGS = "app.quit"`; follow that pattern for submit/cancel
  too, and **test your modal's keys with the modal actually mounted** — a test that presses without the modal up
  passes while the real thing is dead.
- **`Ctrl+C` is now a global quit binding (`q` and `Ctrl+C` run the same judgement).** Anything that adds an
  `Input`/`TextArea` — **#42's form, #39's title input, #44's structured date input, #45's pickers, #36's condition
  form** — must **re-decide** it: Textual binds `Ctrl+C` to *copy* precisely so Ctrl+C inside a text box doesn't quit
  the app, and a focused `Input` consumes keys. Leaving it undecided makes the behaviour depend on focus, which reads
  as a bug. Decide, and say what you decided.
- **Factual correction worth knowing:** in Textual 8.2.8 `Ctrl+C` did **not** quit anything — `App.BINDINGS` binds it
  to `action_help_quit`, which only fires a toast 「Press q to quit the app」. Ticket #47's stated diagnosis ("Ctrl+C
  takes another default exit path") was **wrong in direction**; the real defect was that no judgement ran at all, and
  that overlays had no reachable quit key. The AC was still right and is now true.
- **`DidaApp.layer` shadows Textual's `Widget.layer`** (`widget.py:2605`) — pre-existing, benign in 8.2.8 because
  nothing on the App path reads it, and the same class as `_animate` / `TasksPage._name`. **A shadow-scanning guard
  must not assume `DidaApp.layer` is clean**; whoever owns the navigation stack should decide whether to rename it. A
  full AST scan of `src/dida/tui/` found no other shadow beyond intentional framework overrides (`BINDINGS`,
  `DEFAULT_CSS`, `compose`, `__init__`, `action_quit`, `can_focus`, `TITLE`).
- `_finish_quit` touches **no DOM**, so the "ask `is_running` after every await" rule does not apply to it — its
  docstring says why, so the absence isn't read as an oversight.

### From #35 (built-in views) — one for #36, one that sharpens a rule you already have

- **⚠ `DueWindow(first=0)` is the default, and writing 「今天」 as `DueWindow(last=0)` silently drops every overdue
  task** — which is exactly the union-not-a-range failure #35's prompt warned about, and #35's first run was red on
  it. **#36's date-range filter must use `first=None` for 「含逾期」.** Getting this wrong produces a view that looks
  right and quietly hides work.
- **Colour assertions must delete `NO_COLOR` *before the app is constructed*** — not merely before rendering.
  `tests/support.py::screen_styled_text` comes out grey otherwise, and this shell has `NO_COLOR=1`. So the ordering
  matters: clear it, then build the app, then assert.
- **The evaluator seam is `dida/sync/views.py`** (`ViewDefinition` / `DueWindow` / `Completion` / `evaluate_view` /
  `order_key`), re-exported through `dida.sync.engine` because the TUI may not import `dida.sync.views` directly.
  **Extending it is meant to be additive** — #36 adds fields plus one clause in `_matches`, no rewrite. The three
  built-ins go through the same `evaluate_view` as a user-created view; there is no `if builtin` branch anywhere, and
  adding one would break #36.
- `TaskItem.overdue` is the **read-model bit** and `TaskList.shows_list_name` is the field that tells a view to show
  each task's list name (True for views, False for real lists). Both are #35's; #37 consumes them.

### From #48's merge — a new global constraint that can red *your* test

- **⚠ `tests/test_keymap.py::test_the_help_body_is_exactly_as_wide_as_the_terminal_will_draw_it` reds on ANY
  East-Asian-Ambiguous character in a help line — labels and `LAYER_TITLES` included.** The guard lives at
  **`tests/test_keymap.py:108`**, *not* `tests/test_theme.py` — earlier notes named the wrong file and #47's merger
  caught it. `·` (U+00B7) and `—` (U+2014) must be mapped or replaced before they reach a help label or a heading.
  **#37 already set the pattern for task rows**: `NO_DUE_TEXT` is now ASCII `-` and low/none priority is `.`, with a
  guard pinning every column glyph (`len == 1`, `cell_len == 1`, EAW ∉ A/W/F). Copy that, don't invent a third way.
- **`STRUCTURAL_GLYPHS`' guard asserts `len(glyph) == 1`**, so a multi-character rule cannot be added — repeat a
  1-cell glyph instead (`HEADING_RULE = "-"` is the pattern).
- **`keys.py` lost `↓`/`↑` from `KEY_NAMES` deliberately** (ambiguous width). A ticket that rewrites `keys.py`
  wholesale from an older base would **silently drop `unreliable_reason` and `HEADING_RULE`** — keep both, and keep
  `page.BINDINGS == bindings_for(LAYER)`.
- The layering test matches **label strings against the whole screen**, so a page that prints text equal to another
  layer's help label can falsely red it.
- Corrected numbers for the record: the widest help row went **29 → 32** cells (the disclosure said 30 → 32), and the
  newly-clipped band is **44–47** columns (it said 45–47). The judgement call itself stands.

## Working agreement

- **Every commit message must name its ticket**, e.g. `feat(views): built-in 今天 filter as a pure function (#35)`.
  The final `code-review` pass locates the spec by scraping issue references (`#NN`) out of the commit log —
  so a commit that doesn't name its ticket is invisible to the Spec axis of that review. Merge commits
  must name it too.
- **TDD is mandatory.** Call the `tdd` skill (via the Skill tool) and follow it: red → green → refactor.
  Write the failing test first, from the ticket's acceptance criteria, asserting **external behaviour
  only** — "I pressed this key on this layer and the next layer showed this". Never assert widget
  trees, internal state objects, or whitespace/colour codes in rendered strings.
- **Commit early and often on your own branch**, `git commit` per red-green cycle is fine. Conventional
  commit subjects, lowercase, e.g. `feat(views): built-in 今天 filter as a pure function`.
- **Before reporting done**: `git merge feat/v2-terminal-client` into your branch (merges from the
  integration branch tip — resolve conflicts, do not force), then run the tests per the **测试预算**
  below. Report the merged tip SHA and the test line you actually ran.
- **测试预算（用户 2026-10 定的，为省时间）**：**一张票全量只跑一次**，别每步都全量。
  - 实现者：迭代时跑**受影响的测试文件**；收工前跑**一次全量**，把那行数字报出来。
  - 合并者：合并**无冲突且没碰到共享接缝**时，跑受影响的文件 + `tests/test_architecture.py`；
    有冲突、或改动落在写路径 / 读模型 / `tui/keys.py` / `storage/` 这类共享面上，**跑全量**。
  - 一轮的**最后一次合并之后必须有一次全量**——那一次是全批的闸。
  - 全量约 4 分钟、近千条。**跑得久不等于挂了。**
- If the ticket turns out to be wrong, ambiguous, or impossible as written, **stop and report that
  instead of inventing a resolution**. The user's tracker is the arbiter.
- Keep every other ticket's scope out of your branch. No drive-by refactors. No new dependencies
  without saying so.

---
