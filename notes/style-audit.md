# dida TUI 外观审计（v2 终端客户端 · 现状）

**审计对象**：`/home/tofu/dida-v2-worktrees/integration`（分支 `feat/v2-terminal-client`，HEAD `062042c`，工作区干净）。
**环境**：Python 3.12.3，textual **8.2.8**，rich **15.0.0**（`.venv`，`uv` 未重新 sync——直接用了仓库自带的 `.venv`）。
**方法**：`DidaApp(FakeBackend(...))` + `tests/support.py::screen_styled_text`（`app.run_test()`，逐行 ANSI）+ 真实 pty 跑一遍同一个 app。
**数据**：全部是**编造**的账号（`tests/test_pages.py:45-86` 那一份的形状，加了已完成任务 / 长标题 / 子任务 / 提醒 / 重复规则）。**没有碰过 `~/.config/dida-tui/config.toml`，没有对滴答 API 发过任何请求。**
**本文件之外没有改动仓库任何文件**（只在 `/tmp/style_audit/` 放了渲染脚本，附录 B 有全文）。

> ⚠ 渲染时的环境变量会改变屏幕上的颜色。本机 shell 默认 `NO_COLOR=1`、`TERM=dumb`——那份渲染是**全灰**的（textual 的 `Monochrome` 滤镜，`textual/filter.py:78-93`）。下面所有彩色渲染都是在 `env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor` 下跑的；16 色那一节是显式降级跑的。**审计自己的终端设了 `NO_COLOR` 会看不到颜色，这不是 app 的问题。**

---

## 0. 一句话结论

今天的界面**几乎没有自己的外观**：仓库自己写的 CSS 一共 **两条规则**（`app.py:94-102`）+ **一块浮层 CSS**（`overlays.py:24-41`），其余全部是 Textual 默认主题（`textual-dark`）给的背景、页脚、滚动条与模态遮罩。所有前景色最终都以 **truecolor `38;2;R;G;B`** 输出（`ansi_cyan` 也不是 ANSI 码，见 §1.2），**唯一真正的 16 色约束是一条扫源码文本的正则测试**（`tests/test_architecture.py:181-194`），它只拦 `#rrggbb` 字面量。

---

## 1. 现状长什么样（本节最重要）

### 1.1 清单列表页 @ 80×24 —— 逐行原始 ANSI（`app.run_test(size=(80,24))`，光标停在收集箱）

第 0-11 行与第 22-23 行**逐字节**照抄（第 12-21 行是同一串空白填充，见注）：

```
00 '\x1b[7;38;2;224;224;224;48;2;18;18;18m❯ ▣ 收集箱  \x1b[0m\x1b[7;38;2;153;153;153;48;2;18;18;18m3\x1b[0m\x1b[48;2;18;18;18m                                                                   \x1b[0m'
01 '\x1b[38;2;224;224;224;48;2;18;18;18m  ▸ 今天  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m4\x1b[0m\x1b[48;2;18;18;18m                                                                     \x1b[0m'
02 '\x1b[38;2;224;224;224;48;2;18;18;18m  ▸ 最近七天  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m2\x1b[0m\x1b[48;2;18;18;18m                                                                 \x1b[0m'
03 '\x1b[38;2;224;224;224;48;2;18;18;18m  ▸ 所有  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m7\x1b[0m\x1b[48;2;18;18;18m                                                                     \x1b[0m'
04 '\x1b[38;2;224;224;224;48;2;18;18;18m  ★ 我的一天  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m1\x1b[0m\x1b[48;2;18;18;18m                                                                 \x1b[0m'
05 '\x1b[38;2;224;224;224;48;2;18;18;18m  \x1b[0m\x1b[1;38;2;224;224;224;48;2;18;18;18m── 项目组 g1 ──\x1b[0m\x1b[48;2;18;18;18m                                                               \x1b[0m'
06 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 工作  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m2\x1b[0m\x1b[48;2;18;18;18m                                                                    \x1b[0m'
07 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 生活  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m1\x1b[0m\x1b[48;2;18;18;18m                                                                    \x1b[0m'
08 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 学习  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m1\x1b[0m\x1b[48;2;18;18;18m                                                                    \x1b[0m'
09 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 笔记本  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m0\x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m  ⚠ 不可进入\x1b[0m\x1b[48;2;18;18;18m                                                      \x1b[0m'
10 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 别人的清单  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m0\x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m  ⚠ 不可进入\x1b[0m\x1b[48;2;18;18;18m                                                  \x1b[0m'
11 '\x1b[38;2;224;224;224;48;2;18;18;18m  ☰ 空清单  \x1b[0m\x1b[38;2;153;153;153;48;2;18;18;18m0\x1b[0m\x1b[48;2;18;18;18m                                                                  \x1b[0m'
（12-21 行：10 行逐字节相同 —— '\x1b[48;2;18;18;18m' + 80 个空格 + '\x1b[0m'；即整行只有背景色，没有内容）
22 '\x1b[38;2;88;209;235;48;2;18;18;18m已同步 12:03 · \x1b[0m\x1b[1;38;2;253;151;31;48;2;18;18;18m待推送 2\x1b[0m\x1b[38;2;88;209;235;48;2;18;18;18m · 逻辑日 03-14\x1b[0m\x1b[48;2;18;18;18m                                          \x1b[0m'
23 '\x1b[1;38;2;255;166;43;48;2;36;47;56m ↑ \x1b[0m\x1b[38;2;224;224;224;48;2;36;47;56m上一行 \x1b[0m\x1b[1;38;2;255;166;43;48;2;36;47;56m ↓ \x1b[0m\x1b[38;2;224;224;224;48;2;36;47;56m下一行 \x1b[0m\x1b[1;38;2;255;166;43;48;2;36;47;56m ? \x1b[0m\x1b[38;2;224;224;224;48;2;36;47;56m当前这一层的键位 \x1b[0m\x1b[1;38;2;255;166;43;48;2;36;47;56m r \x1b[0m\x1b[38;2;224;224;224;48;2;36;47;56m手动同步 \x1b[0m\x1b[1;38;2;255;166;43;48;2;36;47;56m o \x1b[0m\x1b[38;2;224;224;224;48;2;36;47;56m在浏览器里打开当前任务 \x1b[0m\x1b[1;38;2;255;166;43;48;2;36;47;56m q\x1b[0m'
```

同一屏的纯文本（`screen_text`）：

```
❯ ▣ 收集箱  3
  ▸ 今天  4
  ▸ 最近七天  2
  ▸ 所有  7
  ★ 我的一天  1
  ── 项目组 g1 ──
  ☰ 工作  2
  ☰ 生活  1
  ☰ 学习  1
  ☰ 笔记本  0  ⚠ 不可进入
  ☰ 别人的清单  0  ⚠ 不可进入
  ☰ 空清单  0
（10 行空白）
已同步 12:03 · 待推送 2 · 逻辑日 03-14
 ↑ 上一行  ↓ 下一行  ? 当前这一层的键位  r 手动同步  o 在浏览器里打开当前任务  q 退出  ⏎ 进入这一行
```

### 1.2 这些颜色**是什么**（逐个命名）

