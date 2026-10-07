# `TEXTUAL_DISABLE_KITTY_KEY`: the flag is frozen at import time, and the obvious test proves nothing

Found and verified in this session against Textual 8.2.8 in the integration worktree. **Ticket #34
("启动时 kitty 键盘协议推送已关闭") and #43 (中文输入) both depend on this.**
Harnesses: `/tmp/kitty-order-test.py`, `/tmp/kitty-recipe.py`, `/tmp/k_*.py` (reproduced inline below).

## Fact 1 — it is read once, at import, into a `Final`

```python
# textual/constants.py:116
DISABLE_KITTY_KEY: Final[bool] = _get_environ_bool("TEXTUAL_DISABLE_KITTY_KEY")
```

Both consumers read the **module attribute at runtime**, so the value is whatever it was when
`textual.constants` was first imported:

- `textual/_xterm_parser.py:424` — `not constants.DISABLE_KITTY_KEY`
- `textual/drivers/linux_driver.py:285` — `if not constants.DISABLE_KITTY_KEY:` … `:292 self.write(f"\x1b[>{KITTY_PROTOCOL_FLAG}u")`

Measured:

```
EXP1  import textual.constants; print(c.DISABLE_KITTY_KEY)              -> False
      os.environ["TEXTUAL_DISABLE_KITTY_KEY"] = "1"                     -> still False
EXP2  os.environ["TEXTUAL_DISABLE_KITTY_KEY"] = "1"; import textual...  -> True
EXP4  import dida.bootstrap; textual.constants.DISABLE_KITTY_KEY        -> False   # today it is never set
```

**So a `os.environ.setdefault(...)` inside `main()` is too late** — by the time `main()` runs, the TUI
module (and therefore `textual`) is already imported. It must be set **before `textual` is first
imported by the process.**

## Fact 2 — where that is, precisely

`bootstrap.py` is the composition root and `dida` / `python -m dida` both enter through it
(`pyproject.toml: [project.scripts] dida = "dida.bootstrap:main"`, `src/dida/__main__.py`).
Verified with a clean environment:

- `src/dida/__init__.py` imports nothing — just `__version__`.
- `httpx`, `dida.api.*`, `dida.clock`, `dida.config`, `dida.storage.store`, `dida.sync.engine` do **not**
  pull `textual`.
- **`bootstrap.py:32` — `from dida.tui.app import PUSH_TICK_SECONDS, DidaApp` — is the first import in the
  process that pulls `textual`.** (`dida.tui.escape` on line 33 also pulls it, but line 32 comes first.)

So an `os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")` placed **above the import block** in
`bootstrap.py` (right after `from __future__ import annotations`) is sufficient today. Verified:

```
os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")
import dida.bootstrap
textual.constants.DISABLE_KITTY_KEY   ->  True
```

Line 32 is *also* where `PUSH_TICK_SECONDS` has to be rescued from before `tui/app.py` is deleted, so both
changes land in the same place. **`setdefault`, not `=`,** so an explicit environment setting still wins.

Order-independence belt-and-braces, if you want it: after setting the env var, also assign the constant —
`import textual.constants as _c; _c.DISABLE_KITTY_KEY = True`. Both consumers do runtime attribute lookup,
so this works regardless of import order (measured: it reaches the parser). It is a monkeypatch of a
`Final`, so argue for it explicitly if you use it rather than reaching for it by default.

## Fact 3 — **the obvious acceptance test asserts the opposite of what it looks like**

I fed the kitty CSI-u sequence for a short IME commit into `XTermParser` both ways:

| `DISABLE_KITTY_KEY` | same 4-CJK CSI-u sequence decodes to |
|---|---|
| `False` (kitty **enabled**) | `'你好世界'` — **correct** |
| `True` (kitty **disabled**) | `'[32;;20320:22909:19990:30028u'` — garbage |

So a test of the form "feed `\x1b[32;;…u` and assert the field gets the real text" **passes when the guard
is broken and fails when it works.** The ADR's protection is not that Textual decodes CSI-u sequences
better — it is that **the terminal is never asked to send them**, because the driver never writes
`\x1b[>25u` (`linux_driver.py:285–292`).

The two honest assertions are therefore:

1. **In a subprocess with a clean environment** (this is the one that matters, because it is the one that
   catches "set too late"): after `import dida.bootstrap`, `textual.constants.DISABLE_KITTY_KEY is True`.
2. Optionally, that the Linux driver's guard is what suppresses the write — assert the driver does not
   emit `\x1b[>` … `u` on startup. Harder to observe under the headless test driver; assertion 1 is the
   load-bearing one.

And the genuinely end-to-end check — a real IME in a real terminal — is a **manual** acceptance step, not
an automated one. See `terminal-input-evidence.md` §1 for why a hand-test with a *short* word proves
nothing either. Say "verified at the parser/constant level, needs a manual IME pass" rather than implying
end-to-end coverage.

## Fact 4 — why any of this is worth the trouble

Restated from `terminal-input-evidence.md` so this file stands alone: an IME commit of 11 CJK characters
arrives as one 78-byte CSI-u event, exceeds Textual's 32-character parse cap, and is re-emitted
character-by-character as the literal escape string. **4 characters (30 bytes) decode fine.** The bug is
invisible in short-word self-testing and destroys Chinese typing in real use.
