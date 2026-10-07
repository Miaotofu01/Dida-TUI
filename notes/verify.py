import sys, asyncio
sys.path.insert(0, "/home/tofu/我的项目/Tips/.venv/lib/python3.12/site-packages")
from textual import events
from textual._xterm_parser import XTermParser

def parse(seq):
    p = XTermParser()
    msgs = [*p.feed(seq), *p.feed("")]   # EOF flush, as the real loop does on exit
    return [(m.key, m.character) for m in msgs if isinstance(m, events.Key)]

print("=== A. corrected probe WITH EOF flush ===")
for label, seq in [
    ("plain Enter (CR)", "\r"),
    ("Ctrl+J / LF", "\n"),
    ("ALT+Enter (ESC CR)  <- Meta prefix", "\x1b\r"),
    ("kitty ctrl+enter CSI 13;5u", "\x1b[13;5u"),
    ("kitty shift+enter CSI 13;2u", "\x1b[13;2u"),
    ("xterm modifyOtherKeys=2 ctrl+enter CSI 27;5;13~", "\x1b[27;5;13~"),
    ("SPACE", " "),
    ("kitty space CSI 32u", "\x1b[32u"),
    ("kitty shift+space CSI 32;2u", "\x1b[32;2u"),
    ("kitty ctrl+shift+a CSI 97;6u", "\x1b[97;6u"),
    ("alt+left CSI 1;3D", "\x1b[1;3D"),
]:
    print(f"  {label:48s} -> {parse(seq)}")

print()
print("=== B. issue #6721 repro: IME commit of 11 CJK chars (Ghostty, flags 25) ===")
text = "你帮我检查一下这个代码库"
sequence = "\x1b[32;;" + ":".join(str(ord(c)) for c in text) + "u"
print(f"  sequence length = {len(sequence)}")
typed = "".join((c or "") for _, c in parse(sequence))
print(f"  received: {typed!r}")
print(f"  correct? {typed == text}")

print()
print("=== B2. same IME commit but SHORT (4 CJK chars, under the 32-char cap) ===")
short = "你好世界"
seq2 = "\x1b[32;;" + ":".join(str(ord(c)) for c in short) + "u"
typed2 = "".join((c or "") for _, c in parse(seq2))
print(f"  seq len={len(seq2)} received={typed2!r} correct? {typed2 == short}")

print()
print("=== C. does Binding('ctrl+enter') construct, and does it fire? ===")
from textual.binding import Binding
b = Binding("ctrl+enter", "toggle_done", "Toggle done")
print(f"  Binding ok: key={b.key!r} action={b.action!r}")

from textual.app import App, ComposeResult
from textual.widgets import Static

class TestApp(App):
    BINDINGS = [Binding("ctrl+enter", "toggle_done", "Toggle done")]
    def __init__(self):
        super().__init__()
        self.fired = []
    def compose(self) -> ComposeResult:
        yield Static("hi")
    def action_toggle_done(self):
        self.fired.append("ctrl+enter")

async def main():
    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.post_message(events.Key("ctrl+enter", None))
        await pilot.pause()
        app.post_message(events.Key("enter", "\r"))
        await pilot.pause()
        print(f"  after dispatching Key('ctrl+enter') then Key('enter'): fired={app.fired}")
asyncio.run(main())
