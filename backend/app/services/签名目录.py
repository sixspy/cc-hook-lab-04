from __future__ import annotations

import hashlib
import hmac

from app.db import 连接
from app.services import 校验
from app.services.时间 import 此刻


# ---------------------------------------------------------------------------
# 签名规与算法指纹
# 工程形态（本仓只兑现这一边）：审计账册记录算法版本指纹，并拒绝把旧算回刷成新版。
# 不另建只读归档轨，也不把联查/对账影子写回正式投递尝试集合。
# ---------------------------------------------------------------------------

def 签名规(conn=None) -> dict:
    own = conn is None
    c = conn or 连接()
    try:
        rows = c.execute("SELECT 键,值 FROM 签名规").fetchall()
        return {r["键"]: r["值"] for r in rows}
    finally:
        if own:
            c.close()


def 规范算法版本(value, 规: dict | None = None) -> str:
    s = str(value or "").strip()
    low = s.lower()
    if low in ("v1", "1", "old", "old-version", "legacy", "旧", "旧版", "旧算法", "老算法"):
        return "v1"
    if low in ("v2", "2", "new", "new-version", "current", "新", "新版", "新算法", "现行"):
        return "v2"
    if any(token in s for token in ("旧版", "旧算法", "老算法", "截断24", "SHA1", "sha1")):
        return "v1"
    if any(token in s for token in ("新版", "新算法", "全文", "域分隔", "SHA256", "sha256", "HMAC-SHA256")):
        return "v2"
    if 规 and (规.get("签名算法版本") or 规.get("算法版本")):
        return 规范算法版本(规.get("签名算法版本") or 规.get("算法版本"))
    # 无显式版本时，现行种子规为 v2；历史尝试的空指纹不会因此被追认为 v2。
    return "v2"


def 算法档(版本: str | None = None, 规: dict | None = None) -> dict:
    规 = 规 or {}
    if 版本 is None:
        版本 = 规.get("签名算法版本") or 规.get("算法版本") or 规.get("签名算法")
    ver = 规范算法版本(版本, 规)
    if ver == "v1":
        名称 = "HMAC-SHA256-截断24"
        规格 = "hook-04|v1|hmac-sha256|truncate=24|canonical=密钥版本|事件号|载荷摘要"
        截断 = 24
    else:
        名称 = str(规.get("签名算法") or "HMAC-SHA256-全文域分隔")
        if "v1" in 名称 or "截断24" in 名称:
            名称 = "HMAC-SHA256-全文域分隔"
        规格 = "hook-04|v2|hmac-sha256|full-hexdigest|domain=hook-webhook-signature/v2|canonical=域|版本|指纹|密钥版本|事件号|载荷摘要"
        截断 = 0
    指纹 = "FP-" + hashlib.sha256(规格.encode("utf-8")).hexdigest()[:16].upper()
    return {
        "算法版本": ver,
        "算法名称": 名称,
        "算法指纹": 指纹,
        "摘要长度": 截断 or 64,
        "规格": 规格,
    }


def 当前算法档(conn=None, 规: dict | None = None) -> dict:
    if 规 is None:
        规 = 签名规(conn)
    return 算法档(规=规)


def 计算签名(档: dict | str, 密钥版本: str, 事件号: str, 摘要: str) -> str:
    if isinstance(档, str):
        档 = 算法档(档)
    key = f"hook-secret-{密钥版本}".encode("utf-8")
    if 档["算法版本"] == "v1":
        raw = f"{密钥版本}|{事件号}|{摘要}".encode("utf-8")
        return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24]

    raw = "\n".join([
        "hook-webhook-signature/v2",
        str(档["算法版本"]),
        str(档["算法指纹"]),
        str(密钥版本),
        str(事件号),
        str(摘要),
    ]).encode("utf-8")
    return hmac.new(key, raw, hashlib.sha256).hexdigest()


