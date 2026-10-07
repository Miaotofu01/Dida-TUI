# Environment audit — what the user's terminal actually renders

Measured on this machine, 2026-10-05. Read-only: no file outside this one was created or modified.
`~/.config/dida-tui/` was **never opened** (not even stat'd). No `env` / `set -x` / `bash -x` was run;
environments were read per-pid from `/proc/<pid>/environ` and only `TERM`/`COLORTERM`/`LANG`-class
variables are reported. No request was made to the TickTick/Dida365 API.

---

## 0. Headline: the app's `ansi_*` names never reach his palette

The premise "with ANSI names, the RGB comes from his terminal" **does not hold for this app as built.**
Textual 8.2.8 rewrites every `ansi_*` colour to an RGB triplet before it reaches the terminal, using a
palette baked into Textual (Monokai), not the terminal's.

The chain, each step read in the installed venv:

| # | Fact | Location |
|---|---|---|
| 1 | Default theme is `textual-dark` | `.venv/…/textual/constants.py:163` (`DEFAULT_THEME = get_environ("TEXTUAL_THEME", "textual-dark")`); app never sets `TEXTUAL_THEME` (`grep` over `src/dida/` finds none) |
| 2 | `Theme.ansi` defaults to **`False`**; only `ansi-dark`/`ansi-light` set `ansi=True` | `.venv/…/textual/theme.py:40`; `textual-dark` at `:71` does not set it |
| 3 | `App.ansi_color` is `Reactive(None)`, so `native_ansi_color` falls back to the theme → `False` | `.venv/…/textual/app.py:569`, `:1551-1557` |
| 4 | Therefore `ANSIToTruecolor` is constructed **enabled** | `.venv/…/textual/app.py:611` (`enabled=not self.native_ansi_color`) |
| 5 | Its palette is `ansi_theme_dark`, which is `MONOKAI` | `.venv/…/textual/app.py:563`; `.venv/…/textual/_ansi_theme.py:22` |
| 6 | The filter runs on **every rendered line** | `.venv/…/textual/_styles_cache.py:122` → `widget.get_line_filters()` → `.venv/…/textual/widget.py:731-737` → `.venv/…/textual/app.py:894-900` |
| 7 | It replaces any colour whose `triplet is None` (all `ansi_*`) with the Monokai RGB | `.venv/…/textual/filter.py:219-262` |

Measured, not inferred. Keys `MONOKAI` apart with `rich/terminal_theme.py:29`
(`ansi_colors = Palette(normal + bright)`, normal has 9 entries + bright has 7 = 16):

```
ansi_cyan  ansi_idx=6   rich=color(6)  -> filtered #58d1eb  ColorType.TRUECOLOR
ansi_default ansi_idx=-1 rich=default  -> filtered #d9d9d9  ColorType.TRUECOLOR
```

End-to-end proof — a headless Textual app carrying the app's own CSS
(`#status-bar { color: ansi_cyan; }`, `.box { border: round ansi_cyan; background: $surface; }`),
rendered and its segment styles read back:

```
seg text='SYNCED 12:00 pending 0'
style=Color('#58d1eb', ColorType.TRUECOLOR, triplet=ColorTriplet(red=88, green=209, blue=235)),
bgcolor=Color('#121212', ColorType.TRUECOLOR, ...)
```

and the SGR bytes the terminal receives:

```
status bar      \x1b[38;2;88;209;235;48;2;18;18;18m      -> fg #58D1EB on bg #121212
pending count   \x1b[1;38;2;253;151;31;48;2;18;18;18m    -> bold + #FD971F on #121212
cursor row      \x1b[7;38;2;224;224;224;48;2;18;18;18m   -> reverse video
completed row   \x1b[9;38;2;153;153;153;48;2;18;18;18m   -> strikethrough + #999999 on #121212
```

`ansi_cyan` resolves to **#58D1EB**, not his Catppuccin `#94E2D5`. Confirmed both ways: `#58d1eb`
is present in the rendered output, `#94e2d5` is absent.

Consequence: the palette below is what his **terminal** is configured with, but the app **bypasses all
of it**. The only thing the app takes from his environment is the glyph cell grid and the font — not a
single colour.

The repo's own rule is therefore satisfied on paper and violated in effect:

- `docs/architecture.md:36` — "颜色只用终端 16 色（CSS 里写 `ansi_*`），不写死 hex"
- `docs/ui-mockups.md:12`, `:106` — same rule
- `README.md:68` — "只用终端 16 色，不需要真彩" (only terminal 16 colours, truecolor not needed)
- `src/dida/tui/app.py:57` — same rule restated in the docstring

All four hold for the source text; none holds for the pixels. To actually get terminal-palette colours
the app would need `App(ansi_color=True)`, or a theme with `ansi=True` (Textual ships `ansi-dark` /
`ansi-light` for exactly this). No change was made.

---

## 1. ANSI slot → his configured RGB → what the app puts there

### 1a. Kitty — the terminal actually running (active theme: **Catppuccin Mocha**)

Configured at `~/.config/kitty/current-theme.conf:51-80`, pulled in by
`~/.config/kitty/kitty.conf:67` (`include current-theme.conf`). Kitty's theme was applied via
`kitten themes` — the answered file is Mocha, and the Tokyo Night block in `kitty.conf:22-57` is
**commented out** (abandoned, not merely overridden).

| Slot | `colorN` | Kitty RGB (Catppuccin Mocha) | What the app actually emits there |
|---|---|---|---|
| 0 | `color0` #45475A | `#45475A` slate | not used by the app; Textual's `ansi_black` → `#1A1A1A` |
| 1 | `color1` #F38BA8 | `#F38BA8` soft red | not used by the app; Textual's `ansi_red` → `#F4005F` |
| 2 | `color2` #A6E3A1 | `#A6E3A1` soft green | not used by the app; → `#98E024` |
| 3 | `color3` #F9E2AF | `#F9E2AF` soft yellow | **`PENDING_STYLE = "bold yellow"`** (`app.py:54`) — but he sees `#FD971F`, an **orange** |
| 4 | `color4` #89B4FA | `#89B4FA` soft blue | app CSS never names it; Textual's own `$border`/`$primary` `#0178D4` is a different blue |
| 5 | `color5` #F5C2E7 | `#F5C2E7` pink | not used by the app |
| 6 | `color6` #94E2D5 | `#94E2D5` soft teal | **the only app accent**: status bar text (`app.py:100`), overlay border (`overlays.py:33`) — he sees `#58D1EB`, a harder cyan |
| 7 | `color7` #BAC2DE | `#BAC2DE` | not used by the app; Textual's `ansi_white` → `#C4C5B5` |
| 8 | `color8` #585B70 | `#585B70` | not used by the app |
| 9 | `color9` #F38BA8 | `#F38BA8` | not used by the app |
| 10 | `color10` #A6E3A1 | `#A6E3A1` | not used by the app |
| 11 | `color11` #F9E2AF | `#F9E2AF` | not used by the app |
| 12 | `color12` #89B4FA | `#89B4FA` | not used by the app |
| 13 | `color13` #F5C2E7 | `#F5C2E7` | not used by the app |
| 14 | `color14` #94E2D5 | `#94E2D5` | not used by the app |
| 15 | `color15` #A6ADC8 | `#A6ADC8` | not used by the app |

Two quirks of this theme worth recording: **normal and bright are identical** for slots 1-6 (Mocha's
kitty port repeats each colour), so bold/bright text gains no colour contrast; and `color15` (#A6ADC8)
is **darker** than `color7` (#BAC2DE), so "bright white" is duller than "white".

Kitty also sets `foreground #CDD6F4` (`current-theme.conf:12`) and `background #1E1E2E` (`:13`) — neither
is reached either, because the app paints `#121212` explicitly.

### 1b. The colours the app actually paints (from `textual-dark`, measured)

These are the values that matter for any redesign; none of them come from his theme.

| Element | Source | Rendered value |
|---|---|---|
| Screen background | `textual-dark` `$background` | `#121212` |
| Overlay box background | `overlays.py:35` `background: $surface` | `#1E1E1E` |
| Status bar text | `app.py:100` `color: ansi_cyan` | `#58D1EB` |
| Pending-count text | `app.py:54` `"bold yellow"` | `#FD971F`, bold |
| Overlay box border | `overlays.py:33` `border: round ansi_cyan` | `#58D1EB` |
| Default widget border (`$border`) | Textual `textual-dark` | `#0178D4` |
| Footer bar | Textual `$footer-background` | `#242F38` |
| Footer key text | Textual `$footer-key-foreground` | `#FFA62B` |
| Body text `$foreground` | Textual `textual-dark` | `#E0E0E0` |
| Cursor row | `base.py:146` `line.stylize("reverse")` | reverse video of the above → ~`#E0E0E0` bar, `#121212` text |
| Completed task | `tasks.py:25` `"dim strike"` | `#999999` + strikethrough |
| Empty state / sub-heading | `base.py:33` `"dim"` | theme fg + SGR 2 (no colour of its own) |
| Group heading | `index.py:56` `"bold"` | theme fg, bold |
| Scrollbar | Textual `textual-dark` | `#003054` / active `#0178D4` |

The whole app surface is therefore: **one cyan accent (#58D1EB), one orange (#FD971F), Textual's stock
blue borders/footer (#0178D4 / #242F38), on a neutral #121212.** Everything the app actually colours is
cool or stock-blue except the single pending-count orange.

The widget surface is small — only `Static`, `VerticalScroll`, `Footer`, `ModalScreen` are imported
(`grep '^from textual' src/dida/tui/`), and the only two CSS blocks in the whole TUI are
`app.py:94-102` and `overlays.py:24-38`. `grep` for `border|background|color|text-style` across
`src/dida/tui/` returns **three** directives (`overlays.py:33`, `:34`, `:35`); everything else is
Textual's defaults.

### 1c. The other two terminals on this machine (different palettes)

| | Alacritty (`~/.config/alacritty/alacritty.toml`) | gnome-terminal (dconf profile `b1dcc9dd-…`, the `default`) |
|---|---|---|
| Theme | **Tokyo Night** (`:23-35`) | **Tokyo Night** (`palette=`) |
| Background / fg | `#1a1b26` / `#c0caf5` (`:24-25`) | `#1A1B26` / `#C0CAF5` |
| Palette | black `#15161e`, red `#f7768e`, green `#9ece6a`, yellow `#e0af68`, blue `#7aa2f7`, magenta `#bb9af7`, cyan `#7dcfff`, white `#a9b1d6` | identical 8 + brights `#414868`/`#f7768e`/`#9ece6a`/`#e0af68`/`#7aa2f7`/`#bb9af7`/`#7dcfff`/`#c0caf5` |
| Bright block | **absent** — `[colors.bright]` is never defined, so brights fall back to Alacritty's built-in defaults | present |
| Font | FiraCode Nerd Font 12.0 (`:2-19`) | `FiraCode Nerd Font 12` |
| Opacity | `opacity = 0.80`, `blur = true` (`:39-40`) | `background-transparency-percent=20` |

So if he ever ran `dida` outside kitty the intended palette would change from Catppuccin Mocha to Tokyo
Night — except that, per §0, the app bypasses both and looks the same either way. That is the one
silver lining of the §0 finding: the app is currently **terminal-independent in colour**, which the
spec's cross-terminal requirement accidentally gets for free.

---

## 2. Terminal emulator, font, locale

- **Running now: kitty.** Process tree of this session is `bash ← MainThread ← fish ← kitty`
  (`ps -o pid,ppid,comm` walk); four `kitty` processes are alive (PIDs 1200102, 3438292, 3450089,
  3684440). `TERM=xterm-kitty`, `COLORTERM=truecolor` read from `/proc/3450112/environ` (the fish
  shell) — truecolor is advertised.
- **kitty version 0.32.2** (`kitty --version`). Old — the kitty.conf itself notes `cursor_trail` "需要
  kitty ≥ 0.41 (?), 当前 0.32.2 不支持" (`kitty.conf:19`).
- **Font: `FiraCode Nerd Font`, size `12.0`** (`kitty.conf:2-5`; bold/italic faces named explicitly
  `:3-4`). Alacritty and gnome-terminal use the same family and size.
- **It is a real Nerd Font.** `fc-match "FiraCode Nerd Font"` →
  `FiraCodeNerdFont-Regular.ttf: "FiraCode Nerd Font" "Regular"`; 24 FiraCode faces installed, 18 of
  them Nerd Font, in `~/.fonts/FiraCode/`. The set includes the **v3 families** (`…Mono-*`, `…Propo-*`),
  so the modern Nerd Fonts v3 glyph coverage is present. **Powerline and icon glyphs are safe.**
  Independently corroborated by a screenshot: the starship prompt's ``/`` separators render
  correctly (see §6).
- **Not a CJK font.** FiraCode has no CJK coverage; `fc-match "FiraCode Nerd Font:lang=zh-cn"` resolves
  the fallback to **`Noto Sans CJK SC`** (`NotoSansCJK-Regular.ttc`, the *proportional* face — a
  monospace query instead yields `Noto Sans Mono CJK SC`). Chinese text in the app is therefore drawn
  in a different family from the Latin, at whatever advance kitty's grid imposes.
- **Locale is `zh_CN.UTF-8`** (`locale`, `localectl status`; X11 layout `cn`). This is the signal that a
  CJK width mode is in play — see §5.
- Kitty window chrome: `hide_window_decorations yes`, `window_padding_width 10`, `background_opacity
  0.80`, `dynamic_background_opacity yes`, `cursor_shape beam`, `dpi 96` (`kitty.conf:8-17`, `:61`).
- The spec's other named targets are **absent from this machine**: no `wezterm`, no `foot`, no
  `ghostty`, no `xfce4-terminal` config; Windows Terminal config does not exist; no `~/.Xresources`
  or `~/.Xdefaults`. `gnome-terminal` *is* installed and configured (Tokyo Night) but is not running.
  `screen` is installed; `wezterm`/`foot` binaries are not.

---

## 3. tmux — not installed, so not a hazard here (but the notes assume otherwise)

- **`tmux` is not installed.** `command -v tmux` → nothing; `/usr/bin/tmux`, `/usr/local/bin/tmux`,
  `/bin/tmux` all missing; no `dpkg -l` entry. `command -v screen` → `/usr/bin/screen`.
- No `~/.tmux.conf`, no `~/.config/tmux/`. No tmux server can be running (no binary, no processes).
- `~/.config/byobu/` exists with a full byobu profile (`status`, `color.tmux`, `keybindings.tmux`,
  …) but **byobu's binary is not on `PATH`** either, and byobu needs tmux or screen — with only screen
  present this config is inert for tmux purposes.
- **This contradicts the handoff's assumption.** `notes/terminal-input-evidence.md:66-67` states
  "he has tmux / WezTerm / gnome-terminal per the handoff". Of those three, only **gnome-terminal** is
  actually present, and it is not the terminal in use. The practical consequence for that note's own
  caveat: the end-to-end IME check it defers still cannot be done in tmux or WezTerm **on this
  machine** — it can only be done in kitty (running) or gnome-terminal (installed), or by installing
  tmux/WezTerm first.
- No `TMUX`/`TERM=screen*` variables exist in his session, so the app is not running under a
  multiplexer: **no `terminal-overrides`, no 256-colour or truecolor downgrade via tmux, and no glyph
  passthrough question applies today.**

---

## 4. Baseline: what his shell and tools look like

This is the thing the app is being judged against, and it is worth noting it is **not warm**.

- Shell is **fish** — the login shell (`getent passwd tofu` → `/usr/bin/fish`), config
  `~/.config/fish/config.fish`. Line 9 runs `starship init fish | source`.
- Which prompt actually draws: `~/.config/fish/functions/fish_prompt.fish` (122 bytes) would print only
  `basename $PWD ) ` with no powerline at all, and `~/.config/fish/conf.d/_tide_init.fish` only does
  fisher bootstrap. `conf.d/` is sourced before `config.fish`, so **`starship init` defines
  `fish_prompt` last and wins**; there is no `fish_right_prompt.fish`. Tide's palette in
  `fish_variables` (`tide_*_color`, e.g. `tide_git_color_branch:5FD700`, `tide_character_color:5FD700`)
  is therefore **dead config**.
- **`~/.config/starship.toml` is a hex-truecolor powerline**, and it is cool blue-grey throughout:
  separator `#a3aed2` (`:4`), segment backgrounds `#769ff0` (`:6`, `:8`, `:22`, `:36`, `:40`),
  `#394260` (`:8`, `:11`, `:35`), `#212736` (`:11`, `:16`, `:44`), `#1d2230` (`:16`, `:18`, `:65`),
  text `#e3e5e5` (`:22`) and `#a0a9cb` (`:66`). The glyphs are Nerd Font private-use icons
  (``, ``, ``, …) plus powerline separators.
- So the baseline is a **cool blue-grey powerline prompt on a dark background** — the same temperature
  family as the app's stock blue borders. Note that `~/.config/fish/config.fish:3-4` also sets
  `FISH_TERMINAL_FONT "JetBrains Mono Nerd Font"` / `FISH_TERMINAL_FONT_SIZE 14`, which is **not** the
  font kitty actually uses (FiraCode Nerd Font 12) — harmless, but do not read it as the terminal font.
- No `LS_COLORS` override was found. `~/.bashrc` does carry a colour prompt, but it is the **stock
  Debian** one and it is **inactive**: the gate is commented out (`~/.bashrc:46`
  `#force_color_prompt=yes`), so `color_prompt` is empty and the `PS1` at `:60`
  (`\[\033[01;32m\]\u@\h…\[\033[01;34m\]\w…`, green host / blue path) is never selected. Bash is not
  the login shell anyway, so this contributes nothing to what he sees.
- Kitty itself uses `background_opacity 0.80`, and the desktop wallpaper behind it is
  `~/图片/壁纸/anime-girl-with-bicycle-silhouette-at-dramatic-dusk-sky-5w-3840x2160.jpeg`
  (dconf `/org/cinnamon/desktop/background/picture-uri`, Cinnamon session on X11 — `XDG_SESSION_TYPE=x11`,
  `XDG_CURRENT_DESKTOP=X-Cinnamon`). Its mean colour is **`srgb(44.3%, 27.9%, 32.3%)` ≈ `#714752`** in
  the bottom-left half (`#634D62` ≈ whole image) — a muted plum dusk sky. At 80 % opacity that bleeds
  ~20 % into every app background:

  | Painted value | Over the wallpaper |
  |---|---|
  | `#121212` screen background | `#251D1F` (bottom-left) / `#221E22` (whole image) |
  | `#1E1E1E` overlay surface | `#2F2628` / `#2C272C` |
  | `#242F38` footer bar | `#33343D` / `#313540` |

  i.e. his backgrounds are very slightly **warm**-tinted, which sharpens rather than softens the
  cold-accent contrast: a hard cyan (`#58D1EB`) glyph on a near-neutral warm-black field.
- `~/.config/compton.conf` exists and sets Alacritty opacity rules, but **no compositor is running**
  (only Cinnamon/Muffin processes; no `compton`/`picom` in `ps`), so those rules are inert. Kitty's own
  `background_opacity` is handled by kitty under Cinnamon's compositor and *is* in effect.

---

## 5. CJK / glyph-width hazards

The app's row markers are the risk surface. Widths below are from Python's `unicodedata` (Unicode 15.0)
and from the width table Textual actually calls.

| Glyph | Codepoint | EAW | Textual/Rich cells | Used as |
|---|---|---|---|---|
| `❯` | U+276F | Neutral | 1 | `CURSOR_MARK` (`base.py:27`) |
| `▣` | U+25A3 | **Ambiguous** | 1 | `INBOX_MARK` (`index.py:41`) |
| `▸` | U+25B8 | Neutral | 1 | `BUILTIN_MARK` (`index.py:44`) |
| `★` | U+2605 | **Ambiguous** | 1 | `CUSTOM_MARK` (`index.py:47`) |
| `☰` | U+2630 | Neutral | **2** | `LIST_MARK` (`index.py:50`) |
| `☑` | U+2611 | Neutral | 1 | `SUBTASK_DONE_MARK` (`detail.py:28`) |
| `☐` | U+2610 | Neutral | 1 | `SUBTASK_TODO_MARK` (`detail.py:29`) |
| `⚠` | U+26A0 | Neutral | 1 | `BLOCKED_MARK` (`index.py:53`) |
| `·` | U+00B7 | **Ambiguous** | 1 | low-priority mark |
| `─` | U+2500 | **Ambiguous** | 1 | group/heading rules, e.g. `index.py:87` |

Two concrete hazards:

1. **`☰` is inconsistent inside the app's own width table.** Textual delegates all width maths to Rich
   (`.venv/…/textual/_wrap.py:6`, `_compositor.py:938` both import `rich.cells.get_character_cell_size`),
   and Rich scores `☰` as **2 cells** while scoring `▣ ▸ ★ ❯ ☑ ☐ ⚠ · ─` as 1. So `cell_len("☰ 真实清单")`
   = 11 while `cell_len("▣ 收集箱")` = 8. Any padding, truncation or right-alignment computed by the app
   treats the real-project rows as one cell wider than the glyph inventory suggests. U+2630's East Asian
   Width is *Neutral*, so terminals commonly give it **1** cell — jagging against Rich's 2.
2. **Four of the ten marks are East-Asian-Ambiguous** (`▣ ★ · ─`). Ambiguous codepoints are exactly the
   class that flips 1→2 cells in a CJK width mode, and `LANG`/`LC_CTYPE` are `zh_CN.UTF-8`. Each of
   these marks is emitted **followed by a space** (`base.py:143` `f"{CURSOR_MARK if selected else
   BLANK_MARK} "`, `index.py:87` `f"── 项目组 {group_id} ──"`), which doubles the exposure: kitty
   documents that for "Private Use Unicode characters and some symbol/dingbat characters, if the
   character is followed by one or more spaces, kitty will use those extra cells to render the
   character larger" (`narrow_symbols` doc, `/usr/lib/kitty/kitty/options/definition.py:99-112`).
   `LIST_MARK`/`INBOX_MARK`/`CUSTOM_MARK` are all in that symbol/dingbat category. Note the option's
   documented default `U+E0A0-U+E0A3,U+E0C0-U+E0C7 1` is declared `add_to_default=False`, so I do
   **not** claim those powerline codepoints are pinned to one cell by default — that is unverified.

The app is otherwise aware of the fragility: `index.py:1-9` states the prefix characters exist so that
rows are **not** distinguished by colour alone, "不靠颜色单独承担" — for colour-blind users, `NO_COLOR`,
and 8-colour terminals. That intent is sound and is worth preserving.

Related, and independently relevant to the redesign: `src/dida/bootstrap.py:33` sets
`TEXTUAL_DISABLE_KITTY_KEY=1`. Nothing in this audit changes that, but note it means kitty's keyboard
protocol is off, so kitty-specific key handling is unavailable by design (ADR-0006).

**Trap for anyone re-measuring: `NO_COLOR` is set in an agent shell but NOT in his.** It is
`NO_COLOR=1` in *this agent's* shell, but **absent** from his session — read from
`/proc/3450089/environ` and `/proc/3450112/environ` (55 variables readable, zero `NO_COLOR` matches). Any test run from an agent shell will render
**monochrome** (Textual appends `Monochrome` to its filters when `NO_COLOR` is set,
`.venv/…/textual/app.py:614-616`) and will therefore misrepresent what he sees. This bit during this
audit: an unpolluted re-run with `env -u NO_COLOR` was required to get the real colours.

---

## 6. Screenshots found

- No screenshots, renders, or image references exist in the repo: `find` over
  `/home/tofu/dida-v2-worktrees/integration` (excluding `.venv`) returns **no** `.png/.svg/.jpg/.gif`;
  `README.md` and `docs/*.md` reference **no** images. There is no screenshot of the TUI anywhere.
- `~/图片/2026-06-24 17-29-20屏幕截图.png` (2560×1600) shows a **kitty window in the bottom-left with
  the starship powerline prompt rendering correctly** — blue-grey blocks, working Nerd Font separators,
  a dark terminal background. It is the best available evidence of what his terminal looks like, and it
  visually confirms both the cool-blue baseline (§4) and the Nerd Font glyph rendering (§2).
- The other same-size candidates were sampled: `~/图片/图片1.png` (1318×844) is a matplotlib figure —
  four polar radiation-pattern plots — so it is not a terminal capture, and its twin `图片2.png`
  (1294×850) is the same kind of output. I did **not** open `~/图片/code.png`, `d2c.png`, `1.png` or
  `2.png`; nothing links them to this app. `~/Pictures`, `~/Screenshots` and `~/Desktop` do not exist.

---

## 7. Not established

- **Kitty's actual rendered cell width for U+2630 (and for the four Ambiguous glyphs) was not measured.**
  Measuring needs a live tty; kitty's width logic is compiled into a stripped C binary (the
  `/usr/bin/kitty` on PATH is an 18 KB launcher) and kitty 0.32.2 ships no `east_asian_width` option in
  its Python option table, so there is nothing to read. I did not write escape sequences into his live
  shell to probe it. What *is* established: the Rich/Textual table (which the app computes with) scores
  `☰` as 2; the East Asian Width property (Unicode 15.0) marks `▣ ★ · ─` Ambiguous and everything else
  Neutral; and kitty documents that symbol/dingbat glyphs followed by spaces may be drawn across those
  cells. Upstream kitty has open reports on this exact area — [kitty#6560 "incorrect handling of CJK
  ambiguous width
  characters"](https://github.com/kovidgoyal/kitty/issues/6560) and [kitty#8265 "Addressing Unicode
  character width
  ambiguities"](https://github.com/kovidgoyal/kitty/issues/8265) — but I could only retrieve their
  titles and snippets, not their contents (`web_fetch` of the issue page failed), so I do not assert
  what kitty does.
- **Whether he ever runs `dida` from alacritty or gnome-terminal** — kitty is what is running and what
  this session sits inside, but nothing records where the app was launched from. Moot for colour per §0.
- **The true visual result of `EMPTY_STYLE = "dim"` with no colour of its own** (SGR 2 applied to the
  terminal's rendering of the theme foreground) depends on kitty's dim algorithm; the `dim strike` case
  that *does* carry a colour was measured as `#999999`.
- **`~/.config/kitty/kitty.conf.bak`** (mode 0600) was not read — the readable `current-theme.conf` and
  `kitty.conf` were sufficient, and the backup is not included by anything.
- Per the rules, **`~/.config/dida-tui/` was not opened at all** — not read, not listed, not stat'd.
  If the app has any colour/theme setting there, it is unreported. `src/dida/config.py` contains no
  colour handling and reads no colour-related environment variables (`grep 'environ|getenv'` → no
  matches), so no app-side override of §0 is plausible from code alone.

---

## 8. What this means for a redesign (facts only)

- Any palette decision made against "his ANSI colours" is currently **unfalsifiable**, because the app
  never emits ANSI colour codes — it emits `38;2;R;G;B`. Changing his kitty theme would change
  **nothing** in the app.
- The app's visible palette is exactly four values (`#121212`, `#1E1E1E`, `#58D1EB`, `#FD971F`) plus
  Textual's stock blue/grey furniture (`#0178D4`, `#242F38`, `#FFA62B`, `#E0E0E0`).
- The one accent that exists is cyan, and it is Monokai's hard cyan rather than his Mocha teal.
- His shell baseline is **also cool** (starship blue-grey powerline), so "the app looks cold" is
  relative to a cool baseline, not to a warm one.
- The two levers that would make the app honour his palette are `App(ansi_color=True)` and/or Textual's
  `ansi-dark` theme; the two that would let it use real hex are already in play. No change was made.
