"""Independent probe for the「新建之后那条任务不能编辑」bug：认领换了 id，屏幕上那一行还挂着旧的。

Deliberately NOT a test: this walks the real app (real Store + real SyncEngine + fake transport)
and prints what the window shows next to what the local database holds, so a fix cannot pass by
pinning a different invariant than the user-visible one.

Run from the repo root:  uv run python notes/probe-stale-id.py

**修完之后（#75 / ADR-0009）这份输出仍然会显示「行上是 local-task-…／库里是 srv1」——那是设计，
不是没修好**：旧 id 继续解析得到真那一行，所以界面不必重画，也不必知道认领这回事。要看修好没有，
看这几处就够：A 的「返回的那个 id 还查得到吗」不再是 None、B/C 的详细页有字段、F 的「按 d 有确认框吗
= True」。（这份输出是**修之前**量的，工单 #75 抄的就是它。）
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, "tests")

import httpx  # noqa: E402

from dida.api.client import DidaApiClient  # noqa: E402
from dida.storage.store import Store  # noqa: E402
from dida.sync.engine import SyncEngine  # noqa: E402
from dida.testing import ManualClock  # noqa: E402
from dida.tui.app import DidaApp  # noqa: E402
from dida.tui.pages.detail import DetailPage  # noqa: E402
from support import screen_text  # noqa: E402

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)


def inbox() -> dict:
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def project(id: str = "work", name: str = "工作") -> dict:
    return {"id": id, "name": name, "sortOrder": 1}


class Server:
    """假服务端：`POST /open/v1/task` 给真 id（认领就发生在这里），其余照 test_app_sync 的样式。"""

    def __init__(self, *, create_gate: asyncio.Event | None = None) -> None:
        self.create_gate = create_gate
        self.requests: list[httpx.Request] = []
        self.created: list[dict] = []
        self.next_id = 1

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/open/v1/task" and request.method == "POST":
            if self.create_gate is not None:
                await self.create_gate.wait()
            body = json.loads(request.content)
            created = {
                "id": f"srv{self.next_id}",
                "projectId": body.get("projectId"),
                "title": body.get("title", ""),
                "status": 0,
            }
            self.next_id += 1
            self.created.append(created)
            return httpx.Response(200, json=created)
        if path == "/open/v1/project" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"id": "srvlist1", "name": body.get("name", ""), "sortOrder": 5}
            )
        if path == "/open/v1/project":
            return httpx.Response(200, json=[inbox(), project()])
        if path.endswith("/data"):
            list_id = path.split("/")[4]
            return httpx.Response(
                200,
                json={"project": project() if list_id == "work" else inbox(), "tasks": []},
            )
        if path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        if request.method in ("POST", "DELETE"):
            return httpx.Response(200, json={})
        raise AssertionError(f"探针没准备这条路径：{path}")


def make_engine(store: Store, server: Server) -> SyncEngine:
    return SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=server),
    )


def rows(page) -> list:
    return [row.id for row in page._rows]


async def stand_on_work(app: DidaApp, pilot) -> None:
    for _ in range(10):
        if app.index_page().selected_id == "work":
            break
        app.index_page().action_cursor_down()
    await pilot.press("right")
    await pilot.pause()


async def create_one(app: DidaApp, pilot, title: str = "买牛奶") -> None:
    await pilot.press("n")
    await pilot.pause()
    await pilot.press(*title)
    await pilot.press("enter")
    await pilot.pause()


def last_line(app: DidaApp) -> str:
    return screen_text(app).splitlines()[-1].strip()


# ---------------------------------------------------------------- A：最小复现（引擎层）


async def scenario_a(store: Store) -> None:
    """``create()`` 返回的那个 id，推送落地之后就不认了。整件事不需要界面。"""
    engine = make_engine(store, Server())
    returned = engine.create("买牛奶", "work")
    print("  create() 返回     ", returned)
    print("  推之前库里的 id   ", [t.id for t in store.tasks()])
    await engine.wait_for_pushes()
    print("  推落地之后库里的  ", [t.id for t in store.tasks()])
    print("  返回的那个 id 还查得到吗：", engine.task_detail(returned))


# ---------------------------------------------------------------- B：界面（新建之后立刻 →）


async def scenario_b(store: Store) -> None:
    app = DidaApp(make_engine(store, Server()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await stand_on_work(app, pilot)
        await create_one(app, pilot)
        await app.engine.wait_for_pushes()
        await pilot.pause()
        print("  库里的 id         ", [t.id for t in store.tasks()])
        print("  任务列表页行上的  ", rows(app.tasks_page()))

        await pilot.press("right")
        await pilot.pause()
        print("  按 → 进详细页，屏幕上说：")
        for line in screen_text(app).splitlines()[1:3]:
            print("     ", line)

        app.refresh_view()  # 任何一次重画都会把行 id 换成真的
        await pilot.pause()
        print("  一次重画之后行上的", rows(app.tasks_page()))
        print("  详细页仍然说      ", [line.strip() for line in screen_text(app).splitlines() if line.strip()][1])


# ---------------------------------------------------------------- C：人已经站在详细页上


async def scenario_c(store: Store) -> None:
    gate = asyncio.Event()
    app = DidaApp(make_engine(store, Server(create_gate=gate)))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await stand_on_work(app, pilot)
        await create_one(app, pilot)

        doing = app.tasks_page().selected_id
        await pilot.press("right")  # 推送还挂在闸门上：这时候进去是好的
        await pilot.pause()
        print("  闸门没开：库里", [t.id for t in store.tasks()], "页上：", doing)

        gate.set()
        await app.engine.wait_for_pushes()
        await pilot.pause()
        print("  推送落地：库里", [t.id for t in store.tasks()], "页上：", app.detail_page().task_id)

        await app.on_detail_page_field_edited(DetailPage.FieldEdited(doing, "content", "改一下"))
        await pilot.pause()
        print("  在详细页上改一个字段，屏幕上（只留非空行）：")
        for line in screen_text(app).splitlines():
            if line.strip():
                print("     ", line.rstrip())


# ---------------------------------------------------------------- D：清单那条路（同一个形状）


async def scenario_d(store: Store) -> None:
    app = DidaApp(make_engine(store, Server()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app._finish_list_form({"name": "新清单", "color": ""})
        await pilot.pause()
        await app.engine.wait_for_pushes()
        await pilot.pause()
        print("  库里的清单 id     ", [row.id for row in store.lists()])
        print("  清单列表页行上的  ", rows(app.index_page()))


# ---------------------------------------------------------------- E：会不会自己好（周期泵）


async def scenario_e(store: Store) -> None:
    app = DidaApp(make_engine(store, Server()), push_tick_seconds=0.05)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await stand_on_work(app, pilot)
        await create_one(app, pilot)
        await app.engine.wait_for_pushes()
        print("  建完   ：库里", [t.id for t in store.tasks()], "行上", rows(app.tasks_page())[-1])
        for _ in range(8):
            await pilot.pause()
            await asyncio.sleep(0.08)
        print("  泵跳 8 次：库里", [t.id for t in store.tasks()], "行上", rows(app.tasks_page())[-1])
        print("  状态栏    ", last_line(app))


# ---------------------------------------------------------------- F：同一行上别的键


async def scenario_f(store: Store) -> None:
    app = DidaApp(make_engine(store, Server()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await stand_on_work(app, pilot)
        await create_one(app, pilot)
        await app.engine.wait_for_pushes()
        await pilot.pause()
        print("  行上的 id", app.tasks_page().selected_id, "／库里", [t.id for t in store.tasks()])

        await pilot.press("d")
        await pilot.pause()
        print("  按 d ：屏幕上有确认框吗 =", "删除" in screen_text(app), "／状态栏 =", last_line(app))
        await pilot.press("space")
        await pilot.pause()
        print("  按 space：", last_line(app))

        app.refresh_view()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        print("  重画后按 d ：有确认框吗 =", "删除" in screen_text(app))


# ---------------------------------------------------------------- G：用户说的「过一会儿自己好了」


async def scenario_g(store: Store) -> None:
    """第一次 → 是坏的，但那一下顺手把行 id 修对了；退回来再进一次就好了。"""
    app = DidaApp(make_engine(store, Server()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await stand_on_work(app, pilot)
        await create_one(app, pilot)
        await app.engine.wait_for_pushes()
        await pilot.pause()
        print("  建完：行上", rows(app.tasks_page())[-1], "／库里", [t.id for t in store.tasks()])

        await pilot.press("right")
        await pilot.pause()
        print("  第一次 → ：详细页 task_id =", app.detail_page().task_id)
        print("             屏幕第二行 =", [line.strip() for line in screen_text(app).splitlines() if line.strip()][1])
        print("             这一下之后行上 =", rows(app.tasks_page())[-1])

        await pilot.press("left")
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        print("  退回来再 → ：详细页 task_id =", app.detail_page().task_id,
              "／屏幕第二行 =", [line.strip() for line in screen_text(app).splitlines() if line.strip()][1])


SCENARIOS = {
    "G 用户那边「过一会儿自己好了」是哪一下": scenario_g,
    "A 引擎层：create 返回的 id 会被推送换掉": scenario_a,
    "B 界面：新建之后立刻按 →": scenario_b,
    "C 人已经站在详细页上，推送才落地": scenario_c,
    "D 清单那条路：同一个形状": scenario_d,
    "E 周期泵会不会自己把它修好": scenario_e,
    "F 同一行上别的键（d / space）": scenario_f,
}


async def main() -> None:
    for title, scenario in SCENARIOS.items():
        print(f"\n=== {title} ===")
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "dida.sqlite3")
            try:
                await scenario(store)
            finally:
                store.close()


if __name__ == "__main__":
    asyncio.run(main())
