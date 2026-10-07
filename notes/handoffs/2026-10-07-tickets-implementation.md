# Handoff — Dida TUI：实现 #62–#66（会话交接）

**写给下一个 agent。** 上一轮的交接在 `/tmp/handoff-dida-tui-input-model.md`（设计背景、物理状态、用户偏好、不要做的事）**仍然有效，先读它**。本文件只补这一轮新产生的、别处没有的东西：切票结果、我实测到的几处纠正、以及落地时要当心的地方。设计内容、行号、用户故事、验收标准**不在这里重复**——见 #61、ADR-0008 与五张票。

## 先读这些

| 产物 | 位置 | 说明 |
|---|---|---|
| 实现 spec | <https://github.com/Miaotofu01/Dida-TUI/issues/61> | 40 条用户故事、实现决定、测试决定、out of scope、验收标准 |
| 设计记录 ADR-0008 | `/home/tofu/dida-v2-worktrees/integration/docs/adr/0008-arrows-replace-enter-and-escape.md` | **仍未提交**（工作区里唯一未跟踪文件）；逐处文件与行号、实测探针、已知风险 |
| 五张实现票 | #62 #63 #64 #65 #66（都是 #61 的 sub-issue，都带 `ready-for-agent`） | **全部无阻塞**，frontier 一开始就是这五张 |
| 上一轮交接 | `/tmp/handoff-dida-tui-input-model.md` | 物理状态、用户偏好、不要做的事 |

五张票是这一轮 `/to-tickets` 切出来的，标题与验收标准在票里，别把 #61 或 ADR-0008 的内容抄成第三份文档。

## 这一轮实际做了什么（别重做）

- 只读代码与产物：**一行代码没改，什么都没提交**。ADR-0008 依旧是未跟踪状态。
- `gh issue create` 建了 #62–#66，都打了 `ready-for-agent`，并用 sub-issues API 挂到 #61 下面（都已验证：`blocked_by=0`、label 正确）。
- #61 本身没动（仍 OPEN、label 不变）——按 skill 要求，没有关也没有改父票。
- 票的正文与 `/tmp/ticket-0{1..5}-body.md` 逐字相同（那是发布时用的暂存文件，已发出去，可忽略或删掉）。

## 实测纠正（上一轮交接与 ADR 里说得不准的地方）

1. **`uv run pytest` 不是「约一分钟」。** 我在 `feat/v2-terminal-client` tip 上实跑：**862 passed in 241.82s（约 4 分钟）**，全绿。别因为跑了 90 秒还没完就以为它挂了。
2. **#59 已经修好了。** `Stage` 的 `#stage { overflow-x: hidden }` 已在分支上，所以「清单列表页按 `←` 什么都不发生」**不需要额外交互代码**——那一个键在页面与轨道上都没有绑定，Textual 自己就 SkipAction。同理 #60（屏外页面被 tab 聚焦）也已经修在 `CursorPage.allow_focus` 上。别把这两条当成 #65 的额外工作。
3. **ADR 里那 6 条测试的简称与真名不一致。** 真名是 `test_escape_cancels_the_form_without_writing_anything` / `test_escape_cancels_without_writing_anything` / `test_escape_closes_the_picker_without_writing_anything` / `test_escape_on_the_question_writes_nothing_at_all` / `test_escape_cancels_the_view_form_without_writing_anything` / `test_escape_closes_the_tag_picker_while_the_multi_select_has_focus`。搜 `without_writing` / `writes_nothing` 比搜 ADR 里那句简写管用。
4. **`☑` 已经有了**（`theme.DONE_MARK = "☑"`，`completed_line` 一直在用）。#63 真正要新增的只有 `☐`（未完成那一半）与它的宽度守卫条目。
5. **#65 有一处票里没展开的耦合（我读代码确认的）**：详细页的 `action_back` 现在是一个方法兼两个意思——编辑中「结束编辑（保存）」、字段列表上「退回任务列表页」，两条都绑在同一个 `escape` 上。按新规矩 `esc` 只能留前一半、`←` 才是退回，所以 #65 必须把这条路拆成两个动作；拆的时候**别把「结束编辑=保存」弄丢**（那是用户故事 62 的现行规矩）。这也是我把「导航键」与「编辑态 esc」切成两张票（#65 / #66）的原因：#66 只管表单浮层，内联编辑器的这一半归 #65。

## 落地时的现实约束

- **活跃分支在别的 worktree**：`feat/v2-terminal-client` 在 `/home/tofu/dida-v2-worktrees/integration`。要改代码去那儿。
- 会话 cwd `/home/tofu/我的项目/Tips` 停在 `docs/v2-domain-model`，**那里的 `src/` 是 v1**，别在那儿改。
- **#62 必须先落地（或至少先 `git add`/commit ADR-0008）**：ADR-0008 现在是未跟踪文件，只有它提交进分支，#61 与 #63–#66 里指向它的链接才通。票 #62 的验收标准里写了这一条。
- 并行跑票时的**文件重叠**（不是依赖，是合并面）：#63 与 #64 都碰 `tests/test_task_row_model.py`；#65 与 #66 都少量碰 `src/dida/tui/app.py`（#65 是导航/动作与 docstring，#66 是表单回调那一小段）。按票的粒度它们可以并行；真要并行开工，让一个人先合、后一个人 rebase。
- 用户偏好没变：**别问鸡毛蒜皮**（市面软件有共识的键位照通行做法定，写明「这是照市面做法定的」）；**已经定过的事不要问第二遍**（顶栏键位提示、把「光标」写进术语表、`?` 与 `h` 并存，都被否决过）；不要给 `ctrl+enter` 绑定。
- `notes/terminal-input-evidence.md` 在这条分支上**不存在**，别去找。

## 建议下一个 agent 调用这些 skill

- **`tdd`** —— 主路径。五张票的验收标准都是可逐条变红的清单；先读 #61 的「Testing Decisions」（两个接缝、先例文件、要改写的既有测试），再开工。
- **`domain-modeling`** —— #62 里 ADR-0007 第三条的状态注记、#65 里 `GLOSSARY.md` 的「导航路径」键名与那批 docstring。这两处是「文档现在写的是假话」。
- **`code-review`** —— 改动落完后照 #58 那条路再跑一次两轴复核（Standards + Spec）。
- **`pr`** —— 分支工作准备上交时写 PR body。
