"""校验参照路。

参照路是校验时与被校表同步走量的另一路计量。它独立积分自己的累计质量，
速率越界（低于下限或高于上限）直接拒绝；还可以通过 :meth:`trip_fault`
注入故障演练参照路异常——参照路异常属于部分失败，编排层据此作废本次校验，
只留记录不出结论。
"""

from __future__ import annotations

from ..config import VerifyLimits
from ..errors import FlowError


class ReferenceLeg:
    """参照路累计与故障状态。"""

    def __init__(self, limits: VerifyLimits) -> None:
        self._limits = limits
        self._total_kg = 0.0
        self._fault = ""

    def commit(self, rate_kg_per_min: float, dt: float) -> float:
        """按速率推进一个步长，返回参照路累计质量。"""

        if self._fault:
            raise FlowError(f"参照路异常：{self._fault}", component="flow")
        rate = float(rate_kg_per_min)
        if rate < self._limits.ref_min_rate_kg_per_min or rate > self._limits.ref_max_rate_kg_per_min:
            raise FlowError(
                f"参照路速率 {rate} 超出允许区间 "
                f"[{self._limits.ref_min_rate_kg_per_min}, {self._limits.ref_max_rate_kg_per_min}]",
                component="flow",
            )
        if dt <= 0:
            raise ValueError("步长必须为正")
        self._total_kg += rate * dt / 60.0
        return self._total_kg

    def total_kg(self) -> float:
        """返回参照路累计质量。"""

        return self._total_kg

    def reset(self) -> None:
        """把参照路累计清零（故障状态保留，需显式清除）。"""

        self._total_kg = 0.0

    def trip_fault(self, reason: str) -> None:
        """注入参照路故障（演练用），此后提交一律被拒绝。"""

        self._fault = str(reason).strip() or "unspecified"

    def clear_fault(self) -> None:
        """清除参照路故障。"""

        self._fault = ""

    def fault_reason(self) -> str:
        """返回当前故障原因；无故障返回空串。"""

        return self._fault
