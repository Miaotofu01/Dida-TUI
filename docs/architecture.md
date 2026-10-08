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
| 3 | 共用词汇 | `dida/vocabulary.py` | 本地副本与引擎**都要用**的那点类型与常量，**谁都不依赖**（#78）：任务 / 清单快照与同步状态（`TaskSnapshot` / `ListSnapshot` / `SyncState` / `INBOX_ID`）、待推送改动（`PendingChange` / `PendingListChange`）、刷新报告（`RefreshReport` / `FieldOverride`）、存储侧同步状态（`StoredSyncState`）、完成 / 未完成两个状态码（`COMPLETED_STATUS` / `UNCOMPLETED_STATUS`）、写入种类与线路调用形状（`WriteKind` / `WireCall` / `LocalEffect` / `ListWriteKind` / `ListWire` / `ListLocalEffect` 及两张行为表，表上还有一位 `converges`）、本地 id 前缀（`LOCAL_TASK_PREFIX` / `LOCAL_LIST_PREFIX` 与 `is_local_id` 一族）、**读本地那一份原文的口径**（#79：`read_text` / `read_time` / `read_priority` / `read_tags`——本地库怎么读、写路径的判据就怎么比，两个口径是**同一份代码**）、视图定义与它落库的原文（`ViewDefinition` / `DueWindow` / `Completion` / `view_payload` / `view_from_payload`） | #78 已实现；读口径 #79 |
| 4 | 本地存储 | `dida/storage/store.py` + `dida/storage/queue.py` | `Store`：清单、任务快照、待推送改动、同步状态；它 import 的共用词汇全部从 `dida.vocabulary` 来，**不再 import 任何 `dida.sync.*`**（#78）。两张队列表的**记账机制只有一份**（#82，`dida/storage/queue.py`）：`QueueShape(table=, owner_column=)` 递进各自的表名与「谁的 id」那一列，`PendingQueue` 管入账的账面、失败一次、出队、认领换名与并单改写 | t08 已实现；记账单一出处 #82 |
| 5 | 同步引擎 | `dida/sync/engine.py` + `dida/sync/view.py` + `dida/sync/read.py` + `dida/sync/views.py` + `dida/sync/lists.py` + `dida/sync/pump.py` + `dida/sync/capabilities.py` | `SyncEngine(clock=, day_end=, source=, client=)`、`status() -> SyncStatus`、`logical_day() -> date`、**整份读模型**（#81）：`read_model() -> ReadModel \| None`（从当下这一份本地副本**一次装配**，没有缓存），**三种读形状是它的投影**（#33）：`list_index() -> tuple[ListRow, ...]`、`tasks_in(container_id) -> TaskList`、`task_detail(task_id) -> TaskDetail \| None`、`refresh() -> RefreshReport`（**async**）、`complete(task_id)`、`defer(task_id, days=) -> bool`、`push_pending()`、`set_day_end(day_end) -> bool`（换日界，#46）、**清单的建 / 改 / 删**（#42）：`create_list(name, color=)`、`update_list(list_id, name=, color=) -> bool`、`delete_list(list_id)`；行的字段与纯读法（截止时间读法、优先级标记、排序、逾期判定）在 `dida/sync/view.py`，读模型在 `dida/sync/read.py`，视图求值在 `dida/sync/views.py`（#35），清单的写路径与颜色档在 `dida/sync/lists.py`（#42；#54 加了「建好但服务端没回 id」时的认领：认领记录、按名字唯一对上、认领前不改 / 删那个临时 id）；**推送只有一台机器**（#84）：重试泵在 `dida/sync/pump.py`（按「抽哪个队列」参数化，两本账各一个小适配器 `push.TaskQueue` / `lists.ListQueue`），分派表是 `push._TASK_WIRE` / `lists._LIST_WIRE`，`refresh` 与 `push_pending` 各自只有一个实现；**判据只有一个家、出口只有一道门**（#58）：优先级四档的用户语言 `PRIORITY_NAMES`（家在 `dida/sync/view.py`，与 `PRIORITY_CYCLE` 同一片词汇）与「搬到它已经在的那个清单不算一次改动」`is_a_move`（家在 `dida/sync/writes.py`）都经这里转出；**写的那一次自己回话「改了 / 没改」**（#79）：判据本体 `is_a_change`（`dida/sync/writes.py`，拿这次要盖上去的字段与**本地那一份原文**逐位比，归一化口径由 `dida/vocabulary.py` 的 `read_text` 一族与本地库共用）——改字段 `write(...) -> bool`、搬运 `move_task(...) -> bool`、改期 `reschedule(...) -> bool`、顺延 `defer(...) -> bool`、改清单 `update_list(...) -> bool`、改视图 `update_view(...) -> bool`；**删除 / 完成 / 取消完成不做同值收敛**（它们没有「什么都没改」这一档），签名不动；**v1 的 `view() -> TodayView` 已由 #58 删除**，三种读形状是唯一的读面 | t05 / t09 / t10 已实现；读形状 #33；视图求值 #35；清单写 #42；日界重读 #46；单一出处 #58；共用词汇 #78；改了没改 #79；读一次只算一遍 #81；一台写入机器 #84；**本地副本一个完整的接口 #85**（`capabilities.py`：构造那一处问清「谁会做什么」，`MissingCapability` 一种说法；`ListWriteTarget` 补上写路径真的会调的那 4 件） |
| 6 | 逻辑日 | `dida/logical_day.py` | `parse_day_end(text) -> timedelta`、`logical_day(now, day_end) -> LogicalDay`（`label` / `start` / `end`，半开区间） | t04 已实现 |
| 7 | TUI | `dida/tui/` | `DidaApp(engine)`；三层页面 `dida/tui/pages/`（`index` / `tasks` / `detail`）、按层的键位表 `dida/tui/keys.py`、浮层 `dida/tui/overlays.py`、**写入的后半段 `dida/tui/write_flow.py`**（#83）、**视觉常量唯一出处 `dida/tui/theme.py`**（颜色 / 字形 / 间距 / 动效）、顶栏 `#top-bar`（`top_line()`，词标 + 导航路径）、状态栏 `#status-bar`（`status_line()` / `format_status()`） | v1 t05 / t18；一栏三层 #34；视觉地基 #51；写入的后半段 #83 |

