from __future__ import annotations

from app.db import 连接
from app.services import 签名目录

建表 = """
CREATE TABLE IF NOT EXISTS 用户 (
  账号 TEXT PRIMARY KEY,
  密码哈希 TEXT NOT NULL,
  角色 TEXT NOT NULL DEFAULT '管理员'
);
CREATE TABLE IF NOT EXISTS 签名规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递端点 (
  端点键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  回调URL TEXT NOT NULL,
  密钥版本 TEXT NOT NULL DEFAULT 'v1',
  启用 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 事件 (
  事件号 TEXT PRIMARY KEY,
  事件类型 TEXT NOT NULL,
  端点键 TEXT NOT NULL DEFAULT '',
  载荷摘要 TEXT NOT NULL DEFAULT '',
  优先级 TEXT NOT NULL DEFAULT '普通',
  状态 TEXT NOT NULL DEFAULT '待投',
  入队时 TEXT NOT NULL,
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递尝试 (
  尝试号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL,
  端点键 TEXT NOT NULL,
  签名摘要 TEXT NOT NULL DEFAULT '',
  算法版本 TEXT NOT NULL DEFAULT '',
  算法指纹 TEXT NOT NULL DEFAULT '',
  算法版本指纹 TEXT NOT NULL DEFAULT '',
  拒因 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '失败',
  响应码 INTEGER NOT NULL DEFAULT 0,
  说明 TEXT NOT NULL DEFAULT '',
  投递时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号),
  FOREIGN KEY (端点键) REFERENCES 投递端点(端点键)
);
CREATE TABLE IF NOT EXISTS 死信 (
  死信号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL UNIQUE,
  端点键 TEXT NOT NULL,
  失败次数 INTEGER NOT NULL DEFAULT 0,
  末次原因 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '待处置',
  入匣时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号)
);
CREATE TABLE IF NOT EXISTS 互证批次 (
  批次号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  事件总数 INTEGER NOT NULL,
  已投递数 INTEGER NOT NULL,
  死信数 INTEGER NOT NULL,
  待投数 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL,
  说明 TEXT NOT NULL,
  签名对齐 INTEGER NOT NULL DEFAULT 1,
  新版已齐数 INTEGER NOT NULL DEFAULT 0,
  旧算成功数 INTEGER NOT NULL DEFAULT 0,
  签名异常数 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS 操作留痕 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  操作 TEXT NOT NULL,
  事件号 TEXT NOT NULL DEFAULT '',
  详情 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 签名审计账册 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  动作 TEXT NOT NULL,
  算法版本 TEXT NOT NULL DEFAULT '',
  算法指纹 TEXT NOT NULL DEFAULT '',
  事件号 TEXT NOT NULL DEFAULT '',
  尝试号 TEXT NOT NULL DEFAULT '',
  结论 TEXT NOT NULL DEFAULT '',
  详情 TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_签名审计_对象 ON 签名审计账册(事件号, 尝试号);
CREATE VIEW IF NOT EXISTS 审计账册 AS
SELECT 编号, 时刻, 动作, 算法版本, 算法指纹, 事件号, 尝试号, 结论, 详情
FROM 签名审计账册;
"""


