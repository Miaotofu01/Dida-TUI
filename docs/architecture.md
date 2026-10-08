# 架构：深模块与两个测试接缝

每个模块都是一个窄接口，行为藏在里面。两条硬规则：「现在」只能来自注入的时钟，
网络只能走注入的传输层。下面这张表就是模块与公开接口的全部；它们都已实现，表里留着
工单号是为了回溯，不再是待办。**标题里不写数目**：v1 的日期解析器删掉之后，原来的
「七个深模块」就成了一句与表对不上的假话（名字说七、表里六行）。

## 模块与公开接口

| # | 模块 | 路径 | 公开接口 | 工单 |
|---|---|---|---|---|
| 1 | 配置与凭据 | `dida/config.py` | `Config`（`token` / `day_end` / `refresh_on_start` / `push_on_change` / `completed_window_hours`）、`config_path()`、`load_config()`、`save_config()`、`needs_token()`、`Credentials(transport=, path=).verify_and_store(token)`、`DayEndReader(path=).current()`（日界跟着配置文件走，#46）；失败是 `ConfigError` / `CredentialsError` | t03 已实现 |
| 2 | 滴答 API 客户端 | `dida/api/` | `DidaApiClient(token=, transport=, base_url=)`：`list_projects(offset=, limit=)`、`get_project_data(project_id)`、`get_task(project_id, task_id)`、`list_tags()`、`list_completed(project_ids=, start_date=, end_date=)`、`create_task(body)`、`update_task(project_id, task_id, changes, snapshot=)`、`complete_task(project_id, task_id)`、`delete_task(project_id, task_id)`、**清单的建 / 改 / 删**（#42）：`create_project(body)`、`update_project(project_id, changes, snapshot=)`、`delete_project(project_id)`——三条都有 `200 → Project` 与 `201 No Content` 两种成功形状，所以后两者返回「那一份清单，或者 `None`」；失败一律是 `dida.api.errors.DidaError` 的子类（`NetworkError` / `AuthError` / `ServerRejectionError` / `FieldIgnoredError`，守卫另有 `InvalidDateError` / `DatelessRepeatError` / `MalformedResponseError`） | t07 已实现 |
| 3 | 共用词汇 | `dida/vocabulary.py` | 本地副本与引擎**都要用**的那点类型与常量，**谁都不依赖**（#78）：任务 / 清单快照与同步状态（`TaskSnapshot` / `ListSnapshot` / `SyncState` / `INBOX_ID`）、待推送改动（`PendingChange` / `PendingListChange`）、刷新报告（`RefreshReport` / `FieldOverride`）、存储侧同步状态（`StoredSyncState`）、完成 / 未完成两个状态码（`COMPLETED_STATUS` / `UNCOMPLETED_STATUS`）、写入种类与线路调用形状（`WriteKind` / `WireCall` / `LocalEffect` / `ListWriteKind` / `ListWire` / `ListLocalEffect` 及两张行为表）、本地 id 前缀（`LOCAL_TASK_PREFIX` / `LOCAL_LIST_PREFIX` 与 `is_local_id` 一族）、视图定义与它落库的原文（`ViewDefinition` / `DueWindow` / `Completion` / `view_payload` / `view_from_payload`） | #78 已实现 |
| 4 | 本地存储 | `dida/storage/store.py` | `Store`：清单、任务快照、待推送改动、同步状态；它 import 的共用词汇全部从 `dida.vocabulary` 来，**不再 import 任何 `dida.sync.*`**（#78） | t08 已实现 |
| 5 | 同步引擎 | `dida/sync/engine.py` + `dida/sync/view.py` + `dida/sync/read.py` + `dida/sync/views.py` + `dida/sync/lists.py` | `SyncEngine(clock=, day_end=, source=, client=)`、`status() -> SyncStatus`、`logical_day() -> date`、**三种读形状**（#33）：`list_index() -> tuple[ListRow, ...]`、`tasks_in(container_id) -> TaskList`、`task_detail(task_id) -> TaskDetail \| None`、`refresh() -> RefreshReport`（**async**）、`complete(task_id)`、`defer(task_id)`、`push_pending()`、`set_day_end(day_end) -> bool`（换日界，#46）、**清单的建 / 改 / 删**（#42）：`create_list(name, color=)`、`update_list(list_id, name=, color=)`、`delete_list(list_id)`；行的字段与纯读法（截止时间读法、优先级标记、排序、逾期判定）在 `dida/sync/view.py`，读模型在 `dida/sync/read.py`，视图求值在 `dida/sync/views.py`（#35），清单的写路径与颜色档在 `dida/sync/lists.py`（#42；#54 加了「建好但服务端没回 id」时的认领：认领记录、按名字唯一对上、认领前不改 / 删那个临时 id）；**判据只有一个家、出口只有一道门**（#58）：优先级四档的用户语言 `PRIORITY_NAMES`（家在 `dida/sync/view.py`，与 `PRIORITY_CYCLE` 同一片词汇）与「搬到它已经在的那个清单不算一次改动」`is_a_move`（家在 `dida/sync/writes.py`）都经这里转出——界面只许 import 引擎，所以判据本体住在 sync 那一侧，两个时刻各问一次（与 `is_addressable_task` 同一个形状）；**v1 的 `view() -> TodayView` 已由 #58 删除**，三种读形状是唯一的读面 | t05 / t09 / t10 已实现；读形状 #33；视图求值 #35；清单写 #42；日界重读 #46；单一出处 #58；共用词汇 #78 |
| 6 | 逻辑日 | `dida/logical_day.py` | `parse_day_end(text) -> timedelta`、`logical_day(now, day_end) -> LogicalDay`（`label` / `start` / `end`，半开区间） | t04 已实现 |
| 7 | TUI | `dida/tui/` | `DidaApp(engine)`；三层页面 `dida/tui/pages/`（`index` / `tasks` / `detail`）、按层的键位表 `dida/tui/keys.py`、浮层 `dida/tui/overlays.py`、**视觉常量唯一出处 `dida/tui/theme.py`**（颜色 / 字形 / 间距 / 动效）、顶栏 `#top-bar`（`top_line()`，词标 + 导航路径）、状态栏 `#status-bar`（`status_line()` / `format_status()`） | v1 t05 / t18；一栏三层 #34；视觉地基 #51 |

