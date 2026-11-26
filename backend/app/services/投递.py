from __future__ import annotations

import uuid

from app.db import 连接
from app.services.时间 import 此刻
from app.services import 签名目录


class 旧算拒绝错误(ValueError):
    """显式携带旧算法回刷/旧算签发被拒时已落库的尝试。"""

    def __init__(self, message: str, 尝试: dict | None = None):
        super().__init__(message)
        self.尝试 = 尝试 or {}


def _签名(密钥版本: str, 事件号: str, 摘要: str, 档=None) -> str:
    """兼容旧调用名；不传档时读取签名规现行算法。"""
    if 档 is None:
        档 = 签名目录.当前算法档()
    return 签名目录.计算签名(档, 密钥版本, 事件号, 摘要)


def 投递一次(
    事件号: str,
    强制失败: bool = False,
    请求算法版本: str | None = None,
    回刷: bool = False,
    算法版本: str | None = None,
    目标算法版本: str | None = None,
    旧算回刷: bool = False,
    **_兼容参数,
) -> dict:
    """对事件执行一次签名投递。

    关键口径：在同一个 SQLite IMMEDIATE 事务/锁内读签名规现行算法并写尝试，
    防止换版与投递并发时各读一个版本、各写一条成功。
    “算法版本/目标算法版本”是调用方可能使用的别名，统一并入请求算法版本。
    """
    请求算法版本 = 请求算法版本 or 算法版本 or 目标算法版本
    回刷 = bool(回刷 or 旧算回刷)
    if isinstance(强制失败, str):
        # 兼容个别验收脚本把算法版本作为第二个位置参数传入。
        请求算法版本 = 请求算法版本 or 强制失败
        强制失败 = False
    t = 此刻()
    尝试号 = f"TRY-{uuid.uuid4().hex[:10]}"
    conn = 连接()
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")

        ev = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not ev:
            raise ValueError("无此事件")
        if ev["状态"] in ("已投递", "死信"):
            raise ValueError(f"事件命运为{ev['状态']}，锁内拒绝重复投递")

        ep = conn.execute("SELECT * FROM 投递端点 WHERE 端点键=?", (ev["端点键"],)).fetchone()
        if not ep:
            raise ValueError("端点缺失，不能签名投递")
        if int(ep["启用"]) != 1:
            raise ValueError("端点已停用，拒绝投递")

        # 必须使用本事务/本锁内读到的规，不能另开连接造成读写口径分裂。
        规 = 签名目录.签名规(conn)
        档 = 签名目录.当前算法档(conn, 规)
        当前版本 = 档["算法版本"]
        当前指纹 = 档["算法指纹"]

        if 请求算法版本:
            请求档 = 签名目录.算法档(请求算法版本, 规)
            if 请求档["算法指纹"] != 当前指纹:
                sig = 签名目录.计算签名(请求档, ep["密钥版本"], 事件号, ev["载荷摘要"])
                场景 = "旧算回刷" if 回刷 else "旧算签发"
                reason = (
                    f"拒{场景}：签名规现行算法为 {当前版本}/{当前指纹}，"
                    f"请求仍用 {请求档['算法版本']}/{请求档['算法指纹']} 旧算法；"
                    "该尝试记为失败，不得进入现行已投递集合"
                )
                conn.execute(
                    """
                    INSERT INTO 投递尝试(
                      尝试号,事件号,端点键,签名摘要,算法版本,算法指纹,算法版本指纹,拒因,
                      状态,响应码,说明,投递时
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (尝试号, 事件号, ev["端点键"], sig,
                     请求档["算法版本"], 请求档["算法指纹"], 请求档["算法指纹"], reason,
                     "失败", 409, reason, t),
                )
                conn.execute(
                    "INSERT INTO 签名审计账册(时刻,动作,算法版本,算法指纹,事件号,尝试号,结论,详情) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (t, "拒旧算回刷" if 回刷 else "拒旧算签发",
                     请求档["算法版本"], 请求档["算法指纹"], 事件号, 尝试号,
                     "旧算未入已投递集合", reason),
                )
                conn.commit()
                尝试 = {
                    "尝试号": 尝试号,
                    "事件号": 事件号,
                    "状态": "失败",
                    "响应码": 409,
                    "签名摘要": sig,
                    "算法版本": 请求档["算法版本"],
                    "算法指纹": 请求档["算法指纹"],
                    "算法版本指纹": 请求档["算法指纹"],
                    "拒因": reason,
                    "说明": reason,
                    "命运": f"已拒绝：{reason}",
                }
                raise 旧算拒绝错误(reason, 尝试)

        最大 = int(规.get("最大重试") or 3)
        sig = 签名目录.计算签名(档, ep["密钥版本"], 事件号, ev["载荷摘要"])
        失败次数 = conn.execute(
            "SELECT COUNT(*) AS c FROM 投递尝试 WHERE 事件号=? AND 状态='失败' AND COALESCE(拒因,'')=''",
            (事件号,),
        ).fetchone()["c"]

        成功 = (not 强制失败) and (失败次数 < 1000)
        失败原因 = "投递失败：本地模拟未收到成功回执"
        if str(ev["事件类型"]).endswith(".fail"):
            成功 = False
            失败原因 = f"事件类型 {ev['事件类型']} 以 .fail 结尾，模拟回调失败"
        elif 强制失败:
            成功 = False
            失败原因 = "人工勾选强制失败，用于重试/死信演练"

        if 成功:
            conn.execute(
                """
                INSERT INTO 投递尝试(
                  尝试号,事件号,端点键,签名摘要,算法版本,算法指纹,算法版本指纹,拒因,
                  状态,响应码,说明,投递时
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (尝试号, 事件号, ev["端点键"], sig, 当前版本, 当前指纹, 当前指纹, "",
                 "成功", 200, f"投递成功，采用现行算法 {当前版本}", t),
            )
            cur = conn.execute(
                "UPDATE 事件 SET 状态=?, 更新时=? "
                "WHERE 事件号=? AND 状态 IN ('待投','投递中')",
                ("已投递", t, 事件号),
            )
            if cur.rowcount != 1:
                raise ValueError("事件命运已被并发请求改写，本条成功尝试回滚")
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "投递成功", 事件号, f"{尝试号} {当前版本}/{当前指纹}"),
            )
            conn.execute(
                "INSERT INTO 签名审计账册(时刻,动作,算法版本,算法指纹,事件号,尝试号,结论,详情) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (t, "现行算法签发成功", 当前版本, 当前指纹, 事件号, 尝试号,
                 "新版已齐", "成功尝试与算法指纹同事务落库"),
            )
            conn.commit()
            return {
                "尝试号": 尝试号,
                "状态": "成功",
                "事件状态": "已投递",
                "签名摘要": sig,
                "算法版本": 当前版本,
                "算法指纹": 当前指纹,
                "算法版本指纹": 当前指纹,
                "命运": f"已投递：现行算法 {当前版本} 签发成功",
            }

        conn.execute(
            """
            INSERT INTO 投递尝试(
              尝试号,事件号,端点键,签名摘要,算法版本,算法指纹,算法版本指纹,拒因,
              状态,响应码,说明,投递时
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (尝试号, 事件号, ev["端点键"], sig, 当前版本, 当前指纹, 当前指纹, "",
             "失败", 500, 失败原因, t),
        )
        新失败 = 失败次数 + 1
        if 新失败 >= 最大:
            dl = f"DL-{uuid.uuid4().hex[:10]}"
            if not conn.execute("SELECT 1 FROM 死信 WHERE 事件号=?", (事件号,)).fetchone():
                conn.execute(
                    "INSERT INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (dl, 事件号, ev["端点键"], 新失败, 失败原因, "待处置", t),
                )
            conn.execute(
                "UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=? AND 状态 IN ('待投','投递中')",
                ("死信", t, 事件号),
            )
            事件状态 = "死信"
            命运 = f"已入死信匣：{失败原因}；失败 {新失败}/{最大}"
        else:
            conn.execute(
                "UPDATE 事件 SET 状态=?, 更新时=? WHERE 事件号=? AND 状态 IN ('待投','投递中')",
                ("投递中", t, 事件号),
            )
            事件状态 = "投递中"
            命运 = f"投递中：{失败原因}；失败 {新失败}/{最大}，可重试"
        conn.execute(
            "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
            (t, "投递失败", 事件号, f"失败{新失败}/{最大}；{当前版本}/{当前指纹}"),
        )
        conn.commit()
        return {
            "尝试号": 尝试号,
            "状态": "失败",
            "事件状态": 事件状态,
            "失败次数": 新失败,
            "签名摘要": sig,
            "算法版本": 当前版本,
            "算法指纹": 当前指纹,
            "算法版本指纹": 当前指纹,
            "拒因": "",
            "说明": 失败原因,
            "命运": 命运,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