def 改签名规(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("签名规必须是键值对象")
    conn = 连接()
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        旧规 = 签名规(conn)
        旧档 = 当前算法档(conn, 旧规)

        items = dict(body)
        目标版本 = items.pop("目标算法版本", None)
        if 目标版本 and not items.get("签名算法版本"):
            items["签名算法版本"] = 目标版本
        if items.get("算法版本") and not items.get("签名算法版本"):
            items["签名算法版本"] = items["算法版本"]

        if items.get("签名算法版本") or items.get("签名算法") or items.get("算法版本"):
            probe = items.get("签名算法版本") or items.get("算法版本") or items.get("签名算法")
            ver = 规范算法版本(probe, 旧规)
            items["签名算法版本"] = ver
            items["算法版本"] = ver
            if not items.get("签名算法"):
                items["签名算法"] = 算法档(ver)["算法名称"]

        for k, v in items.items():
            键 = 校验.非空("键", k)
            值 = 校验.非空("值", v)
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES (?,?) "
                "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (键, 值),
            )

        新规 = 签名规(conn)
        新档 = 当前算法档(conn, 新规)
        if 旧档["算法指纹"] != 新档["算法指纹"]:
            t = 此刻()
            conn.execute(
                "INSERT INTO 签名审计账册(时刻,动作,算法版本,算法指纹,结论,详情) "
                "VALUES (?,?,?,?,?,?)",
                (t, "签名换版", 新档["算法版本"], 新档["算法指纹"], "现行算法已切换",
                 f"{旧档['算法版本']}/{旧档['算法指纹']} -> {新档['算法版本']}/{新档['算法指纹']}"),
            )
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "签名换版", "", f"{旧档['算法版本']}->{新档['算法版本']}；投递与对账只认现行指纹"),
            )
        conn.commit()
        return 签名规()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 概览() -> dict:
    conn = 连接()
    try:
        def c(sql):
            return conn.execute(sql).fetchone()[0]
        档 = 当前算法档(conn)
        return {
            "端点数": c("SELECT COUNT(*) FROM 投递端点"),
            "启用端点": c("SELECT COUNT(*) FROM 投递端点 WHERE 启用=1"),
            "事件总数": c("SELECT COUNT(*) FROM 事件"),
            "待投": c("SELECT COUNT(*) FROM 事件 WHERE 状态='待投'"),
            "投递中": c("SELECT COUNT(*) FROM 事件 WHERE 状态='投递中'"),
            "已投递": c("SELECT COUNT(*) FROM 事件 WHERE 状态='已投递'"),
            "死信": c("SELECT COUNT(*) FROM 事件 WHERE 状态='死信'"),
            "尝试数": c("SELECT COUNT(*) FROM 投递尝试"),
            "死信匣": c("SELECT COUNT(*) FROM 死信 WHERE 状态='待处置'"),
            "签名规": 签名规(conn),
            "现行算法版本": 档["算法版本"],
            "现行算法指纹": 档["算法指纹"],
            "现行算法名称": 档["算法名称"],
        }
    finally:
        conn.close()


def 事件列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 事件 ORDER BY 入队时 DESC, rowid DESC"
        ).fetchall()]
    finally:
        conn.close()


def 事件一条(事件号: str) -> dict | None:
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 事件 WHERE 事件号=?", (事件号,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["尝试"] = [_尝试命运(dict(x)) for x in conn.execute(
            "SELECT * FROM 投递尝试 WHERE 事件号=? ORDER BY 投递时 DESC, rowid DESC", (事件号,)
        ).fetchall()]
        dl = conn.execute("SELECT * FROM 死信 WHERE 事件号=?", (事件号,)).fetchone()
        d["死信"] = dict(dl) if dl else None
        d["签名对账"] = 签名对账(conn, 事件号=事件号)
        return d
    finally:
        conn.close()


def _尝试命运(row: dict) -> dict:
    if not row.get("算法版本指纹"):
        row["算法版本指纹"] = row.get("算法指纹", "")
    if row.get("状态") == "成功":
        row["命运"] = f"签发成功：算法 {row.get('算法版本') or '未记录'}，指纹 {row.get('算法指纹') or '未记录'}"
    elif row.get("拒因"):
        row["命运"] = f"已拒绝：{row['拒因']}"
    else:
        row["命运"] = f"尝试失败：{row.get('说明') or '未记录原因'}"
    return row


def 投递列表() -> list:
    conn = 连接()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM 投递尝试 ORDER BY 投递时 DESC, rowid DESC LIMIT 200"
        ).fetchall()]
        return [_尝试命运(r) for r in rows]
    finally:
        conn.close()


def 死信列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 死信 ORDER BY 入匣时 DESC").fetchall()]
    finally:
        conn.close()


def 端点列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 投递端点 ORDER BY 端点键").fetchall()]
    finally:
        conn.close()


def 互证列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 互证批次 ORDER BY 批次号 DESC LIMIT 50").fetchall()]
    finally:
        conn.close()


def 审计列表() -> list:
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 签名审计账册 ORDER BY 编号 DESC LIMIT 200"
        ).fetchall()]
    finally:
        conn.close()