`SyncStatus` 字段：`checked_at`（来自时钟）、`pending_count`、`last_refresh_at`、`logical_day`。
状态栏文本由 `dida.tui.app.status_line()` 生成（纯文本那一份是 `format_status()`），措辞按 GLOSSARY
（已同步 / 待推送 / 逻辑日），待推送非零时那一段高亮。

v1 的**日期解析器**（`dida/date_parser.py`，当时排在 TUI 前面那一行）已由 #34 删除：v2 不做
自然语言日期输入，新建只填标题、改截止时间走结构化选择器（spec 的 Out of Scope）。它删掉之后
表里的号数往前挪过一位——所以这里说「当时」的位置，不再拿一个会变的号数指它。

## 依赖方向

```
bootstrap ─► tui ─► sync.engine ─► storage / api.client ─► api.transport ─► httpx
                    └─► clock（谁要「现在」就注入它）
bootstrap ─► config ─► api.client（「列清单」验证凭据；失败一律结构化）

sync（各片）─► vocabulary ◄─ storage.store
```

- **共用词汇是一条线的交点**（#78）：`sync` 与 `storage` 都要用的那点类型住在
  `dida.vocabulary`，两边都只指向它，**谁都不依赖**（只用标准库）。在这之前那批词汇住在
  本地库那一侧，`sync` 靠 6 处「只在类型检查时 import」加 3 处函数内 import 绕开那个环；
  搬完之后两边都不必再 import 对方，加一个共用类型也有明确落点。
  `tests/test_architecture.py` 用 AST 守着：`sync/` 里一处 `dida.storage` import 都不许有
  （顶层 / `TYPE_CHECKING` / 函数体内三种形状一视同仁）。

- TUI 只许 import `dida.sync.engine` 和自己的 `dida.tui.*`；不许碰存储与 API 客户端，
  `tests/test_architecture.py` 用 AST 守着这条。排序、逾期判定、视图求值全在引擎里。
- 组合根是 `dida/bootstrap.py`（`dida` 命令 = `dida.bootstrap:main`），由它注入协作者。
- 颜色**跟随终端主题**，而不是 app 自带的一套调色板（ADR-0007 一）。2026-10 的实测推翻了原先写在
  这一行的说法：Textual 8.2.8 默认把每个 `ansi_*` 改写成它内建 Monokai 调色板里的**真彩色**
  （`ansi_cyan` 出去是 `38;2;88;209;235`），所以「只写 `ansi_*`」在源码里成立、在像素上不成立——
  用户自己的主题一眼都没被用到。落地方式是 `App(ansi_color=True)`，之后同一条 CSS 出去是 `36`。