| 屏幕上是什么 | 代码里写的 | 实际发出的字节 | 来源 |
|---|---|---|---|
| 整屏背景（每一行都带） | 没人写 —— Textual 主题 | `48;2;18;18;18` = `#121212` | `textual-dark` 没显式给 background（`textual/theme.py:70-81`），由 `textual/design.py:16` 的 `DEFAULT_DARK_BACKGROUND = "#121212"` 兜底（`design.py:269`），再经 `Screen { background: $background }`（`textual/screen.py:174-178`） |
| 普通行文字 | 没写（继承默认） | `38;2;224;224;224` = `#E0E0E0` | `$text = auto 87%`（`textual/design.py:363`）+ `foreground="#e0e0e0"`（`textual/theme.py:78`） |
| 条数 / 「⚠ 不可进入」/ 空态 / 详情页字段名 | `EMPTY_STYLE = "dim"`（`pages/base.py:33`） | `38;2;153;153;153` = `#999999` | **不是 SGR 2**：`dim` 被 `ANSIToTruecolor` 换成「向背景混 66%」的实色（`textual/filter.py:129-149`、`textual/constants.py:172-175` 的 `DIM_FACTOR=66`），dim 属性被摘掉（`filter.py:124` 的 `NO_DIM`） |
| 项目组小标题 | `HEADING_STYLE = "bold"`（`pages/index.py:56`） | `1;38;2;224;224;224` | SGR 1 |
| 光标行 | `line.stylize("reverse")`（`pages/base.py:146`） | `7;38;2;224;224;224;48;2;18;18;18` | SGR 7；**反色只覆盖文字本身**，同一行的尾部填充是 `48;2;18;18;18`（没有 `7`），所以高亮条不到行尾 |
| 状态栏文字 | `#status-bar { color: ansi_cyan; }`（`app.py:100`） | `38;2;88;209;235` | `ansi_cyan` → ANSI 6 → 被 Textual 映射成 **Monokai 的 (88,209,235)**（`app.py:563` `ansi_theme_dark = MONOKAI`，`filter.py:220-262`）。**不是 `36`，也不是 256 色** |
| 待推送非零那一段 | `PENDING_STYLE = "bold yellow"`（`app.py:54`） | `1;38;2;253;151;31` | Rich 的 `yellow` = **ANSI 3**（不是 CSS 的 `#ffff00`）→ Monokai 的 (253,151,31)。见 §7.6 |
| 已完成行 | `COMPLETED_STYLE = "dim strike"`（`pages/tasks.py:25`） | `9;38;2;153;153;153` | SGR 9 + dim 混合色 |
| 页脚键位 | 没人写 —— Footer 的组件类 | `1;38;2;255;166;43` on `48;2;36;47;56` | `$footer-key-foreground` 默认取 accent = `#ffa62b`（`textual/design.py:458`、`textual/theme.py:75`），`$footer-background` 默认取 panel = `#242F38`（`textual/design.py:456`）；CSS 在 `textual/widgets/_footer.py:39-56` |
| 页脚说明 | 同上 | `38;2;224;224;224` on `48;2;36;47;56` | `$footer-description-foreground = #E0E0E0`（`textual/design.py:455` 一带） |
| 滚动条（内容超屏时，最右 2 列） | 没人写 | `7;38;2;0;48;84;48;2;0;0;0` + `▅▅` | `$scrollbar = #003054`、`$scrollbar-background = #000000`（`textual/widget.py:290-300` 把主题变量接到每个 widget 上） |
| 浮层边框 | `border: round ansi_cyan`（`overlays.py:33`） | `38;2;88;209;235` on `48;2;30;30;30` | `ansi_cyan` 同上 |
| 浮层内部背景 | `background: $surface`（`overlays.py:35`） | `48;2;30;30;30` = `#1E1E1E` | 主题 `$surface` |
| 浮层后面的遮罩 | 没人写 —— `ModalScreen` | 底下的内容整体变成 `content × 0.4 + #121212 × 0.6`：`#E0E0E0 → 38;2;100;100;100`，`#999999 → 38;2;72;72;72`，青色 `(88,209,235) → 38;2;46;94;104`，黄色 `(253,151,31) → 1;38;2;112;71;23` | `textual/screen.py:2164-2173` 的 `ModalScreen { background: $background 60%; }` |

**没有一处颜色是仓库自己挑的十六进制**，但**也没有一处输出是 16 色 ANSI 码**：除了 `ansi_cyan` 这一处（它也会被翻成 truecolor），其余全是 Textual 主题的 truecolor 值。

### 1.3 清单列表页 @ 120×30（纯文本，同一份数据）

与 80 列逐字符相同（这一页没有折行、没有对齐列），多出来的宽度全部是空白填充；页脚完整显示到 `⏎ 进入这一行`。

### 1.4 任务列表页 @ 80×24（收集箱：3 条未完成 + 1 条已完成）

```
  ── 收集箱 ──
❯ 买牛奶
  写周报
  交水费
  ☑ 打完了的那条
（11 行空白）
已同步 12:03 · 待推送 2 · 逻辑日 03-14
 ↑ 上一条  ↓ 下一条  ? 当前这一层的键位  r 手动同步  o 在浏览器里打开当前任务  q 退出  ⏎ 任务详细页  esc 退回清单列表页
```

- 标题行 `── 收集箱 ──` 是 `Text(f"── {name} ──", style="bold")`（`pages/tasks.py:83`）。
- **行里只有标题**：`task_line()` 就一句 `return Text(item.title)`（`pages/tasks.py:33-38`，注释写着「行的样子归 #37」）。所以优先级标记、截止时间、标签、重复/提醒标记**今天一个都不在屏幕上**——spec 用户故事 37 要的那些还没画。
- 已完成那条的 ANSI：`\x1b[9;38;2;153;153;153;48;2;18;18;18m☑ 打完了的那条`。

### 1.5 任务详细页 @ 120×30（带标签/描述/备注/子任务/重复/提醒的那条）

```
  季度报告
  清单  工作
  截止  今天 20:00
  优先级  !
  标签  #季度 #汇报
  描述  季度汇报的正文，第一段。
  备注  备注：下周一前交。
  子任务  ☑ 收集数据  ☐ 画图表
  重复  RRULE:FREQ=WEEKLY
  提醒  TRIGGER:P0DT9H0M0S
（17 行空白）
已同步 12:03 · 待推送 2 · 逻辑日 03-14
 ? 当前这一层的键位  r 手动同步  o 在浏览器里打开当前任务  q 退出  esc 退回任务列表页
```

- 标题 `style="bold"`（`pages/detail.py:97`），字段名 `EMPTY_STYLE`（`detail.py:36`），值不加样式。
- **这一页没有任何光标**：`show_detail()` 造的行全部是 `Row(id=None, …)`（`detail.py:97-98`），而光标只在 `row.id == self._selected_id` 时才画（`base.py:141-147`）。所以 `j`/`k` 在这一页什么都不做，`enter` 也不进任何字段——spec 用户故事 59-61 要的字段光标还没有（`detail.py:1-7` 写着这是 #43 的接缝）。

### 1.6 `?` 帮助浮层 @ 80×24（覆盖在清单列表页上）

```
❯ ▣ 收集箱  3
  ▸ 今天  4
  ▸ 最近七天  2
  ▸ 所有  7
  ★ 我的一天  1
  ── 项目组 g1 ──
  ☰ 工作  2
  ☰ 生活  1
  ☰ 学习  1                              ╭─────────────────────────────────╮
  ☰ 笔记本  0  ⚠ 不可进入                │                                 │
  ☰ 别人的清单  0  ⚠ 不可进入            │  ── 清单列表页 ──               │
  ☰ 空清单  0                            │                                 │
                                          │  ?      当前这一层的键位        │
                                          │  r      手动同步                │
                                          │  o      在浏览器里打开当前任务  │
                                          │  q      退出                    │
                                          │  j / ↓  下一行                  │
                                          │  k / ↑  上一行                  │
                                          │  enter  进入这一行              │
                                          │                                 │
                                          ╰─────────────────────────────────╯
（5 行空白）
已同步 12:03 · 待推送 2 · 逻辑日 03-14
 ↑ 上一行  ↓ 下一行  ? 当前这一层的键位  r 手动同步  o 在浏览器里打开当前任务  q 退出  ⏎ 进入这一行
```

- 浮层正文来自 `keys.py:164-175` 的 `help_body()`：抬头 `── {层名} ──`，然后 `row.key.ljust(width)`（`keys.py:172-174`，`width = max(len(key)) + 2`）。**用字符数补空格，不是显示宽度**。
- `MessageOverlay.TITLE = ""`（`overlays.py:51`）→ `box.border_title = ""` → 这个框**没有标题**（对比确认框有 `╭─ 仍然退出 ──…`）。
- 底下的清单列表页被 `ModalScreen` 的 60% 遮罩压暗（§1.2 最后一行）。
- 浮层的 4 条边都是 `round`：`╭ ╮ ╰ ╯ ─ │`（`overlays.py:33` + `textual/_border.py` 的 `BORDER_CHARS['round']`）。

### 1.7 确认浮层（`q` 时还有 2 处待推送）@ 120×30

```
❯ ▣ 收集箱  3
  ▸ 今天  4
  …（清单行照旧，被遮罩压暗）
  ☰ 空清单  0                  ╭─ 仍然退出 ───────────────────────────────────────────╮
                                │                                                      │
                                │  还有 2 处改动没推上去。                             │
                                │  退出不会丢：它们留在本地，下次打开 dida 接着补推。  │
                                │                                                      │
                                │  y 仍然退出 · n / Esc 留下                           │
                                │                                                      │
                                ╰──────────────────────────────────────────────────────╯
已同步 12:03 · 待推送 2 · 逻辑日 03-14
 ↑ 上一行  ↓ 下一行  ? 当前这一层的键位  r 手动同步  o 在浏览器里打开当前任务  q 退出  ⏎ 进入这一行
```

标题来自 `ConfirmOverlay(..., title="仍然退出")`（`app.py:440`），正文是 `messages.quit_prompt(2)`（`messages.py:104-108`）原样显示。

### 1.8 toast（**只有真实 pty 跑得出来**，`run_test` 里我一次都没看到 Toast 挂上 DOM）

用 pty 跑 `DidaApp`（子类在 `on_mount` 里 `self.notify("完成：写周报", title="已推送", timeout=30)`）捕获到的屏幕尾部：

```
▌ 已推送
▌ 完成：写周报
▌
```

即 Textual 自带的通知条：左下角一条 `▌`（`Toast.-information { border-left: outer $success; }`，`textual/widgets/_toast.py:62-64`；`outer` 的字符就是 `▌`），`timeout` 到点自己消失。**这是这个版本里现成的「短暂视觉反馈」**——spec 用户故事 43 要的正是它，而今天的 dida 只用状态栏改字（`app.py:263`、`app.py:365`）来反馈。

