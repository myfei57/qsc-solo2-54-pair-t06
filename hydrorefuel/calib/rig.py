"""校验台架：按同量走一遍。

定死的校验方式（规则 ``calib-v1.0``）：选定目标量后，同一批量同时流经被校
流量计与参照路，台架读取两路对**同一批量**的累计读数，交由规则判读。台架
不判合格与否——判读口径在规则版本里，台架只负责把两路读数和参照路健康状态
原样交出来。

被校表读数通道与加注计量用的是同一个 :class:`MassFlowMeter` 类型（同一速率
量程校验），但台架持有专用实例，避免校验累计量污染加注总量。参照路异常由
:class:`ReferenceMeter` 在读数前直接抛出，服务层据此落"部分失败、不出
结论"。
"""

from __future__ import annotations

from ..config import FlowLimits
from ..errors import CalibError
from ..flow.meter import MassFlowMeter
from .reference import ReferenceMeter


class VerificationRig:
    """把被校表与参照路接成一条同量校验回路。"""

    def __init__(self, limits: FlowLimits, reference: ReferenceMeter) -> None:
        self._meter = MassFlowMeter(limits)
        self._reference = reference

    def run(self, target_kg: float, dut_bias_pct: float = 0.0) -> dict:
        """按同量走一遍，返回两路对本批量的读数与参照路健康状态。

        取的是本次校验的**增量**读数，因此参照路累计量不影响本次偏差。
        ``dut_bias_pct`` 用于在无真实标准表的环境注入被校表偏差（被校表
        读数相对真值的百分比），让"超差被记一条并标不合格"可被直接验证；
        生产接线置零即可。
        """

        if target_kg <= 0:
            raise CalibError("校验目标量必须为正", component="calib")
        # 每次校验都从零积分本批量，避免上一轮读数累计进本次偏差。
        self._meter.reset()
        # 参照路异常属于部分失败：读数前直接暴露，不允许产出结论数据。
        ref_before = self._reference.total_kg()
        self._reference.measure(float(target_kg))
        ref_kg = self._reference.total_kg() - ref_before
        # 被校表按其自身误差读出同一批量；读数量沿用其速率积分通道，因此
        # 超出被校表速率量程的读数会被同一套口径拒绝。
        delivered = float(target_kg) * (1.0 + float(dut_bias_pct) / 100.0)
        if delivered <= 0:
            raise CalibError("被校表读数异常：累计量不为正", component="calib")
        self._meter.commit(
            self._meter.limits.max_rate_kg_per_min,
            delivered * 60.0 / self._meter.limits.max_rate_kg_per_min,
        )
        return {
            "target_kg": round(float(target_kg), 3),
            "dut_kg": round(self._meter.total_kg(), 3),
            "ref_kg": round(ref_kg, 3),
            "ref_healthy": self._reference.healthy(),
        }