- 于是 `ansi_*` 这个名字是**承重的**：`rgb()` / `hsl()` / CSS 具名色 / `$` 主题变量，任何一处都会把
  真彩色偷偷放回来。视觉常量只有**一个出处** `dida/tui/theme.py`（颜色、字形、间距、动效时长），
  页面与浮层只引用语义名（`theme.ACCENT` / `theme.MUTED` / `theme.RULE` …）。
- 这条规矩有**两条**守卫，缺一不可：源码扫描（`tests/test_architecture.py`，AST 只取「会被用到
  的字符串」，hex / `rgb()` / `hsl()` / 具名色 / `$` 变量都拦）与**渲染级 SGR 断言**
  （`tests/test_theme.py`：真跑一遍 app，断言强调色发出的是 ANSI 6、屏幕上一条 `38;2;` / `48;2;`
  都没有）。前者抓不住 `Text("x", style="cyan")`——它走 Textual 的 CSS 颜色解析，静默发出 `#00FFFF`。

## 两个测试接缝

```python
# 接缝一：时钟（dida/clock.py + dida/testing.py）
class Clock(Protocol):
    def now(self) -> datetime: ...          # 带时区；业务代码不许调 datetime.now()

engine = SyncEngine(clock=ManualClock(T0))
clock.advance(timedelta(minutes=90))        # 测试摆布「现在」

# 接缝二：HTTP 传输（dida/api/transport.py + dida/testing.py）
class Transport(Protocol):
    async def send(self, request: httpx.Request) -> httpx.Response: ...

transport = FakeTransport(json={"tasks": []})
client = DidaApiClient(token="tok", transport=transport)
await client.get_project_data("inbox")
assert transport.last_request.method == "GET"
assert transport.last_request.headers["Authorization"] == "Bearer tok"
assert transport.last_json == {"title": "写周报"}      # 请求体字段
```

生产用 `HttpxTransport`。传输层失败必须抛 `httpx.TransportError`，客户端把它翻成
`NetworkError`；401/403 → `AuthError`，其它 ≥400 → `ServerRejectionError`（都带 `status_code`）。
失败按**状态码**分类，不解析错误载荷：文档里 401/403/404 都可能没有响应体。

`FieldIgnoredError` 是四个本地守卫（`dida/api/guards.py`）在发送前抛的：非法日期
（`InvalidDateError`）、无日期任务上的重复规则（`DatelessRepeatError`）、不可写的 `status`。
另外两件事不走报错，走**原样回写**：时区与未知字段一律逐字节带回——写路径把服务端给的
快照与本次改动合并后再发（`update_task(..., snapshot=)`），所以调用方递进来的那份快照
越新越好。

## v2 的三种读形状（#33）

v1 的读入口只有一个为「今日」硬编码的 `view() -> TodayView`；#58 把它连同 `TodayView`、
三个分区（逾期 / 今日 / 收集箱无日期）、左栏的 `ListSummary`、`/` 的模糊过滤与子任务的勾选
写路径一起**删掉了**——那些只为「今日执行台」服务，spec 要求读模型重写、模糊过滤不迁移、
子任务只看不勾。三种读形状因此是**唯一**的读面，类型与组装纯函数在 `dida/sync/read.py`，
引擎把它们再导出（TUI 只 import `dida.sync.engine` 这一条不变）：

- `list_index() -> tuple[ListRow, ...]` —— 清单索引：收集箱置顶 → 内置视图（今天 / 最近七天 /
  所有）→ 自定义视图 → 真实清单。`ListRow.kind`（`ListKind`）是**行的身份**，真实清单那一行
  还带 `color` / `group_id` / `project_kind`（服务端的 `TASK`/`NOTE`）/ `permission`，
  每行带 `unfinished`（未完成条数）。`enterable` 说清 NOTE 清单与 permission 非 write 的行
  进不去（用户故事 23 / 24）。
