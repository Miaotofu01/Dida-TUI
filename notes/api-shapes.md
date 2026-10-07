# HTTP request/response shapes — exact reference for request-shape tests

Source of record: `/home/tofu/dida-v2-worktrees/notes/openapi-dida365.md` (2497 lines, verified
byte-identical to the live published Dida365 Open API doc). **Every citation is
`openapi-dida365.md:<line>`.**

Secondary authority: issue #30 spec, section 「已实测的 API 事实」 (spec lines 320–325) and
「未经验证、实现时要留意的」 (spec lines 327–333). Where the doc and the spec disagree, both are
shown and labelled.

**"doc silent" is used literally**: the doc does not contain the statement. It never means "probably
false", and it never means "so do this instead". Where the repo already assumes something, the
assumption is named with its file:line so an implementer knows what they are pinning.

Two encodings are used throughout:

- **doc** = a statement that exists in the doc, with its line.
- **repo assumes** = current code/docstring behaviour, with its file:line.

---

## 0. Conventions that apply to every endpoint

| Thing | Value | Cite |
|---|---|---|
| Base host (examples only — doc never states a base URL in prose) | `api.dida365.com` | openapi-dida365.md:133, :168, :257 |
| Repo's base URL constant | `https://api.dida365.com` (`DEFAULT_BASE_URL`) | integration/src/dida/api/client.py:40 |
| Auth header, all endpoints | `Authorization: Bearer {{token}}` | openapi-dida365.md:78–81, :134 |
| Personal token (no OAuth needed) | avatar → Settings → Account → API Token | openapi-dida365.md:14–25 |
| Request body content type | `Content-Type: application/json` (shown on write examples) | openapi-dida365.md:258, :350, :1204 |
| Date format, documented | `"yyyy-MM-dd'T'HH:mm:ssZ"`, example `"2019-11-13T03:00:00+0000"` | openapi-dida365.md:226–227 |
| Date format, as the newer examples actually write it | `"2026-03-01T00:58:20.000+0000"` — **milliseconds present** | openapi-dida365.md:670–671, :687, :735–736 |
| Repo's date regex accepts both (optional `.mmm`) and re-emits the string verbatim | `_API_DATE_RE` | integration/src/dida/api/guards.py:26–29 |
| Field casing | camelCase everywhere: `projectId`, `taskId`, `fromProjectId`, `isAllDay`, `dueDate`, `startDate`, `repeatFlag`, `sortOrder`, `completedTime`. **No snake_case appears anywhere in the doc.** | openapi-dida365.md:186–191, :226–227 |
| Success codes listed per endpoint | 200 / 201, plus 401 / 403 / 404 | openapi-dida365.md:243–250 (representative) |
| Error **body** shape | **doc silent — no error payload is documented for any endpoint.** Every failure row is `No Content` | openapi-dida365.md:247–250, :414–418, :481–485 |
| 429 / rate limit / quota | **doc silent**; the string `429` does not occur in the file; no `RateLimit`/`Retry-After`/`X-RateLimit` header is mentioned. Only quota-adjacent token is the batch per-task code `EXCEED_QUOTA` | openapi-dida365.md:567 (only occurrence) |
| Trash / undelete / restore / recovery | **doc silent — there is no such endpoint anywhere.** `grep -ni 'trash\|recover\|undelete\|restore\|recall'` matches exactly one line, and it is a batch error code, not an endpoint | openapi-dida365.md:567 |
| `PATCH` | **does not exist in the doc at all**; updates are `POST /{id}` | full endpoint index, openapi-dida365.md:85–2227 |

### Complete documented endpoint index (for "does an endpoint exist?" questions)

`POST /oauth/revoke` (:85) · `POST /open/v1/preference` (:118) · `GET /open/v1/project/{projectId}/task/{taskId}` (:146) ·
`POST /open/v1/task` (:215) · `POST /open/v1/task/{taskId}` (:305) · `POST /open/v1/project/{projectId}/task/{taskId}/complete` (:398) ·
`POST /open/v1/task/completeTasks` (:430) · `DELETE /open/v1/project/{projectId}/task/{taskId}` (:468) · `POST /open/v1/task/move` (:499) ·
`POST /open/v1/task/batch` (:553) · `POST /open/v1/task/assign` (:594) · `POST /open/v1/task/unassign` (:615) ·
`POST /open/v1/task/completed` (:635) · `POST /open/v1/task/filter` (:697) · `POST /open/v1/task/undone` (:788) ·
`POST /open/v1/task/search` (:825) · `GET /open/v1/project/{projectId}/task/{taskId}/comments` (:867) ·
`POST /open/v1/project/{projectId}/task/{taskId}/comment` (:906) · `DELETE /open/v1/project/{projectId}/task/{taskId}/comment/{id}` (:950) ·
`GET /open/v1/project` (:979) · `GET /open/v1/project/{projectId}` (:1020) · `GET /open/v1/project/{projectId}/data` (:1062) ·
`GET /open/v1/project/{projectId}/members` (:1140) · `POST /open/v1/project` (:1178) · `POST /open/v1/project/{projectId}` (:1228) ·
`DELETE /open/v1/project/{projectId}` (:1280) · `GET /open/v1/project/group` (:1308) · `POST /open/v1/project/group` (:1342) ·
`POST /open/v1/project/group/{projectGroupId}` (:1383) · `DELETE /open/v1/project/group/{projectGroupId}` (:1425) ·
`GET /open/v1/project/{projectId}/column` (:1453) · `POST /open/v1/project/{projectId}/column` (:1491) ·
`POST /open/v1/project/{projectId}/column/{columnId}` (:1534) · `GET /open/v1/tag` (:1580) · `POST /open/v1/tag` (:1614) ·
`GET /open/v1/focus/{focusId}` (:1660) · `GET /open/v1/focus` (:1709) · `POST /open/v1/focus` (:1753) · `DELETE /open/v1/focus/{focusId}` (:1808) ·
`GET /open/v1/countdown` (:1845) · `GET /open/v1/habit/{habitId}` (:1883) · `GET /open/v1/habit/sections` (:1939) ·
`GET /open/v1/habit` (:1973) · `POST /open/v1/habit` (:2014) · `POST /open/v1/habit/{habitId}` (:2088) ·
`POST /open/v1/habit/{habitId}/checkin` (:2151) · `GET /open/v1/habit/checkins` (:2206)

Note: **no endpoint anywhere is `GET /open/v1/task/{taskId}`** and **no endpoint is `GET /open/v1/task/{projectId}/{taskId}`**.
The read-one path is `GET /open/v1/project/{projectId}/task/{taskId}` (Section A7).

---

## Section A — Task writes

### A1. Create — `POST /open/v1/task`

Authoritative: openapi-dida365.md:213–299 (body table :219–241, responses :243–250, example :253–299).

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `title` | **required** | string | :221 |
| `projectId` | **required** | string | :222 |
| `content` | optional | string | :223 |
| `desc` | optional | string | :224 |
| `isAllDay` | optional | boolean | :225 |
| `startDate` | optional | date, `"yyyy-MM-dd'T'HH:mm:ssZ"` | :226 |
| `dueDate` | optional | date, same format | :227 |
| `timeZone` | optional | String | :228 |
| `reminders` | optional | list | :229 |
| `tags` | optional | list | :230 |
| `repeatFlag` | optional | string | :231 |
| `priority` | optional | integer, **"default is 0"** | :232 |
| `sortOrder` | optional | integer | :233 |
| `items` | optional | list of subtasks | :234 |
| `items.title` / `items.startDate` / `items.isAllDay` / `items.sortOrder` / `items.timeZone` / `items.status` / `items.completedTime` | optional | string / date / boolean / integer / string / integer / date | :235–241 |