`SyncStatus` 字段：`checked_at`（来自时钟）、`pending_count`、`last_refresh_at`、`logical_day`。
状态栏文本由 `dida.tui.app.status_line()` 生成（纯文本那一份是 `format_status()`），措辞按 GLOSSARY
（已同步 / 待推送 / 逻辑日），待推送非零时那一段高亮。

v1 的**日期解析器**（`dida/date_parser.py`，当时排在 TUI 前面那一行）已由 #34 删除：v2 不做
自然语言日期输入，新建只填标题、改截止时间走结构化选择器（spec 的 Out of Scope）。它删掉之后
表里的号数往前挪过一位——所以这里说「当时」的位置，不再拿一个会变的号数指它。

### 队列记账只有一份机制（#82）

待推送队列是**两张表**：`pending_changes`（任务改动）与 `pending_list_changes`（清单改动，
#42）。分成两张是领域事实，不是重复——任务 id 与清单 id 是两件事，把 `list_id` 塞进 `task_id`
那一列，一个字段两个意思。**分开的是表与词汇**（`PendingChange` / `PendingListChange`，两个
dataclass 照旧各写各的），重复的是记这本账的**做法**：

- 入队那一行的账面：还没试过、没有下次重试、没有错误；
- 失败一次：尝试次数 +1、写下最后一次错误与下次重试时刻；
- 出队；认领时把指向临时 id 的改动重指到服务端给的 id；以及 #54 的并单改写（把后来的
  改动并进这一行的几格）。

这几件事现在只有一份实现，住在 `dida/storage/queue.py` 的 `PendingQueue` 里。它**不知道任务与
清单是什么**：`QueueShape(table=, owner_column=)` 给表名与 id 列，其余字段（`kind` / `payload` /
任务那行的 `list_id`）由 `Store` 的队列动词递进去，行译回各自词汇也由各自那侧做。所以加一条
队列级的事实（比如「试满几次怎么记」）只改这一处，加一本新队列也拿现成的机制。

