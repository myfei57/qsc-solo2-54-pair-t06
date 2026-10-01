"""流量计在线校验。

现场流量计缺乏校验手段时，用一路参照表做在线比对：校验方式定死为"按同量
走一遍"（``calib-v1.0``），按相对偏差对照最大允许偏差判合格/不合格；超差
记一条并标不合格，下次加注按计量点策略当场拦下或给提示。结论带规则版本，
换版即迁移、历史结论按当时版本解释；参照路异常属于部分失败，不出结论。

:class:`CalibService` 是装配入口，持有规则版本、计量点、参照路与校验/加注
两条流水，并与审计三方对账。
"""

from __future__ import annotations

from .point import MeteringPoint, MeteringPointRegistry
from .record import CalibRecord, FillGateRecord
from .reference import ReferenceMeter
from .reconcile import reconcile
from .rig import VerificationRig
from .rule import CalibRule, RuleBook
from .service import CalibService
from .snapshot import CalibSnapshot

__all__ = [
    "CalibService",
    "CalibRecord",
    "FillGateRecord",
    "CalibRule",
    "RuleBook",
    "MeteringPoint",
    "MeteringPointRegistry",
    "ReferenceMeter",
    "VerificationRig",
    "CalibSnapshot",
    "reconcile",
]
