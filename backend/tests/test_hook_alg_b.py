"""失败测乙：签名规对账不得把旧算成功当成新版已齐。

审计账册路径：投递尝试/审计账册记录算法版本与指纹；
旧尝试只能被识别为旧算，禁止回刷成新版指纹。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["数据目录"] = tempfile.mkdtemp(prefix="hook04-pytest-b-")
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app.services import 入队, 投递, 互证, 签名目录  # noqa: E402


@pytest.fixture
def 独立库(tmp_path, monkeypatch):
    import app.db as db
    from app import seed

    monkeypatch.setattr(db, "库路径", tmp_path / "test_hook_alg_b.db")
    conn = db.连接()
    try:
        conn.executescript(seed.建表)
        for key, name in (("ep-billing", "账单回调"), ("ep-partner", "合作方回调")):
            conn.execute(
                "INSERT INTO 投递端点(端点键,名称,回调URL,密钥版本,启用,说明) "
                "VALUES (?,?,?,?,1,'测试')",
                (key, name, f"https://example.test/{key}", "v1"),
            )
        conn.commit()
    finally:
        conn.close()
    return db.库路径


def test_乙_对账区分旧算成功与新版已齐并拒绝回刷(独立库):
    签名目录.改签名规({"签名算法版本": "v1"})
    old_eid = "EVT-HOOK-ALG-B-OLD"
    入队.建事件({
        "事件号": old_eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-billing",
        "载荷摘要": "hook-04-b-old",
    })
    old_rep = 投递.投递一次(old_eid)
    assert old_rep["状态"] == "成功"
    old_try = old_rep["尝试号"]

    # 签名规换版；旧成功尝试留在账上，但不能算新版已齐。
    签名目录.改签名规({"签名算法版本": "v2"})

    rep = 互证.跑一次()
    assert rep.get("签名对齐") is False, "只有旧算成功时，签名规对账必须红"
    assert rep.get("新版已齐数", 0) == 0
    assert rep.get("旧算成功数", 0) >= 1
    assert rep.get("是否全部对齐") is False

    catalog = 签名目录.签名对账()
    assert catalog["现行算法版本"] == "v2"
    assert catalog["新版已齐"] == 0
    assert catalog["旧算成功"] >= 1
    assert catalog["是否全部对齐"] is False
    assert any(old_eid in str(x) and "旧算" in str(x) for x in catalog["明细"])

    # 审计账册拒绝把旧指纹回刷成新版。
    with pytest.raises(ValueError, match="拒旧算回刷|旧算|回刷"):
        签名目录.拒旧算回刷(old_try, 目标算法版本="v2")

    import app.db as db
    conn = db.连接()
    try:
        row = dict(conn.execute(
            "SELECT 算法版本,算法指纹 FROM 投递尝试 WHERE 尝试号=?", (old_try,)
        ).fetchone())
    finally:
        conn.close()
    assert row.get("算法版本") != "v2"
    assert row.get("算法指纹") != catalog["现行算法指纹"]


def test_乙_新算投递后只认新算集合(独立库):
    签名目录.改签名规({"签名算法版本": "v1"})
    old_eid = "EVT-HOOK-ALG-B-OLD-2"
    入队.建事件({
        "事件号": old_eid,
        "事件类型": "invoice.paid",
        "端点键": "ep-billing",
        "载荷摘要": "old",
    })
    投递.投递一次(old_eid)

    签名目录.改签名规({"签名算法版本": "v2"})
    new_eid = "EVT-HOOK-ALG-B-NEW"
    入队.建事件({
        "事件号": new_eid,
        "事件类型": "order.paid",
        "端点键": "ep-partner",
        "载荷摘要": "new",
    })
    new_rep = 投递.投递一次(new_eid)
    assert new_rep["算法版本"] == "v2"

    catalog = 签名目录.签名对账()
    assert catalog["新版已齐"] == 1
    assert catalog["旧算成功"] >= 1
    事件项 = next(x for x in catalog["事件"] if x["事件号"] == new_eid)
    assert 事件项["新版已齐"] is True
    old项 = next(x for x in catalog["事件"] if x["事件号"] == old_eid)
    assert old项["新版已齐"] is False
