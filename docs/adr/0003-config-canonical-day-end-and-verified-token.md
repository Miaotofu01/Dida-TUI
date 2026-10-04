# 配置的规范形式，以及「先验证、后落盘」

配置文件 `~/.config/dida-tui/config.toml`（权限 0600，缺失时生成一份默认的、没有 token 的版本——那份「没有 token」就是首次运行的信号）。本 ADR 记两条跨工单的约定。

**`day_end` 的规范形式是 `"00:00"`。** 输入接受 `00:00`–`24:00`、必须是整分钟（`"25:00"`、`"04:30:30"`、`"banana"` 一律在构造 `Config` 时就被拒），其中 `"24:00"` 折成 `"00:00"`：两者都表示「不偏移」，但 `00:00` 同时是合法的 `datetime.time`，而 `24:00` 不是。解析只发生在 `dida.config` 这一层；t04 的 `logical_day(now, day_end)` 拿到的永远是零填充的 `"HH:MM"`，判断「不偏移」就看 `"00:00"`。（t04 若为了健壮也认 `"24:00"`，不冲突。）

**token 只有经过一次成功的「列清单」调用才会落盘。** `dida.config.Credentials.verify_and_store()` 是唯一的写入路径：先 `GET /open/v1/project`，成功了才把 token 写进配置文件；任何失败都不碰磁盘——既不写入新 token，也不改动文件里原有的 `day_end` 等键，所以「重新粘贴 token」不会顺手把配置重置回默认值。

失败分两类，UI 不能混为一谈：401/403 是凭据问题，抛 `CredentialsError`（`DidaError` 的子类，带 `status_code`），消息直接引导用户重新粘贴 token；网络失败与其它服务端拒绝原样抛出（`NetworkError` / `ServerRejectionError`），绝不能把一次网络抖动说成「凭据失效」。空粘贴在本地就拦下，不发请求。

配置一层的其它约定：文件内容坏掉（坏 TOML、不认识的键、类型不对）抛 `ConfigError`，消息里带路径或键名，而不是 `TypeError`；`Config.__repr__` 用 `token='***'` 代替真实 token，pytest 的失败输出与日志里不会留下凭据。路径按 spec 写死为 `~/.config/dida-tui/config.toml`，不认 `XDG_CONFIG_HOME`。