### 1.9 三种色彩能力下的同一屏（`TEXTUAL_COLOR_SYSTEM` 强制）

| 控制台色彩 | 屏幕上的字节 | 备注 |
|---|---|---|
| `auto` + `COLORTERM=truecolor`（默认） | `38;2;224;224;224` / `38;2;88;209;235` / `1;38;2;253;151;31` | 全 truecolor |
| `auto` + `TERM=xterm-256color` 无 `COLORTERM` | `38;5;253` / `38;5;80` / `1;38;5;58`；背景 `48;5;233` | 256 色，**自动降级，无报错** |
| `auto` + `TERM=xterm`（16 色） | `97;40` / `96;40` / `1;91;40`；页脚键位 `1;91;100` | **`bold yellow` 变成 `1;91`（亮红）**——因为 Monokai 的「黄」是橙黄 (253,151,31)，离亮红比离黄近；`ansi_cyan` 正常回到 `96`（亮青） |
| `NO_COLOR=1` | 全是灰：`38;2;185;185;185`（= 亮度的单色版） | `Monochrome` 滤镜（`textual/filter.py:78-93`）；`App.__init__` 会**从 `os.environ` 里 pop 掉** `NO_COLOR`（`textual/app.py:614`） |
| `TEXTUAL_THEME=ansi-dark` | `39;49` / `2;39;49` / `7;34;49` | **Textual 有原生 ANSI 模式**：`ANSIToTruecolor(enabled=False)`，`dim` 变回真的 SGR 2。见 §4.7 |

pty 实测（`TERM=xterm-256color COLORTERM=truecolor`，80×24）发出的终端控制序列：`?1049h`（备用屏）、`?1000h ?1003h ?1015h ?1006h`（鼠标）、`?1004h`（焦点）、`?2004h`（括号粘贴）、`?25l`（藏光标）、`?7l`（关自动换行）、查询 `?2026$p` 与 `?2048$p`、`OSC 22;default`。

---

## 2. 完整的样式清单

### 2.1 仓库里**所有**的 CSS（一共两块）

| 位置 | 选择器 | 设了什么 |
|---|---|---|
| `src/dida/tui/app.py:94-102` | `CursorPage` | `height: 1fr`（三层页面各自撑满，给页脚/状态栏让位） |
| 同上 | `#status-bar` | `height: 1`、`color: ansi_cyan` |
| `src/dida/tui/overlays.py:24-41`（`BOX_CSS`，`.format(name=…)` 注入两个类的 `DEFAULT_CSS`：`overlays.py:53`、`overlays.py:87`） | `MessageOverlay` / `ConfirmOverlay` | `align: center middle` |
| 同上 | `… .overlay-box` | `width: auto`、`max-width: 80%`、`height: auto`、`max-height: 80%`、`border: round ansi_cyan`、`padding: 1 2`、`background: $surface` |
| 同上 | `… .overlay-body` | `width: auto`、`height: auto` |

**没有 `CSS_PATH`，没有 `.tcss` 文件**（`grep -rn "CSS_PATH\|\.tcss" src/ tests/ docs/` 无命中）。

### 2.2 颜色 token 全表（源码里出现的每一处）

| token | 出现位置 | 类型 | 实际发出的颜色 |
|---|---|---|---|
| `ansi_cyan` | `app.py:100`（状态栏 `color`）、`overlays.py:33`（浮层 `border`） | Textual 的 `ansi_*` 名（16 色之一） | `(88,209,235)` truecolor（Monokai 调色板第 6 位） |
| `$surface` | `overlays.py:35` | **Textual 主题变量**（本仓库唯一一处） | `#1E1E1E` |
| `bold yellow` | `app.py:54`（`PENDING_STYLE`） | **Rich 样式字符串**里的颜色名 | ANSI 3 → `(253,151,31)` |
| `dim` | `pages/base.py:33`（`EMPTY_STYLE`） | Rich 属性 | `#999999`（混色，非 SGR 2） |
| `bold` | `pages/index.py:56`、`pages/tasks.py:83`、`pages/detail.py:97` | Rich 属性 | SGR 1 |
| `dim strike` | `pages/tasks.py:25`（`COMPLETED_STYLE`） | Rich 属性 | SGR 9 + `#999999` |
| `reverse` | `pages/base.py:146`（`line.stylize("reverse")`） | Rich 属性 | SGR 7 |
| （无） | 其余全部 | 主题默认 | `#E0E0E0` / `#121212` / `#242F38` / `#FFA62B` / `#003054` / `#000000` |

**没有 hex，没有 CSS 具名颜色（`red`/`cyan` 之类），没有 `rgb()`，没有 256 色码。** 唯一的主题变量是 `$surface`——也就是说「16 色」这条规矩在 CSS 层只被遵守了 3 处，其余视觉全部由主题决定（见 §3.5）。

### 2.3 边框 / 内边距 / 外边距 / 高宽算术

| 项 | 位置 | 值 |
|---|---|---|
| 唯一的边框 | `overlays.py:33` | `round ansi_cyan`（四边；`border_title` 走 `overlays.py:67`、`overlays.py:103`） |
| 唯一的内边距 | `overlays.py:34` | `padding: 1 2`（上下 1 行、左右 2 列） |
| 外边距 | —— | **一处都没有** |
| 高度算术 | `app.py:96`（页面 `1fr`）、`app.py:99`（状态栏 `1`）、`base.py:164-172`（滚动：`line < top` → `scroll_to(y=line)`；`line >= top+height` → `scroll_to(y=line-height+1)`） | 页面区 = 屏高 − 状态栏 1 − 页脚 1；`CursorPage` 的 `EMPTY_TEXT` 与行数决定内容高 |
| 宽度算术 | —— | 没有任何 `width` 计算；行文本自己决定长度（`list_line()` 的 `f"{mark} {name}  "`、`detail.py:36` 的 `f"{label}  "`、`keys.py:172-174` 的 `ljust`） |
| 文本换行 | 默认值（无人设） | `text-wrap: wrap`、`text-overflow: fold`（`textual/css/styles.py` 的默认）→ 长标题**折行**，见 §7.1 |

### 2.4 实际用到的文字样式（全部）

| 样式 | 出处 | 用在哪 |
|---|---|---|
| `reverse` | `pages/base.py:146` | 光标行（整行文字） |
| `bold` | `pages/index.py:56`、`pages/tasks.py:83`、`pages/detail.py:97` | 项目组小标题、容器标题、详情页标题 |
| `dim` | `pages/base.py:33` → `base.py:56`（空态行）、`base.py:76`/`base.py:135`（`#page-body` 初始空态）、`index.py:75`（条数）、`index.py:77`（不可进入记号）、`detail.py:36`（字段名）、`detail.py:45`（没有截止时间时的 `—`） | 见左 |
| `bold yellow` | `app.py:54` → `app.py:76` | 状态栏「待推送 N」且 N≠0 |
| `dim strike` | `pages/tasks.py:25` → `tasks.py:43` | 已完成任务行 |
| （Textual 自带） | `textual/widgets/_footer.py:45-56` | 页脚键位 `bold`、说明常规 |
| 未使用 | —— | `italic` / `underline` / `overline` / `blink` 在 `src/dida/tui/` 里一次都没出现 |

### 2.5 哪些内置控件提供「外框」

| 控件 | 用在哪 | 提供了什么外观 |
|---|---|---|
| `Footer` | `app.py:146`（compose） | 底部一行键位提示；`dock: bottom`、`height: 1`、`background: $footer-background`（`textual/widgets/_footer.py:39-56`、`textual/widgets/_footer.py:207-300`）；`ENABLE_COMMAND_PALETTE = False`（`app.py:89`）让它不显示命令面板那一格（`_footer.py:290`） |
| `Static` | `app.py:62`（`StatusBar`）、`base.py:76`（`#page-body`）、`overlays.py:68`/`overlays.py:104`（浮层正文） | 只是容器：`Static { height: auto }`（`textual/widgets/_static.py:26-30`）、`background: transparent`（`textual/widget.py:307`）；**注意 `Static` 默认 `markup=True`**（`textual/widgets/_static.py:38`） |
| `VerticalScroll` | `pages/base.py:59`（`CursorPage` 的基类） | 自动纵向滚动 + 滚动条：`layout: vertical; overflow-x: hidden; overflow-y: auto`（`textual/containers.py:150-156`）；滚动条 2 列宽、`$scrollbar`/`$scrollbar-background`（`textual/widget.py:290-300`） |
| `ModalScreen` | `overlays.py:44`、`overlays.py:75` | 60% 背景遮罩（`textual/screen.py:2164-2173`） |
| `ListView` / `DataTable` / `Tabs` / `Tree` / `Header` … | **一个都没用** | 三层页面全是「一个 `Static` + 自己拼的 `Text`」，没有任何 Textual 的列表控件参与 |
| `Screen` | 根 | `background: $background`、`overflow-y: auto`（`textual/screen.py:174-178`） |

