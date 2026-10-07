# Prompt — ticket #42 (清单的建 / 改 / 删)

Follow `/home/tofu/dida-v2-worktrees/notes/implementer-template.md`. Worktree
`/home/tofu/dida-v2-worktrees/t42`, branch `ticket/42-list-crud`. Fetch your acceptance criteria from the
tracker — they are your definition of done, verbatim:

```bash
gh api repos/Miaotofu01/Dida-TUI/issues/42 --jq .body
gh api repos/Miaotofu01/Dida-TUI/issues/42/comments --jq '.[].body'
```

**Read before designing:** `cross-ticket-corrections.md` (the `#42` entry — two documented response traps),
`api-shapes.md` §B (exact request/response shapes for project create/update/delete, cited by doc line),
`wave-plan-and-seams.md` (**you build the shared overlay shell**), and §4 + §8 of `codebase-map.md`.

## You build the shared overlay; #36 reuses it

Ticket #36 (custom views) needs the same overlay, with fields chosen by the selected row's type — list
name+colour for you, view filter conditions for it. Both tickets say 「谁先落地谁把浮层壳子搭出来」, and you
land first (it is blocked by #35, you are not). **So build the shell in its own module
(`dida/tui/overlays.py` per the seam plan), make the field set a parameter, and don't hard-code list-only
assumptions into it.** #36 will extend it, not rewrite it.

## Two documented traps on the list-write responses — both will bite you

**1. Two success shapes with no rule for which.** `POST /open/v1/project` and the update both document
**`200 → Task` and `201 → No Content`** (`openapi-dida365.md:247`, `:339`). `api/client.py:199–212`
(`_payload_object`) parses the body unconditionally, so **a 201 with an empty body raises
`MalformedResponseError` on a success.** Handle the no-body success case explicitly, and pin it with a
transport-seam test — this is exactly the kind of thing that only shows up against the real server.

**2. `sortOrder` may be silently reset by a rename.** Update-project documents `sortOrder` with "default 0"
(`:1237`) and **replace-vs-merge is undocumented**. Omitting it may reset the user's list order — the same
shape of bug as the unknown-field rule the spec already states, just for a field the doc gives a default for.
**Echo the current `sortOrder` back on rename.** (Same for any other field you don't intend to change.)

## Confirmed, so don't second-guess these

- **`groupId` appears only in Project *responses*** (`:1011`, `:1052`, `:1095`, `:2302`) — no request accepts
  it. So 「客户端做不到把清单放进项目组」 is correct, and you must **not** write a `groupId` field, nor pretend
  to offer it. Project-group headings are display-only in v2.
- **The delete-confirmation copy is verified.** `:1278–1302` says nothing about what happens to the tasks
  inside a deleted project, and there is **no trash / undelete / restore endpoint anywhere** (the only
  `DELETED` hit is a batch error code, `:567`). So the truthful wording is 「里面的任务会怎样，文档没写」 plus
  **no promise of recovery**. This is not hedging — it is the accurate statement, and your AC asks for exactly it.

## Two things you must inherit rather than rediscover

- **Your ticket is blocked on #41 for a reason: prune.** Before #41, a deleted list came back on the next
  refresh because the store only upserted. #41 landed prune. **Your AC 「删掉的清单不再出现在清单列表页（刷新之后也不回来）」
  depends on it** — verify it end-to-end after a refresh, don't just assert the local delete.
- **#41's list prune deliberately keeps tasks that have a pending change, which leaves orphan tasks whose list
  no longer exists**; `view.py` then falls back to `list_name = list_id`. Deleting a list is exactly the flow
  that produces them. Decide what your delete flow should show for that case and **say in your report what you
  decided** — the orchestrator flagged this to you rather than fixing it silently upstream.
  Also note #33 taught the store to synthesise the inbox row (`is_inbox`), and #41's prune exemption keys on
  that flag; don't break either.

## Empty/updated state must show up immediately

All three operations push immediately (that is the app's whole premise — `ADR-0002`), and a failure goes to
the retry queue rather than being lost. Your AC says so for all three. Assert the *optimistic* local effect
**and** the queued-change state on failure, at the transport seam, not just the happy path.

## Scope discipline

You own `dida/tui/overlays.py` + the list-CRUD additions to the list-index page, and the config/client plumbing
for project create/update/delete. Do **not** implement view CRUD (#36), task CRUD (#39/#40), or the detail page
(#43). Keep edits to `app.py` small — seven tickets run concurrently and the seam plan exists to keep them apart.
