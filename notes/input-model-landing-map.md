# Input-model landing map — tickets #62–#66

All line numbers **verified against `feat/v2-terminal-client` tip `f499427`** in
`/home/tofu/dida-v2-worktrees/integration` (read directly, not copied from ADR-0008).
Spec = issue #61. Design record = untracked `docs/adr/0008-arrows-replace-enter-and-escape.md`.

## ADR-0008 pointer audit

**No ADR-0008 pointer is stale.** Every file:line in §1–§4 and Consequences was checked and
matches the worktree at `f499427`, including all six "escape cancels" test lines
(`test_list_overlay.py:134`, `test_create_task.py:183`, `test_picker_fields.py:395` & `:642`,
`test_view_overlay.py:151` & `:309`) and the `app.py:3/122/406-410/435/442/448/453/593/601/605/636`
docstring list. Two are *imprecise*, not wrong:

- ADR §2 "`tui/keys.py:152-158` 的 `unreliable_reason()`": the function is at **keys.py:180-194**;
  152-158 is the `ctrl+enter` / `ctrl+return` entries of `_UNRELIABLE_KEYS` (L152-162).
- ADR §4 "`tui/pages/base.py:345`": L345 is the `def _land_bar` line; the quoted comment
  ("一个还在画的控件会把底下那一行擦掉") is at **L349**.

**ADR-0008 *omissions* (real, will break the build if not handled):** see #62 items T2/T3 below —
`detail.py` calls `self._bar()` in two places, and `theme.py` has `BAR` in `__all__`/`RICH_ROLES`
plus the `#cursor-bar` CSS block. ADR lists none of these.

---

# #62 — delete the travelling cursor bar; commit ADR-0008

"Verified against f499427."

### T1 — `src/dida/tui/pages/base.py` (`CursorPage`)

| delete/change | lines | note |
|---|---|---|
| `bar_pos: reactive[float] = reactive(0.0)` + docstring | L119-120 | |
| `self._motion = False` + docstring | L129-135 | ADR's `:129` |
| `set_animate()` | L176-178 | only writer of `_motion` |
| `yield Static(id="cursor-bar")` in `compose()` | L164 | |
| `self._land_bar()` in `set_rows` | L201 | |
| `self._flash_bar(from_line)` in `_move` | L227 | keep the rest of `_move` |
| `_bar()` | L234-235 | |
| `_total_lines()` | L286-288 | becomes dead once `watch_bar_pos` goes |
| `#--- 会追赶的装饰光标条` section header | L313 | |
| `watch_bar_pos()` | L315-321 | |
| `_flash_bar()` | L323-343 | |
| `_land_bar()` | L345-353 | |
| `from textual.geometry import Offset` | L33 | **only** used at L321 |
| `from textual.reactive import reactive` | L34 | **only** used at L119 |