### 2.6 每一行/每一个记号（页面自己吐出来的字符）

| 字符 | 常量与位置 | 出现在哪 |
|---|---|---|
| `❯` | `CURSOR_MARK`（`pages/base.py:27`） | 光标行行首，后面跟一个空格 |
| ` `（空格） | `BLANK_MARK`（`pages/base.py:30`） | 非光标行行首占位 |
| `▣` | `INBOX_MARK`（`pages/index.py:41`） | 收集箱行 |
| `▸` | `BUILTIN_MARK`（`pages/index.py:44`） | 内置视图（今天/最近七天/所有） |
| `★` | `CUSTOM_MARK`（`pages/index.py:47`） | 自建视图 |
| `☰` | `LIST_MARK`（`pages/index.py:50`） | 真实清单 |
| `⚠ 不可进入` | `BLOCKED_MARK`（`pages/index.py:53`），由 `index.py:76-77` 追加 | `kind=NOTE` 或无写权限的清单行 |
| `── 项目组 {groupId} ──` | `pages/index.py:87` | 项目组小标题（不可停光标，`index.py:153`） |
| `── {容器名} ──` | `pages/tasks.py:83` | 任务列表页标题 |
| `☑ ` | `pages/tasks.py:43` | 已完成任务行前缀 |
| `☑` / `☐` | `SUBTASK_DONE_MARK` / `SUBTASK_TODO_MARK`（`pages/detail.py:28-29`） | 详情页子任务 |
| `── {层名} ──` | `keys.py:173` | `?` 帮助抬头 |
| `?  r  o  q  j / ↓  k / ↑  enter` | `KEY_NAMES`（`keys.py:100-106`） | 帮助里的键名 |
| `已同步 … · 待推送 … · 逻辑日 …` | `app.py:73-77` | 状态栏（`·` 是 U+00B7） |
| `↑` `↓` `⏎` `esc` | **Textual 自带**（`textual/keys.py:257-271` 的 `KEY_DISPLAY_ALIASES`） | 页脚与帮助里的键名显示 |
| `▅`（滚动条） | **Textual 自带**（`textual/scrollbar.py:76` 的 `VERTICAL_BARS`） | 内容超屏时最右列 |
| `▌`（toast 左边） | **Textual 自带**（`textual/widgets/_toast.py:62-64` 的 `border-left: outer`） | 通知条 |
| `╭ ╮ ╰ ╯ ─ │` | **Textual 自带**（`round` 边框，`textual/_border.py`） | 浮层边框 |
| `!` `~` `·` | `priority_mark()`（`src/dida/sync/view.py:232-234`） | **引擎算好了，但今天的界面不画**（只有详情页的「优先级」字段用；任务列表页 `task_line` 只画标题） |
| `#季度 #汇报` | `format_tags()`（`src/dida/sync/view.py:262-268`） | 详情页「标签」字段 |
| `—` | `NO_DUE_TEXT`（`src/dida/sync/view.py:35`） | 没有截止时间时的读法（详情页那一行同时套 `dim`） |

**`rich` 标记（`[bold]…[/]`）在字符串里一处都没有用。** 但风险在于：所有面向用户的消息都是**普通 `str`**，交给 `Static`（默认 `markup=True`，`textual/widgets/_static.py:38`）——任务标题里若出现 `[red]`，`delete_prompt(title)`（`messages.py:138`）与 `no_browser_message(url)`（`messages.py:127`）会被当成标记解析。行文本走 `Text(...)`（不解析标记）所以没事，**浮层与状态栏那一类会**。见 §7.7。

---

## 3. 「16 色」这条约束到底禁止什么、怎么执行的

### 3.1 规矩的原文

`docs/architecture.md:36`：

> `- 颜色只用终端 16 色（CSS 里写 `ansi_*`），不写死 hex。`

同一条在 v1 设计记录里（`docs/ui-mockups.md:12-13`，注明只有两条仍然生效）：

> `> 仍然生效的只有下面「配色与标记」里的两条通用规矩：**颜色只用终端 16 色、不写死 hex**，以及`
> `> **待推送 > 0 时状态栏那一格高亮**。其余（三栏线框、分组标题、已完成折叠区）随 v1 布局一起作废。`

`docs/ui-mockups.md:106` 的表格行：`| 颜色 | 只用终端 16 色，不写死 hex |`。

### 3.2 执行它的那条测试（今天仍在、今天仍然通过）

`tests/test_architecture.py:22-23`：

```python
HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
"""颜色字面量：``#rrggbb`` / ``#rrggbbaa`` / ``#rgb``（不是照抄实现——这是终端那条规矩的形状）。"""
```

`tests/test_architecture.py:181-194`：

```python
def test_the_tui_never_hardcodes_a_hex_colour():
    """只用终端 16 色：源码里不许出现 ``#rrggbb`` 之类的颜色字面量。

    颜色字面量写死之后，浅色主题、``NO_COLOR``、以及不是 24 位的终端上那一行就糊了。
    这条规矩扫的是**源码文本**，与渲染出来的屏幕无关，所以它和上面那些边界测试住在一起
    （原本钉在 ``test_app_view.py`` 里，#32 搬出来的——那是纯模块边界的房客）。
    """
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
        if HEX_COLOUR.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
```

**现状**：`uv run`/`.venv/bin/python -m pytest tests/test_architecture.py -q` → **16 passed**；整个套件 `407 passed in 31.88s`。

### 3.3 「线」到底划在哪里（逐项实测）

把每种写法塞进 `src/dida/tui/` 的一个临时文件，跑 `tests/test_architecture.py`：

| 写进 `src/dida/tui/*.py` 的东西 | 16 色那条测试 | 依据 |
|---|---|---|
| `color: #ff0000` | **FAIL**：`AssertionError: assert ['src/dida/tui/_probe_hex.py'] == []` | 正则命中 `#ff0000` |
| `color: #f00` | **FAIL** | 正则含 3 位分支 |
| `color: #ff000080` | **FAIL** | 正则含 8 位分支 |
| `Text("x", style="#ff0000")`（Rich 样式字符串里的 hex） | **FAIL** | 扫的是源码文本，不区分 CSS/Rich |
| `background: linear-gradient(#f00, #00f)` | **FAIL**（被 hex 分支抓到） | 同上 |
| `color: red`（Textual 的 CSS 具名色 = `#ff0000`，`textual/color.py:514-560`） | **PASS**（16 passed） | 正则不认名字 |
| `color: $primary` / `$text-muted`（主题变量） | **PASS** | 同上 |
| `color: ansi_cyan` | **PASS**（也是规矩**要求**的写法） | 同上 |
| `color: rgb(255,0,0)` / `hsl(0,100%,50%)` | **PASS** | 同上 |
| `background: rgb(0,95,135)`（等于写死一个 256 色值） | **PASS** | 同上 |
| `background: linear-gradient(red, blue)`（**没有 hex**） | **PASS**（测试层面），但**运行时启动直接失败** | 见 §4.3 |
| `Style(color="color(6)")`（Rich 的 ANSI 6） | **PASS** | 同上 |
| 从 API 拿到的清单颜色 `style=row.color`（`#FF6161`） | **FAIL**（如果那串字面量写在 `src/dida/tui/` 里） | 正则命中 `#FF6161`；今天 `pages/index.py:71-78` 没有用它 |

**结论（精确的边界）**：这条规矩实际执行的是「**`src/dida/tui/**/*.py` 的文本里不许出现 `#` + 3/6/8 位十六进制**」。它不是「16 色」的守卫：

- 它**管不到**：CSS 具名色、`rgb()`/`hsl()`、主题变量、Rich 的 `color(N)`、以及**任何不在 `src/dida/tui/` 下的文件**（`src/dida/sync/**`、`src/dida/bootstrap.py`、`.tcss`、将来的 `assets/`）。
- 它**不检查渲染结果**，所以「屏幕上到底发了几种颜色」没有测试守——本文件 §1.9 那张表就是手工证据。
- 它**不是 Textual 的限制**：Textual 完全接受 hex / `rgb()` / 具名色（`textual/color.py:514-560` 的 `Color.parse` 文档），hex 会照常渲染成 truecolor。**唯一的拦路者就是这个正则测试。**

### 3.4 CSS 里用主题变量了吗？

**用了一处**：`overlays.py:35` 的 `background: $surface`。其余两处颜色是 `ansi_cyan`（`app.py:100`、`overlays.py:33`）与 Rich 的 `bold yellow`（`app.py:54`）。所以仓库内部**自己就不一致**：一块浮层用主题变量、状态栏与边框用 `ansi_*`。附带后果：`$surface` 在 16 色/`ansi-dark` 主题下是 `transparent`（§4.7 实测 `$surface = transparent`），浮层就没有填充色了。

### 3.5 spec 要的「逾期标红」今天是怎么产生的？