def 此刻() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _补列(conn, 表名: str, 列名: str, 列定义: str) -> None:
    列 = [r[1] for r in conn.execute(f"PRAGMA table_info({表名})").fetchall()]
    if 列名 not in 列:
        conn.execute(f"ALTER TABLE {表名} ADD COLUMN {列名} {列定义}")


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        # 旧库平滑补列；不回刷历史签名指纹，旧行将被对账如实识别为旧算/未记录。
        _补列(conn, "投递尝试", "算法版本", "TEXT NOT NULL DEFAULT ''")
        _补列(conn, "投递尝试", "算法指纹", "TEXT NOT NULL DEFAULT ''")
        _补列(conn, "投递尝试", "算法版本指纹", "TEXT NOT NULL DEFAULT ''")
        _补列(conn, "投递尝试", "拒因", "TEXT NOT NULL DEFAULT ''")
        _补列(conn, "互证批次", "签名对齐", "INTEGER NOT NULL DEFAULT 1")
        _补列(conn, "互证批次", "新版已齐数", "INTEGER NOT NULL DEFAULT 0")
        _补列(conn, "互证批次", "旧算成功数", "INTEGER NOT NULL DEFAULT 0")
        _补列(conn, "互证批次", "签名异常数", "INTEGER NOT NULL DEFAULT 0")
        # 冗余别名字段只同行复制既有指纹；绝不按现行规重算或改写历史算法版本。
        conn.execute(
            "UPDATE 投递尝试 SET 算法版本指纹=算法指纹 "
            "WHERE COALESCE(算法版本指纹,'')='' AND COALESCE(算法指纹,'')<>''"
        )
        # 索引必须在旧库补列之后创建，否则旧表会因缺 算法版本 列而初始化失败。
        conn.execute("CREATE INDEX IF NOT EXISTS idx_投递尝试_事件 ON 投递尝试(事件号)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_投递尝试_算法 "
            "ON 投递尝试(算法版本, 算法指纹, 状态)"
        )

        if conn.execute("SELECT COUNT(*) AS c FROM 用户").fetchone()["c"] == 0:
            conn.execute("INSERT INTO 用户(账号,密码哈希,角色) VALUES (?,?,?)", ("admin", "123456", "管理员"))

        默认规 = {
            "最大重试": "3",
            "产品名": "投递台",
            "主链": "端点登记→事件入队→签名投递→失败重试与死信→投递账互证",
            "签名算法版本": "v2",
            "算法版本": "v2",
            "签名算法": "HMAC-SHA256-全文域分隔",
            "旧版签名算法": "v1/HMAC-SHA256-截断24",
            "骨签": "hook-04-签名换版拒旧",
            "超时秒": "5",
        }
        for k, v in 默认规.items():
            conn.execute(
                "INSERT INTO 签名规(键,值) VALUES (?,?) ON CONFLICT(键) DO NOTHING",
                (k, v),
            )

        if conn.execute("SELECT COUNT(*) AS c FROM 投递端点").fetchone()["c"] == 0:
            for row in [
                ("ep-billing", "账单回调", "https://example.local/hooks/billing", "v1", 1, "账务"),
                ("ep-notify", "通知回调", "https://example.local/hooks/notify", "v1", 1, "通知"),
                ("ep-audit", "审计回调", "https://example.local/hooks/audit", "v2", 1, "审计"),
                ("ep-legacy", "旧版回调", "https://example.local/hooks/legacy", "v1", 0, "停用"),
                ("ep-partner", "合作方回调", "https://partner.local/ingest", "v1", 1, "外部"),
            ]:
                conn.execute(
                    "INSERT INTO 投递端点(端点键,名称,回调URL,密钥版本,启用,说明) VALUES (?,?,?,?,?,?)",
                    row,
                )
        if conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"] == 0:
            t = 此刻()
            for row in [
                ("EVT-demo-01", "invoice.paid", "ep-billing", "invoice=1001", "普通", "待投"),
                ("EVT-demo-02", "user.created", "ep-notify", "user=u42", "普通", "投递中"),
                ("EVT-demo-03", "order.refund", "ep-audit", "order=908", "紧急", "待投"),
                ("EVT-demo-04", "user.updated", "ep-notify", "user=u7", "低", "已投递"),
                ("EVT-demo-05", "invoice.failed", "ep-billing", "invoice=1002", "普通", "死信"),
            ]:
                conn.execute(
                    "INSERT INTO 事件(事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时) VALUES (?,?,?,?,?,?,?,?)",
                    (*row, t, t),
                )
            档 = 签名目录.算法档("v2")
            demo_sig = 签名目录.计算签名(档, "v1", "EVT-demo-04", "user=u7")
            conn.execute(
                "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,算法版本,算法指纹,算法版本指纹,拒因,状态,响应码,说明,投递时) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TRY-demo-01", "EVT-demo-04", "ep-notify", demo_sig, "v2", 档["算法指纹"], 档["算法指纹"], "",
                 "成功", 200, "投递成功", t),
            )
            conn.execute(
                "INSERT INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时) VALUES (?,?,?,?,?,?,?)",
                ("DL-demo-01", "EVT-demo-05", "ep-billing", 3, "连接超时", "待处置", t),
            )
            conn.execute(
                "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
                (t, "种子", "", "初始化投递台样例"),
            )
            conn.execute(
                "INSERT INTO 签名审计账册(时刻,动作,算法版本,算法指纹,事件号,尝试号,结论,详情) VALUES (?,?,?,?,?,?,?,?)",
                (t, "初始化算法指纹", "v2", 档["算法指纹"], "", "TRY-demo-01", "新版已齐", "hook-04-签名换版拒旧"),
            )
        conn.commit()
    finally:
        conn.close()
