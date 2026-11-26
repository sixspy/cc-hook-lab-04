#!/usr/bin/env python3
"""hook-04 收工争用脚本：两个独立登录会话同时投同一事件，并在当刻换版。

用法：
  python3 scripts/concurrent_sign_lock.py [http://127.0.0.1:8504]

只使用标准库，避免评测机缺少 requests。退出码 0 表示同锁下只有一条成功尝试。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8504").rstrip("/")


def 新会话():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def 调用(opener, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    safe_path = urllib.parse.quote(path, safe="/?=&%")
    req = urllib.request.Request(BASE + safe_path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=35) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"错误": raw}


def 登录() :
    opener = 新会话()
    status, rep = 调用(opener, "POST", "/api/登录", {"账号": "admin", "密码": "123456"})
    if status != 200:
        raise RuntimeError(f"登录失败 {status}: {rep}")
    return opener


def main() -> int:
    admin = 登录()
    session_a = 登录()
    session_b = 登录()

    eid = f"EVT-LOCK-{uuid.uuid4().hex[:10]}"
    status, rep = 调用(admin, "POST", "/api/签名规/换版", {"目标算法版本": "v1"})
    if status != 200:
        raise RuntimeError(f"预置 v1 失败: {status} {rep}")
    status, rep = 调用(admin, "POST", "/api/事件/入队", {
        "事件号": eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-partner",
        "载荷摘要": f"lock-{time.time_ns()}",
    })
    if status != 200:
        raise RuntimeError(f"造事件失败: {status} {rep}")

    barrier = threading.Barrier(3)
    outcomes = {"甲": [], "乙": [], "换版": []}

    def 投递(名字: str, opener):
        barrier.wait(timeout=10)
        code, data = 调用(opener, "POST", "/api/投递", {"事件号": eid})
        outcomes[名字].append({"http": code, "data": data})

    def 换版():
        barrier.wait(timeout=10)
        code, data = 调用(admin, "POST", "/api/签名规/换版", {"目标算法版本": "v2"})
        outcomes["换版"].append({"http": code, "data": data})

    threads = [
        threading.Thread(target=投递, args=("甲", session_a), name="会话甲"),
        threading.Thread(target=投递, args=("乙", session_b), name="会话乙"),
        threading.Thread(target=换版, name="换版"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
        if t.is_alive():
            raise RuntimeError(f"线程 {t.name} 超时未结束")

    status, detail = 调用(admin, "GET", f"/api/事件/{eid}")
    if status != 200:
        raise RuntimeError(f"回读事件失败: {status} {detail}")
    successes = [a for a in detail.get("尝试", []) if a.get("状态") == "成功"]
    口径 = {(a.get("算法版本"), a.get("算法指纹")) for a in successes}

    print(json.dumps({
        "base": BASE,
        "事件号": eid,
        "并发响应": outcomes,
        "事件状态": detail.get("状态"),
        "成功尝试数": len(successes),
        "成功口径": sorted([list(x) for x in 口径], key=lambda x: str(x)),
        "成功尝试": [{
            "尝试号": a.get("尝试号"),
            "算法版本": a.get("算法版本"),
            "算法指纹": a.get("算法指纹"),
            "签名摘要": a.get("签名摘要"),
        } for a in successes],
    }, ensure_ascii=False, indent=2))

    if len(successes) != 1:
        print(f"红：同一事件成功尝试数={len(successes)}，存在双成功", file=sys.stderr)
        return 1
    if len(口径) != 1:
        print("红：成功尝试出现双算法口径", file=sys.stderr)
        return 2
    if detail.get("状态") != "已投递":
        print(f"红：事件命运={detail.get('状态')}，不是已投递", file=sys.stderr)
        return 3

    # 换版后，另一个待投事件若显式走 v1 回刷，必须 409 且落中文拒因。
    reject_eid = f"EVT-LOCK-REJ-{uuid.uuid4().hex[:8]}"
    调用(admin, "POST", "/api/事件/入队", {
        "事件号": reject_eid, "事件类型": "invoice.paid",
        "端点键": "ep-partner", "载荷摘要": "reject-old",
    })
    code, rejected = 调用(session_a, "POST", "/api/投递", {
        "事件号": reject_eid, "算法版本": "v1", "回刷": True,
    })
    if code != 409 or "旧算" not in str(rejected.get("错误", "")):
        print(f"红：旧算回刷未被 409 中文拒绝: {code} {rejected}", file=sys.stderr)
        return 4
    print(f"绿：双会话同锁只有一条成功；旧算回刷 {reject_eid} 返回 409 并落中文拒因。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
