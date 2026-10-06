# `date_parser` 的消费者清单（#32 收尾说明，交给 #34 用）

v2 整体作废日期解析器（spec #30：「整体作废：日期解析器」；「新建只填标题，改截止时间走
结构化选择器」）。这份清单是**删除时的核对清单**：一个漏掉的 import 会让 `dida` 起不来。

数据在三处：**生产调用点**（第 1 节）、**测试覆盖**（第 2 节）、**删的时候按什么顺序**（第 3 节）。
行号是本分支（#32 完成时）的行号：`src/dida/sync/schedule.py` 与 `src/dida/tui/form_actions.py`
是 #32 拆模块之后的落点，v1 的 `sync/engine.py:845-874` / `tui/app.py:513-585` 那几处已经
搬进它们里了。

## 1. 生产调用点（每一个，逐个核实过）

| # | 文件:行 | 它消费什么 | 删法 |
|---|---|---|---|
| 1 | `src/dida/sync/schedule.py:18` | `from dida.date_parser import ParsedTask, parse` | **全仓库唯一一处 import `parse`**：删掉它 |
| 2 | `src/dida/sync/schedule.py:50-60` | `ScheduleMixin.plan(text)` → `parse(text, self._clock.now(), self._day_end)`（`:60`） | **全仓库唯一一处调用 `parse()`**：整个方法删掉 |
| 3 | `src/dida/sync/engine.py:57` | `from dida.date_parser import ParsedTask`（只作标注） | 删掉这一行 import |
| 4 | `src/dida/sync/engine.py:207` | `Engine.plan(self, text) -> ParsedTask`（协议成员） | 删掉这个成员（与 #6 一起删，见第 3 节） |
| 5 | `src/dida/testing.py:24` | `from dida.date_parser import ParsedTask`（只作标注） | 删掉这一行 import |
| 6 | `src/dida/testing.py:291-293` | `FakeBackend.plan()` → `self._engine.plan(text)` | 删掉这个方法 |
| 7 | `src/dida/tui/form_actions.py:66` | 改期框：`parsed = self.engine.plan(event.text)`，随后读 `diagnostics` / `due` / `all_day` | #44 的结构化选择器替掉这一段 |
| 8 | `src/dida/tui/form_actions.py:104` | 新建框：`parsed = self.engine.plan(event.text)`，随后读 `diagnostics` / `title` / `due` / `all_day` / `priority` / `tags` | #39 的「只填标题」替掉这一段 |

**只有文档/注释提到它、没有代码依赖**（删解析器时顺手改字，不删会误导后来人）：

- `src/dida/sync/schedule.py:6`（模块 docstring）、`:54`、`:65`、`:73`（`plan()` 与 `reschedule()` 的 docstring）
- `src/dida/sync/create.py:45`（`create()` docstring 里说「拦它的是调用方：`plan()` 的 `diagnostics`」）
- `src/dida/sync/view.py:235`（`next_priority` docstring 说「`!5` 那种写法仍然是诊断」）
- `src/dida/tui/panes.py:503`、`:598`（两个输入框控件的注释：「语法归 `dida.date_parser`」）
- `src/dida/sync/engine.py` 模块 docstring 的公开面列表里那一行 `plan(text)`

**没有别的消费者**：#32 拆 `tui/app.py` 时新建的 `dida/tui/messages.py` / `keys.py` /
`layout.py` / `task_actions.py` / `pane_actions.py` 都不碰它；`dida/api/guards.py` 里那个
`api_date` 是**另一件事**（日期格式校验，不是自然语言解析），它必须留着。

## 2. 测试覆盖（实测，逐个跑出来的）

量法：把 `dida.sync.schedule.parse` 换成一个会抛异常的替身，跑整套测试，数红掉的用例
（被炸到 = 这个用例真的走到了解析器）。这样数出来的比按行号读准。

| 文件 | 收集到的用例 | 走到解析器的 | 说明 |
|---|---|---|---|
| `tests/test_date_parser.py` | 75 | **75** | 解析器自己的文件，整份删 |
| `tests/test_architecture.py` | 16 | **1** | `:31` 的 `SEVEN_MODULES` 条目 → `test_module_is_importable[dida.date_parser]` 那一格。**删解析器时这一行也必须删**，不然它当场红（它正是防漏的那道闸） |
| `tests/test_quick_add.py` | 16 | **14** | 3 条直接调 `engine.plan()`（`:98`、`:185`、`:209`），11 条把速记打进新建框 |
| `tests/test_reschedule.py` | 22 | **15** | 全部是把速记打进改期框（含 `:305` 那张语法表 7 格、`:338` 的诊断 4 格） |
| `tests/test_sync_session.py` | 20 | **1** | `test_offline_still_reads_the_cache_and_queues_writes`（`:307` 往新建框里打「买牛奶」） |
| **合计** | | **106** | 5 个文件 |

两点与 `notes/codebase-map.md` §6 不一致，**以这份为准**（那个数是估算，这里是跑出来的）：

1. map 说「84 条测试、4 个文件」。实测是 **106 条、5 个文件**——它漏了
   `test_sync_session.py` 那一条（那一条的名字看不出跟解析器有关），而 `test_quick_add` /
   `test_reschedule` 的 UI 用例也比 map 数的多（它按「类型别写法」粗数，这里按「真的走到了
   `parse()`」实测）。
2. 反过来，**#32 搬出来的那批测试一条都不碰解析器**：`tests/test_engine_writes.py` 里
   新建与改期的期望值是测试直接给的（`due=at(14, 0, 0), all_day=True`），不绕 `plan()`。
   所以 #34 删掉上面 5 个文件之后，测试数不会因为解析器再掉一轮。

## 3. 删的时候按这个顺序

1. **`testing.py` 与 `engine.py` 的 `plan` 一起删**（第 6、4 条）。只删一边的话
   `tests/test_fake_backend.py::test_fake_backend_satisfies_the_engine_interface`
   （`isinstance(FakeBackend(...), Engine)`）会红着告诉你还差哪边——这是故意留的闸，
   不要绕过它。
2. `schedule.py`：删 import（`:18`）与 `plan()`（`:50-60`），顺手改写 `:6`/`:54`/`:65`/`:73`
   那几处 docstring。改期本身的写路径（`reschedule()`）不依赖解析器，留着。#44 只换掉
   「日期从哪来」。
3. `form_actions.py`：两个 `self.engine.plan(...)` 调用点（`:66`、`:104`）与随后的
   `diagnostics` 分支。这一段由 #39（新建只填标题）与 #44（结构化选择器）分别重写；
   #34 若先落地，就把整块拆掉、把那两个键接到新页面上。
4. `tests/test_architecture.py:31`：删掉 `"dida.date_parser"` 那一行。
5. 删 `tests/test_date_parser.py` 整份；`test_quick_add.py` / `test_reschedule.py` /
   `test_sync_session.py` 按 #34 的界面重写一起走（它们本来就在那 203 条里）。
6. 删 `src/dida/date_parser.py` 本体。

**删完自查一条命令**（应该什么都不剩，除了解释性文字）：

```bash
grep -rn "date_parser\|ParsedTask\|\.plan(" src/ tests/
```

再跑 `uv run pytest`：`test_architecture.py` 里那条 `SEVEN_MODULES` 守卫与
`test_fake_backend.py` 那条协议守卫会分别在「名单没删」与「假后端没跟上」时当场红。
