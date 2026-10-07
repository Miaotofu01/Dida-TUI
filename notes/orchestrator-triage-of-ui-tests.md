# Orchestrator's independent triage of the 203 TUI-file tests

Written by the orchestrator (not by the ticket owner) as a **cross-check** for ticket #32 part 3
("先搬后删"). Mechanical classification, then a judgement call per test. #32 owns the real decision —
this exists so a silent loss of v1's conclusions is caught by two independent passes.

## Mechanical facts (reproduce with the commands at the bottom)

- **19** test files (not 20) contain `dida.tui` or `DidaApp`, and they hold **exactly 203** collected tests.
  The spec's "20 files / 203 tests" is right on the count, off by one on the file count.
- Of those 203, **107 actually drive a pilot** (`run_test`/`pilot`) and **55 do not**. The remaining 41
  are module-level/class-level tests my crude scan didn't attribute — treat 55 as a floor, not a total.
- 55 is close to the ticket's "约 50 条其实是引擎/API 级断言". **The estimate is sound.** But "not a pilot"
  ≠ "engine-level": some of the 55 assert *colour*, *rendered text*, or *TUI-internal* behaviour and
  should still die with the rewrite. The split below is the part that matters.

## Should be rescued into non-UI test files (engine / API / storage / config level)

| File | Test | Why it must survive |
|---|---|---|
| `test_architecture.py` | all 6 | Module-boundary guards. **#34 must edit these** when `dida.date_parser` leaves `SEVEN_MODULES`, and when the TUI import rule changes. Deleting them would remove the only guard on the architecture. |
| `test_bootstrap.py` | `test_console_script_points_at_an_importable_main` | The `dida` entry point. #34 deletes `tui/app.py`; this test is what catches a broken console script. |
| `test_sync_session.py` | all 3 | Credentials: verify-before-store, rejected token never stored/printed, auth-rejection message. Config/credential level — survives verbatim. |
| `test_escape.py` | 3 × `test_task_url_*` | Deep-link assembly. The spec explicitly keeps 深链拼装. **⚠ Interaction with #33:** these pin "substitute the literal `inbox` when the project id contains `inbox`" — #33 says a missing projectId must no longer be guessed as the literal `inbox`. Reconcile deliberately; don't let one silently rewrite the other. |
| `test_reschedule.py` | 5 of 8 | `reschedule_changes_only_the_due_date_and_pushes_at_once`, `an_all_day_due_is_written_verbatim_as_a_date_marker`, `rescheduling_an_all_day_task_to_a_time_clears_the_all_day_flag`, `a_failed_push_keeps_the_reschedule_in_the_retry_queue`, `rescheduling_a_task_outside_the_cache_is_refused_not_queued`. **Ticket #40 (顺延) is built on exactly these conclusions.** Highest-value rescue in the set. |
| `test_delete.py` | `deleting_through_the_engine_enqueues_one_delete_change`, `a_delete_that_cannot_be_pushed_does_not_come_back_on_the_next_refresh` | Engine write path for delete. #40 needs them. (The other two delete tests are confirmation *copy* — see below.) |
| `test_complete.py` | `test_completing_through_the_engine_pushes_the_complete_endpoint` | The documented complete endpoint. #38's complement (un-complete) is new; this one stays. |
| `test_detail_description.py` | `test_a_refresh_carries_them_from_the_server_payload_into_the_snapshot` | Refresh carries `content`/`desc`/tags into the snapshot. #43 depends on it. (The other two are rendered-text assertions.) |
| `test_quick_add.py` | `test_a_thin_create_response_does_not_drop_what_the_user_wrote`, and the retry-queue half of `a_failed_create_push_stays_in_the_retry_queue` | Create-path conclusions that survive #39. **But** "quick_add creates locally and pushes the whole line" and "an all_day create writes the date marker verbatim" encode v1's *date-parser* quick-add syntax, which v2 kills outright — those die. Split carefully. |
| `test_priority_filter.py` | `p_walks_the_wire_values_0_1_3_5`, `p_pushes_the_wire_value_and_marks_it_with_the_existing_marks`, `a_failed_push_keeps_the_priority_change_in_the_retry_queue` | The `0/1/3/5` wire encoding and the write path. #45 needs the encoding. **The two `fuzzy_match` tests die** — `/` fuzzy filter is explicitly out of scope in v2. And the `p` *key* itself is out of scope; only the wire-value conclusion moves. |
| `test_subtasks.py` | the read-only half: `subtasks_show_title_and_completion_state_even_without_a_due_date`, `a_task_without_subtasks_reads_as_no_rows` | #43 must show subtasks **read-only**. **The other six die** — they pin *toggling* a subtask, the reread/collision dance, and pending-tick survival, all of which v2 drops ("子任务只看不勾"). Don't rescue write behaviour the spec deletes. |

## Should die with the rewrite (they assert colour, rendered strings, or deleted behaviour)

- `test_app_completed.py` ×2 (dim/struck-through, header count wording) — colour + rendering.
- `test_app_view.py` ×2 — one colour assertion; `test_the_tui_never_hardcodes_a_hex_colour` is arguably a
  lint worth keeping, but it scans v1's CSS strings and v2's CSS is new. #34's call.
- `test_overdue_row.py` ×2 — colour assertions.
- `test_complete.py` ×3 keymap assertions (`key_is_not_adjacent_to_navigation_keys`,
  `key_is_not_part_of_the_cursor_cluster`, `completed_row_carries_a_highlight`) — **#48 rebuilds this
  class of guard** against the new keymap. Don't rescue the v1 instances; rescue the *idea*.
- `test_key_help.py` ×1 `help_table_covers_every_key_the_app_and_the_panes_bind` — same: #48 rebuilds it
  layered, driven off the real binding table. The v1 version dies.
- `test_priority_filter.py` ×2 `fuzzy_match_*` — out of scope in v2.
- `test_subtasks.py` ×6 write-path tests — out of scope in v2.

## Judgement calls worth a second look

- `test_delete.py` ×2 confirmation-copy tests (`prompt_names_the_task_and_says_it_cannot_be_undone`,
  `prompt_never_implies_the_task_can_be_recovered`): the *conclusion* is required by #40 ("确认文案明确告知
  删除在服务端不可恢复"), but the test reaches it through a rendered screen. Rescue the assertion, not the
  mechanism.
- `test_priority_filter.py::filter_groups_*` ×2: v1's grouping pure functions. v2's view evaluation
  replaces them — but the "differences between groups" conclusions may inform #35. Read, then decide.
- The 41 unattributed tests: my scan only looked at module-level and one level of class nesting. **#32
  should re-run the classification more carefully** rather than trusting my 55.

## Reproduce

```bash
cd /home/tofu/dida-v2-worktrees/integration
grep -lE "dida\.tui|DidaApp" tests/test_*.py | wc -l        # 19
for f in $(grep -lE "dida\.tui|DidaApp" tests/test_*.py); do
  uv run pytest --collect-only -q "$f" | tail -1
done                                                         # sums to 203
```