**Is `projectId` accepted here? YES — it is required** (openapi-dida365.md:222), and the request
example sends a real project id, not the inbox alias (:263). ⇒ Ticket #39's premise holds *for the
client but not for the API*: `create_task(body)` is a passthrough that already forwards whatever
`projectId` the caller supplies (integration/src/dida/api/client.py:142–148); the hard-wiring lives
one layer up at integration/src/dida/sync/engine.py:914 (`project_id=INBOX_ID`), with
`INBOX_ID = "inbox"` (src/dida/sync/view.py:38).

- **Response:** `200 OK` → [Task] (:246). Also listed: `201 Created` → **No Content** (:247). The doc
  does not say when 200 vs 201 is returned. `401/403/404` → No Content (:248–250).
  - **Landmine (doc-ambiguous, real crash risk):** the repo parses the create response as a JSON
    object unconditionally — `_payload_object(...)` (client.py:148 → :199–212) raises
    `MalformedResponseError` on a body that is not JSON. If the server ever answers `201 No Content`,
    the repo turns a success into a structured error. Doc silent on which code you get.
- **Not accepted on create (absent from the table):** `id`, `status`, `completedTime`, `kind`,
  `parentId`, `assigneeUsername`, `repeatFrom`, `focusSummaries`, `etag`. Doc silent on what happens
  if you send them anyway.
- **`inbox` as `projectId` on create:** **doc silent.** The literal `inbox` is documented as a
  project id only for `GET /project/{projectId}/data` (:1068), `POST /task/undone` (:796) and
  `POST /task/completeTasks` (:433, :438).
- **Repo guard:** create runs `guard_writable` first, so `status` is rejected locally with
  `FieldIgnoredError` (integration/src/dida/api/guards.py:157, `NON_WRITABLE_FIELDS` at :49).

### A2. Update — `POST /open/v1/task/{taskId}`

Authoritative: openapi-dida365.md:302–392 (path :305, body table :309–333, responses :335–342).

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `taskId` | **required (path)** | string | :311 |
| `id` | **required (body)** | string | :312 |
| `projectId` | **required (body)** | string | :313 |
| `title` | optional | string | :314 |
| `content` | optional | string | :315 |
| `desc` | optional | string | :316 |
| `isAllDay` | optional | boolean | :317 |
| `startDate` | optional | date | :318 |
| `dueDate` | optional | date | :319 |
| `timeZone` | optional | String | :320 |
| `reminders` | optional | list | :321 |
| `tags` | optional | list | :322 |
| `repeatFlag` | optional | string | :323 |
| `priority` | optional | integer, **"default is normal"** — note the wording differs from create's "default is 0" | :324 vs :232 |
| `sortOrder` | optional | integer | :325 |
| `items` (+ 7 `items.*` subfields, incl. `items.status`) | optional | as create | :326–333 |

| Question | Answer |
|---|---|
| Which fields are writable? | Exactly the table above (:309–333). `status`, `completedTime`, `kind`, `parentId`, `assigneeUsername`, `repeatFrom`, `etag` are **not in it**. |
| Which fields are ignored? | **doc silent.** The doc never names an ignored field on this endpoint. |
| Omitted fields: full replace or merge? | **doc silent.** No statement about omitted-field semantics exists. Repo already records this as open: "文档没说省略的字段是被保留还是被清空（ticket #22 的实测问题）" (client.py:164–165). Repo's strategy is to always send snapshot ⊕ changes so it is correct under both semantics (client.py:150–177, `merge_snapshot` guards.py:172). |
| Can it un-complete a task (`status: 0`)? | **doc silent** — `status` is not in the body table. Spec line 330 says this is unverified and must not be treated as equivalent to the batch path. Repo blocks `status` here entirely (guards.py:49, :157). |
| Response | `200 OK` → [Task] (:338); `201 Created` → No Content (:339). Same 200/201 ambiguity and same parse risk as A1 (client.py:175–177). |

**Landmine for ticket #45 (field editing):** editing any field of an **already-completed** task goes
through this endpoint, which cannot carry `status` (§A2). Whether the server preserves `status: 2`
is **doc silent**. If the server replaces, editing a completed task's title silently un-completes it.
No doc sentence settles this; it needs a live probe (out of scope here).

### A3. Batch — `POST /open/v1/task/batch`

Authoritative: openapi-dida365.md:551–590 (intro :556, body table :559–562, responses :565–567, example :571–590).

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `add` | optional | `[Task](openapi.md#task)` array, **up to 50** | :561, cap at :556 |
| `update` | optional | `[Task](openapi.md#task)` array, **up to 50** | :562, cap at :556 |
| `delete` | **NOT DOCUMENTED — doc silent.** There are only two arrays in the table (:559–562). | — | — |

| Question | Answer |
|---|---|
| Is there a `delete` array? | **doc silent.** No `delete` key appears in the section (:551–590). The repo has no batch-delete path either. |
| **Does `status` appear anywhere in the doc for this endpoint?** | **NO — confirmed.** The section spans :551–590; the word `status` does not occur in it. Global `status` occurrences are :188, :201, :240, :285, :290, :332, :377, :382 (examples), :688/:700/:711/:739/:758/:776 (`task/filter` + its example), :828/:836/:859 (`task/search`), :1115/:1120 (`project/data` example), :2258 (ChecklistItem), :2286 (Task). **None is in :551–590.** |
| The one structural hint that exists | Both arrays are typed as `[Task]` arrays (:561–562) and the `Task` definition *does* contain `status` (:2286). That is a type reference, **not** a statement that the server honours `status` on write. The example's update item carries only `{id, projectId, title}` (:581). Read together: the doc is silent, with a shape that does not forbid it. |
| Doc confirmation of spec's finding | Spec :322 ("取消完成可行 … 文档对此零提及") — **CONFIRMED as "doc silent"**; the doc never mentions it. |
| Response | `200 OK` → object with `id2etag` (map taskId→etag) and `id2error` (map taskId→error code) (:567); example :585–590. `401/403` → No Content (:568–569). |
| Documented error codes in `id2error` | `EXISTED`, `DELETED`, `NOT_EXISTED`, `PROJECT_MOVE_ERROR`, `EXCEED_QUOTA`, `UNKNOWN`, `NO_PROJECT_PERMISSION`, `NO_TEAM_PERMISSION` (:567). |
| **Landmine** | Per-task failures arrive **inside a `200 OK`**. Repo classifies failures by status code only (client.py:290–309), so it would report a batch where every task failed as a full success. Only `EXCEED_QUOTA` is quota-related; there is no rate-limit semantics documented for it. |
| Merge vs replace inside `update` | **doc silent**; spec :229 records it as experimentally merge ("只发 `{id, projectId, status}`，标题/描述/备注/优先级全部保留"). Repo's `guard_writable` would reject `status` on the batch path if it were reused there — the batch writer must **not** run through `guard_writable` (guards.py:157). |

### A4. Move — `POST /open/v1/task/move`

Authoritative: openapi-dida365.md:497–548 (body :503–510, responses :513–520, example :523–548).

