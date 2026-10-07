import sys, asyncio
sys.path.insert(0, "/home/tofu/我的项目/Tips/.venv/lib/python3.12/site-packages")
from textual.app import App, ComposeResult
from textual.widgets import Input, TextArea
from textual.binding import Binding

class A(App):
    BINDINGS = [Binding("space", "mark", "mark"), Binding("ctrl+enter", "mark2", "m2")]
    def __init__(self): super().__init__(); self.fired=[]
    def compose(self) -> ComposeResult: yield Input(id="inp")
    def action_mark(self): self.fired.append("space")
    def action_mark2(self): self.fired.append("ctrl+enter")

async def main():
    app = A()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(Input)
        inp.focus(); await pilot.pause()
        await pilot.press("space"); await pilot.pause()
        print(f"  focused Input, pressed space -> app.fired={app.fired}  Input.value={inp.value!r}")
        # now blur the input
        inp.blur(); await pilot.pause()
        await pilot.press("space"); await pilot.pause()
        print(f"  no focus,     pressed space -> app.fired={app.fired}  Input.value={inp.value!r}")
asyncio.run(main())