- `tasks_in(container_id) -> TaskList` —— 某个容器的任务列表：这个容器的**全部**未完成任务
  （截止时间在未来的也在，v1 把它们整条丢掉了）+ `completed`（这个容器里窗口内完成的）。
  视图那个容器的行由**视图求值**决定顺序（#35），并带 `shows_list_name=True`；真实清单的行
  照旧 `by_due` 排、不重复写清单名。
- `task_detail(task_id) -> TaskDetail | None` —— 单条任务的详情：标题/描述/备注/清单/截止/
  优先级/标签，外加只读的 `repeat_flag` / `reminders` / `subtasks`，以及原文里我们不认识的
  字段（`unknown`，`raw` 整份带着，回写不丢字段）。

**收集箱的身份**（spec 已实测 API 事实 #2）：服务端的清单索引里**没有**收集箱，`resolve_lists()`
自己补上那一行；它的 id 是**服务端返回**的那一串（形如 `inbox` 加数字，实测
`inbox1234567890`），归类、分组、计数一律按这个 id。缺失的 `projectId` 读作空串
（「不知道在哪个清单」），**不再**猜成字面量 `inbox`——那既让深链的兜底分支不可达，又让归类
一条都对不上。深链拼装（`dida.tui.escape.task_url`）同样只认「真的是收集箱」的 id：
请求侧别名，或服务端那一串；id 里恰好带 `inbox` 的真实清单不再被改写。

自定义视图的行由源上的可选能力 `ViewReader.views()` 给（#36 把视图定义落库并求值）；
内置视图由 `builtin_view_rows()` 算（#35：三个写死的 `ViewDefinition` 走
`dida/sync/views.py` 的**同一条** `evaluate_view`，逾期置顶、排序、视图里的所属清单名都在
那一次求值里定下来）。视图行里的 `task_ids` 是求值结果，行上的条数与进去看到的列表因此
来自同一次求值。

### 视图求值（#35）

`dida/sync/views.py` 是**视图求值**那一层：一组纯函数，`定义 + 全量任务缓存 + 当前逻辑日`
→ 一份排好序的任务列表。不碰网络、不碰存储、不 import Textual，所以可以直接测。

- `ViewDefinition` 是一个视图的**过滤条件 + 名字**（截止区间 `DueWindow` 与完成状态
  `Completion` 两维，#36 往这里加清单范围 / 优先级 / 标签，不另写一条求值路径）。
- `builtin_view_definitions()` 给三个**写死的定义**：「今天」= `DueWindow(first=None, last=0)`
  （上界今天、下界不设 ⇒ **逾期 ∪ 今天到期**，这正是它不是一个干净区间的地方）、
  「最近七天」= `DueWindow(first=0, last=6)`、「所有」= 不设截止条件（未完成的全都要）。
- `evaluate_view(definition, tasks, now=, day_end=)` 走**同一条路径**求值——内置与自定义
  没有分支，`builtin_view_rows()` 只是「三个定义各调一次」。
- `order_key()` 是客户端统一的那份顺序：逾期置顶 → 截止时间升序 → 优先级降序 → 无日期靠后
  → 已完成沉底。逾期置顶用的是逻辑日判断（`is_overdue`），所以「今天 03:00」在
  `day_end = "04:00"` 下会被置顶，而它按时刻比当天那个全天标记更晚。
- 求值结果 `ViewTask` 带着 `overdue` 位；读模型把它落到 `TaskItem.overdue`（TUI 拿到的是
  成品，日期判断全在引擎里）。`TaskList.shows_list_name` 则区分「视图」与「清单」：视图里的
  行要写出所属清单名，清单里不重复写。

日期判断只有一份：`due_day()`（全天任务的截止是日期标记）与 `is_overdue()` 在
`dida/sync/view.py`，视图求值复用它们。

## 读路径：三种读形状与内存假后端（t05 定稿；读面在 #58 收成一条）

一启动就读本地缓存渲染，网络不是这一屏的前置条件。三种读形状的数据来源是注入的
`dida.sync.view.ViewSource`（t08 的 `Store` 是生产实现，测试用 `dida.testing.InMemorySource`），
类型分两组：

