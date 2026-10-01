"""校验记录与加注门禁记录。

两类记录都是只追加的不可变流水：

* :class:`CalibRecord` —— 一次在线校验。成功出具结论时嵌一份**出具时的规则
  快照**，因此规则版本迁移后，历史结论永远按当时那一版解释；参照路异常等
  部分失败时结论为 ``inconclusive``，不带合格/不合格，不改变计量点状态。
* :class:`FillGateRecord` —— 加注前的一次门禁判定，记录校验依据、处置策略
  与操作员确认，使"凭哪条校验结论放的行/拦的车"可以逐条回溯。

记录只负责结构，落盘由 :class:`CalibService` 统一完成，保证校验记录、加注
记录与审计三条线的 ``record_id`` 完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rule import CONCLUSION_NONE

# 门禁处置：拦下 / 放行但提示 / 直接放行。
GATE_BLOCK = "blocked"
GATE_WARN = "warned"
GATE_PASS = "allowed"


@dataclass(frozen=True)
class CalibRecord:
    """一次在线校验记录。"""

    record_id: str
    ts: float
    point_id: str
    operator: str
    rule_version: str
    rule_snapshot: dict
    method: str
    target_kg: float
    dut_kg: float
    ref_kg: float
    deviation_pct: float | None
    conclusion: str
    status: str
    reason: str
    ref_healthy: bool

    @classmethod
    def from_dict(cls, raw: dict) -> "CalibRecord":
        return cls(
            record_id=str(raw["record_id"]),
            ts=float(raw["ts"]),
            point_id=str(raw["point_id"]),
            operator=str(raw.get("operator", "")),
            rule_version=str(raw.get("rule_version", "")),
            rule_snapshot=dict(raw.get("rule_snapshot", {})),
            method=str(raw.get("method", "")),
            target_kg=float(raw.get("target_kg", 0.0)),
            dut_kg=float(raw.get("dut_kg", 0.0)),
            ref_kg=float(raw.get("ref_kg", 0.0)),
            deviation_pct=raw.get("deviation_pct"),
            conclusion=str(raw.get("conclusion", CONCLUSION_NONE)),
            status=str(raw.get("status", "partial_failure")),
            reason=str(raw.get("reason", "")),
            ref_healthy=bool(raw.get("ref_healthy", True)),
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "ts": round(self.ts, 3),
            "point_id": self.point_id,
            "operator": self.operator,
            "rule_version": self.rule_version,
            "rule_snapshot": self.rule_snapshot,
            "method": self.method,
            "target_kg": round(self.target_kg, 3),
            "dut_kg": round(self.dut_kg, 3),
            "ref_kg": round(self.ref_kg, 3),
            "deviation_pct": self.deviation_pct,
            "conclusion": self.conclusion,
            "status": self.status,
            "reason": self.reason,
            "ref_healthy": self.ref_healthy,
        }

    def is_conclusive(self) -> bool:
        """是否出具了合格/不合格结论。"""

        return self.status == "complete" and self.conclusion in ("pass", "fail")


@dataclass(frozen=True)
class FillGateRecord:
    """一次加注门禁判定记录。"""

    record_id: str
    ts: float
    point_id: str
    vehicle: str
    operator: str
    decision: str
    fail_policy: str
    reason: str
    basis_record_id: str = ""
    rule_version: str = ""
    conclusion: str = ""
    deviation_pct: float | None = None
    acknowledged: bool = False
    warning: bool = False
    fill_op_id: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> "FillGateRecord":
        return cls(
            record_id=str(raw["record_id"]),
            ts=float(raw["ts"]),
            point_id=str(raw["point_id"]),
            vehicle=str(raw.get("vehicle", "")),
            operator=str(raw.get("operator", "")),
            decision=str(raw["decision"]),
            fail_policy=str(raw.get("fail_policy", "")),
            reason=str(raw.get("reason", "")),
            basis_record_id=str(raw.get("basis_record_id", "")),
            rule_version=str(raw.get("rule_version", "")),
            conclusion=str(raw.get("conclusion", "")),
            deviation_pct=raw.get("deviation_pct"),
            acknowledged=bool(raw.get("acknowledged", False)),
            warning=bool(raw.get("warning", False)),
            fill_op_id=str(raw.get("fill_op_id", "")),
            extra=dict(raw.get("extra", {})),
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "ts": round(self.ts, 3),
            "point_id": self.point_id,
            "vehicle": self.vehicle,
            "operator": self.operator,
            "decision": self.decision,
            "fail_policy": self.fail_policy,
            "reason": self.reason,
            "basis_record_id": self.basis_record_id,
            "rule_version": self.rule_version,
            "conclusion": self.conclusion,
            "deviation_pct": self.deviation_pct,
            "acknowledged": self.acknowledged,
            "warning": self.warning,
            "fill_op_id": self.fill_op_id,
            "extra": self.extra,
        }