**Request body: a JSON *array* of move operations, not an object** (:504 "A JSON array containing task
move operations"; example :530–536).

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `fromProjectId` | **required** | string | :508 |
| `toProjectId` | **required** | string | :509 |
| `taskId` | **required** | string | :510 |

| Question | Answer |
|---|---|
| Response | `200 OK` → array of `{ "id": <taskId>, "etag": <new etag> }` (:516 "Returns an array of move results, including the task ID and its new etag)", example :542–547). `201` → No Content (:517); `401/403/404` → No Content (:518–520). |
| inbox → list / list → inbox | **doc silent.** `inbox` is never mentioned in this section; the doc's `inbox`-as-project-id statements are for `/data` (:1068), `/task/undone` (:796) and `/completeTasks` (:433). Spec :230 claims both directions work ("搬运在收集箱与真实清单之间双向可用") — that is experimental, not documented. |
| What it does to the task's other fields | **doc silent.** No field-preservation statement exists in :497–548. |
| Does the moved task keep its id? | Yes, by shape: the response echoes the same task id with a new etag (:544–545). Doc does not say the id is stable in prose. |
| Repo status | **not implemented.** No `move` method exists in client.py (methods at :59–187) — ticket #45 must add it. |

### A5. Complete — `POST /open/v1/project/{projectId}/task/{taskId}/complete`

Authoritative: openapi-dida365.md:395–426.

| Item | Value | Doc line |
|---|---|---|
| Method + path | `POST /open/v1/project/{projectId}/task/{taskId}/complete` | :398 |
| Path params | `projectId` required, `taskId` required | :406–407 |
| Request body | **none documented** (no body table) | :402–407 |
| Response | `200 OK` No Content; also `201 Created` No Content; `401/403/404` No Content | :414–418 |
| Example | header-only request, no body | :422–426 |

Repo: matches exactly — no body, response ignored (client.py:179–183). Spec :228 requires this
endpoint for completion, and it is the only completion path in the doc.

**Related (not requested, but it is the only "bulk complete"):** `POST /open/v1/task/completeTasks`
(:430) — body `projectId` (optional; **defaults to the current user's inbox when omitted or empty**,
:433/:438) + `taskIds` required, **only the first 50 processed** (:439); response = array of
successfully completed task ids (:444), example :462–464. This is the one place the doc explicitly
documents inbox-as-default on a write path.

### A6. Delete — `DELETE /open/v1/project/{projectId}/task/{taskId}`

Authoritative: openapi-dida365.md:466–495.

| Item | Value | Doc line |
|---|---|---|
| Method + path | `DELETE /open/v1/project/{projectId}/task/{taskId}` | :468 |
| Path params | `projectId` required, `taskId` required | :474–475 |
| Request body | none | :471–475 |
| Response | `200 OK` No Content; `201 Created` No Content; `401/403/404` No Content | :479–485 |

**Recovery / undelete / trash — searched the whole file: none exists.** The only line in the entire
document that mentions deletion as a state is the batch per-task error code `DELETED`
(openapi-dida365.md:567), i.e. "this task id is already gone" — which is evidence that a deleted task
id is unusable, and no evidence at all of a restore path.

⇒ **The delete-confirmation copy must not promise recovery.** Safe, doc-grounded wording is
"文档没有任何回收站 / 恢复接口". Ticket #42's acceptance criteria and spec :329 both require exactly this.

Repo: matches (client.py:185–187, response body deliberately not parsed).

### A7. Read one task

| Item | Value | Doc line |
|---|---|---|
| Method + path | `GET /open/v1/project/{projectId}/task/{taskId}` | :146 |
| Path params | `projectId` required, `taskId` required | :152–153 |
| Response | `200 OK` → [Task] (full example :174–209); `401/403/404` No Content | :158–161 |
| Fields visible in the response example | `id`, `isAllDay`, `projectId`, `title`, `content`, `desc`, `timeZone`, `repeatFlag`, `startDate`, `dueDate`, `reminders`, `tags`, `priority`, `status`, `completedTime`, `sortOrder`, `parentId`, `focusSummaries`, `items[]` | :175–208 |
| Fields in the example that are **not** in the `Task` definition table | `etag` is absent from both; `kind` is absent from this example but present in the `Task` definition (:2289) and in the update-response example (:390) | — |

**Read a task by id alone: NO SUCH ENDPOINT IS DOCUMENTED.** The full index (§0) contains no
`/open/v1/task/{taskId}` read path. Closest documented options, none of which is id-alone:

1. `POST /open/v1/task/undone` (:788) accepts `taskIds` (:797) — but `startDate` **and** `endDate` are
   required (:798–799) and the range is capped at **14 days** (:791). A task outside that window
   cannot be reached by id.
2. `POST /open/v1/task/search` (:825) — keyword search, no id parameter.
3. `POST /open/v1/task/filter` (:697) — no id parameter.

Repo: `get_task(project_id, task_id)` — same path, requires the real project id
(client.py:133–140). It re-reads before writing (engine.py uses it as `TaskReader`, src/dida/sync/engine.py:324).

### A8. Filter — `POST /open/v1/task/filter`

Authoritative: openapi-dida365.md:695–784 (intro :700, params :703–711, responses :714–721, example :724–784).

| Field | Required? | Type | Meaning as documented | Doc line |
|---|---|---|---|---|
| `projectIds` | optional (not marked required) | list | "Filters tasks belonging to the specified project ID" | :705 |
| `startDate` | optional | date | "Filters tasks where the task's **startDate** ≥ startDate" | :706 |
| `endDate` | optional | date | "Filters tasks where the task's **startDate** ≤ endDate" | :707 |
| `priority` | optional | list | "Valid Values: None(0), Low(1), Medium(3), High(5)" | :708 |
| `tag` | optional | list | "contain **all** of the specified tags" — **singular `tag`**, not `tags` | :709 |
| `kind` | optional | list | "Filters tasks by task kind" (values undocumented here; Task.kind is TEXT/NOTE/CHECKLIST at :2289) | :710 |
| `status` | optional | list | "current status codes (e.g., [0] for Open, [2] for Completed)" | :711 |

| Question | Doc answer |
|---|---|
| Default / max page size | "Retrieves **at most 200 tasks**" (:700). **No page-size, offset or cursor parameter exists** (:703–711) ⇒ no pagination. |
| `projectIds` omitted ⇒ "all projects plus inbox"? | **doc silent.** The section says only "Filters tasks belonging to the specified project ID" (:705) and never describes omission. Spec :325 claims omission returns all projects + inbox (experimental). **Corroborating analogue, not proof:** `POST /open/v1/task/undone` documents omission explicitly — "Omit to search all accessible projects; use `inbox` for the inbox project" (:796) — so omission-means-all is plausible for siblings, but `/task/filter` itself does not state it. |
| Does the date range filter `startDate` or `dueDate`? | **`startDate`** — stated twice, verbatim, at :706 and :707. `dueDate` is not a filter parameter. Spec :240 ("那个接口的日期区间过滤的是 `startDate`…根本表达不了「今天到期」") — **CONFIRMED**. |
| Does a `dueDate` filter exist at all? | Not here. The only documented due-based filters are `dueFrom` / `dueTo` in `POST /open/v1/task/search` (:837–838). Note the different names. |
| Silent truncation risk | Response is a bare `<Task> array` (:717) with no total and no next-page token; a 200-item answer is indistinguishable from a truncated one. Spec :325 calls this out as a silent truncation risk. **Doc gives no signal to detect it** — same structural problem as the project index (§B13). |

**Related read endpoints with their own shapes** (useful when a ticket needs a date range):

| Endpoint | Body fields | Filters on | Cap / limits | Cite |
|---|---|---|---|---|
| `POST /open/v1/task/completed` | `projectIds`, `startDate`, `endDate` (all optional) | `completedTime` ≥ startDate and ≤ endDate (:645–646) | "at most 200" (:637) | :633–693 |
| `POST /open/v1/task/undone` | `projectIds`, `taskIds`, **`startDate` required**, **`endDate` required** | date range "up to 14 days"; invalid range ⇒ empty array (:791) | 14-day range | :786–821 |
| `POST /open/v1/task/search` | `keywords`, `projectIds`, `tags` (plural here), `status`, `dueFrom`, `dueTo` | due window via `dueFrom`/`dueTo` | blank `keywords` ⇒ empty array (:828) | :823–863 |

---

## Section B — Project (list) writes

### B9. Create project — `POST /open/v1/project`

Authoritative: openapi-dida365.md:1175–1224.

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `name` | **required** | string | :1184 |
| `color` | optional | string, e.g. `"#F18181"` | :1185 |
| `sortOrder` | optional | integer (int64) | :1186 |
| `viewMode` | optional | string, `"list"`, `"kanban"`, `"timeline"` | :1187 |
| `kind` | optional | string, `"TASK"`, `"NOTE"` | :1188 |

- **`groupId` / any project-group field: NOT ACCEPTED — doc confirms ticket #42.** `groupId` appears in
  the file exactly four times (:1011, :1052, :1095 in Project *responses*, and :2302 in the `Project`
  definition). It appears in **no request body table anywhere**. ⇒ the client genuinely cannot set or
  change a project's group. Ticket #42's "接口不接受" — **AGREE**.
- Also not accepted: `closed`, `permission` (Project definition fields at :2301, :2304, absent from
  this body table).
- Responses: `200 OK` → [Project] (:1193); `201 Created` → No Content (:1194). Same 200/201 ambiguity.
- Example response omits `closed`, `groupId`, `permission` even though the `Project` definition has
  them (:1215–1224 vs :2294–2305) — doc inconsistency; treat absent Project fields as "unknown", not "false".
- Note the doc's own example lowercases the enum: `"kind": "task"` in the request (:1210) but
  `"kind": "TASK"` in responses (:1222, :1274). The definition says `"TASK" or "NOTE"` (:2305).

### B10. Update project — `POST /open/v1/project/{projectId}`

The verb is **POST**, not PATCH/PUT: openapi-dida365.md:1228.

| Field | Required? | Type | Doc line |
|---|---|---|---|
| `projectId` | **required (path)** | string | :1234 |
| `name` | optional | string | :1235 |
| `color` | optional | string | :1236 |
| `sortOrder` | optional | integer (int64), **"default 0"** | :1237 |
| `viewMode` | optional | string | :1238 |
| `kind` | optional | string | :1239 |

- Responses: `200 OK` → [Project] (:1244); `201` No Content (:1245).
- **No `groupId`** anywhere ⇒ cannot set a group (ticket #42 confirmed), and no `closed`/`permission`.
- **Doc-ambiguous landmine for ticket #42's rename form:** `sortOrder` is described as "sort order
  value, **default 0**" (:1237). Read literally, omitting `sortOrder` while renaming a list could
  reset its order to 0. **doc silent** on whether update is replace or merge (same silence as §A2).
  Safe implementation: echo the list's current `sortOrder` back in the update body (same
  snapshot-merge strategy client.py:150–177 already uses for tasks). Ticket #42's form only edits
  name + colour, so this is the exact scenario that would trip it.

### B11. Delete project — `DELETE /open/v1/project/{projectId}`

Authoritative: openapi-dida365.md:1278–1302.

| Item | Value | Doc line |
|---|---|---|
| Method + path | `DELETE /open/v1/project/{projectId}` | :1280 |
| Path param | `projectId` required | :1286 |
| Response | `200 OK` No Content; `401/403/404` No Content | :1291–1294 |
| Example | header-only DELETE, no body | :1298–1302 |

**What happens to the tasks inside the deleted project: THE DOC SAYS NOTHING.** The whole section is
:1278–1302 and consists of the path, one path-parameter row, the response table and a request
example. There is **no** sentence about cascading deletes, orphaning, moving to inbox, or
recoverability — and the repo-wide search for trash/restore/undelete (§0) finds nothing either.

⇒ Ticket #42's required confirmation copy "里面的任务会怎样，文档没写" is **VERIFIED CORRECT**, and
"不承诺任何恢复手段" is **VERIFIED CORRECT** (§A6). Spec :329 states the same. An implementer must
not upgrade this to "tasks are deleted with the list" or "tasks move to inbox" — neither is written.

### B12. Read one project — `GET /open/v1/project/{projectId}`

Authoritative: openapi-dida365.md:1018–1056.

| Item | Value | Doc line |
|---|---|---|
| Method + path | `GET /open/v1/project/{projectId}` | :1020 |
| Path param row | named `**project**` *required* in the table, while the path template says `{projectId}` — **doc inconsistency**; the example uses `{{projectId}}` | :1026 vs :1020, :1040 |
| Response | `200 OK` → [Project]; `401/403/404` No Content | :1031–1034 |
| Example response | `id`, `name`, `color`, `closed`, `groupId`, `viewMode`, `kind` — **no `permission`, no `sortOrder`** | :1046–1055 |

### B13. List index — `GET /open/v1/project`

Authoritative: openapi-dida365.md:977–1016.

| Query param | Type | Documented behaviour | Doc line |
|---|---|---|---|
| `offset` | integer | "Zero-based result offset. When either pagination parameter is provided, omitted or negative values default to `0`." | :985 |
| `limit` | integer | "Maximum number of projects to return. When either pagination parameter is provided, omitted or negative values default to `200`." | :986 |

| Question | Answer |
|---|---|
| **Bare array or paginated envelope?** | **Bare array.** `200 OK` schema is `< [Project] > array` (:991) and the example is a top-level `[ {...} ]` (:1005–1016). There is **no** wrapper object, **no** `total`, **no** `hasMore`, **no** next-cursor. |
| Documented default | 200, **but conditionally stated**: it applies "When either pagination parameter is provided" (:986). With **no** query parameters at all the doc does not state the page size. Spec fact #3 ("limit 默认 200") — **AGREE with this nuance**; the experimental finding is what tells you the no-parameter case also yields 200. |
| Documented maximum | **doc silent.** The doc calls `limit` "Maximum number of projects to return" but names no upper bound and no error for exceeding one (:985–986). |
| Can the client tell it got everything? | **Not from the response.** Because the body is a bare array with no total (§:991), the only inferable signal is `len(result) < limit` ⇒ complete; `len(result) == limit` ⇒ *possibly* truncated. The reliable check is one extra request at `offset += limit` returning `[]`. Any "did I get everything?" flag must be derived by the client, not read from the payload. (Contrast: the engine already does this for the completed stream — `CompletedReport.truncated`, src/dida/sync/engine.py:1129.) |
| Repo status | `list_projects(*, offset=None, limit=None)` exists and only writes the params when the caller passes them, deliberately (client.py:59–72). Ticket #41's phrasing "API 客户端本身会带上 offset/limit，缺的是刷新路径从不翻页" — **AGREE**. |

---

## Section C — Reads the refresh uses

### C14. `GET /open/v1/project/{projectId}/data`

Authoritative: openapi-dida365.md:1059–1136; response schema `ProjectData` at :2327–2332.

| Path param | Note | Doc line |
|---|---|---|
| `projectId` *required* | "Project identifier, **`"inbox"`**" — this is where the literal inbox alias is documented | :1068 |

**Response shape (ProjectData, :2327–2332):**

| Key | Schema | Doc wording | Doc line |
|---|---|---|---|
| `project` | [Project] | "Project info" | :2330 |
| `tasks` | `<Task> array` | "**Undone** tasks under project" | :2331 |
| `columns` | `<Column> array` | "Columns under project" | :2332 |

- Response code table: `200 OK` → ProjectData; `401/403/404` No Content (:1074–1077).
- **`tasks` is documented as undone-only** (:2331) ⇒ **completed tasks are not reachable through this
  call**; they must come from the completed stream (§C15). This is the doc basis for the two-source
  refresh in spec :142.
- **Field-by-field of the `data` example's task object** (:1099–1128): `id`, `isAllDay`, `projectId`,
  `title`, `content`, `desc`, `timeZone`, `repeatFlag`, `startDate`, `dueDate`, `reminders`,
  `priority`, `status`, `completedTime`, `sortOrder`, `items[]` (with `id`, `status`, `title`,
  `sortOrder`, `startDate`, `isAllDay`, `timeZone`, `completedTime`).
  - **Absent from this example but present in the `Task` definition (:2266–2291):** `tags`, `kind`,
    `parentId`, `assigneeUsername`, `repeatFrom`, `focusSummaries`. **doc silent** on whether they are
    omitted from the payload or merely omitted from the example. ⇒ implementers must treat these as
    *may be missing*, never default them to `[]`/`""` and write that back (that is the silent-erasure
    failure mode of §A2 and the repo's `merge_snapshot`, guards.py:172).
  - `etag` is **not** a `Task` field at all (:2266–2291) — it appears only in move/batch responses and
    in completed/filter examples (:545, :689, :762, :780).
- Repo: `get_project_data` matches the path and treats missing/`null` `project`/`tasks` as "empty list"
  rather than an error (client.py:110–131).
- Not ours but adjacent: `GET /open/v1/project/{projectId}/column` (:1453) lists the same `columns`.

### C15. The completed-task query — `POST /open/v1/task/completed`

Authoritative: openapi-dida365.md:633–693.

| Field | Required? | Type | Documented filter | Doc line |
|---|---|---|---|---|
| `projectIds` | optional | list | "List of project identifier" | :644 |
| `startDate` | optional | date | "The start of the time range (inclusive). Filters tasks where **completedTime** ≥ startDate" | :645 |
| `endDate` | optional | date | "The end of the time range (inclusive). Filters tasks where **completedTime** ≤ endDate" | :646 |
| `status` | **DOES NOT EXIST** | — | **doc silent — the parameter is not in the table.** The section's four rows are `projectIds`, `startDate`, `endDate` only (:642–646). | — |

| Question | Answer |
|---|---|
| What do the date parameters filter on? | **`completedTime`** — stated explicitly for both bounds (:645, :646). Not `startDate`, not `dueDate`. |
| Is there a `status` parameter? | **No.** Doc silent (:642–646). |
| Cap | "Retrieves at most **200** tasks" (:637); reiterates "at most 200" for `task/filter` (:700). No pagination parameter ⇒ no way to continue past 200 in this window. Repo mirrors the cap as `COMPLETED_PAGE_LIMIT = 200` (src/dida/sync/engine.py:135). |
| Response | `<Task> array` (:652); example :675–693. |
| Fields in the response example | `id`, `projectId`, `sortOrder`, `title`, `content`, `timeZone`, `isAllDay`, `priority`, **`completedTime`**, **`status`: 2**, `etag`, `kind` (:678–690). |
| Omission semantics for `projectIds` | **doc silent** here too; the "All fields are optional, but at least one filter is recommended" note (:640) is the only guidance, and it does not say what omission matches. |

**⇒ Ticket #37's requirement ("拉取要同时按完成时间窗口与完成状态过滤") cannot be satisfied by a
server-side parameter on this endpoint.** The doc offers exactly two honest options:

1. Keep `POST /open/v1/task/completed` and **post-filter locally on the returned `status` field** —
   the field is present in the documented response (:688), so this is doc-supported and cheap.
2. Use `POST /open/v1/task/filter` for the `status` parameter (:711) — but that endpoint's date range
   filters **`startDate`** (:706–707), not `completedTime`, and it also caps at 200 (:700), so it
   cannot express "completed in the last 7 days".

Option 1 is the only one consistent with both tickets. Anything asserting a `status` key inside the
`/task/completed` request body would be pinning **invented API surface** — the doc's example body is
`{projectIds, startDate, endDate}` only (:666–672).

Spec :144's claim "取消完成之后完成时间戳并不会被清掉" — **doc silent** (nothing in the doc discusses
what un-completion does to `completedTime`; consistent with §A2/§A3 being silent on un-completion
entirely).

---

## Section D — Reference data and dialects

### D16. Tags — `GET /open/v1/tag`, and the create endpoint ticket #45 assumes does not exist

Authoritative: openapi-dida365.md:1576–1654; `OpenTag` definition :2456–2464.

| Endpoint | Method + path | Request | Response | Doc line |
|---|---|---|---|---|
| List tags | `GET /open/v1/tag` | none | `200 OK` → `<OpenTag> array`; `401/403/404` No Content | :1580, :1586–1589 |
| **Create tag** | **`POST /open/v1/tag`** | **`name` *required*** (max 64 chars, lowercase, trimmed) and **`label` *required*** (max 64 chars, must match `name` when lowercased) | `200 OK` → OpenTag; `201` No Content | :1614, :1620–1621, :1626 |
| Update tag | **none — doc silent, no such endpoint** | — | — | — |
| Delete tag | **none — doc silent, no such endpoint** | — | — | — |

`OpenTag` fields (:2456–2464): `name` (string), `label` (string), `sortOrder` (int64), `color`
(string), `parent` (string, parent tag name), `type` (int32; **Personal: 1, Team: 2** — :2464).

**⇒ The task's premise is half wrong, and this is the single highest-value correction in this file:**

- **"no tag delete endpoint" — CORRECT.** The Tag section is :1576–1654 and contains only the two
  endpoints above. Ticket #45's "API 也没有删除端点" is **AGREE**.
- **"no tag create endpoint" — CONFLICT.** `POST /open/v1/tag` is documented at :1612–1654.
  Ticket #45's acceptance criterion "明确告知不能在客户端新建标签，要新标签得回官方客户端" would,
  if implemented as "the API cannot do this", **state something false to the user**. The spec's
  Out-of-Scope line (:306) is careful — it only claims the *delete* endpoint is missing — but the
  ticket's copy is not.

  Two honest resolutions for the implementer, neither of which is "invent a fact":
  1. Keep tags create-free as a **product scope** decision and word the copy as "这个客户端还不能新建
     标签" rather than "API 没有这个能力"; or
  2. Use the documented `POST /open/v1/tag` and lift the limitation. `name` and `label` are both
     required and must match when lowercased (:1620–1621) — that is a real, testable request shape.
     There is still **no delete and no rename**, so tag removal stays impossible either way.
- Repo: `list_tags` exists and is documented as unused-by-production in the brief (client.py:75–78).
  No create/delete tag method exists.

### D17. `Task` object — field by field

Definition table: openapi-dida365.md:2266–2291. "Writable?" is judged **only** against the create
body table (:219–241) and the update body table (:309–333); a field absent from both is marked "not in
any body table" rather than "read-only", because the doc never uses the word read-only.

| Field | Type | Meaning (doc) | In create body? | In update body? | Doc line |
|---|---|---|---|---|---|
| `id` | string | Task identifier | no | **`id` required** | :2270, :312 |
| `projectId` | string | Task project id | **required** | **required** | :2271, :222, :313 |
| `title` | string | Task title | **required** | optional | :2272, :221, :314 |
| `isAllDay` | boolean | All day | optional | optional | :2273, :225, :317 |
| `completedTime` | string (date-time), `"yyyy-MM-dd'T'HH:mm:ssZ"` | Task completed time | no | no | :2274 |
| `content` | string | Task content — **this is the client's 「描述」** (spec :250) | optional | optional | :2275, :223, :315 |
| `desc` | string | "Task description of checklist" — **this is the client's 「备注」** (spec :250) | optional | optional | :2276, :224, :316 |
| `dueDate` | string (date-time) | Task due date time | optional | optional | :2277, :227, :319 |
| `items` | `<ChecklistItem> array` | Subtasks of Task | optional | optional | :2278, :234, :326 |
| `priority` | integer (int32) | **None: 0, Low: 1, Medium: 3, High: 5** | optional, "default is 0" (:232) | optional, "default is normal" (:324) | :2279 |
| `reminders` | `<string> array` | e.g. `["TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S"]` | optional | optional | :2280, :229, :321 |
| `tags` | `<string> array` | e.g. `["work","urgent"]` | optional | optional | :2281, :230, :322 |
| `repeatFlag` | string | Recurrence rule, e.g. `"RRULE:FREQ=DAILY;INTERVAL=1"` | optional | optional | :2282, :231, :323 |
| `repeatFrom` | string enum `0`/`1`/`2` | Recurrence calculation mode; 0 = from original due date, 1 = from completion date, 2 = default calendar recurrence in the task time zone; "When omitted or empty, the server usually uses `2`" | **no** | **no** | :2283 |
| `sortOrder` | integer (int64) | Task sort order | optional | optional | :2284, :233, :325 |
| `startDate` | string (date-time) | Start date time | optional | optional | :2285, :226, :318 |
| `status` | integer (int32) | **Abandoned: -1, Normal: 0, Completed: 2** | **no** | **no** | :2286 |
| `assigneeUsername` | string | Username of the project member assigned | no (use `/task/assign` :594, `/task/unassign` :615) | no | :2287 |
| `timeZone` | string | e.g. `"America/Los_Angeles"` | optional | optional | :2288, :228, :320 |
| `kind` | string | `"TEXT"`, `"NOTE"`, `"CHECKLIST"` | **no** | **no** | :2289 |
| `parentId` | string | "Parent task identifier. **Set to empty string `""` to remove parent-child relationship**" — the doc never says which endpoint accepts it | **no** | **no** | :2290 |
| `focusSummaries` | `<OpenFocusSummary> array` | Focus summaries (`pomoCount`, `estimatedPomo`, `estimatedDuration`, `pomoDuration`, `stopwatchDuration` :2317–2324) | no | no | :2291 |
| `etag` | — | **NOT a `Task` field.** It exists only in move/batch responses (:545, :587) and in completed/filter examples (:689, :762, :780) | — | — | — |

**`ChecklistItem` (subtask) — note the different status encoding** (:2252–2263):

| Field | Type | Doc line |
|---|---|---|
| `id` | string | :2256 |
| `title` | string | :2257 |
| `status` | integer (int32) — **Normal: 0, Completed: 1** | :2258 |
| `completedTime` | string (date-time) | :2259 |
| `isAllDay` | boolean | :2260 |
| `sortOrder` | integer (int64) | :2261 |
| `startDate` | string (date-time) | :2262 |
| `timeZone` | string | :2263 |

**Landmine: subtask `status: 1` means completed, while task `status: 2` means completed** (:2258 vs
:2286). Any code that reuses the task's status predicate on a subtask will read every completed
subtask as not-completed. Also: subtasks have **no `dueDate`** (:2252–2263) — only `startDate`.
`items.status` **is** writable on create and update (:240, :332) even though the parent's `status` is
not — a fact ticket #30's "子任务只读" scope decision overrides, but the API does allow it.

**Points the ticket asked to be called out explicitly:**

| Asked about | Answer with cite |
|---|---|
| `priority` numeric encoding vs the client's four user-facing levels | Doc: None `0`, Low `1`, Medium `3`, High `5` (:2279; identical list restated as a filter at :708). The client's 无/低/中/高 maps 1:1 onto 0/1/3/5 — nothing is lost, but note **2 and 4 are not valid** and the doc gives no meaning for them. The two body tables disagree in wording about the default (`0` at :232, `"normal"` at :324). |
| `status` numbers | Abandoned `-1`, Normal `0`, Completed `2` (:2286). Subtasks: Normal `0`, Completed `1` (:2258). `/task/filter` documents `[0]` = Open, `[2]` = Completed (:711). Spec :257 ("2 是完成，0 是正常，-1 是已放弃") — **CONFIRMED**. |
| `dueDate` / `startDate` | Two independent fields, same format (:2277, :2285), both writable (:227, :319 / :226, :318). Doc never says writing one sets the other. |
| `timeZone` | String, e.g. `"America/Los_Angeles"` (:2288); writable (:228, :320). Doc silent on what happens if it is wrong (spec :159 records silent displacement as an experimental trap; repo enforces verbatim round-trip, guards.py:52). |
| `isAllDay` | boolean, writable (:2273, :225, :317). Doc silent on how it interacts with `dueDate`'s time component. |
| `repeatFlag` | string, writable (:2282, :231, :323). Doc silent on the "no date ⇒ rule silently cleared" trap (spec :158; repo guard guards.py:134). |
| `reminders` | `<string> array` of `TRIGGER:` strings (:2280). Writable on paper (:229, :321). Client's scope: read-only display (spec :305). |
| `items` (subtasks) | `<ChecklistItem> array` (:2278); writable on paper (:234, :326). Client's scope: read-only (spec :305). |
| `sortOrder` | int64 (:2284); writable (:233, :325). Client deliberately does not write it (spec :307, :336–337). |
| `etag` / `id` immutability | **doc silent on both.** `id` is a required body field on update (:312) and should be the task's own id, but the doc never says ids are immutable; `etag` is not even a `Task` field (§:2266–2291). Nothing in the doc describes optimistic concurrency, `If-Match`, or an etag precondition on any write. |

**Fields the repo's `client.py` does not currently handle** (it is a passthrough, so "handle" =
"understand or special-case"):

