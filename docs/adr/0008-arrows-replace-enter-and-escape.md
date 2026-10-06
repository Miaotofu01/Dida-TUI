# 输入模型：`←` / `→` 取代 `enter` / `esc`，编辑态用 `esc` 保存，光标条撤掉

v2 的键位一直是「`enter` 向下、`esc` 向上」（[ADR-0004](0004-terminal-client-not-today-console.md) 的一栏三层），`?` 出键位表，光标另有一条会追赶的装饰条（[ADR-0007](0007-visual-identity-follows-terminal-theme.md) 三）。真实使用后用户要求改三件事，它们其实是同一个面——**键盘怎么说话、屏幕怎么回应**：导航改用方向键（`→` 进、`←` 退），编辑态改用 `esc` 保存并退出，`h` 取代 `?`；同时把那条装饰光标条撤掉。本 ADR 记这四条的取舍与实测依据。**它推翻 ADR-0007 第三条里「装饰性光标条」那一半**，第三条里其余几项（横向平移、阈值之后的转圈、`auto|on|off` 开关）不受影响。

## 一、导航：`→` 进入、`←` 退回，`enter` / `esc` 在这三页解绑

直接替换，不是别名：「退回只有一个键」。这两条不违反键位能力守卫——`left` / `right` 本来就在「终端一定送得上来的键」那一批里（`tests/test_keymap.py::test_the_keys_the_terminal_does_deliver_are_not_flagged` 逐个点过名）。

**要记住的是：这两个键在本 app 里被占过，不是空的。** 浮层的 `ChoiceField`（清单 / 优先级 / 截止三个**单档**挑选器）绑 `left` = 上一档、`right` = 下一档（`tui/overlays.py:146-147`），界面上写着「选择框用左右方向键」（`tui/overlays.py:68`）。

**决定：挑选器保持原义。** 挑选项算「编辑」，不算「导航」；导航页的 `←` / `→` 与表单里的 `←` / `→` 靠**焦点**分开。文本框里更不用管：`←` / `→` 天然归 `Input` 自己（Textual 原生 `cursor_left` / `cursor_right`，且焦点控件的绑定先于 app 绑定），所以 app 层绑上去也抢不到。

## 二、编辑态：`esc` = 保存并退出，**没有「取消」**

这条不是新发明，是把**表单浮层**对齐到**详细页内联编辑器**早就走的规矩。`DetailPage.action_back`（`tui/pages/detail.py:1002`）的 docstring 写着：三种编辑器同一个含义，「自由文本框把文字交出去，截止时间把两格草稿交出去——**没有「取消」**（用户故事 62）」。反向的那一半是浮层：`FormOverlay` 的 `escape` → `action_cancel` → `dismiss(None)`（`tui/overlays.py:354`、`:398`）。

代价与缓解都要说清：

- **代价**：想放弃一段刚打的字，只能自己改回去。
- **缓解是真的**：写操作只写变化的（ADR-0001 的本地比对），没动过的字段不会产生一次写；新建表单有「没写标题」的守卫（`tui/messages.py:35` 的 `NO_TITLE_MESSAGE`，用在 `tui/app.py:664`）。所以误按 `esc` 落地的大多是一次空写而不是一次坏写。
- **会红的测试有六条**，名字全是「escape cancels … without writing anything」：`tests/test_list_overlay.py:134`、`tests/test_create_task.py:183`、`tests/test_picker_fields.py:395` 与 `:642`、`tests/test_view_overlay.py:151` 与 `:309`。
- 文本框里 `enter` **保留**为同一个结果（`Input` 原生把它绑给 `submit`，`FormOverlay.on_input_submitted` 收的正是它）：两个键同一个结果，不去改输入框。

**确认框是例外，而且必须例外。** `ConfirmOverlay`（删除与退出共用，`tui/overlays.py:455`）的 `esc` 保持「没做」：它问的两件事都不可挽回（API 里没有 undelete、没有回收站；待推送改动只活在本地库里），所以默认答案永远是「没做」。于是「一个键在整个程序里只有一个意思」这条原则在这里**让了一步**：`esc` 在编辑器里是保存、在确认框里是没做。理由就是上面那句——一处是「改了什么」，一处是「删掉/退出」。

`ctrl+enter` 不能当保存键：ADR-0006 关掉 kitty 协议推送之后它收不到（大多终端把它发成与 `enter` 同一个字节 `0x0D`），`tui/keys.py:152-158` 的 `unreliable_reason()` 一处判定，`tests/test_keymap.py` 拿它断「键位表与帮助里没有这种键」。

## 三、`h` 取代 `?`

`?` 解绑，`h` 是唯一入口。帮助仍是**三张**（三个导航页各一张，「只列当前这一层可用的键」,用户故事 119）——**不新增「模式」这个维度**：编辑态不进 `h` 的键位表，`h` 也不绑在浮层上。理由是模态表单自带一行键位提示（就是底下那行 `FORM_HINT`，`tui/overlays.py:68`），而浮层里去看那张按层的表只会看到**错的**内容（编辑态的 `←` / `→` / `esc` 与页面上的不是一套意思）——一张会骗人的表不如没有。这一条是照市面做法定的，不是用户逐条确认过的。

帮助里的键名仍只写**宽度不含糊**的拼法：`←` / `→` 是东亚歧义宽度（rich 量 1 格、CJK 字体下终端可能画 2 格），所以照 `down` / `up` 的旧例写成 ASCII 的 `left` / `right`，`KEY_NAMES` 不给它们条目（查不到就原样显示键名）。`?` 解绑之后 `KEY_NAMES` 里的 `question_mark` 一并摘掉。

