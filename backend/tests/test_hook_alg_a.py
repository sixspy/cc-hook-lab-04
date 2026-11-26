"""失败测甲：签名规换版后，待投事件不得再用旧算法签出成功尝试。

夹具动作固定为：先改签名算法，再投待投事件。
在未挂「签名换版」的旧实现上，本文件必须见红。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from pathlib import Path

import pytest

os.environ["数据目录"] = tempfile.mkdtemp(prefix="hook04-pytest-a-")
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app.services import 入队, 投递, 签名目录, 夹具  # noqa: E402


@pytest.fixture
def 独立库(tmp_path, monkeypatch):
    import app.db as db

    库文件 = tmp_path / "test_hook_alg_a.db"
    monkeypatch.setattr(db, "库路径", 库文件)
    from app import seed

    seed.初始化库()
    return 库文件


def _旧版v1签名(密钥版本: str, 事件号: str, 摘要: str) -> str:
    """事故复现用的旧算法：与旧投递服务一致，HMAC-SHA256 后截断 24 位。"""
    raw = f"{密钥版本}|{事件号}|{摘要}".encode("utf-8")
    key = f"hook-secret-{密钥版本}".encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24]


def test_甲_换版后再投待投事件必须落新版指纹(独立库):
    签名目录.改签名规({"签名算法版本": "v1", "签名算法": "HMAC-SHA256-截断24"})

    eid = "EVT-HOOK-ALG-A"
    入队.建事件({
        "事件号": eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-partner",
        "载荷摘要": "hook-04-a",
    })

    # 夹具复现：签名规从 v1 切到 v2 后，再投同一张待投事件。
    签名目录.改签名规({
        "签名算法版本": "v2",
        "签名算法": "HMAC-SHA256-全文域分隔",
    })
    rep = 投递.投递一次(eid)

    assert rep["状态"] == "成功"
    assert rep.get("算法版本") == "v2", "投递响应必须直接给出本局算法版本"
    assert rep.get("算法指纹"), "成功尝试必须记下现行算法指纹"
    assert len(rep["签名摘要"]) == 64, "新版签名应输出全文摘要，不能继续截断 24 位"

    事故旧签 = _旧版v1签名("v1", eid, "hook-04-a")
    assert rep["签名摘要"] != 事故旧签, "换版后仍用旧算法签出成功，事故复现"

    import app.db as db
    conn = db.连接()
    try:
        row = dict(conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? AND 状态='成功' ORDER BY 投递时 DESC",
            (eid,),
        ).fetchone())
    finally:
        conn.close()

    assert row.get("算法版本") == "v2"
    assert row.get("算法指纹") == rep["算法指纹"]
    assert not (
        row["状态"] == "成功" and row.get("算法版本", "v1") != "v2"
    ), "旧算法成功尝试不得进入现行已投递集合"


def test_甲_显式旧算回投必须写中文拒因且不成功(独立库):
    签名目录.改签名规({"签名算法版本": "v2"})
    eid = "EVT-HOOK-ALG-A-拒旧"
    入队.建事件({
        "事件号": eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-partner",
        "载荷摘要": "hook-04-a-reject",
    })

    with pytest.raises(ValueError, match="旧算|旧算法|拒旧"):
        投递.投递一次(eid, 请求算法版本="v1", 回刷=True)

    import app.db as db
    conn = db.连接()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? ORDER BY 投递时 DESC", (eid,)
        ).fetchall()]
        ev = dict(conn.execute("SELECT 状态 FROM 事件 WHERE 事件号=?", (eid,)).fetchone())
    finally:
        conn.close()

    assert rows, "拒绝也必须落尝试账，不能只在页面假绿"
    row = rows[0]
    assert row["状态"] in ("失败", "拒绝")
    拒因 = f"{row.get('拒因', '')}{row.get('说明', '')}"
    assert "旧" in 拒因 and ("算法" in 拒因 or "回刷" in 拒因)
    assert not [r for r in rows if r["状态"] == "成功" and r.get("算法版本") == "v1"]
    assert ev["状态"] == "待投", "签名口径被拒后事件应留在待投，不能误报已投递"


def test_甲_点名夹具按_改签名算法后再投待投事件_复现(独立库):
    fx = 夹具.读取("改签名算法后再投待投事件")
    assert fx["骨签"] == "hook-04-签名换版拒旧"

    签名目录.改签名规(fx["签名规"]["旧版"])
    入队.建事件(fx["事件"])
    签名目录.改签名规(fx["签名规"]["新版"])

    rep = 投递.投递一次(fx["事件"]["事件号"])
    assert rep["状态"] == "成功"
    assert rep["算法版本"] == "v2"
    assert len(rep["签名摘要"]) == 64