| Field | Repo state |
|---|---|
| `status` (task) | Actively **blocked** on create/update — `NON_WRITABLE_FIELDS = ("status",)` (guards.py:49, :157). No un-complete path exists at all; batch is not implemented. |
| `items[].status` (subtask, 0/1 encoding) | Not handled anywhere; no predicate for the different encoding (contrast :2258 vs :2286). |
| `repeatFrom` | Not mentioned in any repo file. |
| `parentId` | Not mentioned; repo has no parent/child task concept. |
| `kind` (task) | Not handled; repo has no task-kind concept. |
| `focusSummaries` | Not handled. |
| `assigneeUsername` | Not handled; `/task/assign` and `/task/unassign` not implemented. |
| `etag` | Not handled; repo does not store or send etags, and no doc contract requires it. |
| `completedTime` | Validated as a date on write-back (guards.py:40) — but no status semantics attached. |
| everything else | Preserved verbatim: responses are remembered whole (`_remember`, client.py:267–271) and re-emitted minus `status` (`merge_snapshot`, guards.py:172). |

One repo-side caveat worth knowing: `_require` treats **falsy** as missing —
`if not payload.get(key)` (client.py:251) — so a required-key check against a field whose legitimate
value is `""` or `0` would report a false malformed response. Today it is only called with `("id",)`
and `("name",)` (client.py:73, :78, :139), both of which are non-empty strings in practice.