## 四、光标条撤掉

ADR-0007 第三条写的是「光标 = 选中立即到位，**装饰性的光标条随后追上**」，`_flash_bar` 的自述更具体：「飞行中它会盖住它经过的那两格——**那正是它要的效果**」。**实测推翻了这句话。**

探针（integration 工作树，`DidaApp(fake_backend, animations="on")`，100×30，任务列表页，逐帧读 `screen_text`）：

```
BEFORE            ❯ . 交周报      . 写周报       . 读论文
t=0ms                 . 交周报   ❯ . 写周报       . 读论文    ← 选中瞬间到位（设计如此）
+10 / +20ms        ⟨整行空白⟩   ❯ . 写周报       . 读论文    ← 条子压在旧行上
+40ms                 . 交周报   ⟨整行空白⟩       . 读论文    ← 条子压在新行上
+80ms                 . 交周报   ❯ . 写周报       . 读论文    ← 落地，条子退场
```

盖住的不是两格，是**整行**。机制：那条装饰条是一个 `Static` 控件（`tui/pages/base.py:164`），**控件的宽度是整行**；一个还在画的控件会把底下那一行擦掉，哪怕它只涂两格——`_land_bar` 的注释自己写下了这条实测（`tui/pages/base.py:345`），只是没把结论用到**飞行途中**。于是每一次上下移动，两行依次整行消失——那根条子的时长是 `CURSOR_BAR_MS = 120`（`tui/theme.py:388`），本次取样在 ~80ms 时已经落地。用户报的是「上下切换的瞬间对应行闪出一个方块（`❯` 显示成了方块）」——方块就是它压在新行上时那两格 `on cyan`（`BAR = "on cyan"`，`tui/theme.py:135`）。

**决定：撤掉光标条，换层的横向平移保留，`DIDA_ANIM=off` 仍然管它。** 光标行的反馈回到 ADR-0007 第一条允许的手段：`❯` 与强调色（`theme.SELECTED`），瞬时到位、不动。

## Consequences

- **要删的管道**（只在为这条动效存在）：`CursorPage.bar_pos` / `watch_bar_pos` / `_flash_bar` / `_land_bar` / `#cursor-bar` 与 `_bar()`（`tui/pages/base.py:119`、`:164`、`:235`、`:315-355`，`set_rows` 与 `on_resize` 里的 `_land_bar()` 调用在 `:201` 与 `tui/pages/detail.py:698`）、`CursorPage._motion` 与 `set_animate()`（`:129`、`:176-178`）、`DidaApp` 里给页面下发的那一次（`tui/app.py:359`）、`theme.CURSOR_BAR_MS`（`tui/theme.py:388` 及 `__all__` 的 `:46`）。**`DidaApp._motion` 本身留着**：换层平移还要它（`tui/app.py:355-359`、`:416`）。
- **键位守卫要跟着改的地方**：`?` 解绑后帮助里那一条换成 `h`；`enter` / `esc` 从三个导航页的 `BINDINGS` 里去掉；`left` / `right` 加进导航页（显示成 ASCII）。
- **文档**：`GLOSSARY.md` 的「导航路径」那句「由 `enter` 压入、`esc` 弹出」要改键名（这是本次唯一必须改的术语——「光标」与「选中行」在这个代码库里是同一件事，`_selected_id` 就是键盘动作落的那一行，不另立条目）；`tui/app.py` 与 `tui/keys.py` 里那一批写着 `enter` / `esc` 的 docstring 一起跟上（`tui/app.py:3`、`:122`、`:406-410`、`:435`、`:442`、`:448`、`:453`、`:593`、`:601`、`:605`、`:636`，`tui/keys.py:13-14`、`:113`、`:122`）；`FORM_HINT`（`tui/overlays.py:68`）换成新键。ADR-0007 第三条要加一条状态注记指到本文。
- **同一轮谈定、但不属于本 ADR 的两条**（记在这里免得丢，实现时另开工单）：
  - **任务行**：`☐`（未完成）/ `☑`（已完成）取代现在占着最左那一列的**优先级标记**（`priority_mark()`，`sync/view.py:212`），列表里从此不显示优先级；优先级仍在详细页的字段与挑选器里，排序键也仍然用它。
  - **已完成段**：沉底后按正常排序键（`row_sort_key`，`sync/rows.py:42`）排，不再按完成时刻倒序（`sync/view.py:392` 现在排的是 `(completed_at, title)`）。真实清单与视图同一条规矩——视图那边本来就合规（成员走 `by_due` → `row_sort_key`，「已完成沉底」是第一个元组位）。改法只在 `completed_section()`：先按 `row_sort_key` 排 `TaskSnapshot` 再映射成 `CompletedItem`，后者不必加字段。不加「已完成 N 项」分隔行。
- **已知风险（没有补救）**：`h` 不如 `?` 通用，而 ADR-0007 第四条已经把 Textual 的 `Footer` 删掉（「它显示的键位从此归按层的 `?`」）；两件事叠在一起的结果是**屏幕上没有任何地方提示帮助键存在**。这一轮的起点就是用户问了一个已经做好、却找不到的功能。最省的补救是顶栏加一格 `h 键位`；现在没做，记在这里。
- 这次改动**不碰信息架构**：还是三层页面、一套导航路径，只是压栈/出栈的键换了。
