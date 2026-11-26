#!/usr/bin/env python3
"""收工五页同源核对：页面 200 + 关键中文口径 + API 与 SQLite 逐行一致。"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8504").rstrip("/")
DB = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/hook04-verify/投递台.db")
BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
from app.services.签名目录 import 算法档  # noqa: E402

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    safe = urllib.parse.quote(path, safe="/?=&%")
    req = urllib.request.Request(BASE + safe, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=20) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else {})


def html(path: str) -> str:
    safe = urllib.parse.quote(path, safe="/?=&%")
    with opener.open(BASE + safe, timeout=20) as r:
        return r.read().decode()


call("POST", "/api/登录", {"账号": "admin", "密码": "123456"})
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

checks = [
    {
        "页": "签名规列表",
        "path": "/签名规.html",
        "must": ["签名规列表与换版拒旧", "现行算法版本与指纹", "签名规对账", "签名审计账册", "hook-04-签名换版拒旧"],
        "api": "/api/签名规",
        "table": "签名规",
        "key": "键",
    },
    {
        "页": "投递流水详情",
        "path": "/投递流水.html",
        "must": ["投递流水详情", "算法版本", "算法指纹", "签名摘要", "尝试成败/中文命运", "中文拒因/原因"],
        "api": "/api/投递",
        "table": "投递尝试",
        "key": "尝试号",
        "limit": 200,
    },
    {
        "页": "死信匣卡片",
        "path": "/死信匣.html",
        "must": ["死信匣卡片", "卡片视图", "末次中文原因", "死信重投"],
        "api": "/api/死信",
        "table": "死信",
        "key": "死信号",
    },
    {
        "页": "事件队列目录",
        "path": "/事件队列.html",
        "must": ["事件队列目录", "中文命运", "去投递", "事件详情"],
        "api": "/api/事件",
        "table": "事件",
        "key": "事件号",
    },
    {
        "页": "端点名册策略",
        "path": "/端点名册.html",
        "must": ["端点名册策略", "全端点共同遵守的签名策略", "端点密钥版本", "启用策略"],
        "api": "/api/端点",
        "table": "投递端点",
        "key": "端点键",
    },
]

failures = []
for c in checks:
    text = html(c["path"])
    missing = [x for x in c["must"] if x not in text]
    if missing:
        failures.append(f"{c['页']} 缺文案: {missing}")
        continue

    _, api_rows = call("GET", c["api"])
    if c["table"] == "签名规":
        db_rows = {r["键"]: r["值"] for r in conn.execute(f"SELECT 键,值 FROM {c['table']}")}
        if api_rows != db_rows:
            failures.append(f"{c['页']} API 与库内规则不一致")
            continue
    else:
        if c["table"] == "投递尝试":
            order = " ORDER BY 投递时 DESC, rowid DESC"
        elif c["table"] == "事件":
            order = " ORDER BY 入队时 DESC, rowid DESC"
        else:
            order = ""
        limit = f" LIMIT {c['limit']}" if c.get("limit") else ""
        db_rows = [r[c["key"]] for r in conn.execute(f"SELECT {c['key']} FROM {c['table']}{order}{limit}")]
        api_ids = [r[c["key"]] for r in api_rows]
        if api_ids != db_rows:
            failures.append(f"{c['页']} API 顺序/主键与库不一致: api={api_ids[:5]} db={db_rows[:5]}")
            continue
    print(f"OK {c['页']}: HTML 关键文案齐全；{c['api']} 与 {c['table']} 同源（{len(api_rows)} 行/键）")

_, rep = call("GET", "/api/签名规/对账")
db_rule = {r["键"]: r["值"] for r in conn.execute("SELECT 键,值 FROM 签名规")}
档 = 算法档(规=db_rule)
if rep.get("现行算法指纹") != 档["算法指纹"]:
    failures.append("签名对账指纹与签名规表推算指纹不一致")
else:
    print(f"OK 签名指纹同源: {档['算法版本']} / {档['算法指纹']}")

db_success = conn.execute("SELECT COUNT(*) AS c FROM 投递尝试 WHERE 状态='成功'").fetchone()["c"]
if "算法版本指纹" not in [r[1] for r in conn.execute("PRAGMA table_info(投递尝试)")]:
    failures.append("投递尝试缺 算法版本指纹 列")
else:
    print(f"OK 投递尝试含算法版本指纹列；库内成功尝试 {db_success} 条")

conn.close()
if failures:
    print("\n".join("RED " + x for x in failures), file=sys.stderr)
    raise SystemExit(1)
print("GREEN 五页、API、SQLite 逐页同源核对通过")
