"""校验判定准则与计量点超差动作。

``VerifyCriteria`` 是一次校验下结论所用的全部判据：版本、方法、分段数、
分段目标质量与允许偏差。方法字段当前只接受 ``segmented``（分段比对），
编排层遇到其他取值会直接拒绝——校验方法不允许悄悄分叉。

超差后的处置（拦下加注还是只提示）按计量点定，取值只有 ``block`` 与
``warn`` 两种，配置层与运行层共用同一份字面量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import HydroError

if TYPE_CHECKING:  # 避免 config → verify 的反向依赖成环
    from ..config import VerifyLimits

METHOD = "segmented"
ON_FAIL_BLOCK = "block"
ON_FAIL_WARN = "warn"
ON_FAIL_ACTIONS = (ON_FAIL_BLOCK, ON_FAIL_WARN)


@dataclass(frozen=True)
class VerifyCriteria:
    """一份判定准则快照；结论记录内嵌它，历史结论按它解释。"""

    version: str
    segments: int
    segment_target_kg: float
    tolerance_pct: float
    method: str = METHOD

    @classmethod
    def create(
        cls,
        version: str,
        segments: int,
        segment_target_kg: float,
        tolerance_pct: float,
    ) -> "VerifyCriteria":
        """构造并校验一份准则；取值非法时拒绝（运行期迁移走这里）。"""

        text = str(version).strip()
        if not text:
            raise HydroError("判定版本不能为空", component="verify")
        count = int(segments)
        if count < 1:
            raise HydroError("校验分段数必须为正", component="verify")
        target = float(segment_target_kg)
        if target <= 0:
            raise HydroError("分段目标质量必须为正", component="verify")
        tolerance = float(tolerance_pct)
        if tolerance <= 0:
            raise HydroError("校验公差必须为正", component="verify")
        return cls(version=text, segments=count, segment_target_kg=target, tolerance_pct=tolerance)

    @classmethod
    def from_limits(cls, limits: "VerifyLimits") -> "VerifyCriteria":
        """从配置约束还原准则（配置装配期已校验过取值）。"""

        return cls(
            version=limits.criteria_version,
            segments=limits.segments,
            segment_target_kg=limits.segment_target_kg,
            tolerance_pct=limits.tolerance_pct,
        )

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "method": self.method,
            "segments": self.segments,
            "segment_target_kg": self.segment_target_kg,
            "tolerance_pct": self.tolerance_pct,
        }
