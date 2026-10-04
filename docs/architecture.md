# 架构：七个深模块与两个测试接缝

每个模块都是一个窄接口，行为藏在里面。两条硬规则：「现在」只能来自注入的时钟，
网络只能走注入的传输层。本工单（t02）立骨架，标注了每个模块的归属工单。

## 模块与公开接口

| # | 模块 | 路径 | 公开接口 | 归属 |
|---|---|---|---|---|
| 1 | 配置与凭据 | `dida/config.py` | `Config`（`token` / `day_end` / `refresh_on_start` / `push_on_change` / `completed_window_hours`）、`config_path()`、`load_config()`、`save_config()` | t03 实现 |
| 2 | 滴答 API 客户端 | `dida/api/` | `DidaApiClient(token=, transport=, base_url=)`、`await get_project_data(project_id)`、`await create_task(body)`；失败一律是 `dida.api.errors.DidaError` 的子类 | t07 补端点与守卫 |
| 3 | 本地存储 | `dida/storage/store.py` | `Store`：清单、任务快照、待推送改动、同步状态；签名由 t08 定稿 | t08 |
| 4 | 同步引擎 | `dida/sync/engine.py` + `dida/sync/view.py` | `SyncEngine(clock=, day_end=, source=)`、`status() -> SyncStatus`、`view() -> TodayView`、`refresh()`（t09）、`complete(task_id)`（t11）、`defer(task_id)`（t13）；视图模型与分组纯函数在 `dida/sync/view.py` | t05 定读路径、t09/t10 填数据 |
| 5 | 逻辑日 | `dida/logical_day.py` | `logical_day(now, day_end) -> date` | t04 |
| 6 | 日期解析器 | `dida/date_parser.py` | `parse(text) -> ParsedTask` | t06 |
| 7 | TUI | `dida/tui/` | `DidaApp(engine)`；栏位 `#list-pane` / `#task-pane` / `#detail-pane` / `#status-bar`、`update_status()` | t05/t18 填内容 |

`SyncStatus` 字段：`checked_at`（来自时钟）、`pending_count`、`last_refresh_at`、`logical_day`。
状态栏文本由 `dida.tui.panes.format_status()` 生成，措辞按 GLOSSARY（已同步 / 待推送 / 逻辑日）。

## 依赖方向

```
bootstrap ─► tui ─► sync.engine ─► storage / api.client ─► api.transport ─► httpx
                    └─► clock（谁要「现在」就注入它）
```

- TUI 只许 import `dida.sync.engine` 和自己的 `dida.tui.*`；不许碰存储与 API 客户端，
  `tests/test_architecture.py` 用 AST 守着这条。分组、排序、逾期判定全在引擎里。
- 组合根是 `dida/bootstrap.py`（`dida` 命令 = `dida.bootstrap:main`），由它注入协作者。
- 颜色只用终端 16 色（CSS 里写 `ansi_*`），不写死 hex。

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
`FieldIgnoredError` 留给 t07 的本地守卫：服务端会静默忽略的字段，发送前就拦下。

## 读路径：视图模型与内存假后端（t05 定稿）

一启动就读本地缓存渲染，网络不是这一屏的前置条件。`view()` 的数据来源是注入的
`dida.sync.view.ViewSource`（t08 的 `Store` 是生产实现，测试用 `dida.testing.InMemorySource`），
类型分两组：

```python
# 输入（缓存 → 引擎），只有事实，没有判断
ListSnapshot(id, name)                       # 一条清单
TaskSnapshot(id, title, list_id, due, all_day, priority, completed)
SyncState(last_refresh_at, pending_count)

# 输出（引擎 → TUI），字段都是可以直接画的成品
TodayView(lists: tuple[ListSummary, ...], groups: tuple[TaskGroup, ...])
ListSummary(id, name, unfinished)            # 左栏的未完成条数徽标
TaskGroup(kind: GroupKind, items)            # kind 是 OVERDUE / TODAY；title 与 count 由 kind 推出
TaskItem(task_id, title, list_id, list_name, priority, priority_mark, due, all_day, due_text)
```

分组、排序、逾期判定、截止时间读法（「今天 18:00」「昨天 09:00」「3 天前」「—」）全在
`dida.sync.view` 的纯函数里，TUI 只画字符串。两个已经踩过的坑：

- 全天任务的 `due` 是**日期标记**（当天 00:00），不是时刻：`due_day()` 对它按日期算，
  否则 `day_end = "04:00"` 时一个「今天」的全天任务会被算成昨天。
- 没有截止时间的任务留在「今日」区（读作「—」），收集箱无日期区还没落地。

接缝一的假后端在 `dida.testing.FakeBackend`：读委托给真引擎（分组行为跟生产同一份实现），
写操作只记录（`refreshes` / `completed` / `deferred`）。TUI 测试一律这样搭：

```python
backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
backend.add_task("写周报", list_name="工作", due=T0.replace(hour=18))
app = DidaApp(backend)
```

## 屏幕文本怎么断言

`tests/support.py: screen_text(app)` 把当前屏幕渲染成纯文本（唯一触碰 Textual 合成器的地方）。
测试断言它包含什么文字，不断言私有属性，也不做整屏快照。
