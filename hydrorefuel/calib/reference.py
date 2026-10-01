"""参照路（标准表）。

参照路是在线校验的基准，其自身读数必须可信。参照路一旦处于异常状态，校验
只能算**部分失败**：本次不出任何合格/不合格结论，避免把参照路自己的毛病
误记成被校表超差。参照路异常是显式状态（现场诊断或人工置位），恢复也必须
显式进行并留痕，不允许靠下一次读数自行"痊愈"。
"""

from __future__ import annotations

from ..errors import CalibError


class ReferenceMeter:
    """参照路标准表：累计总量与健康状态。"""

    def __init__(self) -> None:
        self._healthy = True
        self._fault = ""
        self._total_kg = 0.0

    def healthy(self) -> bool:
        """参照路当前是否正常。"""

        return self._healthy

    def fault(self) -> str:
        """返回当前异常原因，正常时为空。"""

        return self._fault

    def mark_fault(self, reason: str = "reference_fault") -> None:
        """置参照路异常；带异常读数的校验据此判为部分失败。"""

        self._healthy = False
        self._fault = reason or "reference_fault"

    def recover(self) -> None:
        """显式恢复参照路；恢复后才能再次出具结论。"""

        self._healthy = True
        self._fault = ""

    def measure(self, mass_kg: float) -> float:
        """参照路对通过批量读数并累计；异常时拒绝读数。"""

        if mass_kg <= 0:
            raise CalibError("参照路校验量必须为正", component="calib")
        if not self._healthy:
            raise CalibError(f"参照路异常，不能作为校验基准: {self._fault}", component="calib")
        self._total_kg += float(mass_kg)
        return self._total_kg

    def total_kg(self) -> float:
        """返回参照路累计总量。"""

        return self._total_kg

    def reset(self) -> None:
        """清零累计总量（健康状态保留，避免清掉现场诊断）。"""

        self._total_kg = 0.0
