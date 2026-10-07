#!/usr/bin/env python3
"""只读探测：只发 GET 与 filter/completed 这类查询，不写任何数据，也不打印 token。"""
import json
import tomllib
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

cfg = tomllib.loads((Path.home() / ".config/dida-tui/config.toml").read_text())
TOKEN = str(cfg["token"]).strip()
BASE = "https://api.dida365.com/open/v1"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(rq, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)


print("=" * 72)
print("1) GET /project")
st, projects = call("GET", "/project")
print("   status =", st, "| 数量 =", len(projects) if isinstance(projects, list) else projects)
if isinstance(projects, list):
    if projects:
        print("   字段集合 =", sorted(projects[0].keys()))
    for p in projects:
        print(
            "   - id=%-26s kind=%-5s groupId=%-26s closed=%-5s viewMode=%-8s color=%-8s sortOrder=%-6s perm=%-6s name=%r"
            % (
                p.get("id"), p.get("kind"), p.get("groupId"), p.get("closed"),
                p.get("viewMode"), p.get("color"), p.get("sortOrder"),
                p.get("permission"), p.get("name"),
            )
        )
    print("   inbox 是否出现在列表里 =", any(p.get("id") == "inbox" for p in projects))
    print("   groupId 为空的清单数 =", sum(1 for p in projects if not p.get("groupId")))
    print("   kind 分布 =", dict(Counter(p.get("kind") for p in projects)))

print("=" * 72)
print("2) GET /project/group")
st, groups = call("GET", "/project/group")
print("   status =", st, "| 数量 =", len(groups) if isinstance(groups, list) else groups)
if isinstance(groups, list):
    for g in groups:
        print("   -", json.dumps(g, ensure_ascii=False))

print("=" * 72)
print("3) GET /tag")
st, tags = call("GET", "/tag")
print("   status =", st, "| 数量 =", len(tags) if isinstance(tags, list) else tags)
if isinstance(tags, list) and tags:
    print("   字段集合 =", sorted(tags[0].keys()))
    print("   前 5 个 =", json.dumps(tags[:5], ensure_ascii=False))

print("=" * 72)
print("4) 逐清单 GET /project/{id}/data（未完成任务）")
ids = [p["id"] for p in projects] if isinstance(projects, list) else []
if "inbox" not in ids:
    ids = ["inbox"] + ids
task_total = 0
sort_samples = []
for pid in ids[:12]:
    st, d = call("GET", "/project/%s/data" % pid)
    if st != 200 or not isinstance(d, dict):
        print("   - %-28s status=%s %s" % (pid, st, str(d)[:140]))
        continue
    tasks = d.get("tasks") or []
    task_total += len(tasks)
    n_content = sum(1 for t in tasks if (t.get("content") or "").strip())
    n_desc = sum(1 for t in tasks if (t.get("desc") or "").strip())
    n_items = sum(1 for t in tasks if t.get("items"))
    n_due = sum(1 for t in tasks if t.get("dueDate"))
    n_start = sum(1 for t in tasks if t.get("startDate"))
    print(
        "   - %-28s tasks=%-4d content非空=%-3d desc非空=%-3d 有子任务=%-3d 有due=%-4d 有start=%-4d status=%s"
        % (pid, len(tasks), n_content, n_desc, n_items, n_due, n_start,
           dict(Counter(t.get("status") for t in tasks)))
    )
    if tasks:
        print("     字段集合 =", sorted(tasks[0].keys()))
        for t in tasks[:4]:
            sort_samples.append(t)
            print(
                "       * sortOrder=%-18s prio=%-2s allDay=%-5s tz=%-20s kind=%-9s content=%-3s desc=%-3s items=%-3d title=%r"
                % (
                    t.get("sortOrder"), t.get("priority"), t.get("isAllDay"), t.get("timeZone"),
                    t.get("kind"), bool((t.get("content") or "").strip()),
                    bool((t.get("desc") or "").strip()), len(t.get("items") or []),
                    (t.get("title") or "")[:26],
                )
            )
print("   合计未完成任务 =", task_total)

print("=" * 72)
print("5) sortOrder 观察")
nums = [t.get("sortOrder") for t in sort_samples if isinstance(t.get("sortOrder"), (int, float))]
if nums:
    print("   样本数 =", len(nums), "| 最小 =", min(nums), "| 最大 =", max(nums),
          "| 负值个数 =", sum(1 for n in nums if n < 0))

print("=" * 72)
print("6) POST /task/filter —— 省略 projectIds 的语义（查询，不改数据）")
st, f1 = call("POST", "/task/filter", {"status": [0]})
print("   省略 projectIds: status =", st, "| 返回条数 =", len(f1) if isinstance(f1, list) else f1)
if isinstance(f1, list) and f1:
    print("   覆盖清单数 =", len({t.get("projectId") for t in f1}), "| 正好 200 条 =", len(f1) == 200)
st, f2 = call("POST", "/task/filter", {"projectIds": ids[:1], "status": [0]})
print("   指定 1 个 projectIds: 条数 =", len(f2) if isinstance(f2, list) else f2)
st, f3 = call("POST", "/task/filter", {"status": [2]})
print("   status=[2]（已完成）: 条数 =", len(f3) if isinstance(f3, list) else f3)
st, f4 = call("POST", "/task/filter", {})
print("   空 body: status =", st, "| 条数 =", len(f4) if isinstance(f4, list) else f4)

print("=" * 72)
print("探测结束（全程只读）")