`tests/test_queue_bookkeeping.py` 把这件事钉成**结构**（不是字面串，空格怎么摆都一样）：

- 一条当场造**第三本队列账**走一遍这些事，证明机制不挑表；
- 按 AST 断**整个存储层**里写死两张队列表名的**写语句一条都没有**——机制那份 SQL 的表名来自
  `QueueShape`，所以连它自己也不含字面表名（认领重指与失败一次内联回去、或另起一个文件抄一份，
  都会红）；
- 按 AST 断读语句只出现在 `store.py` 的**发号**（`_held_local_list_ids`）与**冲突豁免**
  （`_exempt_fields` / `_has_pending_change` / `_has_dirty_list_change` /
  `_has_pending_list_change`）那几处，表结构只许是模块级的 `_SCHEMA`；
- 断 `queue.py` 自己一句写死表名的 SQL 都没有（表名只能从 `QueueShape` 来），以及
  `attempts = attempts + 1` 全存储层只出现一次。

### 一台写入机器（#84）

推送原来被完整实现了两遍：任务一条在 `sync/push.py`、清单一条在 `sync/lists.py`——同一套
重试循环（重读队列 → 到点没有 → 记一次失败 → 退避 → 出队）各抄一遍，各自维护一套「写入种类
→ 打哪个端点」的分支。更硬的一处：`refresh` 与 `push_pending` **各有两个实现**，靠 `super()`
串起来，「哪个先生效」由 `sync/engine.py` 里基类的排列顺序决定——顺序一换，清单改动就悄悄
不再推送、新建的清单也不再被认领，而没有任何测试盯着那个顺序。

现在机器只有一台：

- **重试泵**住在 `dida/sync/pump.py`（`PumpMixin.push_pending` + `_drain`），按
  `PushQueue` 协议**参数化**——协议只有五件事：读队列、可寻址吗、记一次失败、出队、发出去。
  退避与放弃的策略仍是共用的一份（`push.can_attempt` / `push.backoff_delay`）。
- 每本账给一个小适配器：`push.TaskQueue` 与 `lists.ListQueue`，各自把五件事接到自己那一族的
  动词（`Store.pending` / `record_attempt` / `resolve` 与清单那三个同形方法）与自己的分派表上。
- **分派表驱动**：`push._TASK_WIRE` / `lists._LIST_WIRE` 把每一种线路调用形状接到一个小方法上；
  「写入种类 → 线路调用形状」仍然是 `vocabulary._WRITE_BEHAVIOUR` / `_LIST_BEHAVIOUR` 两张表。
  加一种复用既有形状的写只加一行数据；只有一种**新形状**的端点才多一行分派。查表那一句
  （查不到就大声报错）是两族共用的 `writes.wire_handler`——表分开、机器一处（#77）。
- **「可寻址吗」的判据本体各自一份**（任务 `writes.is_addressable_task`，清单
  `lists.is_addressable`——领域里就是两件事），但「要不要发」这一问只在两处：写路径（发不出去
  就拒绝入队）与推送循环（泵挑这一轮的候选时问一次；发送那一步不再问第二遍）。认领那一步用
  同一个判据认出「哪些记录在等 id」，那是分类，不是再问一次要不要发。
- `push_pending` 与 `refresh` **各自只有一个实现**（`engine.SyncEngine` 的 MRO 上各只出现
  一次）：清单那一片不再各写一份、不再靠 `super()` 串起来。`refresh` 落地之后顺手认领新建的
  清单那一步并进了 `sync/refresh.py` 唯一的实现。
- **两份词汇表与两张队列表照旧分开**：任务 id 与清单 id 是两件事，收深收掉的是机器，不是这个
  领域事实。队列记账仍走 #82 那一份机制（`storage/queue.py`）。

