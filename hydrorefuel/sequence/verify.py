"""流量计在线校验编排：被校表与参照路按分段比对走同一标称量。

校验方法定死为分段比对：每个分段内被校表累计到目标质量为止，参照路同步
累计，段末算相对偏差 ``(被校 - 参照) / 参照``；全部分段都在公差内才判
合格，任何一段超差即判不合格。参照路异常（故障注入或速率越界）属于部分
失败：本次校验作废，返回未出结论的报告，由平台留记录但不改变计量点的
既有合格状态。
"""

from __future__ import annotations

from ..errors import FlowError, HydroError
from ..flow.meter import MassFlowMeter
from ..verify.policy import METHOD, VerifyCriteria
from ..verify.reference import ReferenceLeg


def _inconclusive(reason: str, message: str, segments: list[dict], meter: MassFlowMeter, reference: ReferenceLeg) -> dict:
    return {
        "conclusive": False,
        "conclusion": None,
        "reason": reason,
        "message": message,
        "segments": segments,
        "max_deviation_pct": None,
        "meter_total_kg": round(meter.total_kg(), 3),
        "reference_total_kg": round(reference.total_kg(), 3),
    }


def verify_flow(
    meter: MassFlowMeter,
    reference: ReferenceLeg,
    criteria: VerifyCriteria,
    meter_rate_kg_per_min: float,
    reference_rate_kg_per_min: float,
    dt: float = 36.0,
    max_iterations: int = 10000,
) -> dict:
    """执行一次分段比对校验，返回运行报告（可能未出结论）。

    被校表速率是加注指令值，越界属于请求本身非法，直接拒绝；参照路速率是
    参照计量的读数，越界属于参照路异常，按部分失败处理。
    """

    if criteria.method != METHOD:
        raise HydroError(f"不支持的校验方法: {criteria.method}", component="verify")
    if dt <= 0:
        raise HydroError("步长必须为正", component="verify")
    limits = meter.limits
    rate = float(meter_rate_kg_per_min)
    if rate < limits.min_rate_kg_per_min or rate > limits.max_rate_kg_per_min:
        raise FlowError(
            f"校验速率 {rate} 超出流量计允许区间 "
            f"[{limits.min_rate_kg_per_min}, {limits.max_rate_kg_per_min}]",
            component="flow",
        )

    reference.reset()
    segments: list[dict] = []
    for index in range(1, criteria.segments + 1):
        segment_meter_kg = 0.0
        reference_before = reference.total_kg()
        iterations = 0
        while segment_meter_kg < criteria.segment_target_kg:
            iterations += 1
            if iterations > max_iterations:
                raise HydroError("校验分段推进超限，终止本次校验", component="verify")
            before = meter.total_kg()
            meter.commit(rate, dt)
            segment_meter_kg += meter.total_kg() - before
            try:
                reference.commit(float(reference_rate_kg_per_min), dt)
            except HydroError as error:
                return _inconclusive("reference_leg_fault", error.message, segments, meter, reference)
        segment_reference_kg = reference.total_kg() - reference_before
        if segment_reference_kg <= 0:
            return _inconclusive("reference_leg_fault", "参照路累计量非正，无法计算偏差", segments, meter, reference)
        deviation_pct = (segment_meter_kg - segment_reference_kg) / segment_reference_kg * 100.0
        segments.append(
            {
                "index": index,
                "meter_kg": round(segment_meter_kg, 3),
                "reference_kg": round(segment_reference_kg, 3),
                "deviation_pct": round(deviation_pct, 3),
                "within": abs(deviation_pct) <= criteria.tolerance_pct,
            }
        )

    conclusion = "pass" if all(item["within"] for item in segments) else "fail"
    max_deviation = max(abs(item["deviation_pct"]) for item in segments)
    return {
        "conclusive": True,
        "conclusion": conclusion,
        "reason": "",
        "message": "",
        "segments": segments,
        "max_deviation_pct": round(max_deviation, 3),
        "meter_total_kg": round(meter.total_kg(), 3),
        "reference_total_kg": round(reference.total_kg(), 3),
    }