**今天没有红，也产生不出来。** 证据：

- spec 用户故事 25/93（`gh api repos/Miaotofu01/Dida-TUI/issues/30` 正文）：用户故事 25「我要一个内置的**「今天」**视图：逾期任务置顶并标红，下面才是截止时间落在当前逻辑日内的任务」；用户故事 93「我要逾期任务标红」。v1 设计记录的配色表 `docs/ui-mockups.md:101` 也写着「逾期分组标题 | 红」。
- 唯一算逾期的地方在 v1 的读形状里：`src/dida/sync/view.py:47`（`GroupKind.OVERDUE`）、`view.py:323`（`kind = GroupKind.OVERDUE`）——那是 `TodayView.groups` 那条路。
- v2 页面用的是 `Engine.tasks_in()` → `TaskList(items=TaskItem…)`（`src/dida/sync/read.py:181-193`、`read.py:414-423`），而 `TaskItem` 的字段是 `task_id/title/list_id/list_name/due/all_day/due_text/priority/priority_mark/tags/tags_text/desc/content`（`read.py:196-224`）——**没有 `overdue` 这个位**，`tasks_in` 也不分组（`read.py:418-422` 只是 `by_due(...)`）。
- 所以要在任务行上标红，**先得让引擎在 `TaskItem` 上多给一个判断**（这是架构规矩要求的：`docs/architecture.md:34`「分组、排序、逾期判定全在引擎里」，且 `tests/test_architecture.py:34` 的允许表里没有 `dida.logical_day`，TUI 自己判不了日期）。
- 颜色本身用 `ansi_red` 就完全在规矩内（`ansi_red` 是合法名：`textual/_color_constants.py:3-22` 的 `ANSI_COLORS` 第 1 位，`textual/color.py:556-562` 解析）。今天唯一沾红的字节是 16 色终端下降级出来的 `1;91`（那是「黄」被降级的结果，不是红）。

---

## 4. Textual 8.2.8（本仓库 `.venv` 里那一份）给重设计提供了什么

> 全部证据来自 `/home/tofu/dida-v2-worktrees/integration/.venv/lib/python3.12/site-packages/textual/`（8.2.8）+ rich 15.0.0。

### 4.1 边框类型（全表）

`textual/css/constants.py:10-30` 的 `VALID_BORDER`，与 `textual/_border.py:20` 的 `BORDER_CHARS` 键一致：

`ascii` · `blank` · `dashed` · `double` · `heavy` · `hidden` · `hkey` · `inner` · `none` · `outer` · `panel` · `round` · `solid` · `tall` · `tab` · `thick` · `block` · `vkey` · `wide`
（`textual/_border.py:20` 的表里还有一个空串 `''`，即「没设」。）

**边框标题/副标题支持**：`Widget.border_title`（`textual/widget.py:384`）、`Widget.border_subtitle`（`textual/widget.py:386`），CSS 侧 `border-title-align`（默认 `left`）、`border-subtitle-align`（默认 `right`）、`border-title-color`、`border-title-background`、`border-title-style`、`border-subtitle-*`（`textual/css/styles.py:344-346`、`styles.py:477-485`）。今天仓库只用了 `border_title`（`overlays.py:67`、`overlays.py:103`），`border_subtitle` 一次没用。

### 4.2 `text-style:` 支持的词（`textual/css/constants.py:48-64` 的 `VALID_STYLE_FLAGS`）

`bold`(`b`) · `dim` · `italic`(`i`) · `underline`(`u`) · `strike` · `reverse` · `blink` · `overline`(`o`) · `not` · `none` · `uu`（双下划线）

解析在 `textual/css/_styles_builder.py:791-808`；`link-style` / `border-title-style` / `border-subtitle-style` 共用同一个处理器。

### 4.3 CSS 里的渐变：**不支持**

- `textual/css/` 下 `grep gradient` **零命中**。
- 实测 `CSS = "Static { background: linear-gradient(red, blue); }"` → `TokenError: Expected rule value or end of declaration (found '(red, blue)')`，app **起不来**（Textual 解析 CSS 时直接报错，不是忽略）。带 hex 的 `linear-gradient(#f00, #00f)` 同样是 `TokenError`。
- **Rich renderable 里的渐变是有的**：`textual/renderables/gradient.py:13` `VerticalGradient`、`gradient.py:43` `LinearGradient`（都是 `__rich_console__` 的 Rich renderable，可以直接塞进 `Static`）；`textual/color.py:679-709` 的 `Gradient(*stops, quality=50)` 配 `get_color(stop)`。
- 现成的用例：`ProgressBar` 有 `gradient` reactive（`textual/widgets/_progress_bar.py:64`、`:232`），`LoadingIndicator` 用它做 5 个点的渐隐（`textual/widgets/_loading_indicator.py:66-90`）。

### 4.4 动画 / 过渡 / 定时器

| 能力 | 证据 |
|---|---|
| CSS 过渡 | `transition: background 400ms out_cubic;` **实测可用**，落成 `Transition(duration=0.4, easing='out_cubic', delay=0.0)`；属性在 `textual/css/styles.py:398`（`TransitionsProperty`），实现 `textual/css/transition.py` |
| 缓动函数 | `textual/_easing.py:94` 的 `EASING` 字典，33 个：`linear / none / round / in_* / out_* / in_out_*`（`sine quad cubic quart quint expo circ elastic back bounce`）；默认 `in_out_cubic`（`_easing.py:130`） |
| `App.animator` | `textual/app.py:653` `self._animator = Animator(self)`；`App.animate`（`textual/app.py:1120`） |
| widget / styles 上的 `animate()` | `Widget.animate`（`textual/widget.py:2515`）；**样式动画要用 `widget.styles.animate("background", "ansi_red", duration=0.1)`** ——实测成功，动画结束后 `background = Color(128,0,0,ansi=1)`。`widget.animate("background", …)` 会 `AttributeError`，`widget.animate("opacity", …)` 调用时不报错、异步炸在 `property 'opacity' of 'Static' object has no setter`（`_animator.py:145` 用 `setattr`） |
| 全局动画档位 | `App.animation_level`（`textual/app.py:838`）= `TEXTUAL_ANIMATIONS`（`textual/constants.py:104-107`、`:152`），取值 `none/basic/full`，默认 `full` |
| 定时器 | `set_interval`（`textual/message_pump.py:418`）、`set_timer`（`message_pump.py:378`）——仓库已在用（`app.py:158` 的同步泵） |
| 平滑滚动 | `TEXTUAL_SMOOTH_SCROLL` 默认开（`textual/constants.py:168-170`）；`CursorPage` 主动关掉了它（`base.py:170`/`base.py:172` 都传 `animate=False`） |

### 4.5 滚动条的可调项（`textual/css/styles.py:405-431`）

`scrollbar-color`（默认 `ansi_bright_magenta`）· `scrollbar-color-hover`（`ansi_yellow`）· `scrollbar-color-active`（`ansi_bright_yellow`）· `scrollbar-corner-color`（`#666666`）· `scrollbar-background`（`#555555`）· `scrollbar-background-hover`（`#444444`）· `scrollbar-background-active`（`black`）· `scrollbar-gutter`（`stable` 可防抖动）· `scrollbar-size-vertical`（默认 **2**）· `scrollbar-size-horizontal`（默认 **1**）· `scrollbar-visibility`。
注意：这些默认值在 `Widget.DEFAULT_CSS` 里被主题变量覆盖（`textual/widget.py:290-300` → `$scrollbar` 等），实际今天发出的是 `#003054` / `#000000`（§1.2）。

### 4.6 内置控件目录（`textual.widgets.__all__`，40 个；括号里是各自动 docstring 的第一句）

`Button`(简单可点按钮) · `Checkbox`(布尔复选框) · `Collapsible`(可折叠容器) · `ContentSwitcher`(切换子控件) · `DataTable`(表格) · `Digits`(3×3 Unicode 数字) · `DirectoryTree`(文件树) · `Footer`(底部键位条) · `Header`(顶部图标+时钟) · `HelpPanel`(当前控件的上下文帮助) · `Input`(单行输入) · `KeyPanel`(当前控件的绑定) · `Label`(文本标签) · `Link`(可点链接) · `ListItem`/`ListView`(纵向列表) · `LoadingIndicator`(动画转圈) · `Log`/`RichLog`(日志) · `Markdown`/`MarkdownViewer` · `MaskedInput` · `OptionList`(可选列表) · `Placeholder` · `Pretty` · `ProgressBar` · `RadioButton`/`RadioSet` · `Rule`(分隔线，类似 `<hr>`) · `Select` · `SelectionList`(多选列表) · `Sparkline`(迷你趋势图) · `Static` · `Switch` · `Tab`/`Tabs`/`TabPane`/`TabbedContent` · `TextArea` · `Tooltip` · `Tree` · `Welcome`。
另有**未列进 `__all__` 但可用**的 `Toast` / `ToastRack`（`textual/widgets/_toast.py`）。