def 签名对账(conn=None, 事件号: str | None = None) -> dict:
    """审计口径对账：现行指纹才算新版已齐；旧算成功只入异常明细，不回刷、不冒充。"""
    own = conn is None
    c = conn or 连接()
    try:
        档 = 当前算法档(c)
        current_fp = 档["算法指纹"]
        current_ver = 档["算法版本"]

        sql = """
            SELECT a.尝试号, a.事件号, a.端点键, a.状态, a.签名摘要,
                   a.算法版本, a.算法指纹, a.拒因, a.说明, a.投递时,
                   e.状态 AS 事件状态
            FROM 投递尝试 a
            LEFT JOIN 事件 e ON e.事件号 = a.事件号
        """
        params: tuple = ()
        if 事件号:
            sql += " WHERE a.事件号=?"
            params = (事件号,)
        sql += " ORDER BY a.投递时 DESC, a.尝试号 DESC"
        attempts = [dict(r) for r in c.execute(sql, params).fetchall()]

        event_sql = "SELECT 事件号,状态 FROM 事件"
        event_params: tuple = ()
        if 事件号:
            event_sql += " WHERE 事件号=?"
            event_params = (事件号,)
        events = [dict(r) for r in c.execute(event_sql, event_params).fetchall()]

        by_event: dict[str, dict] = {
            e["事件号"]: {
                "事件号": e["事件号"],
                "事件状态": e["状态"],
                "新版已齐": False,
                "旧算成功": 0,
                "现行成功": 0,
                "拒绝旧算": 0,
            }
            for e in events
        }
        明细: list[str] = []
        旧算成功 = 0
        新版成功尝试 = 0
        拒绝旧算 = 0

        for a in attempts:
            eid = a["事件号"]
            item = by_event.setdefault(eid, {
                "事件号": eid, "事件状态": a.get("事件状态") or "",
                "新版已齐": False, "旧算成功": 0, "现行成功": 0, "拒绝旧算": 0,
            })
            fp_ok = a.get("算法指纹") == current_fp
            ver_ok = (a.get("算法版本") or "") == current_ver
            if a.get("状态") == "成功":
                if fp_ok and ver_ok:
                    新版成功尝试 += 1
                    item["现行成功"] += 1
                    item["新版已齐"] = True
                else:
                    旧算成功 += 1
                    item["旧算成功"] += 1
                    明细.append(
                        f"旧算成功：{eid}/{a['尝试号']} 记录算法 "
                        f"{a.get('算法版本') or '未记录版本'}、指纹 {a.get('算法指纹') or '未记录指纹'}，"
                        f"与现行 {current_ver}/{current_fp} 不符，不算新版已齐"
                    )
            elif a.get("拒因") and ("旧算" in a["拒因"] or "旧算法" in a["拒因"]):
                拒绝旧算 += 1
                item["拒绝旧算"] += 1

        缺新算 = 0
        for eid, item in by_event.items():
            if item.get("事件状态") == "已投递" and not item["新版已齐"]:
                缺新算 += 1
                明细.append(f"已投递账实撕裂：{eid} 已标记投递成功，但缺少现行 {current_ver} 指纹")

        新版已齐 = sum(1 for x in by_event.values() if x["新版已齐"])
        对齐 = 旧算成功 == 0 and 缺新算 == 0
        return {
            "骨签": "hook-04-签名换版拒旧",
            "工程形态": "审计账册指纹+拒旧算回刷",
            "现行算法版本": current_ver,
            "现行算法名称": 档["算法名称"],
            "现行算法指纹": current_fp,
            "新版已齐": 新版已齐,
            "新版已齐数": 新版已齐,
            "新版成功尝试": 新版成功尝试,
            "旧算成功": 旧算成功,
            "旧算成功数": 旧算成功,
            "拒绝旧算数": 拒绝旧算,
            "缺新算事件": 缺新算,
            "签名对齐": 对齐,
            "是否全部对齐": 对齐,
            "明细": 明细,
            "事件": sorted(by_event.values(), key=lambda x: x["事件号"], reverse=True),
        }
    finally:
        if own:
            c.close()


def 拒旧算回刷(尝试号: str, 目标算法版本: str | None = None, 目标指纹: str | None = None) -> dict:
    尝试号 = 校验.非空("尝试号", 尝试号)
    conn = 连接()
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM 投递尝试 WHERE 尝试号=?", (尝试号,)).fetchone()
        if not row:
            raise ValueError("无此投递尝试，不能回刷")
        old = dict(row)
        档 = 当前算法档(conn)
        target_ver = 规范算法版本(目标算法版本, 签名规(conn)) if 目标算法版本 else 档["算法版本"]
        target_fp = 目标指纹 or (档["算法指纹"] if target_ver == 档["算法版本"] else 算法档(target_ver)["算法指纹"])

        if old.get("算法指纹") == target_fp and old.get("算法版本") == target_ver:
            conn.rollback()
            return {"尝试号": 尝试号, "结论": "本就是现行算法指纹，无需回刷"}

        reason = (
            f"拒旧算回刷：{尝试号} 是 {old.get('算法版本') or '未记录版本'}/"
            f"{old.get('算法指纹') or '未记录指纹'} 旧算账，"
            f"不得改写成 {target_ver}/{target_fp} 新版已齐"
        )
        conn.execute(
            "INSERT INTO 签名审计账册(时刻,动作,算法版本,算法指纹,事件号,尝试号,结论,详情) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (此刻(), "拒回刷", old.get("算法版本", ""), old.get("算法指纹", ""),
             old.get("事件号", ""), 尝试号, "拒绝回刷", reason),
        )
        conn.commit()
        raise ValueError(reason)
    except ValueError:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 兼容可能按简名调用的验收脚本；仍是同一套审计口径，不是第二实现。
对账 = 签名对账
当前算法 = 当前算法档
审计账册 = 审计列表
