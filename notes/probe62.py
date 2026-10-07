"""Independent probe for ticket #62's core claim: moving the cursor must never blank a row.

Deliberately NOT the implementer's test: this walks the frame sequence itself and reports
which task titles vanish from the screen mid-move, so a fix cannot pass by pinning a
different invariant than the user-visible one.

Run from a worktree root:  uv run python notes/probe62.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "tests")

from dida.testing import FakeBackend, ManualClock  # noqa: E402
from dida.tui.app import DidaApp  # noqa: E402
from support import screen_text  # noqa: E402

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
TITLES = ("交周报", "写周报", "读论文")


def backend() -> FakeBackend:
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task(TITLES[0], list_name="work", id="t1")
    fake.add_task(TITLES[1], list_name="work", id="t2")
    fake.add_task(TITLES[2], list_name="work", id="t3")
    return fake


async def sample_while(action, project) -> list:
    frames: list = []

    async def sample() -> None:
        while True:
            frames.append(project())
            await asyncio.sleep(0.01)

    sampler = asyncio.create_task(sample())
    try:
        await action()
    finally:
        sampler.cancel()
    return frames


def visible(text: str) -> tuple[str, ...]:
    return tuple(title for title in TITLES if title in text)


async def main() -> int:
    app = DidaApp(backend(), animations="on")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for _ in range(20):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        assert len(visible(screen_text(app))) == 3, screen_text(app)

        frames = await sample_while(lambda: pilot.press("j"), lambda: screen_text(app))
        seen = [visible(frame) for frame in frames]

    print(f"frames sampled: {len(seen)}")
    print(f"visible-title sets, in order: {seen}")

    events = []
    for title in TITLES:
        flags = [title in frame for frame in seen]
        if not any(flags):
            continue
        first_seen = flags.index(True)
        last_seen = len(flags) - 1 - flags[::-1].index(True)
        gaps = sum(1 for flag in flags[first_seen : last_seen + 1] if not flag)
        if gaps:
            events.append((title, gaps))
    # a title absent only at the very tail is not evidence of a blank-line flash
    print(f"\nBLANK-ROW EVENTS: {len(events)}")
    for title, gaps in events:
        print(f"  「{title}」的那一行整行空白了 {gaps} 帧，然后又回来了")
    if events:
        print("\nVERDICT: #62's bug is PRESENT — a whole row goes blank during the move.")
        return 1
    print("\nVERDICT: no row ever vanished mid-move — cursor feedback is instantaneous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