### 4.7 运动 / 瞬时反馈（重设计最可能想要的）

| 手段 | 证据 | 实测 |
|---|---|---|
| **toast** | `App.notify(message, title=, severity=, timeout=, markup=)`（`textual/app.py:4621-4629`）；`Notification.timeout` 默认 5 秒、`severity` 默认 `information`（`textual/notifications.py:27-49`）；`Toast`/`ToastRack` 的 CSS 在 `_toast.py:23-76`、`:149-160`；`Screen` 在 `_compose_extra_widgets` 里插 `ToastRack(id="textual-toastrack")`（`textual/screen.py:1163-1164`） | **pty 实测渲染出来**：右下角 `▌ 已推送 / ▌ 完成：写周报`，到点消失。`run_test()` 下我 6 次 `pause()` 都没在 DOM 里看到 `ToastRack`（不是结论，只是这次观测） |
| **spinner** | `LoadingIndicator`：`auto_refresh = 1/16`（16fps）、渲染 5 个 `● `、用 `Gradient` 做渐隐（`textual/widgets/_loading_indicator.py:58-90`）；`animation_level == "none"` 时退化成文本 `Loading...` | 组件存在、`import` 可用；dida 没用 |
| **闪烁/闪一下** | `blink` 是合法 `text-style`（`css/constants.py:50`）；或用 `styles.animate("background", "ansi_yellow", duration=0.15)` 再切回 | `styles.animate` 实测可用 |
| **渐变条** | `Gradient`（`textual/color.py:679`）+ `VerticalGradient`/`LinearGradient`（`renderables/gradient.py:13,43`） | 存在 |
| **进度** | `ProgressBar`（带 `gradient` reactive）、`Sparkline`、`Digits` | 存在 |
| **过渡** | `transition:` CSS 属性 | 实测可用 |

### 4.8 **truecolor RGB 在 CSS 里支持吗？16 色终端上会降级还是坏掉？**

- **支持**：`Color.parse` 接受 `#RGB`/`#RGBA`/`#RRGGBB`/`#RRGGBBAA`/`rgb()`/`rgba()`/`hsl()`/`hsla()` 与 165 个具名色 + 16 个 `ansi_*` + `ansi_default`（`textual/color.py:514-560`，`textual/_color_constants.py:3-23`）。`ansi_default` 是「终端默认前景/背景」（`color.py:554-555` 返回 `ansi=-1`）。
- **默认路径是先转 truecolor**：`App.__init__` 装 `ANSIToTruecolor(ansi_theme, enabled=not native_ansi_color)`（`textual/app.py:611`），把 ANSI/系统色换成 truecolor 三元组（`textual/filter.py:220-262`）。所以 `ansi_cyan` 在默认主题下**不会**发出 `36`，而是发出 Monokai 的 `38;2;88;209;235`。
- **能力检测**：靠环境变量，不靠终端查询。`constants.COLOR_SYSTEM = os.environ.get("TEXTUAL_COLOR_SYSTEM", "auto")`（`textual/constants.py:149`）交给 Rich 的 `Console(color_system="auto")`（`textual/app.py:623-632`）——Rich 依据 `COLORTERM` / `TERM` 判定。pty 实测三种环境分别得到 truecolor / `38;5;N` / `3N`·`9N` 三种输出（§1.9），**没有崩，也没有乱**。
- **降级发生在最后一步**：`Strip.render_ansi` 对每个颜色做 `color.downgrade(color_system).get_ansi_codes()`（`textual/strip.py:681-683`），色彩系统取 `console._color_system or ColorSystem.TRUECOLOR`（`textual/strip.py:718`）。
- **降级会改变色相**：Monokai 的「黄」(253,151,31) 在 16 色下被映射成 `1;91`（亮红），不是黄。想避免这种偏移，就得走下面的原生 ANSI 模式。
- **原生 ANSI 模式（重设计值得知道）**：把 `ansi_color` 打开（或 `TEXTUAL_THEME=ansi-dark` / `ansi-light`，`textual/theme.py:444`、`:474`，`textual/constants.py:163`），`native_ansi_color` 变 True → `ANSIToTruecolor(enabled=False)`（`textual/app.py:1551-1570`），屏幕发出 `39;49` / `2`（真的 dim）/ `7;34`，并且 `&:ansi { … }` 这类 CSS 块生效（`textual/app.py:544` 的伪类映射）。**实测**：`TEXTUAL_THEME=ansi-dark` 下 `$background = ansi_default`、`$surface = transparent`、`$scrollbar = ansi_blue`、`$footer-key-foreground = ansi_magenta`。
- **`NO_COLOR`**：`App.__init__` 读 `NO_COLOR`（并把它从传给 Rich 的环境里 **pop 掉**，`textual/app.py:614-616`），非空就加 `Monochrome()`（`textual/filter.py:78-93`）——所有颜色按亮度变灰（实测 `#E0E0E0 → 185,185,185`）。

---

## 5. 终端能力姿态（仓库假设了什么）

### 5.1 spec（issue #30，`gh api repos/Miaotofu01/Dida-TUI/issues/30 --jq .body`，正文「终端能力」相关原文）

> 用户故事 114：作为使用者，我要键位只用终端一定会传上来的那些键（字母、数字、F1–F10、`space`、`enter`、方向键、`Ctrl`、`Shift`），这样我在 tmux、WezTerm、gnome-terminal、Windows Terminal 里得到的行为是一样的。
> 用户故事 115：作为使用者，我要客户端**关掉 kitty 键盘协议的推送**，因为开着它会让我的输入法一次上屏超过四个汉字就变成乱码。
> 用户故事 116：作为使用者，我要能用中文输入法在标题、描述、备注里正常打长句。
> 用户故事 117：作为使用者，我要 `space` 在任务列表页可靠触发完成，而在输入框里正常打空格（输入框有焦点时它不该触发完成）。
> 用户故事 118：作为使用者，我要在窄终端里也能用——一栏的布局天然不需要降级。
> 用户故事 119：作为使用者，我要在键位帮助里看到当前这一层可用的键，而不是一张混杂了所有层的大表。

Implementation Decisions 的「终端能力」一节：

> **启动时设上 `TEXTUAL_DISABLE_KITTY_KEY`**（关掉 kitty 键盘协议的推送）。原因不是洁癖：开着它，输入法一次上屏超过四个汉字就会被当成一个按键事件，而解析器的 32 字符阈值会让它放弃匹配、把整串逐字符当按键重发——中文直接变成乱码。代价是 `ctrl+enter` 收不到，而我们本来就不依赖它。**升级 Textual 之后要把中文输入当成一条验收项重跑**，上游修掉之前不要去掉这个开关。
> `space` 有一个已知陷阱：输入框拿到焦点时它会被当字符吞掉，app 级绑定静默失效。所以完成键只在任务列表页有效，而那一页没有输入框抢焦点；输入框存在时一律走 `enter` 提交。

**注意：spec 只点名了「键位」要在四个终端里一致（tmux / WezTerm / gnome-terminal / Windows Terminal），没有一句话点名颜色深度、字形宽度或备用屏。** 颜色那条规矩在 `docs/architecture.md:36`，理由是「浅色主题、`NO_COLOR`、以及不是 24 位的终端」——那是测试 docstring 里的说法（`tests/test_architecture.py:184-185`）。

### 5.2 ADR-0006 原文（`docs/adr/0006-disable-kitty-keyboard-protocol.md`）

> `Textual 8.2.8 在 Linux 主驱动上默认向终端推 kitty 键盘协议的 `CSI >25u`（`1|8|16` = disambiguate + report-all-keys + report-associated-text）。这个标志让 `ctrl+enter` 变成一个能收到的独立按键，但**同一个标志**会把输入法**一次上屏**的整串文本当成一个按键事件发来；Textual 的解析器有 32 字符的阈值，超过就放弃匹配、把整串逐字符当按键重发。`（第 3 行）
> `我们选择关掉推送（`TEXTUAL_DISABLE_KITTY_KEY=1`，Textual 的 `constants.py` 与 `_xterm_parser.py` 都会认它）。代价是 `ctrl+enter` 收不到……`（第 5 行）
> `- **不要「顺手」去掉这个环境变量。** 它换来的不是整洁，是中文能不能打字。`（第 9 行）
> `- 键位表只用 Textual 官方 FAQ 的「万能键面」：字母、数字、F1–F10、`space`、`enter`、方向键、`Ctrl`、`Shift`。`（第 10 行）
> `- 这一条**只在 Textual 8.2.8 上必要**。上游修掉 textual#6721 之后可以重新评估；在那之前，升级 Textual 时要把中文输入当成一条验收项重跑。`（第 11 行）
> `- 同一批不可靠的键，实现时不要再引入：`alt+enter` 在 8.2.8 上会塌缩成 `enter`（textual#6663）……`（第 12 行）

代码里的落点：`src/dida/bootstrap.py:20-33`（`os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")`，**必须在 import textual 之前**），常量本身在 `textual/constants.py:116`（`Final[bool]`，import 时读一次）。