`tests/test_write_machine.py` 把这件事钉成**行为**：一轮推送里任务改动与清单改动**都**出去；
新建清单（`201` 空 body）在下一次刷新时被认领——断的是那条认领记录**从队列里出队**
（`pending_lists()` 空）；本地那一行在不在不算证据（刷新自己的「写索引 + 剪枝」也会把临时行
换成真 id），`pending_count()` 也不算（`AWAIT_ID` 本来就不计待推送，它一直是 0）。同一段场景
还跑在一台**把写路径那几片的基类顺序倒过来**的引擎上——#84 之前那一台会静默失效，现在两台
行为一致；另有一条断这两个名字在 MRO 上各只有一个实现。最后一条钉住报备的那处语义对齐：
任务队列里一笔打在一个服务端没见过的 id 上的改动被泵**跳过**，不算一次失败、不发请求、
`status().last_error` 不为它说一句话。

### 本地副本一个完整的接口（#85）

本地副本那条接缝上，「它要会哪几件事」原来被拆成 13 处运行时探测（`isinstance(…, 某个
Protocol)` / `getattr` + `callable`）散在业务路径上，而「做不到」有四种说法（抛错 / 当成空 /
装作没这回事 / 换一种错）。更硬的一处是**声明的比做的少**：`ListWriteTarget` 声明 7 件、
代码实际调 11 件——`identify_list` / `amend_list_change` / `save_list` / `drop_list` 这四件在
`sync/` 里从没声明过，只有真的那个本地库实现过。于是第二个实现能通过入场检查（`isinstance`
只查声明过的那几件），然后在三层调用深处炸掉。

现在：

- **声明就是全部**：`ListWriteTarget` 把那 4 件补上（写路径只许调声明过的方法）。
  `tests/test_local_copy_interface.py` 拿一个「只实现协议声明过的那几件」的替身跑完整条清单写
  路径（新建 → 推送 → 刷新认领 → 改名 → 删除）——声明少一件，替身就少一件，当场红。
- **能力在构造那一处定下来**：`dida/sync/capabilities.py` 的 `Capabilities.of(source, client)`
  是**全程序唯一**一处能力探测，`SyncEngine.__init__` 调它一次，答案存在 `self._caps` 上。
  业务路径不再探测——它们读已经定下来的那一格。
- **「做不到」只有一种说法**：`MissingCapability`（`RuntimeError` 的子类），消息里说清缺的是
  哪一件、该注入什么。八条写 / 网络路径从前各写一句自己的 `RuntimeError`，现在都从
  `Capabilities` 那几个 `require_*` 出去。
- **拒绝为什么不发生在引擎构造那一秒**：只读替身是一个**完整**的替身（验收标准要求它「也能
  通过构造」），`SyncEngine(source=None)` 更是一种刻意的降级态。构造时拒掉它们就是把两种正当
  用法一起拒掉。所以「定下来」在构造处，「说出来」在真正要用它的那一步——同一句话、同一个类型，
  只有一处写。读路径上**本来就没有**的那件事（只读替身没有队列、没有原文）照旧答文档写好的
  空值，只是「为什么是空的」现在来自构造时那一格，而不是临场再探一次。
- **没有调用者的成员退场**：引擎的 `cycle_priority`（`p`，TUI 从 #45 起走挑选器）与 `subtasks`
  （子任务行由 `task_detail().subtasks` 给），本地库的 `list_records` / `ListRecord`
  （`sortOrder` 的用处是 `lists()` 的顺序，不是一个要读出来的字段）。留下两件，理由写在票的
  评论里：推送的 `wait_for_pushes`（**测试的确定性接缝**：`write()` 不等网络，想知道「推完了
  没有」的一律是测试）与本地库的 `close` / `with Store(...)`（`__exit__` 调它，20 个测试文件
  靠它释放文件句柄）。
- `tests/test_architecture.py` 用 AST 盯着它不会被写回来：`src/dida/sync/` 里除
  `capabilities.py` 之外**一处** `isinstance(…, 能力协议)` 或 `callable(…)` 都不许有
  （`isinstance(payload, Mapping)` 那种**形状**检查不算）。

## 依赖方向