Rewrite these docstrings that name the bar: module docstring **L21** ("滚动与装饰光标条的位置都能
直接算出来"), `_move` comment **L221-224** (keep the "选中立刻到位" fact, drop "除了那条装饰条"),
`_total_lines` gone with the method.

**SURVIVES** (must not be touched): `CursorPage._row_lines` L277-284, `_cursor_lines` L290-296,
`_line_of_cursor` L298-306, `scroll_cursor_into_view` L355-376. `DidaApp._motion` — app.py L319
(`self._motion = False`), L355-357 (`self._motion = theme.animations_enabled(...)` + `animation_level`),
L416 (`animate=self._motion and layer != previous`). Only the page fan-out goes:

```python
# app.py L358-359  — DELETE the loop
        for page in self._pages().values():
            page.set_animate(self._motion)
```

app.py L287-289 docstring "关掉时换层不滑、光标条不飞" → drop "光标条不飞".

### T2 — `src/dida/tui/pages/detail.py` (NOT in ADR-0008 — will raise `NoMatches`)

| delete | lines | quote |
|---|---|---|
| `self._land_bar()` in `on_resize` | L698 | inside `on_resize` L694-699 |
| `self._bar().styles.visibility = "hidden"` | L762 | in `_begin_edit`, after `self._body().styles.display = "none"` |
| `self._bar().styles.visibility = "hidden"` | L788 | in `_begin_due_edit`, ditto |
| docstring mention of `_total_lines` | L685 | |

### T3 — `src/dida/tui/theme.py` (only `CURSOR_BAR_MS` is in ADR-0008)

| delete | lines |
|---|---|
| `"BAR",` in `__all__` | L40 |
| `BAR = "on cyan"` + docstring | L135-136 |
| `"BAR",` in `RICH_ROLES` | L148 |
| `"CURSOR_BAR_MS",` in `__all__` | L46 |
| `CURSOR_BAR_MS = 120` + docstring | L388-394 |
| `#cursor-bar { width:100%; height:1; visibility:hidden }` | L531-535 (inside `app_css()`) |

`BAR`/`CURSOR_BAR_MS` have no other readers (`theme.BAR` used only at base.py:332;
`theme.CURSOR_BAR_MS` only at base.py:340). `test_theme.py:85-86` iterates `RICH_ROLES` — removing
`BAR` keeps it green.

### Existing tests to delete / rewrite

| file:line | test / helper | why |
|---|---|---|
| `tests/test_visual_identity.py:869` | section comment `# --- 会追赶的光标条` | delete |
| `tests/test_visual_identity.py:872-894` | `test_the_cursor_marker_survives_the_travelling_bar` | asserts `after.lstrip().startswith("❯ . 交水费")` at L894 — rewrite as a plain "`❯` stays after moving" test; **the `❯ .` string is also changed by #63 → `❯ ☐`** |
| `tests/test_visual_identity.py:897-911` | `test_the_travelling_bar_is_a_short_lived_accent_block` | delete (asserts SGR `46`) |
| `tests/test_detail_page.py:349-357` | `bar_row()` helper | delete |
| `tests/test_detail_page.py:357-386` | `test_the_travelling_cursor_bar_flies_to_the_wrapped_blocks_own_line` | delete (uses `bar_row`) |
| `tests/test_visual_identity.py:1` | module docstring names "会追赶的光标条" | update |
| `tests/test_visual_identity.py:816-838` | `test_neither_the_app_nor_the_pages_shadow_textuals_animator` | **keep**; L824 comment cites `pages/base.py` `_motion` (L130) which is being deleted — reword, keep the assertion |

### New test (real-app seam)

In `tests/test_visual_identity.py`, beside the animation tests (precedent:
`test_animations_off_switches_layers_in_one_frame` L740, and `sample_while` L85-102):

```python
async def test_moving_the_cursor_never_blanks_a_row_on_screen():
    app = DidaApp(backend(), animations="on")   # MUST be "on" — auto turns anims off in tests
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)            # #65 changes this helper: press("right")
        lines0 = screen_text(app).splitlines()
        rows = [i for i, l in enumerate(lines0) if "写周报" in l or "交水费" in l]
        frames = await sample_while(lambda: pilot.press("j"), lambda: screen_text(app))
        await pilot.pause()
    assert frames
    for frame in frames:
        got = frame.splitlines()
        for i in rows:
            assert got[i].strip(), f"第 {i} 行整行空白了：\n{frame}"
```

Assert **screen text**, never the absence of `#cursor-bar` (internal widget) and never
`CursorPage._motion`. ADR-0008 §4's probe (`+10/+20ms ⟨整行空白⟩`) is reproducible under
`run_test()` — no pty needed.

### Docs (part of #62)

- Commit `docs/adr/0008-arrows-replace-enter-and-escape.md` (currently untracked).
- `docs/adr/0007-visual-identity-follows-terminal-theme.md`: add a status note on **§三 (L49)**,
  pointing at ADR-0008. The superseded bullet is **L55-58** ("光标 = 选中立即到位，装饰性的光标条
  随后追上 … 时长 ≤120ms"). The other three bullets of §三 (pan, spinner threshold, `auto|on|off`)
  are unaffected.

---

# #63 — task-row leading column becomes ☐ / ☑

### Edit sites

| file | symbol | lines | quote |
|---|---|---|---|
| `src/dida/tui/theme.py` | marks block | L266 (`DONE_MARK = "☑"`), L281-282 (`SUBTASK_DONE_MARK = "☑"` / `SUBTASK_TODO_MARK = "☐"`) | `SUBTASK_TODO_MARK = "☐"` already exists — reuse it, do **not** add a second string for U+2610 |
| `src/dida/tui/theme.py` | `__all__` | L83 area | add the new name |
| `src/dida/tui/theme.py` | `STRUCTURAL_GLYPHS` | L352-368 | **already contains** `DONE_MARK` (L359), `SUBTASK_DONE_MARK` (L362), `SUBTASK_TODO_MARK` (L363) → ☐/☑ are already under the guard; only add a name if you add a *new string constant* (the `CHECK_ON = SUBTASK_DONE_MARK` alias pattern at L285-286 is the repo-idiomatic way) |
| `src/dida/tui/pages/tasks.py` | `DONE_MARK = theme.DONE_MARK` + docstring | L65-66 | docstring says "它站在优先级标记那一列上" → reword |
| `src/dida/tui/pages/tasks.py` | `task_line` prefix | **L177** | `prefix = f"{DONE_MARK if item.completed else item.priority_mark} "` → `… else TODO_MARK} "` |
| `src/dida/tui/pages/tasks.py` | `completed_line` prefix | L196 | `prefix = f"{DONE_MARK} "` — unchanged (already ☑) |
| `src/dida/tui/pages/tasks.py` | `__all__` | L43 | export the new name |

Priority is untouched: keep `priority_mark()` (`sync/view.py:212-220`), `TaskItem.priority_mark`
(`view.py:145`, set at `:279`), `TaskDetail.priority_mark` (`read.py:237`, set at `:510`), and the
sort key. **Lowest-risk path: change only the rendering.** Deleting `priority_mark()` cascades into
`engine.py` re-exports (`:126`, `:266`), `rows.py:15` docstring, `view.py:237/248`, and tests
`test_task_row_model.py:32/374-376/750-760`, `test_read_model.py:274`. `TaskDetail.priority_mark` is
already unused by the TUI (detail page reads `detail.priority`, `detail.py:233-234/313`).

### Existing tests to rewrite

| file:line | test | current assertion → new |
|---|---|---|
| `tests/test_task_row_model.py:327-345` | `test_a_row_reads_the_priority_mark_the_title_the_due_the_tags_and_the_marks` | L342 `startswith("! 写周报")` → `"☐ 写周报"`; rename |
| `tests/test_task_row_model.py:347-353` | `test_a_plain_task_row_carries_no_marks_at_all` | L351 `startswith(". 写周报")` → `"☐ 写周报"` |
| `tests/test_task_row_model.py:366-389` | `test_every_mark_that_goes_into_a_column_is_width_unambiguous` | replace the three priority rows L374-376 |
| `tests/test_task_row_model.py:585-599` | narrow-width table | every `". 回邮件给产品经理…"` → `"☐ …"` (L591/593/595/597/599) |
| `tests/test_task_row_model.py:617-627` | title-keeps-cells tests | L618, L627 `". 回邮件给产品经理"` → `"☐ …"` |
| `tests/test_task_row_model.py:641` | `test_a_completed_row_keeps_its_title_and_drops_its_time_when_narrow` | `"☑ 回邮件给产品经理"` — **unchanged** |
| `tests/test_task_rows_page.py:132-146` | `test_a_row_reads_its_priority_mark_its_due_and_its_tags_on_screen` | L144 `f"{theme.CURSOR_MARK} ! 交季度报告"` → `☐` |
| `tests/test_visual_identity.py:642` | `LAYER_BODY_ROW[LAYER_TASKS]` | `(". 写周报", 2)` → `("☐ 写周报", 2)` — the *cell offset 2* is load-bearing for the pan test |
| `tests/test_visual_identity.py:894` | see #62 T-tests | `"❯ . 交水费"` → `"❯ ☐ 交水费"` |

`tests/test_complete_toggle.py:406` (`theme.DONE_MARK not in line_with(text, "交水费")`) — still valid;
`tests/test_task_rows_page.py:278` and `:315` (`theme.DONE_MARK in done`) — still valid.

### Glyph-width guard — exact file / test / table entry

Two guards, both must stay green:

1. **`tests/test_theme.py:132` `test_structural_glyphs_are_one_cell_and_never_ambiguous`** — iterates
   `theme.STRUCTURAL_GLYPHS` (`theme.py:352-368`), asserting for each glyph `len(glyph)==1`,
   `cell_len(glyph)==1`, `unicodedata.east_asian_width(glyph) not in ("A","W","F")`.
   ☐/☑ are **already covered** via `DONE_MARK` L359 / `SUBTASK_DONE_MARK` L362 /
   `SUBTASK_TODO_MARK` L363. No row to add unless a new literal string is introduced.
2. **`tests/test_task_row_model.py:366` `test_every_mark_that_goes_into_a_column_is_width_unambiguous`** —
   the literal dict at **L373-380** is where the row goes. Replace L374-376 with:

```python
    glyphs = {
        "未完成勾选框": theme.CHECK_OFF,   # ☐ U+2610
        "已完成勾选框": theme.CHECK_ON,    # ☑ U+2611  (== DONE_MARK)
        "没有截止时间": NO_DUE_TEXT,
        "重复标记": theme.REPEAT_MARK,
        "提醒标记": theme.REMINDER_MARK,
    }
```

(Also `tests/test_picker_fields.py:718` already covers `CURSOR_MARK`/`CHECK_ON`/`CHECK_OFF` —
free coverage for the new row mark.)

### New tests

- **Pure seam** (`tests/test_task_row_model.py`, precedent `test_a_row_reads_the_…` L327):
  `task_line(item, width=…)` starts with `☐ ` for unfinished and `☑ ` for completed; no `!`/`~`/`.`
  priority glyph appears anywhere in `line.plain`. `completed_line` keeps `☑ `.
- **Real-app seam** (`tests/test_task_rows_page.py`, precedent L261
  `test_completed_rows_are_struck_through_and_sunk_below_the_unfinished_ones`): the on-screen row
  starts `❯ ☐ ` / `  ☐ `; press `space` and assert the same row's first glyph flips `☐`→`☑` in
  `screen_text` (precedent `tests/test_complete_toggle.py:383`
  `test_space_again_on_a_completed_task_turns_it_back_to_unfinished`); assert the overdue-red test
  `tests/test_task_rows_page.py:105-126` still holds.
- **Do not** assert `TaskItem.priority_mark` is absent or inspect the widget tree.

---

# #64 — completed section sorts by the normal key

### Edit site

`src/dida/sync/view.py` → `completed_section()`, **L355-393**. `row_sort_key` is **already imported**
at `view.py:38` (`from dida.sync.rows import completed_window_start, row_sort_key`).

The line to change is **L391-393**:

```python
    return CompletedSection(
        items=tuple(sorted(rows, key=lambda row: (row.completed_at, row.title), reverse=True))
    )
```

Sort the **snapshots** by `row_sort_key` *before* mapping to `CompletedItem`, e.g.:

```python
    ordered = sorted((s for s in tasks if s.completed and s.completed_at is not None
                      and s.completed_at >= window_start), key=row_sort_key)
    rows = [CompletedItem(task_id=s.id, title=s.title,
                          list_name=names.get(s.list_id, s.list_id),
                          completed_at=s.completed_at,
                          completed_text=format_due(s.completed_at, all_day=False, now=now, day_end=day_end))
            for s in ordered]
    return CompletedSection(items=tuple(rows))
```

`CompletedItem` (`view.py:186-196`) and `CompletedSection` (`view.py:197-209`) need **no new fields**.
`TaskSnapshot` (`view.py:68-111`) already has `due`, `priority`, `completed`, `title`, `id`.
Update the docstring **L363** ("最近的排在最前") and L365-369 which calls `completed_at` "唯一有意义的
排序依据".

Single call site: `src/dida/sync/read.py:446-452` (real list branch). The view branch at
`read.py:453-456` uses `by_due` and is **already compliant** (mixed members, "已完成沉底" is the
first `row_sort_key` tuple position) — do not change it.

### Existing test to rewrite

`tests/test_task_row_model.py:198-213`
`test_the_completed_section_keeps_only_the_last_seven_days` — L212 currently asserts the
`completed_at`-descending order:

```python
    assert [item.title for item in section.items] == ["刚刚做完的", "六天前做完的"]
```

After the change the three snapshots (no `due`, all `priority=0`) sort by title codepoint:
**`["六天前做完的", "刚刚做完的"]`** (六 U+516D < 刚 U+521A); keep the window-filter assertion.
The `snapshot()` helper at **L60-75** takes only `id/title/list_id/completed/completed_at` — add
`due`/`priority` params to write the order test below.

### New tests

- **Pure seam** (`tests/test_task_row_model.py`, precedent `test_the_whole_sort_chain_holds_in_one_list`
  L97, and `test_the_sort_key_is_a_pure_function_of_the_row` L134):
  `test_the_completed_section_uses_the_same_sort_chain_as_the_unfinished_half` — three completed
  snapshots whose `completed_at` order is the reverse of their `(due, priority)` order; assert the
  returned titles follow due-asc → priority-desc → no-due-last, i.e. **not** `completed_at`.
  Also assert a row with no `due` sinks below one with a `due`.
- **Real-app seam** (`tests/test_task_rows_page.py`, precedent L261): enter a real list with ≥2
  completed tasks whose completion times invert their due order; assert the on-screen line order in
  `screen_text`. Do not assert engine internals.
- No "已完成 N 项" separator line (explicitly out of scope).

---

# #65 — `→` / `←` navigation, `h` help

### Shared infrastructure to reuse — `src/dida/tui/keys.py` (one table, two readers)

| symbol | lines | shape |
|---|---|---|
| `GLOBAL = "global"` / `LAYER_INDEX` / `LAYER_TASKS` / `LAYER_DETAIL` / `LAYERS` | L56-69 | layer values are exactly `"global"`, `"index"`, `"tasks"`, `"detail"` |
| `Key` dataclass | L89-100 | `key.keys: tuple[str, ...]` (Textual key names), `key.action: str` (→ `action_<name>`), `key.label: str` (help text) |
| `BINDINGS: dict[str, tuple[Key, ...]]` | **L103-135** | the single source of truth |
| `GLOBAL` tuple | L104-109 | `Key(("question_mark",), "help", "当前这一层的键位")` at **L105**; `r`/`o`/`QUIT_KEYS` follow |
| `LAYER_INDEX` tuple | L110-117 | `Key(("enter",), "enter", "进入这一行")` at **L113** |
| `LAYER_TASKS` tuple | L118-128 | `enter` at **L122** (`"任务详细页"`), `Key(("escape",), "back", "退回清单列表页")` at **L124** |
| `LAYER_DETAIL` tuple | L129-134 | `enter` at **L132** (`"编辑这个字段"`), `Key(("escape",), "back", "结束编辑 / 退回任务列表页")` at **L133** |
| `KEY_NAMES` | L137-141 | `{"question_mark": "?", "escape": "esc", "enter": "enter"}` — **only unambiguous-width spellings allowed** |
| `key_text(key)` | L197-199 | `KEY_NAMES.get(key, key)` — no entry ⇒ the raw Textual name is displayed (that is how `left`/`right`/`down`/`up` come out ASCII for free) |
| `bindings_for(layer)` | L202-211 | `[Binding(",".join(key.keys), key.action, key.label) for key in BINDINGS[GLOBAL] + BINDINGS[layer]]` |
| `help_rows(layer)` | L227-240 | `HelpRow(key=" / ".join(key_text(n) for n in key.keys), label=key.label, keys=key.keys)` |
| `help_body(layer)` | L251-271 | heading + `theme.pad(row.key, width)` per row; `width = max(cell_len(row.key))+2` |
| `LAYER_TITLES` | L243-247 | 清单列表页 / 任务列表页 / 任务详细页 |
| `unreliable_reason(key)` | L180-194 | table `_UNRELIABLE_KEYS` L152-162, prefixes L165-176; returns `None` for reliable keys |

**How `h` is bound today / how to rebind.** `h` is currently bound to nothing. `?` is
`Key(("question_mark",), "help", "当前这一层的键位")` at **keys.py:105**, with the display name
`"question_mark": "?"` at **keys.py:138**. The edit is exactly two lines:

```python
    Key(("h",), "help", "当前这一层的键位"),          # keys.py:105
```
and delete `"question_mark": "?",` from `KEY_NAMES` (**keys.py:138**). `h` needs no `KEY_NAMES`
entry — `key_text` falls back to the raw name, which is ASCII 1-cell on both sides (same mechanism
that makes `left`/`right`/`down`/`up` safe). **Never add `left`/`right`/`←`/`→` to `KEY_NAMES`** —
ADR-0008 §3 and `test_keymap.py:108-124` (the `drawn_width` guard) forbid it.

`help` action stays `DidaApp.action_help` (**app.py:1157-1159**, pushes
`MessageOverlay(help_body(self._layer))`); its docstring says `?` → change. `DidaApp.BINDINGS =
bindings_for(GLOBAL)` at **app.py:249-251** (docstring mentions `?` at L248, L251).

### The three navigation pages declare `BINDINGS = bindings_for(LAYER)`

| file | line | code |
|---|---|---|
| `src/dida/tui/pages/index.py` | L349-350 | `LAYER = LAYER_INDEX` / `BINDINGS = bindings_for(LAYER)` |
| `src/dida/tui/pages/tasks.py` | L205-206 | `LAYER = LAYER_TASKS` / `BINDINGS = bindings_for(LAYER)` |
| `src/dida/tui/pages/detail.py` | L529-530 | `LAYER = LAYER_DETAIL` / `BINDINGS = bindings_for(LAYER)` |

So the whole navigation rebind is *table-only*. Action names survive, so no handler renames:

- `IndexPage.action_enter` index.py L425-433 (keep name `enter`, rebind key to `right`).
- `TasksPage.action_enter` tasks.py L348-350; `TasksPage.action_back` tasks.py L394-396.
- App handlers: `on_index_page_entered` app.py L592-594, `on_tasks_page_entered` L600-602,
  `on_tasks_page_back` L604-606, `on_detail_page_back` L635-637.

**Detail page needs a split — `esc` must stop meaning "back".**
`DetailPage.action_back` (**detail.py:1002-1015**) currently does both:

```python
        if self._due_editing:
            self._escape_due_edit(); return
        if self._editing is not None:
            self._finish_edit(); return
        self.post_message(self.Back())
```

The design requires `←` = back (unconditional) and `esc` = end-edit-only. Cleanest table shape:

```python
    LAYER_DETAIL: (
        Key(("j", "down"), "cursor_down", "下一个字段"),
        Key(("k", "up"), "cursor_up", "上一个字段"),
        Key(("enter",), "enter", "编辑这个字段"),
        Key(("left",), "back", "退回任务列表页"),
        Key(("escape",), "end_edit", "结束编辑（保存）"),
    ),
```
with a new `DetailPage.action_end_edit` containing the first two branches and `action_back` reduced
to `self.post_message(self.Back())`. `on_input_submitted` (**detail.py:990-1001**) already routes
`enter` → `_finish_edit`; unchanged.

`LAYER_INDEX` must **not** bind `left` (user story 124: nothing happens). UNVERIFIED: whether
`left`/`right` left unbound on a page fall through to `ScrollableContainer`'s `scroll_left/right`
(`test_visual_identity.py:556-566` builds `scroll_bindings()` from `ScrollableContainer.BINDINGS`);
Textual's `VerticalScroll` sets `overflow-x` hidden, so it should raise `SkipAction` — but **the
implementer must run `test_the_pan_track_is_not_user_scrollable_at_all` (L565) and
`test_the_user_scroll_keys_never_push_the_pan_track` (L391) to confirm `left` on layer 0 does not
move `#stage`**.

### Files whose docstrings / prose must follow

`GLOSSARY.md:81` — "由 `enter` 压入、`esc` 弹出" → new keys (this is the only terminology change
required). `keys.py:13-14, 113, 122`, `app.py:3, 122, 406-410, 435, 442, 448, 453, 593, 601, 605,
636`, `app.py:248/251`, `app.py:287-289`, `overlays.py:426` (MessageOverlay docstring says `?`),
`overlays.py:68` `FORM_HINT` (shared with #66 — see overlap), `base.py:13` ("`enter` 也因此永远落不到
小标题上"), `base.py:359-360` ("`esc` 回来时…"), `pages/tasks.py:349`, `pages/index.py:426, 438`,
`pages/detail.py:1003-1009`, `theme.py:531` region comments no longer mentioning the bar.

### Existing tests that must be rewritten

**Chokepoint: the navigation helpers.** Changing `press("enter")` → `press("right")` inside these
covers most of the suite:

| helper | file:line |
|---|---|
| `enter_detail` | `tests/test_detail_page.py:124-132` |
| `enter_detail` | `tests/test_picker_fields.py:314-322` |
| `enter_detail` | `tests/test_reminders.py:73` |
| `enter_work` | `tests/test_task_rows_page.py:96-102` |
| `enter_container` | `tests/test_task_rows_page.py:195-202` |
| `enter_work` | `tests/test_visual_identity.py:113-119` |
| `go_to_layer` | `tests/test_visual_identity.py:386` |
| `go_to_layer` | `tests/test_layer_focus.py:47-57` |
| `enter_the_list` / `enter_view` | `tests/test_complete_toggle.py:312` / `:348` |
| `enter_view` | `tests/test_views.py:423` |
| `open_the_today_view` | `tests/test_live_day_boundary.py:284` |
| `open_work` | `tests/test_task_delete_defer.py:106-116` |
| `open_container` | `tests/test_create_task.py:70` |

**`esc`-as-back sites (must become `left`):** `tests/test_pages.py:204, 249, 297, 375, 398`;
`tests/test_visual_identity.py:173, 700, 703`; `tests/test_layer_focus.py:164, 167`;
`tests/test_app_actions.py:157, 166`; `tests/test_detail_page.py:791, 1145, 1297`;
`tests/test_live_day_boundary.py:307, 367`; `tests/test_views.py:500`;
`tests/test_view_overlay.py:138`. (`tests/test_visual_identity.py:700/703` also drive
`test_every_layer_change_still_parks_the_track_on_that_layer` L654.)

**`enter`-as-nav sites in test bodies (must become `right`):** `tests/test_pages.py:198, 206, 244,
254, 271, 288, 291, 320, 332, 373, 394, 402`; `tests/test_app_actions.py:160, 191, 308, 328`;
`tests/test_visual_identity.py:161, 167, 244, 353, 697`; `tests/test_layer_focus.py:158`;
`tests/test_view_overlay.py:357`; `tests/test_complete_toggle.py:483`;
`tests/test_app_sync.py:217`; `tests/test_live_day_boundary.py:314`; `tests/test_pages.py:179`.
**Careful:** `press("enter")` that confirms a FormOverlay must NOT change (e.g.
`tests/test_list_overlay.py:95, 100, 118, 126, 143, 164, 166, 392, 433`;
`tests/test_view_overlay.py:99, 111, 134, 142, 181, 184, 246, 276, 279, 300, 353, 399, 481, 484, 504`;
`tests/test_create_task.py:110, 130, 150, 171, 217, 239, 263, 286, 326`;
`tests/test_picker_fields.py:348, 354, 379, 385, 407, 441, 452, 496, 506, 536, 564, 567, 593, 634,
656, 683, 707, 767, 769`; `tests/test_complete_toggle.py:480`).

**Help-key sites (`question_mark` → `h`):** `tests/test_keymap.py:226` (the `open_help` helper
L224-231), `tests/test_app_actions.py:153, 162`, `tests/test_quit_interception.py:186, 208`.

**Test names/prose to update:** `tests/test_keymap.py:234`
`test_the_question_mark_help_shows_only_the_keys_of_the_layer_you_are_on` (also L270
`await pilot.press("enter")` → `"right"`, and L234-268 assert `"退回"` presence by layer — still
correct, but L239's parenthetical "spec 的状态机里清单列表页只有 `enter` 向下" is now false);
`tests/test_app_actions.py:147` `test_the_help_lists_the_keys_of_the_layer_you_are_on` and its
L168 assertion `"进入这一行" in on_index` (the *label* survives; the key becomes `right`);
`tests/test_pages.py:233` `test_enter_opens_the_container_and_esc_comes_back_…` and L278
`test_enter_on_a_task_opens_the_detail_page_and_esc_comes_back` — rename to `→`/`←`;
`tests/test_detail_page.py:784` `test_esc_on_the_field_list_goes_back_to_the_task_you_came_from`
→ rename to `left`; `tests/test_visual_identity.py:654` docstring says "``enter`` / ``esc`` 换层";
`tests/test_visual_identity.py:1` module docstring.

**Unaffected (keep):** `tests/test_detail_page.py` all `esc` presses that end an edit — L414, 440,
470, 476, 554, 643, 756, 842, 894, 920, 940, 1204, 1238, 1272, 1305, 1332, 1395 — and the
due-editor tests named `test_esc_*` (L1199, L1234, L1268, L1289, L1329, L1389).

### New tests (real-app seam)

Precedents: `tests/test_keymap.py:234`, `tests/test_pages.py:244`, `tests/test_visual_identity.py:654`.

1. `test_the_arrow_keys_walk_the_three_layers_and_back` — `right` index→tasks→detail, `left`
   detail→tasks→index; assert via `screen_text` (container name / task title) and the public
   `app.layer` / page `selected_id`. Do **not** read `_layer`.
2. `test_enter_and_escape_no_longer_navigate_on_the_three_pages` — on each layer press `enter`
   (where it is not the edit key) and `escape`; assert `app.layer` unchanged and `screen_text`
   unchanged (precedent `test_visual_identity.py:391`).
3. `test_left_on_the_index_page_does_nothing` — `left` on layer 0: `app.layer == LAYER_INDEX`,
   `screen_text` unchanged, `#stage` offset unchanged.
4. `test_h_opens_the_help_and_question_mark_does_not` — `h` ⇒ the layer title + rows appear;
   `?` ⇒ `screen_text` unchanged.
5. `test_h_on_the_help_overlay_does_not_stack_a_second_one` — precedent
   `tests/test_quit_interception.py:177`; `h` twice ⇒ still one overlay (`h` is not bound on
   `MessageOverlay`; `overlays.py` L405-415 documents that modal resolution truncates before
   `app._bindings`, which is why `QUIT_BINDINGS` L402 exists).
6. `test_left_right_in_a_text_field_move_the_cursor_instead_of_leaving_the_layer` — enter the title
   editor, press `left`/`right`, assert the editor is still on screen and `app.layer == LAYER_DETAIL`
   (precedent `tests/test_detail_page.py:409`). Pure-function side already exists:
   `tests/test_keymap.py:187-196` asserts `unreliable_reason("left"/"right") is None`.
7. Keep `test_picker_fields.py:348` (ChoiceField `left`/`right` still changes step) and
   `tests/test_picker_fields.py:377-391` (MultiChoiceField `up`/`down`/`space`); add an assertion
   that they are unchanged after the rebind.
8. Pure: `test_the_keymap_lists_left_and_right_by_their_ascii_names` — `help_rows` for each layer
   contains `"left"`/`"right"` and no `"←"`/`"→"`; already enforced by
   `test_keymap.py:108-124`.

---

# #66 — `esc` in a form means save-and-exit; confirm dialog unchanged

### Edit sites — `src/dida/tui/overlays.py`

| symbol | lines | quote / action |
|---|---|---|
| `FORM_HINT` | **L68** | `"Tab 换一格 / 选择框用左右方向键 / Enter 确认 / Esc 取消 / Ctrl+C 退出"` → `Esc 保存`（keep ASCII, no `←`/`→`; guarded by `tests/test_list_overlay.py:474-500`) |
| `FormOverlay` docstring | L304-346 | L307 "``Esc`` 交回 ``None``（取消），**什么都不写**" → new semantics; L344-345 "表单自己的出口是 ``Esc``" → "``Esc`` 就是保存" |
| `FormOverlay.BINDINGS` | **L350-356** | `*FORM_QUIT_BINDINGS`, then **L354** `Binding("escape", "cancel", "取消", show=False)` → `Binding("escape", "confirm", "确认", show=False)`; L355 `Binding("enter", "confirm", "确认")` stays |
| `FormOverlay.action_confirm` | L394-396 | unchanged (`self.dismiss(self.values())`) |
| `FormOverlay.action_cancel` | **L398-400** | **DELETE** (`self.dismiss(None)`) |
| class base | L304 | `ModalScreen[dict[str, str] | None]` → `ModalScreen[dict[str, str]]` if you also drop the `None` branches (see below) |
| `ConfirmOverlay` | L455-493 | **DO NOT TOUCH** — `Binding("escape", "cancel", "取消")` at **L473**, `action_cancel` L491-493; `y` L471, `n` L472 |
| `MessageOverlay` | L423-452 | unchanged (`esc`/`enter` = close, L436-437) |

`ChoiceField.BINDINGS` (L145-148, `left`/`right`) and `MultiChoiceField.BINDINGS` (L211-215,
`up`/`down`/`space`) are unchanged, but they must now inherit the new `esc` meaning because they live
inside `FormOverlay` — `esc` reaches the overlay because neither widget binds it.

### Callbacks whose `if values is None: return` becomes unreachable

`src/dida/tui/app.py` — five sites push `FormOverlay`: L644 (`_finish_new_task`, def L648),
L780 (`_finish_pick`, def L784), L910 (`_finish_kind_form`, def L918), L993 (`_finish_list_form`,
def L1025), L1015 (`_finish_view_form`, def L1050). Their `None` guards are at **L660, L794, L920,
L1031, L1056**. Lowest-risk change: keep the guards (harmless, and `partial`-wrapped signatures
stay typed). Tidiest change: drop `| None` from all five signatures and the five guards.

**Behavioural consequence for the "清单还是视图" question** (`IndexPage.new_kind_fields`,
`index.py:243-254`; default value `KIND_LIST` at L249): `esc` now submits the *default* choice, so
`esc` on that overlay opens the **list** form instead of closing everything. This is the intended
new semantics and it invalidates `tests/test_view_overlay.py:151-170` (see below).

### Existing tests to rewrite — the six ADR-named "escape cancels" tests

All six verified at `f499427`; names taken verbatim from the source.

| # | file:line | test | currently asserts | new assertion |
|---|---|---|---|---|
| 1 | `tests/test_list_overlay.py:134` | `test_escape_cancels_the_form_without_writing_anything` | after typing `shopping` then `esc`: `fake.created_lists == []`, `"shopping" not in after` | `esc` saves: `fake.created_lists == [("shopping", …)]`, overlay closed. Rename to `…_escape_saves_…` |
| 2 | `tests/test_create_task.py:183` | `test_escape_cancels_without_writing_anything` | `fake.created == []`, `"标题" not in after` | `fake.created == ["写周报"]`; overlay closed |
| 3 | `tests/test_picker_fields.py:395` | `test_escape_closes_the_picker_without_writing_anything` | presses `right` then `esc`; `fake.moved == [] and fake.writes == []` | `esc` saves the changed step: `fake.moved == [("t1", …)]` |
| 4 | `tests/test_picker_fields.py:642` | `test_escape_closes_the_tag_picker_while_the_multi_select_has_focus` | `focused.id == "field-tags"` (keep), presses `space` then `esc`, `fake.writes == []` | `esc` reaches the overlay with focus in the multi-select **and saves** the ticked tag: `fake.writes != []` |
| 5 | `tests/test_view_overlay.py:151` | `test_escape_on_the_question_writes_nothing_at_all` | `fake.created_lists == [] and fake.created_views == []`, `"名字" not in after` | `esc` = the default answer ⇒ the **list** form opens: `"名字" in after`, still `created_lists == []` |
| 6 | `tests/test_view_overlay.py:309` | `test_escape_cancels_the_view_form_without_writing_anything` | `fake.created_views == []`, `"buzhu" not in after` | needs a *valid* view form; `esc` saves. Rename to `…_escape_saves_…` |

**Also broken by #66 (not in ADR-0008's list):**

| file:line | test | why |
|---|---|---|
| `tests/test_view_overlay.py:120-146` | `test_n_asks_whether_it_is_a_list_or_a_view_first` | L138 presses `esc` on the kind question and then L140 presses `n` again, assuming the question closed; now `esc` submits `清单` and the list form is already open |
| `tests/test_create_task.py:95` | (inside `test_the_new_task_form_asks_only_for_a_title`) | asserts `"Esc 取消" in form` — reword to the new hint text |
| `tests/test_list_overlay.py:382-401` | `test_the_form_keeps_the_keyboard_while_it_is_open` | L399 `esc` then asserts "Esc 是出口：回到清单列表页" — with an empty `名字` field, `esc`-save hits `EMPTY_LIST_NAME_MESSAGE` and leaves the form open; the test's premise ("Esc 是出口") must become "Esc 是保存（这一格空着就什么都不建）" |
| `tests/test_list_overlay.py:474-500` | `test_the_form_text_uses_no_ambiguous_width_glyphs` | reads `FORM_HINT` by import — only fails if you introduce `←`/`→` into the hint; keep ASCII |
| `tests/test_list_overlay.py:443-455` | `test_ctrl_c_still_quits_from_the_form_in_every_state` | L451 `esc` "关掉表单" → now saves (empty name ⇒ refused); still asserts `q` is a letter and `Ctrl+C` quits — adjust the `esc` reset |

`tests/test_picker_fields.py:190` (`test_moving_a_task_to_the_list_it_is_already_in_writes_nothing`)
and `:752` (`test_a_pick_that_changes_nothing_writes_nothing`) are the two "write only what changed"
guards and **must stay green** — that is what makes "no cancel" safe.

### Where the natural assertion would violate "external behaviour only"

`esc`-saves has an obvious internal formulation (`FormOverlay.action_cancel` no longer exists; the
binding table has no `cancel` action; `app.screen` is a `FormOverlay`). **Do not write those.**
Repo-idiomatic instead: assert the `FakeBackend` write ledger (`created_lists`, `created`,
`created_views`, `moved`, `writes`, `pushes`) **and** `screen_text` (the overlay's title/fields are
gone, the underlying layer is visible). Precedent: `tests/test_list_overlay.py:134-153`.

### New tests

- Real-app seam, precedent `tests/test_create_task.py:183` and
  `tests/test_picker_fields.py:752`: `esc` in the new-task form with a typed title creates the task
  **once** and leaves the form; `esc` in the same form with an **untouched** title writes nothing
  (the `NO_TITLE_MESSAGE` guard, `messages.py:35`, used at `app.py:664`).
- `test_escape_on_the_delete_confirm_still_means_no` — precedent
  `tests/test_quit_interception.py:131` (docstring already claims "`n` / `Esc` 是留下" but only
  presses `n`; no existing test presses `esc` on `ConfirmOverlay`). Assert the task is still present
  in `screen_text` after `d` + `esc`.
- `test_the_form_hint_says_escape_saves` — assert the new `FORM_HINT` string appears in
  `screen_text` (precedent `tests/test_create_task.py:95`).
- `test_q_is_still_a_letter_and_ctrl_c_still_quits_inside_a_form` — already covered by
  `tests/test_list_overlay.py:377` and `:404`; keep.

---

# Overlap matrix (merge-collision surface)

| file | #62 | #63 | #64 | #65 | #66 |
|---|---|---|---|---|---|
| `src/dida/tui/pages/base.py` | **all** | | | L13, L359-360 docstrings | |
| `src/dida/tui/pages/detail.py` | L685, L698, L762, L788 | | | L1002-1015 split + docs | |
| `src/dida/tui/theme.py` | L40/46/135/148/388/531 | L266/281-282/352-368 | | | |
| `src/dida/tui/app.py` | L358-359 | | | many docstrings, L1157-1159 | 5 callbacks |
| `src/dida/tui/keys.py` | | | | **all of L103-141 + docs** | |
| `src/dida/tui/overlays.py` | | | | L426 doc | **L68, L304-356, L398-400** |
| `src/dida/tui/pages/tasks.py` | | L65-66, L177 | | L349 doc | |
| `src/dida/tui/pages/index.py` | | | | L426, L438 docs | |
| `src/dida/sync/view.py` | | L212-220 (maybe) | **L355-393** | | |
| `src/dida/sync/read.py` | | (maybe) | — | | |
| `GLOSSARY.md` | | | | **L81** | |
| `docs/adr/0007-…md` | **L49-58 status note** | | | | |
| `tests/test_visual_identity.py` | 869-911 + new | 642, 894 | | 173, 700, 703, 654, helpers | |
| `tests/test_detail_page.py` | 349-386 | | | 791, 1145, 1297 | |
| `tests/test_task_row_model.py` | | many | 198-213 | | |
| `tests/test_task_rows_page.py` | | 132-146 + new | + new | helpers | |
| `tests/test_pages.py` | | | | many | |
| `tests/test_keymap.py` | | | | 226, 234, 270 | |
| `tests/test_app_actions.py` | | | | 153, 162, 160, 168 | |
| `tests/test_quit_interception.py` | | | | 186, 208 | + new |
| `tests/test_list_overlay.py` | | | | | 134-153, 382-401, 443-455 |
| `tests/test_create_task.py` | | | | helper L70 | 95, 183-197 |
| `tests/test_picker_fields.py` | | | | helpers | 395-413, 642-663 |
| `tests/test_view_overlay.py` | | | | 138, 357 | 120-146, 151-170, 309-322 |
| `tests/test_complete_toggle.py` | | | | 312, 348, 480, 483 | |

**Highest-conflict files: `tests/test_visual_identity.py` (#62 × #63 × #65), `theme.py`
(#62 × #63), `app.py` (#62 × #65 × #66), `overlays.py` (#65 × #66 via `FORM_HINT`),
`sync/view.py` (#63 × #64).**

---

# Landing order / prerequisites

Ticket #61's own suggestion: **#62 first (independent), then #65, then #63+#64.** Verified
test-level couplings:

1. **#62 has no test prerequisite for anything else**, and `#62`'s rewrite of
   `test_the_cursor_marker_survives_the_travelling_bar` (L872) contains the literal
   `"❯ . 交水费"`. If #63 lands first, #62 must use `"❯ ☐ 交水费"`. Whichever lands second must
   rebase that one string — not a design dependency.
2. **#63 must land before `tests/test_visual_identity.py:642` (`LAYER_BODY_ROW`) is final**: the
   cell offset `2` is load-bearing for `#59`'s pan tests, and the marker string changes. #65 is
   independent of it.
3. **#63 and #64 share `tests/test_task_row_model.py`** and both need the row/engine read model —
   they can land in either order, but #64's new snapshot-based test wants the `snapshot()` helper at
   L60-75 extended with `due`/`priority`, which #63 does not touch.
4. **#65 and #66 both edit `overlays.py`**; `FORM_HINT` (L68) is named by **both** tickets
   (#65's "docstring 一并跟上" list in ADR-0008 §Consequences and #66's "底部那行键位提示改成新语义").
   Only one of them should own that line; the other should be told it is already done.
   The narrowest reading: #66 owns `FORM_HINT`'s *semantics* (`Esc 取消` → `Esc 保存`); #65 owns
   only `MessageOverlay`'s `?` mention at L426.
5. **#66 has no test prerequisite on #62/#63/#64.** Its `esc`-saves rewrite of
   `tests/test_picker_fields.py:395` presses `right` first (a `ChoiceField` key, unaffected by #65).
   However `tests/test_picker_fields.py:642` (multi-select `esc`) and
   `tests/test_list_overlay.py:134` navigate via `enter_container`/`open_container` helpers, so if
   **#65 lands first** those helpers already press `right` — no conflict, but the rebase order
   matters for the same hunk.
6. **#64 is fully independent** (one function in `sync/view.py`, one test in
   `test_task_row_model.py:198-213`) and can land any time. It shares no file with #62, and only
   `sync/view.py` with #63.
7. `DidaApp._motion` must exist for **#62** to delete the page fan-out (app.py L358-359) *without*
   deleting `theme.animations_enabled` — #62 is the only ticket that touches `set_animate`, so it is
   a prerequisite for nothing else.

---

# Cross-cutting: "external behaviour only" violations to avoid

| ticket | tempting internal assertion | repo-idiomatic alternative |
|---|---|---|
| #62 | `app.query_one("#cursor-bar")` raises; `page._motion is False`; `theme.BAR` gone | per-frame `screen_text` contains no blank row (see #62 new test); `screen_text` identical under `animations="on"` and `"off"` for the cursor row |
| #63 | `TaskItem.priority_mark` removed; `tasks_page()._rows[...]` | `task_line(...).plain` starts `☐`/`☑` and contains no `!`/`~`/`.`; on-screen row prefix via `line_with(screen_text(app), …)` |
| #64 | `CompletedSection` field added; engine ordering internals | pure `completed_section(...).items` title order; on-screen row order in `screen_text` |
| #65 | binding table has no `question_mark`; `app.screen` class | press the real key, assert `screen_text` / `app.layer` / page `selected_id`; pure `help_rows(layer)` / `help_body(layer)` return values |
| #66 | `FormOverlay.action_cancel` absent; `app.screen` is not a `FormOverlay` | `FakeBackend` write ledger + `screen_text` shows the overlay closed |

The only "inside" this repo's tests read is the page's public `selected_id` and the `app.layer`
string — everything else is `screen_text` / `screen_styled_text` / `screen_sgr`
(`tests/support.py:11-43`) or a pure function's return value.

**UNVERIFIED (must be confirmed by the implementer before claiming done):**

1. Whether leaving `left`/`right` unbound on `IndexPage` is truly inert (Textual
   `VerticalScroll` `overflow-x`) — run `tests/test_visual_identity.py:565` and `:391`.
2. Whether `h` really cannot reach `App.BINDINGS` under `MessageOverlay` (asserted only indirectly by
   `tests/test_list_overlay.py:377` for `q` and by the `overlays.py` L405-415 comment) — the new
   "no second help overlay" test is the evidence.
3. That no *other* test besides the ones listed asserts a `. ` / `! ` / `~ ` task-row prefix — the
   grep covered `tests/*.py` for `"! "`, `"~ "`, `". "` and `priority_mark`, but a hand-run of
   `uv run pytest` after each ticket is the only complete check.
4. Exact `uv run pytest` duration/baseline — not run here (read-only recon; no test execution).