### 5.3 重设计会碰到的风险点（实测证据）

| 风险 | 证据 |
|---|---|
| **备用屏**：Textual 一进来就切 `?1049h`，退出时切回；tmux / `script` / CI 里截图会拿不到内容 | pty 实测序列：`?1049h` 一次、`?7l`、`?25l`、`?1000h ?1003h ?1015h ?1006h ?1004h ?2004h` |
| **鼠标**：Textual 默认开鼠标上报（`?1000h/1003h/1006h`）；重设计若加鼠标点击（列表点击、折叠）会与 tmux 的鼠标模式打架 | 同上 |
| **字形宽度**：CJK 终端里 ambiguous 宽度的符号可能画成双宽，整行右移（§6） | `▣ ★ · ─ │ ╭ ╮ ╰ ╯ ↑ ↓ ▅ █ ▌` 全是 EAW=A |
| **颜色深度**：16 色下 Monokai 的「黄」变亮红（§1.9）；`dim` 在原生 ANSI 模式外不是 SGR 2 | 三档实测表 §1.9 |
| **中文输入**：任何新增的 `Input`/`TextArea`（例如新建清单浮层）会把 `space` 吃掉，app 级绑定静默失效 | spec 正文 + ADR-0006 第 13 行 |
| **窄终端**：一栏不等于不折行——60 列时 38 字的长标题把光标标记挤到单独一行（§7.1） | 60 列渲染 |

---

## 6. 字形与宽度安全

### 6.1 屏幕上真的会出现的每一个非 CJK 字形

（宽度取 `unicodedata.east_asian_width`（Python 3.12 = Unicode 15.0）与 **rich 15.0.0 实际用于排版的表**（`rich/_unicode_data/unicode17-0-0.py`，数据来自 wcwidth 项目，`rich/cells.py` 的 `get_character_cell_size`））

| 字形 | U+ | EAW | rich 算几格 | 出现在哪 | 危险 |
|---|---|---|---|---|---|
| `❯` | U+276F | N | 1 | 光标标记（`base.py:27`） | 否 |
| `▣` | U+25A3 | **A** | 1 | 收集箱（`index.py:41`） | **是**（CJK 字体可能双宽） |
| `▸` | U+25B8 | N | 1 | 内置视图（`index.py:44`） | 否 |
| `★` | U+2605 | **A** | 1 | 自建视图（`index.py:47`） | **是** |
| `☰` | U+2630 | N | **2** | 真实清单（`index.py:50`） | **是（反向）**：Unicode 说 1 格，rich 按 2 格排版——见 6.3 |
| `⚠` | U+26A0 | N | 1 | 不可进入（`index.py:53`） | 否（但很多字体画成 emoji 宽） |
| `☑` `☐` | U+2611 / U+2610 | N | 1 | 已完成行（`tasks.py:43`）、子任务（`detail.py:28-29`） | 否 |
| `·` | U+00B7 | **A** | 1 | 状态栏分隔（`app.py:75,77`）、优先级「无」（`view.py:234`） | **是** |
| `─` | U+2500 | **A** | 1 | 所有 `── … ──` 标题（`index.py:87`、`tasks.py:83`、`keys.py:173`） | **是** |
| `│` `╭` `╮` `╰` `╯` | U+2502 / U+256D-2570 | **A** | 1 | 浮层边框（`overlays.py:33` 的 `round`） | **是**（浮层边框错位会很明显） |
| `↑` `↓` | U+2191 / U+2193 | **A** | 1 | 页脚与帮助的键名（`keys.py:105-106`、`textual/keys.py:258-259`） | **是** |
| `⏎` | U+23CE | N | 1 | 页脚 `enter`（`textual/keys.py:264`） | 否 |
| `▁▂▃▄▅▆▇` | U+2581-2587 | **A** | 1 | 滚动条滑块（`textual/scrollbar.py:76`） | **是** |
| `▉▊▋▌▍▎▏` | U+2589-258F | **A** | 1 | 横向滚动条 / toast 左边框 `▌`（`textual/scrollbar.py:78`、`_toast.py:62-64`） | **是** |
| `␣` | U+2423 | N | 1 | **只在 v1 设计记录里**（`docs/ui-mockups.md:40`、`:102`），代码里没有 | 否（未使用） |
| `█` | U+2588 | **A** | 1 | 未使用（Textual 横向滚动条表里有 `▉`） | —— |

### 6.2 有没有测试守字形宽度？

**没有。** 检查过：`grep -rn "east_asian\|eaw\|wcwidth\|cell_len\|width(" tests/ src/dida/tui/` → 零命中；`tests/` 里唯一与「宽度」有关的是滚动测试（`tests/test_pages.py:212-231`、`354-380`，断的是「那一行在不在屏幕上」）与 `test_pages.py:149` 的 `lstrip().startswith("❯")`。仓库文档里也没有宽度约定（`grep -rn "宽度\|全角\|对齐\|cell\|east" docs/ GLOSSARY.md AGENTS.md README.md` → 只有 `docs/ui-mockups.md:110` 那张 v1 取舍表的「最小宽度」列）。

### 6.3 今天已经存在的一处宽度账不对

`☰`（U+2630，真实清单的记号）：Unicode EAW = **N**（多数拉丁字体画 1 格），**rich 15 的表把它算成 2 格**（`rich/_unicode_data/unicode17-0-0.py`，经 `rich/cells.py` 的 `get_character_cell_size('☰') == 2` 验证）。Textual 排版用的是 rich 的 `cell_len`，所以：

- Textual 认为 `  ☰ 工作  2` 占 **12 格**（`cell_len` 实测 12，`len()` 只有 9），而 `  ▸ 今天  4` 是 11 格；
- 在把 `☰` 画成 1 格的终端里，这一行剩下的字符整体**左移 1 格**（行尾填充多出 1 格）；在把 `☰` 画成 2 格的 CJK 字体里则按 Textual 的计划对齐。
- 今天看不出来是因为**这一页根本没有列对齐**（条数是 `名称 + 两个空格 + 数字`，跟着名字长度走，`index.py:74-75`）；一旦重设计做真正的列（截止时间列、条数列右对齐），`☰` 这一格差就会变成肉眼可见的错列。

同理，`help_body()` 用 `ljust(width)`（`keys.py:172-174`）**按字符数**补空格而不是显示宽度：今天键名里最长的 `j / ↓` 恰好每个字形都是 1 格所以没事；**任何新加的 2 格字形（`→`、`⏎` 之外的 CJK 字符、emoji）都会让帮助表错位**。

---

## 7. 意外发现（以及「为什么重设计会很贵」）

### 7.1 一栏 ≠ 不折行：60 列时长标题把光标标记挤到单独一行

`CursorPage` 的行文本没有任何 `text-wrap` / 截断设置（默认 `wrap`，`text-overflow: fold`），`#page-body` 是一个 `Static`（`base.py:76`）。60×20 实测（`tasks-work`，38 个汉字的长标题）：

```
  ── 工作 ──
❯
一条非常长的任务标题用来看看这一栏在窄终端里到底会不会被截断
或者折行显示
  季度报告
```

三个后果：(1) `❯` 与标题分家；(2) 一行占 3 个屏幕行；(3) `scroll_cursor_into_view()` 把「第几个 Row」当成「第几屏幕行」（`base.py:150-155` 的 `_line_of_cursor()` 返回的是 `self._rows` 的下标），行一折行，滚动位置就算错。spec 用户故事 118 说「一栏的布局天然不需要降级」——**折行说明它需要**。

### 7.2 详细页没有光标（三层里唯一一层）

`detail.py:97-98` 造的行全是 `Row(id=None, …)`，光标只在 `row.id == selected_id` 时出现（`base.py:141-147`），所以这一页永远不画 `❯`、也不反色。`_show()` 仍然 `focus()` 它（`app.py:205`），`j`/`k` 落在 `CursorPage._move` 上但因为 `selectable` 为空直接返回（`base.py:122-124`）。屏幕上这一层看起来就是「一列静态文字」。

### 7.3 一处「看起来像样式、其实是死代码」的地方

`MessageOverlay.TITLE = ""`（`overlays.py:51`）→ `box.border_title = ""`（`overlays.py:67`）。帮助浮层因此永远没有标题（确认框有）。这不是 bug，但重设计时会让人以为「标题没写对」。

### 7.4 同一个「外观」被决定在四个地方

1. **`DidaApp.CSS`**（`app.py:94-102`）：页面高度、状态栏颜色；
2. **`BOX_CSS`**（`overlays.py:24-41`）：浮层全部外观（并且靠 `.format(name=…)` 复制给两个类）；
3. **散在 4 个页面文件里的 ~20 处 `Text(style=…)` / `stylize`**（`base.py:33/56/76/135/146`、`index.py:56/75/77/87`、`tasks.py:25/43/83`、`detail.py:36/37/45/97`）——**行的样子是拼字符串拼出来的，不是 CSS**；
4. **Textual 主题**（`textual-dark`）：背景、页脚、滚动条、遮罩、以及所有默认前景色。