```
bootstrap ─► tui ─► sync.engine ─► storage / api.client ─► api.transport ─► httpx
                    └─► clock（谁要「现在」就注入它）
bootstrap ─► config ─► api.client（「列清单」验证凭据；失败一律结构化）

sync（各片）─► vocabulary ◄─ storage.store ─► storage.queue（只用 stdlib）
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

### 一次装配，三种看法（#81）

`read_model()` 从**当下这一份**本地副本装配出 `ReadModel`：清单（收集箱已补齐 / 并好）、
全部任务快照、以及内置三个加自定义若干个视图**各求值一次**的结果。三个读形状都是它的投影
（`ReadModel.list_index()` / `tasks_in()` / `task_detail()`），所以：

- 界面一次重画只调 `read_model()` 一次（`DidaApp.refresh_view`）——找一个容器不再把**视图**
  重新求值一遍（视图在装配时各算一次，索引行与容器查找读的都是那一份结果；探针实测：容器选中
  时重画 16 → 8 次视图求值；容器 + 详情页都开着时全表扫描 3 → 1 遍）；
- 同一个视图行上的条数与进去看到的成员来自同一次求值，不可能对不上；
- 「这个容器是哪一行」只在一处回答（`list_rows()` 给真实清单行、`ReadModel.views` 给视图行）；
- `move_targets()` 挑搬运目标时只取真实清单那几行，**不装配视图**（8 → 0 次求值）；
- `task_detail()` 是那条**窄**读法：详情形状不要视图成员，为它把所有视图求值一遍是白付的
  （实测 500 条任务 / 8 个视图：0.11ms → 31ms），它走同一条 `detail_of()`，只是少装配一样东西。

**这不是缓存层**：`ReadModel` 这个值活不过一次重画，下一次重画从当下那一份本地副本重新装配，
没有增量、没有过期问题——变的只是算的遍数。`tests/test_read_model.py` 只断三个读形状的
**内容**（条数、顺序、隐含日期、逾期位）；「求值了几遍」由仓库外的探针量，不进测试。

视图行的**定义**来自源上的可选能力 `ViewReader.view_definitions()`（#36 把视图定义落库）；
内置三个定义写死在 `builtin_view_definitions()` 里。两边都由 `assemble_read_model()` 走
`builtin_view_rows()` / `custom_view_rows()`——**同一条** `evaluate_view`（#35：逾期置顶、
排序、视图里的所属清单名都在那一次求值里定下来）。视图行里的 `task_ids` 是求值结果，行上的
条数与进去看到的列表因此来自同一次求值。

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

接缝一的假后端在 `dida.testing.FakeBackend`：**读与写都委托给真引擎**（真 `Store(":memory:")` +
真 `SyncEngine` + 假传输层，工单 #80），所以字段翻译、守卫、发号、队列记账都只有一份；它另外记下
`refreshes` / `completed` / `deferred` 这些观察量。`refresh()` 与 `refresh_completed()` 仍是记录桩
——真 refresh 按服务端剪枝，会把测试摆好的数据清空。TUI 测试一律这样搭：

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

## 写路径：这一次到底改了没有（#79）

**写的那一次自己回话**：一个判据本体 `dida.sync.writes.is_a_change(current, changes)`——拿
「这次要盖上去的字段」与**本地那一份原文**逐位比，任何一位不同才算一次真改动。归一化口径
与本地库读那一份时**是同一份代码**（`dida/vocabulary.py` 的 `read_text` / `read_time` /
`read_priority` / `read_tags`）：文本缺省是空串、优先级缺省是 `0`、标签比**集合**、`isAllDay`
看真假、`dueDate` 的「缺省」与「显式 `null`」是同一件事、认不出的字段原样比。

六条写路径**回报布尔**（真的写了 / 什么都没写）：改字段 `write(...)`、搬运 `move_task(...)`、
改期 `reschedule(...)`、顺延 `defer(...)`、改清单 `update_list(...)`、改视图 `update_view(...)`。
**删除 / 完成 / 取消完成不做同值收敛**——它们没有「什么都没改」这一档（一个已经完成的任务再按
一次完成就是再来一次不可逆的对外动作），签名与行为都不动。哪几条有这一档写在行为表的
`converges` 那一位上，写路径读表，不逐个成员写 `if`。

「没改」在**引擎那一侧**就成立：不入队、不排推送、不动本地那一份，然后如实回报。所以任何
调用方（不只是界面）都不会为一次空操作入队。界面那半边是**纯删除**：挑选器里那三条比较、
两张表单前的判据调用、详细页自由文本框的同值收敛都删掉了，界面只按回报值决定推不推、
说不说「已保存」；什么都没改时它**不出声**（不新增「没有改动」这类提示）。
`tests/test_architecture.py` 用 AST 守着两件事：「`tui/` 里一处都不许出现那几个判据的名字」，
以及那几处内联比较不许被写回来——**扫的是 `src/dida/tui/**/*.py` 一整支**（#83 起：判据被搬到
哪个界面模块里都一样被守着，不再只认某个文件名）。「两边都是属性」的比较不算（`option.value
== detail.list_id` 那种挑选项回填，`pages/detail.py` 的 `list_picker` 真的在用），判据的形状
永远是「**界面自己那份值**（下标 / 调用 / 裸名字）vs 引擎那份原文」。

## 写路径：写出去之后（#83）

「**一次写入的完整后果**」只有一份实现，在 `dida/tui/write_flow.py`（`WriteFlow` 混入
`DidaApp`）：**执行一次写 → 分拣两类失败（本地没有这条 / 服务端与网络）→ 挑出那一句话与
它的落点（状态栏 · 详细页底部）→ 推一轮 → 重画**。十一个处理函数只声明两件事：
**写了什么**（`Write.perform`，那一次引擎调用）与**成功那句说什么**（`Write.said`）；挑哪一支
`Reply` 就定下了失败怎么说与落在哪。次序、落点、以及 `await` 之后「屏幕还在不在」的守卫都
长在那一个模块里，全程序只有一种说法。

- **两处落点**（`Landing`）：报告落在**详细页底部**的那几笔（改字段 / 改期 / 搬运）要**等
  这一轮推送落地**再重画——那一行说的就是「你刚才那一下出去没有」（`已保存` /
  `待推送（N）`），不等它就说不准，这就是「先推再刷」那条次序的由来；落在**状态栏**的那几笔
  （新建 / 删除 / 顺延 / 清单 / 视图）不等，引擎自己排了一轮立刻推送（ADR-0002），状态栏那个
  「待推送 N」自己会跟上。失败那句话写在重画**之后**（重画会把底部那一行重写掉）。
- **浮层交回来的字符串值 → 一次写**的翻译也在这里（`pick_write` / `_apply_pick`）：哪一格、
  值怎么读（优先级 `0/1/3/5`、标签按多选那一格的切法）只在这一处，表单回调只把值递进来。
- **「没改」的界面那一半**也在这里：`perform` 回 `False`（#79 的回报值）就不推、不重画、
  不出声——界面是那个回报值的**纯消费者**，不自己再判一遍。
- 这一层只调用 app 那几道已经带守卫的门（`refresh_view` / `_write_status` /
  `_write_save_line` / `_notify_step`），所以十一个入口都不必再记得那道守卫。
- 加一种新的写：键位 / 页面那一处 + 处理函数里那一处声明（挑一支现成的 `Reply`）。写路径的
  判据只有一个家（#58 / #79），这一层不新增判断。
- `tests/test_architecture.py::test_the_write_chain_has_exactly_one_home` 用 AST 盯着它不会被
  搬回来：`tui/` 里**任何函数**都不许同时出现「接住 `DidaError`」与「叫一次写」——那种组合就是
  一份手写的后半段（接住失败 → 挑话 → 推 → 重画），唯一的例外是 `WriteFlow.finish_write`
  那个家。别把它读成「不许接 `DidaError`」：拉标签、同步收尾那两处接它是正当的，它们没有写。
  守卫自己也有测试（抄一份当场红、那个家放行、只接错或只写都不算）。

## 屏幕文本怎么断言

`tests/support.py: screen_text(app)` 把当前屏幕渲染成纯文本（唯一触碰 Textual 合成器的地方）。
测试断言它包含什么文字，不断言私有属性，也不做整屏快照。