### D18. `Project` object — field by field

Definition table: openapi-dida365.md:2294–2305. Create body :1182–1188; update body :1232–1239.

| Field | Type | Meaning (doc) | In create body? | In update body? | Doc line |
|---|---|---|---|---|---|
| `id` | string | Project identifier | no (server-assigned) | no (path param) | :2297, :1234 |
| `name` | string | Project name | **required** | optional | :2298, :1184, :1235 |
| `color` | string | Project color, e.g. `"#F18181"` | optional | optional | :2299, :1185, :1236 |
| `sortOrder` | integer (int64) | Order value | optional | optional, "default 0" | :2300, :1186, :1237 |
| `closed` | boolean | "Projcet closed" [sic] | **no** | **no** | :2301 |
| `groupId` | string | Project group identifier | **no** | **no** — the client cannot set a group | :2302 |
| `viewMode` | string | `"list"`, `"kanban"`, `"timeline"` | optional | optional | :2303, :1187, :1238 |
| `permission` | string | `"read"`, `"write"` or `"comment"` | **no** | **no** | :2304 |
| `kind` | string | `"TASK"` or `"NOTE"` | optional | optional | :2305, :1188, :1239 |

- `permission` appears in the **`GET /open/v1/project` index example** (:1013) and in the definition
  (:2304), but **not** in `GET /open/v1/project/{projectId}`'s example (:1046–1055). Doc silent on
  whether read-one returns it. Ticket's user story 24 needs `permission != write` to be visible ⇒ read
  it from the index, not from read-one.
