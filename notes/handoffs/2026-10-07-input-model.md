# Handoff — Dida TUI v2 输入模型改版（会话交接）

**写给下一个 agent。** 本文件只记「去哪儿看」与「这次没写进任何产物里的东西」。设计内容、行号、实测与验收标准**不在这里重复**，见下面两个产物。

## 先读这两个

| 产物 | 位置 | 状态 |
|---|---|---|
| 设计记录 ADR-0008 | `/home/tofu/dida-v2-worktrees/integration/docs/adr/0008-arrows-replace-enter-and-escape.md` | **已写好，未提交**（工作区里是唯一未跟踪文件） |
| 实现 spec（本次新开） | <https://github.com/Miaotofu01/Dida-TUI/issues/61> | 已发布，标了 `ready-for-agent`；用户故事接 #30 的编号，122–160 |

ADR-0008 里有：四条决定的取舍、要删/要改的逐处文件与行号、被实测推翻的旧自述、已知风险、以及「同一轮谈定但不属于该 ADR 的两条」。**#61 里有：问题陈述、解决方案、40 条用户故事、实现决定、测试决定、超范围、验收标准。** 两者不重复，别把其中一份的内容抄进另一份。

## 现在这个仓库的物理状态（最容易踩的地方）

- **活跃分支在别的 worktree。** 用户实际在跑的是 `feat/v2-terminal-client`，worktree 在 `/home/tofu/dida-v2-worktrees/integration`（`.venv/bin/dida`，会话当时有两个实例还开着）。**要改代码就去那个目录。**
- 会话的 cwd `/home/tofu/我的项目/Tips` 停在 `docs/v2-domain-model`：**只有文档，`src/` 还是 v1 的三栏「今日执行台」**。在那个目录里改 `src/` 等于改错东西。
- 还有两个相邻 worktree：`/home/tofu/dida-v2-worktrees/proto-style`（`prototype/v2-visual-style`，视觉原型）与 `/home/tofu/dida-v2-worktrees/t59`。别去动。
- 本次**只新增了 ADR-0008 一个文件，一行代码没改，什么都没提交**。代码改动是下一件事。
- 注意：仓库文档与代码注释里多处引用 `notes/terminal-input-evidence.md`（终端的实测证据），但 **`notes/` 目录在 `feat/v2-terminal-client` 上并不存在**，那条路径找不到文件——别花时间去找它。

## 怎么复现那个「方块」bug（下次要验证修复时用）

临时探针文件已经删掉了（当时按约定清干净了）。复现方法在 ADR-0008 第四条里写着，要点是：真 `DidaApp` + `FakeBackend` + `run_test()`，**必须传 `animations="on"`**（默认 `auto` 在测试环境下会把动效关掉，就复现不出来），然后逐帧读 `screen_text`。结论是上下移动光标时**两行依次整行空白**约 120ms，不是「两格」也不是字形问题。

**这条不需要真 pty。** ADR-0007 里那句「`run_test()` 下不会挂载」说的是 toast；光标条实测会跑。别被那句话劝退。

## 用户的偏好（这次会话里最影响协作的两点）

1. **别问鸡毛蒜皮。** 用户明确表示过：编辑态的键位这种市面软件有共识的事，应该照通行做法定下来、并写明「这是照市面做法定的」，而不是拿去问他。设计树上前几轮问得很细是必要的（那些是会改变代码结构的取舍），到「帮助浮层里列哪一层的键」这种就被顶了回来。
2. **已经定过的事不要再问第二遍。** 他明确否决过的：顶栏加键位提示、把「光标」写进术语表、`?` 与 `h` 并存。理由都记在 ADR-0008 里（顶栏那条记在「已知风险」）。

## 还没做、且已经在产物里列好的

- ADR-0007 第三条的状态注记（指到 ADR-0008），以及 `GLOSSARY.md` 里「导航路径」那句「由 `enter` 压入、`esc` 弹出」的键名。按用户指示这次都没动，列在 ADR-0008 的 Consequences 里。
- 键位改动会牵动一批写着 `enter`/`esc` 的 docstring，以及 6 条「escape cancels … without writing anything」的测试——ADR-0008 逐处列了，`#61` 的「Testing Decisions」说了它们该改成什么。
- `#61` 的 `Further Notes` 里给了一个落地顺序建议（先删光标条 → 再改键位表与帮助 → 最后任务行与已完成段）。

## 建议下一个 agent 调用这些 skill

- **`tdd`** —— 主路径。#61 的验收标准是一份可逐条变红的清单，仓库也有现成的两类测试接缝与大量先例（`#61` 的「Testing Decisions」点名了先例文件）。**先读 #61 再动手，尤其那 6 条要改语义的测试。**
- **`domain-modeling`** —— 收尾时必须用：ADR-0007 第三条要加状态注记、`GLOSSARY.md` 的「导航路径」要改键名。这两处是「文档现在写的是假话」，属于这个 skill 的射程。
- **`code-review`** —— 这个仓库在 v2 分支 tip 上跑过两轴复核（Standards + Spec，见 issue #58 的由来），改动落完建议照同一条路再跑一次。
- **`pr`** —— 分支工作准备上交时写 PR body 用。

## 不要做的事

- 不要在 `/home/tofu/我的项目/Tips` 里改 `src/`（那是 v1）。
- 不要再问用户本轮已经定过的四件事（见 `#61` 的「Out of Scope」一节，那里逐条列了明确不做的东西）。
- 不要给 `ctrl+enter` 绑定：ADR-0006 的实测与两条守卫测试会挡下来；`#61` 与 ADR-0008 都写了理由。
- 不要把 ADR-0008 或 `#61` 的内容抄成第三份文档——两份已经互相引用好了。
