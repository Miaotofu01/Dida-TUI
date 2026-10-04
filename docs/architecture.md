# 架构：七个深模块与两个测试接缝

每个模块都是一个窄接口，行为藏在里面。两条硬规则：「现在」只能来自注入的时钟，
网络只能走注入的传输层。本工单（t02）立骨架，标注了每个模块的归属工单。

## 模块与公开接口

| # | 模块 | 路径 | 公开接口 | 归属 |
|---|---|---|---|---|
| 1 | 配置与凭据 | `dida/config.py` | `Config`（`token` / `day_end` / `refresh_on_start` / `push_on_change` / `completed_window_hours`）、`config_path()`、`load_config()`、`save_config()`、`needs_token()`、`Credentials(transport=, path=).verify_and_store(token)`；失败是 `ConfigError` / `CredentialsError` | t03 已实现 |
| 2 | 滴答 API 客户端 | `dida/api/` | `DidaApiClient(token=, transport=, base_url=)`：`list_projects(offset=, limit=)`、`get_project_data(project_id)`、`get_task(project_id, task_id)`、`list_tags()`、`list_completed(project_ids=, start_date=, end_date=)`、`create_task(body)`、`update_task(project_id, task_id, changes, snapshot=)`、`complete_task(project_id, task_id)`、`delete_task(project_id, task_id)`；失败一律是 `dida.api.errors.DidaError` 的子类（`NetworkError` / `AuthError` / `ServerRejectionError` / `FieldIgnoredError`，守卫另有 `InvalidDateError` / `DatelessRepeatError` / `MalformedResponseError`） | t07 已实现 |
| 3 | 本地存储 | `dida/storage/store.py` | `Store`：清单、任务快照、待推送改动、同步状态；签名由 t08 定稿 | t08 |
| 4 | 同步引擎 | `dida/sync/engine.py` | `SyncEngine(clock=)`、`status() -> SyncStatus`、`view()`（t05）、`refresh()`（t09）、`complete(task_id)`（t11）、`defer(task_id)`（t13） | t09/t10 实现 |
| 5 | 逻辑日 | `dida/logical_day.py` | `logical_day(now, day_end) -> date` | t04 |
| 6 | 日期解析器 | `dida/date_parser.py` | `parse(text) -> ParsedTask` | t06 |
| 7 | TUI | `dida/tui/` | `DidaApp(engine)`；栏位 `#list-pane` / `#task-pane` / `#detail-pane` / `#status-bar`、`update_status()` | t05/t18 填内容 |

`SyncStatus` 字段：`checked_at`（来自时钟）、`pending_count`、`last_refresh_at`、`logical_day`。
状态栏文本由 `dida.tui.panes.format_status()` 生成，措辞按 GLOSSARY（已同步 / 待推送 / 逻辑日）。

## 依赖方向

```
bootstrap ─► tui ─► sync.engine ─► storage / api.client ─► api.transport ─► httpx
                    └─► clock（谁要「现在」就注入它）
bootstrap ─► config ─► api.client（「列清单」验证凭据；失败一律结构化）
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
失败按**状态码**分类，不解析错误载荷：文档里 401/403/404 都可能没有响应体。

`FieldIgnoredError` 是四个本地守卫（`dida/api/guards.py`）在发送前抛的：非法日期
（`InvalidDateError`）、无日期任务上的重复规则（`DatelessRepeatError`）、不可写的 `status`。
另外两件事不走报错，走**原样回写**：时区与未知字段一律逐字节带回——写路径把服务端给的
快照与本次改动合并后再发（`update_task(..., snapshot=)`），所以调用方递进来的那份快照
越新越好。

## 屏幕文本怎么断言

`tests/support.py: screen_text(app)` 把当前屏幕渲染成纯文本（唯一触碰 Textual 合成器的地方）。
测试断言它包含什么文字，不断言私有属性，也不做整屏快照。