- `closed` likewise appears in the index example (:1010) and both group examples but not in the
  create/update responses (:1215–1224, :1267–1276).
- Project groups: `GET /open/v1/project/group` (:1308) → `<OpenProjectGroup> array`; `OpenProjectGroup`
  is `{id, name, sortOrder, showAll, viewMode}` (:2446–2454, example :1328–1337). Group writes exist
  (`POST /open/v1/project/group` :1342 with `name` required, max 64 chars :1348; `POST
  /open/v1/project/group/{projectGroupId}` :1383; `DELETE /open/v1/project/group/{projectGroupId}`
  :1425) — but **no endpoint assigns a project to a group**. Spec :308's out-of-scope reasoning
  ("清单创建与更新接口都不接受 groupId，做不到") is **CONFIRMED**.

### D19. Rate limits, quotas, and the error response shape

| Question | Doc answer | Cite |
|---|---|---|
| Rate limit documented? | **No. doc silent.** No request-per-minute figure, no throttling paragraph. | whole file; `429` occurs 0 times |
| 429 documented? | **No.** No endpoint's response table lists 429 — the tables list only 200/201/401/403/404. | e.g. :243–250, :412–418, :989–994, :1191–1197 |
| `Retry-After` / rate-limit headers? | **No mention anywhere. doc silent.** | — |
| Quota documented? | Only as a **per-task batch failure code**: `EXCEED_QUOTA` inside `id2error` (:567). No threshold, no reset window, no semantics. | :567 |
| Other batch failure codes | `EXISTED`, `DELETED`, `NOT_EXISTED`, `PROJECT_MOVE_ERROR`, `UNKNOWN`, `NO_PROJECT_PERMISSION`, `NO_TEAM_PERMISSION` | :567 |
| Error response **body** shape | **doc silent.** Every error row in every endpoint's table is `No Content`. There is no `{"error": ...}`, no `code` field, no `message` field documented anywhere. | e.g. :247–250 |
| Documented failure signals, exhaustively | (a) HTTP status 401/403/404; (b) `id2error` inside a `200` on `/task/batch` (:567); (c) "an invalid range returns an empty array" on `/task/undone` (:791); (d) "a blank [`keywords`] value returns an empty array" on `/task/search` (:828). | — |

