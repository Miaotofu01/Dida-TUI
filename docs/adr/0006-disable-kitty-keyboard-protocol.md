# 关掉 kitty 键盘协议推送：中文输入优先于 ctrl+enter

Textual 8.2.8 在 Linux 主驱动上默认向终端推 kitty 键盘协议的 `CSI >25u`（`1|8|16` = disambiguate + report-all-keys + report-associated-text）。这个标志让 `ctrl+enter` 变成一个能收到的独立按键，但**同一个标志**会把输入法**一次上屏**的整串文本当成一个按键事件发来；Textual 的解析器有 32 字符的阈值，超过就放弃匹配、把整串逐字符当按键重发。在一个中文任务 app 里这是致命的：输入法一次上屏十来个汉字（很常见）就会变成 `^[32;;20320:24110:...u` 这样一串乱码按键。已在仓库自己的 venv 里复现：11 个汉字乱、4 个汉字（序列长 30）正常——**短词没事、长句就乱，自测极易漏过**。

我们选择关掉推送（`TEXTUAL_DISABLE_KITTY_KEY=1`，Textual 的 `constants.py` 与 `_xterm_parser.py` 都会认它）。代价是 `ctrl+enter` 收不到——而它本来也不可靠，且完成/取消完成本来就不靠它（用 `space`）。

## Consequences

- **不要「顺手」去掉这个环境变量。** 它换来的不是整洁，是中文能不能打字。
- 键位表只用 Textual 官方 FAQ 的「万能键面」：字母、数字、F1–F10、`space`、`enter`、方向键、`Ctrl`、`Shift`。`ctrl+enter`、`ctrl+shift+*`、`alt+方向键` 一律不依赖。
- 这一条**只在 Textual 8.2.8 上必要**。上游修掉 textual#6721 之后可以重新评估；在那之前，升级 Textual 时要把中文输入当成一条验收项重跑。
- 同一批不可靠的键，实现时不要再引入：`alt+enter` 在 8.2.8 上会塌缩成 `enter`（textual#6663），`shift+backspace` 在报告独立修饰符的终端上无效（textual#6612），`ctrl+enter` 在大多终端里与 `enter` 是同一个字节 `0x0D`。
- `space` 不是万能的：`Input` / `TextArea` 拿到焦点时它会被当字符吞掉，app 级绑定静默失效。所以「完成」键只在任务列表页有效——那一页没有输入框抢焦点。
