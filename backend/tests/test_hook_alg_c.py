"""争用失败测：换版瞬间双投递请求打同一把库内写锁。

无锁时两个事务都可能读到待投/投递中状态，随后各写一条成功尝试，
形成同一事件双成功、且算法口径可能不一致。旧实现上本文件必须见红。
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

import pytest

os.environ["数据目录"] = tempfile.mkdtemp(prefix="hook04-pytest-c-")
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app.services import 入队, 投递, 签名目录  # noqa: E402


@pytest.fixture
def 独立库(tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setattr(db, "库路径", tmp_path / "test_hook_alg_c.db")
    from app import seed

    seed.初始化库()
    return db.库路径


def test_丙_换版瞬间双投递不得双成功双口径(独立库):
    签名目录.改签名规({"签名算法版本": "v1"})
    eid = "EVT-HOOK-ALG-C"
    入队.建事件({
        "事件号": eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-partner",
        "载荷摘要": "hook-04-c",
    })

    齐步 = threading.Barrier(3)
    成功响应: list[dict] = []
    异常: list[Exception] = []

    def 双路投递():
        齐步.wait(timeout=10)
        try:
            成功响应.append(投递.投递一次(eid))
        except Exception as exc:  # 争用负例允许一路收到中文状态拒绝
            异常.append(exc)

    def 同时换版():
        齐步.wait(timeout=10)
        签名目录.改签名规({"签名算法版本": "v2"})

    threads = [
        threading.Thread(target=双路投递, name="投递甲"),
        threading.Thread(target=双路投递, name="投递乙"),
        threading.Thread(target=同时换版, name="签名换版"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), f"线程 {t.name} 未在超时内结束"

    import app.db as db
    conn = db.连接()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? AND 状态='成功' ORDER BY 投递时",
            (eid,),
        ).fetchall()]
        ev = dict(conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (eid,)).fetchone())
    finally:
        conn.close()

    assert len(rows) == 1, f"同一事件出现 {len(rows)} 条成功尝试：{rows!r}"
    assert sum(1 for r in 成功响应 if r.get("状态") == "成功") == 1
    assert ev["状态"] == "已投递"

    # 成功行只能有一个算法口径；若它在换版锁前提交，则审计必须能识别为 v1，
    # 若在换版锁后提交，则必须是当前 v2，绝不允许同事件 v1/v2 双成功。
    口径 = {(r.get("算法版本") or "未记录", r.get("算法指纹") or "未记录") for r in rows}
    assert len(口径) == 1
    assert rows[0].get("算法版本") in ("v1", "v2")


def test_丙_连续并发同事件也只能有一条成功(独立库):
    eid = "EVT-HOOK-ALG-C-MANY"
    入队.建事件({
        "事件号": eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-partner",
        "载荷摘要": "hook-04-c-many",
    })

    齐步 = threading.Barrier(5)
    响应 = []

    def worker():
        齐步.wait(timeout=10)
        try:
            响应.append(投递.投递一次(eid))
        except ValueError:
            pass

    ts = [threading.Thread(target=worker) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    import app.db as db
    conn = db.连接()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态='成功'", (eid,)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert n == 1, f"并发投递产生 {n} 条成功尝试"