**Is "classify failures by status code only, never parse error payloads" safe? Mostly yes, with one
documented exception.**

- Safe: nothing in the doc guarantees a parseable error body, so not parsing one cannot lose
  documented information. Repo: `AuthError` on 401/403, `ServerRejectionError` on other ≥400, message
  built purely from the status code (client.py:290–309) — consistent with the doc.
- **Not safe:** `POST /open/v1/task/batch` reports **per-task** failure inside a `200 OK` (:567).
  Status-code classification alone will report a fully-failed batch as success. The batch writer must
  inspect `id2error` and surface it.
- Also unlisted by the doc and therefore unclassifiable: **429**, all 5xx, and any 409-style
  conflict. Whether the server sends a body for those is **doc silent** — a live probe would be
  needed, which is out of scope.
- Unrelated but adjacent: the doc's `401` is the only signal for a dead token; there is no documented
  "token expired" body, which matches spec user story 7's need to distinguish auth failure from
  network failure — the repo already separates them by exception type (client.py:299–308).

---

## Section E — Cross-check: spec/ticket claim → doc → verdict

Read this table first. "Spec" line numbers refer to `gh api repos/Miaotofu01/Dida-TUI/issues/30`.

### E1. The spec's four experimental facts (`已实测的 API 事实`, spec :320–325)

| # | Claim | What the doc says (with line) | Verdict |
|---|---|---|---|
| 1 | Un-complete works via `task/batch` `update` with `status: 0`; "文档对此零提及" | The batch section :551–590 does not contain the word `status` anywhere. Body table has only `add`/`update` arrays (:561–562); example update item is `{id, projectId, title}` (:581). The `Task` type referenced by those arrays *does* include `status` (:2286) — a type reference, not a write guarantee. Create/update body tables both omit `status` (:219–241, :309–333). | **AGREE — doc silent, confirmed.** The spec's "zero mention" is literally true of :551–590. The `[Task] array` typing is the only counter-hint and it proves nothing. |
| 2 | Inbox is not in the project index; its tasks carry a per-account `projectId` shaped like `inbox`+digits; `inbox` is a request-side alias only | Index example is a bare array of Projects with no inbox row (:1005–1016); `Project` has no "is inbox" flag (:2294–2305). The literal `inbox` **is** documented as a project id for `/data` (:1068), `/task/undone` (:796) and `/completeTasks` (:433, :438). The per-account id is **doc silent**. | **Mostly AGREE.** Alias: documented (three places) — part of the claim is doc-backed. Absent-from-index: doc never states it, but nothing contradicts it ⇒ **doc silent**. Per-account id: **doc silent**. Repo currently violates the last part: `payload.get("projectId") or INBOX_ID` and `INBOX_ID in project_id.lower()` compare against the literal (src/dida/storage/store.py:538, :453; src/dida/tui/escape.py:32). |
| 3 | `GET /open/v1/project` `limit` defaults to 200; the client never passes `offset`/`limit` so list 201+ is invisible | `limit`: "Maximum number of projects to return. **When either pagination parameter is provided**, omitted or negative values default to `200`" (:986). `offset` similarly defaults to `0` (:985). Response is a bare array with no total (:991). | **AGREE with one nuance.** The 200 default is documented *conditionally* on at least one pagination param being present (:986); the doc does not state the page size for a parameterless call — that is exactly what the experiment supplied. Repo state: `list_projects` **does** accept and forward the params (client.py:59–72), so ticket #41's own correction ("客户端会带上，缺的是刷新路径从不翻页") is right. No documented maximum for `limit` ⇒ **doc silent**. |
| 4 | `/task/filter` with `projectIds` omitted returns all projects + inbox; it hard-caps at 200 with no pagination; `status: [2]` returns a full 200 and truncates silently | Cap: "Retrieves **at most 200 tasks**" (:700) ⇒ **documented**. Pagination: no page/offset/cursor parameter exists in :703–711 ⇒ **documented absence**. Omission semantics: **doc silent** — :705 says only "Filters tasks belonging to the specified project ID". Silent truncation: guaranteed by the bare-array response (:717) plus the documented cap; the doc provides **no** truncation signal. | **AGREE on cap + no-pagination + silent-truncation; the omission semantics are doc silent** (the analogous `/task/undone` states omission explicitly at :796, but `/task/filter` does not). An implementer pinning "omitted ⇒ all" is pinning the spec's experiment, not the doc. |

### E2. The spec's unverified notes (`未经验证、实现时要留意的`, spec :327–333)