```python
# 输入（缓存 → 引擎），只有事实，没有判断；#78 起它们住在 dida.vocabulary
ListSnapshot(id, name)
TaskSnapshot(id, title, list_id, due, all_day, priority, completed)
SyncState(last_refresh_at, pending_count)

# 输出（引擎 → TUI），字段都是可以直接画的成品
ListRow(id, name, kind: ListKind, unfinished, is_inbox, color, group_id, project_kind, permission)
TaskList(container_id, items: tuple[TaskItem, ...], completed: CompletedSection,
         shows_list_name, implied_due)       # 索引 / 容器列表 / 详情这三种形状见上一节
TaskItem(task_id, title, list_id, list_name, priority, priority_mark, due, all_day, due_text,
         overdue, desc, content, tags_text, repeat_flag, reminders, completed)
# desc/content/tags_text 是详细页常驻的「描述 / 备注 / 标签」；overdue 是求值给的逾期判定
# （TUI 不许自己判日期）；repeat_flag/reminders 是行上的重复与提醒标记；completed 决定
# 「划掉 + 沉底」——它跟着**行**走，视图里那一行也照样算（#58 的 S2）
CompletedSection(items: tuple[CompletedItem, ...])
TaskDetail(...)                              # 单条任务的详情（#33）
```

排序、逾期判定、截止时间读法（「今天 18:00」「昨天 09:00」「3 天前」「-」）全在
`dida.sync.view` 的纯函数里（**排序键**在 `dida/sync/rows.py`，`by_due` 只是转发，#37），
TUI 只画字符串。截止时间读法的字形必须是**宽度无歧义**的：没有截止时间读作 ASCII 的 `-`，
不是 `—`（U+2014，东亚歧义宽度，进了对齐列整列会歪，#37）。一个已经踩过的坑：

- 全天任务的 `due` 是**日期标记**（那一天的 UTC 午夜，`YYYY-MM-DDT00:00:00+0000`，#73），
  不是时刻：`due_day()` 对它按日期算，否则 `day_end = "04:00"` 时一个「今天」的全天任务
  会被算成昨天。写侧只有一种形状（`dida.api.guards.all_day_date`）——写成「本地午夜 + 本地
  偏移」会让读侧按 UTC 取到**前一天**。

接缝一的假后端在 `dida.testing.FakeBackend`：读委托给真引擎（三种读形状跟生产同一份实现），
写操作只记录（`refreshes` / `completed` / `deferred`）。TUI 测试一律这样搭：

```python
backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
backend.add_task("写周报", list_name="工作", due=T0.replace(hour=18))
app = DidaApp(backend)
```

## 写路径：全量刷新（t09 / #41，ADR-0001）

`await engine.refresh() -> RefreshReport`：先 `GET /open/v1/project` 拿清单索引（服务端说了算），
**翻页翻到底**（响应没有 total，所以靠「这一页拿满了没有」推断还有没有下一页，`sync/refresh.py`
的 `_project_index`），再逐个清单 `GET /open/v1/project/{id}/data`——**未完成任务只能这样拉**，
日期窗口会静默漏掉「日期在很久以后、但刚被改过」的任务（ADR-0001）。全部取回之后才
`apply_refresh` 落库，所以中途失败不会留下半份刷新；返回的 `RefreshReport` 里 `written_lists` /
`written_tasks` 是这次真正写了几行，同一份数据拉第二次时两个都是 0（界面不闪、光标不丢），
`overwritten` / `suppressed` 是服务端权威盖掉了什么、哪些被待推送改动挡回去了。

落库这一步顺手**剪枝**（#41）：远端已经没有的清单与未完成任务在同一个事务里删掉，`pruned_lists` /
`pruned_tasks` 报出删了几行。两条豁免：本地还有没推成功的改动的任务不剪，本地已经是已完成的任务
不剪（那是已完成流的地盘）；收集箱也不剪（它不在清单索引里）。剪枝**只**发生在落库这一步，
不在翻页途中——取数没取全就绝不落库。

它是 **async** 的：网络等待不能阻塞界面，而 t08 的 `Store` 用的是普通 sqlite 连接（线程亲和），
写必须发生在创建连接的那个线程上。异步协程跑在事件循环同一个线程里，两条同时满足——**不许**
把刷新丢进 `threading.Thread` 工人（t08 故意没开 `check_same_thread=False`，也没加锁）。

## 屏幕文本怎么断言

`tests/support.py: screen_text(app)` 把当前屏幕渲染成纯文本（唯一触碰 Textual 合成器的地方）。
测试断言它包含什么文字，不断言私有属性，也不做整屏快照。