外加 `keys.py:172-174` 的**排版算术**（帮助表的 `ljust`）和 `messages.py` 的全部文案。一次「换皮」必须同时改这五处；而且第 3 处是**逐页重复**的（三个页面各写各的 `Text`），没有任何共享的「行样式」层——`Row`（`base.py:37-46`）只带 `id` 与 `Text`，连「这一行是什么种类」都没有。

### 7.5 `screen_styled_text` 是测试资产里唯一的样式接缝，而且只有一个测试用它

`tests/support.py:17-24`（`strip.render(app.console)`）；唯一使用者是 `tests/test_app_actions.py:55-60`，而它**故意只比「这一段与旁边一样不一样」**（`test_app_actions.py:119-121` 的注释：「颜色系统（真彩 / 16 色）、主题都会影响那串数字」）。所以：**改配色不会弄红任何测试，改「有没有高亮」会**（`test_app_actions.py:98-100`、`103-113`）。

### 7.6 同一个词在两种语境里是两个颜色（会咬人）

- CSS 里写 `color: yellow` → Textual `Color.parse("yellow")` = **`#FFFF00`**（CSS 具名色，`textual/color.py:562-564`）；
- Rich 样式里写 `style="yellow"` → `rich.style.Style.parse("yellow")` = **ANSI 3**（`rich/color.py`），经 Textual 的 ANSI→truecolor 滤镜变成 Monokai 的 `(253,151,31)`；
- CSS 里写 `color: ansi_yellow` → ANSI 3，**和 Rich 的 `yellow` 同一个结果**。

也就是说：`PENDING_STYLE = "bold yellow"`（`app.py:54`，Rich 语境）**等于** CSS 的 `ansi_yellow`，**不等于** CSS 的 `yellow`。仓库里这两种语境混着用（Rich 样式字符串 8 处 + CSS 3 处）。

### 7.7 浮层与状态栏的消息是普通 `str`，会被当 Rich 标记解析

`Static` 默认 `markup=True`（`textual/widgets/_static.py:38`），而 `MessageOverlay.body`（`overlays.py:60-68`）、`ConfirmOverlay.prompt`（`overlays.py:95-104`）、状态栏的 `_write_status`（`app.py:265-278`）都收 `str`。今天喂进去的字符串里没有 `[`，但 `delete_prompt(title)`（`messages.py:130-138`）与 `no_browser_message(url)`（`messages.py:120-127`）都**带用户数据**：一个叫 `[red]急[/]` 的任务、或者一条含 `[...]` 的 URL，会在这里被解释成样式标记（不是安全问题，是显示问题）。行文本走 `Text(...)`（不解析标记）所以只有这两类会中。

### 7.8 已经存在但没有被画出来的数据

- `ListRow.color`（服务端每条清单的颜色，`src/dida/sync/read.py:137-138`；`tests/test_read_model.py:120` 用 `#FF6161` 举例）——`pages/index.py:71-78` **不读它**。spec 用户故事 15/16 要「挑一个颜色」，而颜色一旦画出来就是**运行时的 hex**（测试扫源码文本，扫不到 `f"#{row.color}"`）——这是规矩与实际需求的一个真空地带。
- `TaskItem.priority_mark` / `due_text` / `tags_text` / `repeat_flag` / `reminders` / `subtasks`（`read.py:196-224`）——引擎全都算好了，任务列表页一个都没画（`tasks.py:33-38`）。
- 逾期（§3.5）与「今天/最近七天/所有」的分组（`GroupKind`，`view.py:47-60`）只在 v1 的 `view()` 形状里，v2 的 `tasks_in()` 不分组。

### 7.9 数字上的几个小事

- 状态栏在**页脚上面**（compose 顺序 `app.py:142-147`：三层页面 → `Footer`（`dock: bottom`）→ `StatusBar`（最后一个流式子元素，`height: 1`）），所以底部有两行：先是状态栏，再是键位提示。
- 光标反色**不到行尾**（§1.2 第 5 行）：`line.stylize("reverse")` 只作用于 `Text` 的字符，尾部填充由 `Static` 的背景补（`base.py:143-147`）。想要整行高亮必须改 `#page-body` 的样式或换控件。
- 滚动条占最右 **2 列**（`scrollbar-size-vertical: 2`，`textual/widget.py:299`），内容超屏时会压缩每一行的可用宽度——`scroll_cursor_into_view` 用的是 `self.size.height`（`base.py:168`），没算横向的滚动条，但因为行是软的所以看不出来。
- `?` 帮助的宽度是 `max(len(key)) + 2`（`keys.py:172`），**只有一层**的表；层名抬头来自 `LAYER_TITLES`（`keys.py:156-160`）。

---

## 附录 A：没建立起来的东西（以及我查了什么）

| 问题 | 我查了什么 | 结论 |
|---|---|---|
| toast 在 `run_test()` 里为什么没挂上 DOM | `app.notify()` + 6 次 `pilot.pause()` + `app.query("Toast")` / `"ToastRack"`；读了 `textual/screen.py:1163-1164` 与 `textual/app.py:4595-4613` | **not established**。真实 pty 跑得出来（§4.7），`run_test` 里没看到——可能是测试模式下的时序/`_disable_notifications`，我没有继续挖 |
| `build_app()` 真身跑一遍是什么样 | 读了 `bootstrap.py:169-188`、`:230-246` | **没有跑**：`build_app()` 会 `load_config()` 读用户的 `config.toml`（里面有真 token），`refresh_on_start` 会真的打滴答 API。**故意不跑**（规则：绝不碰用户账号）。pty 那一遍用的是 `DidaApp(FakeBackend)`，终端能力检测走的是同一条路（同一个 Textual 驱动、同样的 `Console`），但**不是** `build_app()` |
| Windows Terminal / WezTerm / tmux 上的实际表现 | 只在本机 pty 上测了 `TERM`/`COLORTERM` 的几种组合 | **not established**（本机没有这些终端）。能说的是：发出的字节只依赖 `COLORTERM`/`TERM`（§4.8）与终端是否应答那两个查询 |
| 用户自己的终端是哪一种、字体把 `☰ ★ ▣` 画几格 | —— | **not established**。§6 给的是「rich 算几格 / Unicode 说几格」，不是「你的终端画几格」 |
| `NO_COLOR` 之外的 `TEXTUAL_FILTERS`（`dim` 滤镜等）在本仓库有没有被用到 | `textual/constants.py:119`、`textual/app.py:618-621` | 代码里没有设置它；环境里也没有（`env \| grep '^TEXTUAL_'` 为空） |
| 全屏截图/像素级对照 | —— | 没有做；本文件的证据是逐行 ANSI 文本，不是图片 |

## 附录 B：复现方式（脚本在 `/tmp/style_audit/`，不在仓库里）

| 脚本 | 干什么 | 关键调用 |
|---|---|---|
| `fakes.py` | 编造的账号（收集箱 4 条含 1 条已完成、工作清单 2 条含长标题、NOTE 清单、只读清单、空清单、1 个自建视图、`pending_count=2`） | `FakeBackend(clock=ManualClock(T0))` |
| `render.py` | 18 张渲染（清单列表 80/120、任务列表 80/120、工作清单 60/80/120、详情页、`?` 帮助 80/120、确认浮层），带样式与纯文本两份 | `DidaApp(backend())` + `app.run_test(size=…)` + `support.screen_styled_text` |
| `capability.py` | 强制 `TEXTUAL_COLOR_SYSTEM`（`auto` / `standard` / `256`）看同一屏的字节 | 必须在 import textual **之前**设环境变量（`textual/constants.py:149` 是 `Final`） |
| `ansi_theme.py` | `TEXTUAL_THEME=ansi-dark` 的原生 ANSI 模式 | 同上 |
| `pty_run.py` / `pty_seq.py` / `pty_modes.py` | 真实 pty 跑同一个 app，统计 SGR 与终端控制序列 | `pty.fork()` + `TIOCSWINSZ` + `TEXTUAL_PRESS=q` |
| `pty_toast.py` | pty 里触发 `app.notify()` 并抓屏 | 子类在 `on_mount` 里 `set_timer` |
| `styles_probe.py` | 每个控件的 `styles.get_rules()`、`theme_variables`、ansi 调色板、浮层计算样式 | `app.query_one(sel).styles.get_rules()` |
| `glyphs.py` | 屏幕上每个非 ASCII 字形的 EAW 与 rich 宽度 | `unicodedata.east_asian_width` + `rich.cells.get_character_cell_size` |
| `repo-copy/` | 仓库的**副本**（不含 `.venv`/`.git`），用来验证 hex 守卫会怎么失败——**没有动真仓库** | `pytest tests/test_architecture.py` |

复现一条命令：

```bash
cd /home/tofu/dida-v2-worktrees/integration
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor .venv/bin/python /tmp/style_audit/render.py
```