| # | Note | What the doc says (with line) | Verdict |
|---|---|---|---|
| a | Deleting a project: what happens to its tasks is unwritten; the confirmation copy must say "don't know" and promise no recovery | `DELETE /open/v1/project/{projectId}` is :1278–1302: path, one path param, a response table of `200 No Content` + 401/403/404, and a header-only example. **No sentence about the tasks, and no trash/restore/undelete endpoint exists in the whole file** (only hit for `DELETED` is the batch error code at :567). | **AGREE — doc silent, verified by full-file search.** Ticket #42's copy requirement is correct as written. |
| b | `POST /open/v1/task/{taskId}` alone may or may not un-complete; not proven; do not treat it as equivalent to batch | The update body table :309–333 does **not** list `status`; `status` is defined as a `Task` field (:2286) and the endpoint returns a `Task` (:338). Nothing states or denies write effect. | **AGREE — doc silent.** Repo enforces the conservative reading: `status` is rejected on this path (guards.py:49, :157, citing "api-contracts.md 第 5 条"). |
| c | A `kind: NOTE` project's behaviour (can it hold tasks?) is unverified; implement "shown but not enterable" | `Project.kind` is `"TASK" or "NOTE"` (:2305) and is settable on create/update (:1188, :1239). Nothing anywhere says a NOTE project cannot hold tasks; `ProjectData.tasks` is "Undone tasks under project" with no kind caveat (:2331). | **AGREE — doc silent.** The doc names the enum and stops. |
| d | The server auto-fills `startDate` when `dueDate` is written (v1 observation); not written down, so don't assume it does or doesn't | **doc silent** — no such statement. Supporting colour only: every doc example that has both shows them equal (:753–754, :771–772, :1108–1109, :183–184), and `/task/filter` filters on `startDate` (:706–707) while `/task/completed` filters on `completedTime` (:645–646). None of that is a rule. | **AGREE — doc silent.** It is however a good reason for the client to always write both fields together rather than relying on either behaviour. |
| e (bonus, spec :333) | Project groups have no sample in the real account; implement "heading only" | Groups are documented as data (`GET /open/v1/project/group`, `<OpenProjectGroup> array` :1308, :2446–2454) and as writes (:1342, :1383, :1425), but **no endpoint sets a project's `groupId`** (only responses/definition carry it: :1011, :1052, :1095, :2302). | **AGREE — the client cannot assign a group, and the doc is silent on whether the account has any.** |

### E3. Ticket-level claims

| Source | Claim | What the doc says (with line) | Verdict |
|---|---|---|---|
| #39 | Creating a task must land in a **named list**, not inbox; the repo's create path is hard-wired to inbox and has no target-list parameter | `POST /open/v1/task` takes `projectId` as a **required** body field (:222) and the example sends a real project id (:263). | **AGREE that the API supports it; the hard-wiring is repo-side, at src/dida/sync/engine.py:914 (`project_id=INBOX_ID`), not in the client (client.py:142–148 passes the body through). `inbox` itself as a create-time `projectId` is doc silent.** |
| #41 | "`GET /open/v1/project` `limit` defaults to 200; the API client does pass `offset`/`limit`, the refresh path just never pages" | :986 (conditional 200 default), :985 (offset default 0), :991 (bare array). | **AGREE.** Add: the doc gives no total, no `hasMore`, and no documented max for `limit` ⇒ "拿不全时不要假装拿全了" must be inferred from `len == limit` / one extra empty page, not read from the payload. |
| #42 | "清单的创建与更新接口都不接受项目组字段" | `groupId` occurs only in Project responses and the definition (:1011, :1052, :1095, :2302); create body :1182–1188 and update body :1232–1239 have no group field. | **AGREE — verified by exhaustive grep.** |
| #42 | "删除清单时它里面的任务会怎样，官方文档没写" | :1278–1302 contains no such statement; no trash/restore endpoint exists. | **AGREE — doc silent.** |
| #45 | "搬运走搬运端点" (not a field update) | `POST /open/v1/task/move` (:499), body = **JSON array** of `{fromProjectId, toProjectId, taskId}`, all required (:504, :508–510); response = array of `{id, etag}` (:516, :542–547). | **AGREE.** Two shape traps for the request test: the top level is an **array**, and the **response is an array** of `{id, etag}`, not the moved Task. |
| #45 | "收集箱与真实清单之间双向可搬" | **doc silent** — `inbox` is never mentioned in :497–548. | **Doc silent; spec :230 claims it experimentally.** A request-shape test can pin the field names but not the inbox alias. |
| #45 | "更不能删除标签（API 也没有删除端点）" | Tag section :1576–1654 has only `GET /open/v1/tag` (:1580) and `POST /open/v1/tag` (:1614). No update, no delete. | **AGREE — no delete endpoint.** |
| #45 | "明确告知不能在客户端新建标签，要新标签得回官方客户端" | **`POST /open/v1/tag` is documented at :1612–1654** with `name` + `label` both required, both max 64 chars, lowercase, and `label` must equal `name` lowercased (:1620–1621). | **CONFLICT.** The API *can* create tags; only delete is impossible. If the copy tells the user "the API has no such endpoint", it is false. Either reword to "this client doesn't do it yet" or implement the documented create. |
| #37 | "拉取要同时按完成时间窗口与完成状态过滤" | `POST /open/v1/task/completed` parameters are `projectIds`, `startDate`, `endDate` — filtered on **`completedTime`** (:644–646). **No `status` parameter exists** (:642–646). | **CONFLICT with a server-side reading.** The dual filter must be: server-side window on `completedTime` (:645–646) + **client-side filter on the `status` field returned in the payload** (present in the documented example at :688, value `2`). `/task/filter` has `status` (:711) but filters dates on `startDate` (:706–707) and caps at 200 (:700) ⇒ it cannot express the window. |
| #37 | "只显示最近 7 天完成的" (config default 24h → 168h) | `/task/completed` caps at **200** per call with no pagination (:637). | **Doc supports the window; the 200 cap is a hard ceiling on any 7-day window.** A 7-day window with >200 completions will silently truncate — the doc offers no cursor. Repo already flags this (`CompletedReport.truncated`, src/dida/sync/engine.py:1129). |
| #30 spec :240 | "服务端的查询接口的日期区间过滤的是 `startDate`" | `/task/filter` filters `startDate` for **both** bounds (:706–707). | **AGREE — verbatim in the doc.** |
| #30 spec :250 | `content` = 「描述」, `desc` = 「备注」, both writable, neither overwrites the other | Both are optional writable string fields on create (:223–224) and update (:315–316). `content` = "Task content" (:2275); `desc` = "Task description of checklist" (:2276). | **AGREE on writability and independence; doc silent on any interaction between them.** |
| #30 spec :234 | "请求侧可以继续用字面量 `inbox`（取收集箱数据、完成任务、删除任务都接受它）" | `inbox` is documented for `/project/{projectId}/data` (:1068), `/task/undone` (:796), `/task/completeTasks` (:433, :438). For **`complete`** (`/project/{projectId}/task/{taskId}/complete` :398) and **`delete`** (:468) the doc says nothing about `inbox`. | **Partially AGREE / partially doc silent.** The `/data` case is documented; the complete and delete cases are **doc silent** and rest on the same experiment as fact #2. |
| #30 spec :187 | New client methods needed: project create/update/delete/read-by-id, task move, batch update, `task/filter`, `task/search` | All exist in the doc: :1178, :1228, :1280, :1020, :499, :553, :697, :825. | **AGREE.** Two documented endpoints the plan omits and may want anyway: `POST /open/v1/task/completeTasks` (:430, bulk complete with inbox default) and `POST /open/v1/task/undone` (:788, undone tasks in a ≤14-day window, accepts `taskIds`). |
| repo client.py:158 | "文档里**没有** PATCH" | No `PATCH` occurs anywhere in the file (endpoint index :85–2227). | **AGREE.** |
| repo guards.py:49 (citing api-contracts.md #5) | "文档说 create / update 都不接受 `status`" | True for both body tables: :219–241 and :309–333 omit `status`. | **AGREE — the guard is doc-grounded.** But note the guard must **not** be applied to the batch path (§A3), which is the only place `status` may be sent. |
| repo client.py:64–65 | "`offset`/`limit` 只有在调用方给了才写进查询串" | The doc's own default clause is conditioned on "when either pagination parameter is provided" (:986) — so omitting them is not the same documented request as sending `limit=200`. | **AGREE with the repo's choice**, and the doc's conditional wording is the reason it is observable at all. |
