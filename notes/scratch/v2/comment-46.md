落到集成分支 `feat/v2-terminal-client`：`fd459dc..e1e1798`（分支 `ticket/46-live-day-boundary`，tip `4b1ffbd`）。

逻辑日改了立刻生效：配置文件的 `day_end` 由新加的读手 `DayEndReader`（每次都重读，读不了给 `None`、从不写盘）跟着文件走，引擎上开出 `set_day_end()` / `logical_day()`，app 把这条检查收在一个入口 `reload_day_boundary()` 里，挂在 1 秒心跳（`push_tick`）与 `r` 两个触发的最前面、任何网络调用之前——所以「没网时按 `r`」也照样按现在的逻辑日重画，`_view_day` 在画行之前记下，光标按行 id 认回原行。

重读时机选了「轮询已有的 1 秒心跳 + `r` 兜底」：不新增依赖 / 线程 / 定时器，代价是每次 25.5 µs 的文件解析（跑满一天约 2.2 秒 CPU），心跳那一跳走零 I/O 的 `logical_day()`（实测 5.1 µs）而不是带两条 SQL 的 `status()`。候选与代价写在 `notes/day-boundary-reload-46.md`，边界也写在里面：`push_tick_seconds=None`（构造 app 的默认值）时没有周期重读，只有 `r`。

合并时三处文本冲突按两边都留解掉（`docs/architecture.md` 的接口表、`testing.py` 与 `app.py` 的标准库 import 行）；接缝上补了一处 #46 自己的缺口：`logical_day` 没进 `engine.__all__`，已按字母序补上。`uv run pytest` 831 passed（合并前 809 + 这张工单的 22 条）；协议成员 29 → 31。

集成分支未进 `main` 之前不关这张工单。
