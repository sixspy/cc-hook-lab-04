from __future__ import annotations

from app.db import 连接
from app.services.时间 import 此刻
from app.services import 签名目录


def 跑一次() -> dict:
    t = 此刻()
    conn = 连接()
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        总 = conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"]
        已 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
        死 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='死信'").fetchone()["c"]
        待 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态 IN ('待投','投递中')").fetchone()["c"]
        匣 = conn.execute("SELECT COUNT(*) AS c FROM 死信 WHERE 状态='待处置'").fetchone()["c"]

        签名账 = 签名目录.签名对账(conn)
        数量对齐 = 死 == 匣 and (已 + 死 + 待) <= 总
        对齐 = 1 if (数量对齐 and 签名账["签名对齐"]) else 0
        if 对齐:
            说明 = "事件/投递/死信口径对齐，且成功尝试均为签名规现行算法指纹"
        else:
            fragments = []
            if 死 != 匣:
                fragments.append("死信匣与事件死信数不一致")
            if not 数量对齐 and 死 == 匣:
                fragments.append("水位异常")
            if not 签名账["签名对齐"]:
                fragments.append(
                    f"签名口径撕裂：旧算成功 {签名账['旧算成功数']}、"
                    f"缺新算事件 {签名账['缺新算事件']}"
                )
            说明 = "；".join(fragments) or "对账未对齐"

        conn.execute(
            """
            INSERT INTO 互证批次(
              时刻,事件总数,已投递数,死信数,待投数,是否对齐,说明,
              签名对齐,新版已齐数,旧算成功数,签名异常数
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (t, 总, 已, 死, 待, 对齐, 说明,
             1 if 签名账["签名对齐"] else 0,
             签名账["新版已齐数"], 签名账["旧算成功数"],
             签名账["旧算成功数"] + 签名账["缺新算事件"]),
        )
        conn.commit()
        return {
            "时刻": t,
            "事件总数": 总,
            "已投递数": 已,
            "死信数": 死,
            "待投数": 待,
            "死信匣待处置": 匣,
            "是否对齐": bool(对齐),
            "是否全部对齐": bool(对齐),
            "签名对齐": 签名账["签名对齐"],
            "新版已齐数": 签名账["新版已齐数"],
            "旧算成功数": 签名账["旧算成功数"],
            "缺新算事件": 签名账["缺新算事件"],
            "现行算法版本": 签名账["现行算法版本"],
            "现行算法指纹": 签名账["现行算法指纹"],
            "签名明细": 签名账["明细"],
            "说明": 说明,
            "明细": {
                "总": 总, "已": 已, "死": 死, "待": 待, "匣": 匣,
                "签名": 签名账,
            },
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
