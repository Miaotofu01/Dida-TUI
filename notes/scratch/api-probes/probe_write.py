#!/usr/bin/env python3
"""写实验（已获授权）。只碰名字带 ZZ-实验-可删 的临时任务；跑完一律删除。

要回答的问题：
  1. 新建任务时 content / desc 分别落到哪、能不能回读
  2. 不给 projectId 时新建落到哪
  3. 新建任务服务端给的 sortOrder / status / timeZone / isAllDay / isFloating
  4. POST /task/batch 带 status=2 能不能完成（文档没写的路）
  5. POST /task/batch 带 status=0 能不能取消完成 ← 决定 space 能不能做成 toggle
  6. POST /task/{id} 带 status=0 能不能取消完成
  7. POST /task/move 在收集箱与真实清单之间能不能搬
"""
import json
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

cfg = tomllib.loads((Path.home() / ".config/dida-tui/config.toml").read_text())
TOKEN = str(cfg["token"]).strip()
BASE = "https://api.dida365.com/open/v1"
MARK = "ZZ-实验-可删-%d" % int(time.time())
made = []  # (project_id, task_id) —— 清理用


def call(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    rq = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(rq, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400].decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)


def show(label, st, payload, keys=None):
    print("   %-46s -> HTTP %s" % (label, st))
    if isinstance(payload, dict):
        if keys:
            print("      " + " | ".join("%s=%r" % (k, payload.get(k)) for k in keys))
        else:
            print("      " + json.dumps(payload, ensure_ascii=False)[:600])
    else:
        print("      " + str(payload)[:400])


def read_task(pid, tid, keys=None):
    st, t = call("GET", "/project/%s/task/%s" % (pid, tid))
    if isinstance(t, dict):
        show("GET 单个任务", st, t, keys)
    else:
        show("GET 单个任务", st, t)
    return t if isinstance(t, dict) else {}


st, projects = call("GET", "/project")
real = [p["id"] for p in projects] if isinstance(projects, list) else []
print("真实清单 id =", real)
print("实验标记 =", MARK)
print("=" * 72)

try:
    # ---- S1: 带 content/desc 建在收集箱 ----
    print("S1  新建（inbox，带 content 与 desc）")
    st, r = call("POST", "/task", {
        "title": MARK + "-A", "projectId": "inbox",
        "content": "CONTENT-标记-正文", "desc": "DESC-标记-说明", "priority": 3,
    })
    show("POST /task", st, r)
    a_id = r.get("id") if isinstance(r, dict) else None
    a_pid = r.get("projectId") if isinstance(r, dict) else None
    if a_id:
        made.append((a_pid or "inbox", a_id))
    print("=" * 72)

    # ---- S2: 不带 projectId ----
    print("S2  新建（不给 projectId）")
    st, r2 = call("POST", "/task", {"title": MARK + "-B"})
    show("POST /task", st, r2)
    b_id = r2.get("id") if isinstance(r2, dict) else None
    b_pid = r2.get("projectId") if isinstance(r2, dict) else None
    if b_id:
        made.append((b_pid or "inbox", b_id))
    print("=" * 72)

    # ---- S3: 回读 ----
    print("S3  回读 A（content / desc / sortOrder / status / tz / isAllDay / isFloating）")
    if a_id:
        read_task(a_pid or "inbox", a_id,
                  ["id", "projectId", "title", "content", "desc", "priority",
                   "sortOrder", "status", "timeZone", "isAllDay", "isFloating",
                   "kind", "createdTime", "etag", "tags", "items"])
    print("=" * 72)

    # ---- S4: batch 完成 ----
    print("S4  batch update status=2（文档没写的完成路径）")
    if a_id:
        st, r = call("POST", "/task/batch", {"update": [
            {"id": a_id, "projectId": a_pid or "inbox", "status": 2}]})
        show("POST /task/batch (status=2)", st, r)
        read_task(a_pid or "inbox", a_id, ["id", "status", "completedTime"])
    print("=" * 72)

    # ---- S5: batch 取消完成 —— 关键 ----
    print("S5  batch update status=0（取消完成？这就是那个决定性问题）")
    if a_id:
        st, r = call("POST", "/task/batch", {"update": [
            {"id": a_id, "projectId": a_pid or "inbox", "status": 0}]})
        show("POST /task/batch (status=0)", st, r)
        t = read_task(a_pid or "inbox", a_id, ["id", "status", "completedTime"])
        print("      >>> 结论：取消完成 %s" % (
            "成功（status 回到 0）" if t.get("status") == 0 else "失败（仍是 %r）" % t.get("status")))
    print("=" * 72)

    # ---- S6: 普通 update 带 status=0 ----
    print("S6  POST /task/{id} 显式带 status=0")
    if a_id:
        st, r = call("POST", "/task/%s" % a_id, {
            "id": a_id, "projectId": a_pid or "inbox", "status": 0})
        show("POST /task/{id} (status=0)", st, r)
        read_task(a_pid or "inbox", a_id, ["id", "status", "completedTime"])
    print("=" * 72)

    # ---- S7: move ----
    if real and a_id:
        print("S7  task/move：收集箱 → %s → 搬回" % real[0])
        st, r = call("POST", "/task/move", [
            {"fromProjectId": a_pid or "inbox", "toProjectId": real[0], "taskId": a_id}])
        show("POST /task/move", st, r)
        read_task(real[0], a_id, ["id", "projectId", "title"])
        made[-1] = (real[0], a_id)
        st, r = call("POST", "/task/move", [
            {"fromProjectId": real[0], "toProjectId": a_pid or "inbox", "taskId": a_id}])
        show("POST /task/move（搬回）", st, r)
        made[-1] = (a_pid or "inbox", a_id)
    else:
        print("S7  跳过（没有真实清单）")
    print("=" * 72)

finally:
    print("清理：删除本次建的全部临时任务")
    for pid, tid in made:
        st, r = call("DELETE", "/project/%s/task/%s" % (pid, tid))
        print("   DELETE %s / %s -> HTTP %s %s" % (pid[:12], str(tid)[:12], st, str(r)[:80]))
    print("=" * 72)
    print("复核：收集箱剩余未完成任务")
    st, d = call("GET", "/project/inbox/data")
    if isinstance(d, dict):
        left = [t.get("title") for t in (d.get("tasks") or [])]
        print("   剩余 titles =", left)
        print("   残留实验任务 =", [t for t in left if t and t.startswith("ZZ-实验")] or "无")
    print("实验结束")
