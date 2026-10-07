# Wave-4 kickoff — read this *in addition to* your own `prompt-NN.md`

Your per-ticket prompt was written **before** the interface existed. Seven of you are running concurrently off
**`3b2a607`** (integration tip, 454 passing). This note is only what changed since, and the rules that are now
enforced by tests.

## What landed while your prompt was being written

- **#34** built the three pages and cut the file seams. They exist now: `tui/pages/{index,tasks,detail}.py`,
  `tui/pages/base.py` (the shared `CursorPage`), `tui/keys.py` (one layer-keyed `BINDINGS` dict that both the real
  bindings and the `?` help read), `tui/overlays.py` (`MessageOverlay` + `ConfirmOverlay`; **the form shell is left
  for #42**), and a thin `app.py`.
- **#51 视觉地基** made the look a system, and **`docs/adr/0007-visual-identity-follows-terminal-theme.md` is now
  the authority on it**. Read that ADR. The short version:
  - `App(ansi_color=True)` — the app now emits **ANSI**, so **the user's terminal theme decides the colours**. It
    used to silently rewrite everything to truecolor via Textual's built-in Monokai palette.
  - Accent = `ansi_cyan` (his slot 6); page background = `ansi_default` (his own background); raised surfaces =
    `ansi_black`. **Chrome is exactly two rows**: a top bar (wordmark + navigation path) and the status bar. The
    Footer was removed to pay for the top bar — **never add a second bottom-docked widget** (two of them overlap
    and eat CJK cells: 「待推送」 rendered as 「待推 」).
  - **Hierarchy cannot come from brighter colours.** His slots 8–15 duplicate 1–6, so bold/bright buys *no*
    contrast. Use weight, `dim`, reverse, underline, spacing.
  - Motion: horizontal pan between pages; the cursor's selection lands **instantly** with a decorative bar
    catching up; a spinner only above ~300 ms; `toasts` for transient feedback. `animations` is reachable via the
    constructor and `DIDA_ANIM=auto|on|off`.

## Rules that are now enforced by tests

- **Visual constants live only in `src/dida/tui/theme.py`.** If you need a new colour, glyph, spacing or duration,
  **add it there** and reference it by name. Never inline a visual value in a page.
- **No hex literals in TUI source** (`tests/test_architecture.py`, AST-based). Note the guard also now covers more
  than hex — but the *real* trap is different and it has already bitten once:
  **`Text("x", style="cyan")` puts the style in the *base* style, which Textual re-parses with its CSS colour
  parser — where `cyan` is `#00FFFF`.** So a call site that *looks* like it uses an ANSI name silently emits
  truecolor and undoes the whole colour decision **without erroring**. Colours must go in **Rich spans**
  (`Text().append(..., style=...)`). `theme.py` exposes both vocabularies — `theme.ACCENT` (Rich, `"cyan"`) vs
  `theme.CSS_ACCENT` (CSS, `"ansi_cyan"`) — so use the right one for the context.
- **Assert SGR *parameters*, never a whole sequence.** Textual merges fg+bg into one sequence, so the accent is
  `\x1b[36;49m`, **never** `\x1b[36m`. A literal-string assertion goes falsely red.

## Two interface rules you must respect

- **Lists clip; the detail page wraps.** Lists clip **by cells** with `…` (never by `len()` — his locale is
  `zh_CN.UTF-8`, a CJK glyph is 2 cells). When width runs out the discard order is
  **tags → repeat/reminder marks → due time → and only then the title** — the title has priority. The detail page
  wraps instead, and its field rows are therefore variable-height, so its cursor and scrolling must work in
  **screen lines** (`#43`). `theme.clip()` / `theme.pad()` / `theme.rpad()` are cell-accurate.
- **Glyph widths are load-bearing.** `STRUCTURAL_GLYPHS` are all width-unambiguous (rich measures 1 cell, EAW is
  N/Na). **East-Asian-Ambiguous glyphs may render double-width in his CJK terminal**, so they must not go in a
  column. Known ones heading your way: `sync/view.py`'s `·` (U+00B7, low priority) and `NO_DUE_TEXT = "—"`
  (U+2014) — **#37/#44 must map or replace them before aligning a due/priority column**. `☰` was already replaced.

## Landmines — all measured, all in the brief

`notes/brief.md`'s **"Landmines from the visual foundation (#51)"** section is required reading. The four that
cost real time:

- **Never name an attribute after a Textual one.** `self._animate = False` shadowed `App._animate` (Textual's
  animator) → `app.animate(...)` raised `TypeError: 'bool' object is not callable` with the traceback *inside
  `textual/app.py`*. `TasksPage._name` shadows `DOMNode._name` the same way (benign today; **#37 renames it**).
- **`page.focus()` scroll-reveals by default and kills a horizontal pan instantly** — use
  `focus(scroll_visible=False)`.
- **`pilot.press` waits for animations to go idle**, so motion is only observable with a concurrent sampler.
- **`animate(...)` needs `on_complete`**, or the widget parks on top of whatever it landed on. (The cursor bar had
  this bug; it painted its own two spaces over the `❯` marker — the user reported it as 「选中的行会消失」.)

## Operational

- **`env -u NO_COLOR TERM=xterm-kitty COLORTERM=truecolor` for every render, probe or pty capture.** This shell has
  `NO_COLOR=1` and `TERM=dumb`, so anything captured without clearing them is **grey and misrepresents the user**.
- **Do not `git fetch`** — the user's dead SOCKS proxy hangs it. Pushing needs
  `GIT_SSH_COMMAND="ssh -o ProxyCommand=none"` (a merger pushes for you; you normally won't).
- **Never** read or touch `~/.config/dida-tui/` (his API token lives there) and never call the TickTick API.
- Your file is yours: `notes/wave-plan-and-seams.md` says which seam each ticket owns. Prefer your own file; keep
  edits to `app.py` / `keys.py` / `overlays.py` small and late, because several of you share them.
