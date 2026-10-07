# Terminal-input evidence, re-verified in this session

Re-ran the previous session's harness against the current venv (Textual 8.2.8) on
2026-xx, from `/home/tofu/dida-v2-worktrees/integration`. **These are measured, not read from docs.**
Harnesses preserved next to this file: `verify.py` (parser level), `space_test.py` (focus level).

```bash
cd /home/tofu/dida-v2-worktrees/integration
uv run python /home/tofu/dida-v2-worktrees/notes/verify.py
uv run python /home/tofu/dida-v2-worktrees/notes/space_test.py
```

## 1. Why `TEXTUAL_DISABLE_KITTY_KEY=1` is load-bearing (ADR-0006), re-confirmed

`verify.py` section B — an IME commit of **11 CJK characters** as one kitty CSI-u event
(`\x1b[32;;<codepoints>u`, 78 bytes) is **not** decoded. Textual's 32-char threshold makes the parser give
up and re-emit the whole sequence character by character, so the field receives the literal escape string:

```
received: '^[32;;20320:24110:25105:26816:26597:19968:19979:36825:20010:20195:30721:24211u'
correct? False
```

Section B2 — the **same** IME commit with **4** CJK characters (`你好世界`, sequence length 30, under the
cap) decodes correctly. **This is why the bug survives self-testing: short words are fine, long sentences
break.** Any hand-test with 「你好」 will pass and prove nothing.

**Implication for tickets #34 and #43:** the acceptance criterion is "paste/IME a run of **more than four**
CJK characters; what lands in the field is the original text, not an escape sequence". The env var must be
set **before the Textual app starts** (`dida.bootstrap` is the composition root — that is where it belongs,
and it is the same place `PUSH_TICK_SECONDS` has to be rescued from `tui/app.py`).

## 2. The keys the spec forbids, measured (`verify.py` section A)

| Sent | Decoded as | Verdict |
|---|---|---|
| plain Enter (`\r`) | `('enter', '\r')` | fine |
| Alt+Enter (`ESC CR`) | `('enter', '\r')` | **collapses to `enter`** — confirms textual#6663 |
| kitty `ctrl+enter` (`CSI 13;5u`) | `('ctrl+enter', None)` | only works *with* the kitty push — the push we disable |
| xterm `modifyOtherKeys=2` `ctrl+enter` (`CSI 27;5;13~`) | `('ctrl+\r', '\r')` | **not `ctrl+enter`** |
| kitty `shift+enter` | `('shift+enter', None)` | kitty-only |
| `space` | `('space', ' ')` | fine |
| kitty `shift+space` / `ctrl+shift+a` / `alt+left` | kitty-only | unreliable per the spec's key list |

So `ctrl+enter` is reachable **only** through the very protocol we turn off, and `alt+enter` silently
becomes `enter` (which would trigger a *different* action — the exact failure mode the keymap guard in
ticket #48 exists to prevent). `Binding("ctrl+enter", ...)` does **construct** and does fire when such an
event is dispatched (section C) — which is precisely why a test that only checks "the binding exists"
would pass while the real terminal can never deliver the key. **#48's guard must assert absence from the
keymap, not presence of a handler.**

## 3. `space` vs. a focused `Input` (ADR-0006's last bullet)

`space_test.py` builds a real `App` with an app-level `space` binding and a focused `Input`:

- input focused → the character is swallowed by the widget, **app-level `space` never fires**;
- input blurred → the app binding fires.

**Implication:** the completion key must only be bound on the task-list layer, which has no focused input.
Any other layer that binds `space` app-wide will look correct in a test that doesn't focus an input, and
be dead in the real app. #38 and #48 both need this.

## 4. What is *not* covered here

- No real terminal or IME was driven — this is the parser and the widget layer only. A genuine end-to-end IME
  check still has to be done by hand in the user's terminal. Say so in reports rather than implying it was
  verified end to end.
- **⚠ Corrected by `environment-audit.md`:** this section used to claim 「he has tmux / WezTerm /
  gnome-terminal per the handoff」. That is **wrong for two of the three** — measured: **tmux is not installed
  at all** (no binary, no dpkg entry, no config, no server; only `screen`), and **WezTerm is not installed
  either**. The terminal actually running is **kitty 0.32.2**; alacritty and gnome-terminal are installed but
  not running. So the spec's cross-terminal matrix (tmux / WezTerm / gnome-terminal / Windows Terminal) is
  **aspirational, not testable on this machine as stated** — and any claim that a behaviour was checked "in
  tmux" here would be false. `gh api repos/Miaotofu01/Dida-TUI/issues/30`'s story 114 is the source of the
  matrix, and note it asks for consistent **keys**, not consistent colours.
- **⚠ `NO_COLOR=1` is set in agent shells but not in the user's.** Measured in this session: the agent shell
  reports `NO_COLOR=1` and `TERM=dumb`. Any render or colour assertion run without clearing it is
  **monochrome and does not represent what the user sees**. Use `env -u NO_COLOR TERM=xterm-kitty …`.
- The upstream issue (textual#6721) is not fixed as of this measurement. If Textual is upgraded, re-run
  `verify.py` section B before considering removing the env var.
