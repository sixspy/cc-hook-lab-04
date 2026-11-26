"""hook-04-签名换版拒旧：已挂接的真实模块。

工程形态：审计账册记录算法版本指纹，并拒绝旧算/回刷冒充新版已齐。
Web 路由 /api/签名换版 与 /api/签名规/换版 都进入 app.services.签名目录.改签名规。
"""
from __future__ import annotations

from app.services import 投递, 签名目录

NAME = "签名换版"
BONE = "hook-04-签名换版拒旧"
ENGINEERING = "审计账册算法指纹+拒旧算回刷"

# 兼容“模块直接导出投递动作”的调用方式。
投递一次 = 投递.投递一次

__all__ = ["NAME", "BONE", "ENGINEERING", "换版", "对账", "拒旧算回刷", "投递一次"]


def 换版(目标算法版本: str) -> dict:
    return 签名目录.改签名规({"目标算法版本": 目标算法版本})


def 对账() -> dict:
    return 签名目录.签名对账()


def 拒旧算回刷(尝试号: str, 目标算法版本: str = "v2") -> dict:
    return 签名目录.拒旧算回刷(尝试号, 目标算法版本=目标算法版本)
