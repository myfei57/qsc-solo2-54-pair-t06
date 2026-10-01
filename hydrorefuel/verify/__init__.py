"""流量计在线校验组件。

校验方法定死为**分段比对**：校验量切成固定的若干段，被校表与参照路逐段
各自累计质量并算相对偏差，任何一段超差即判不合格。不采用"同量走一遍只比
总量"——总量会把各段正负偏差互相抵消，掩盖局部漂移，也与加注链路的阶梯
核对口径不一致。

每条结论都内嵌判定快照（版本、方法、公差、分段），换版属于迁移：迁移是
一次显式登记的操作，历史结论永远按当时那一版解释，不做重判。参照路异常
属于部分失败：本次校验只留记录、不出结论，计量点的既有合格状态不变。
"""

from __future__ import annotations

from .ledger import VerifyLedger
from .policy import METHOD, ON_FAIL_BLOCK, ON_FAIL_WARN, VerifyCriteria
from .reference import ReferenceLeg

__all__ = [
    "METHOD",
    "ON_FAIL_BLOCK",
    "ON_FAIL_WARN",
    "VerifyCriteria",
    "VerifyLedger",
    "ReferenceLeg",
]
