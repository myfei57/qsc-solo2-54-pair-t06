"""计量点。

每个计量点对应一台被校流量计，并带一个**超差处置策略**：``block`` 表示结论
不合格时当场拦下加注；``warn`` 表示放行但强制给出提示、要求操作员确认后才
能加注。策略按计量点定死在登记信息里，修改策略要重新登记并落审计。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CalibError

POLICY_BLOCK = "block"
POLICY_WARN = "warn"
POLICIES = (POLICY_BLOCK, POLICY_WARN)


@dataclass(frozen=True)
class MeteringPoint:
    """一个计量点：被校流量计与其超差处置策略。"""

    point_id: str
    flowmeter: str
    fail_policy: str

    @classmethod
    def create(cls, point_id: str, flowmeter: str, fail_policy: str = POLICY_BLOCK) -> "MeteringPoint":
        if not point_id or not flowmeter:
            raise CalibError("计量点与流量计标识都不能为空", component="calib")
        if fail_policy not in POLICIES:
            raise CalibError(f"超差处置策略必须是 {POLICIES} 之一", component="calib")
        return cls(point_id, flowmeter, fail_policy)

    @classmethod
    def from_dict(cls, raw: dict) -> "MeteringPoint":
        return cls.create(
            point_id=str(raw["point_id"]),
            flowmeter=str(raw["flowmeter"]),
            fail_policy=str(raw.get("fail_policy", POLICY_BLOCK)),
        )

    def as_dict(self) -> dict:
        return {"point_id": self.point_id, "flowmeter": self.flowmeter, "fail_policy": self.fail_policy}


class MeteringPointRegistry:
    """计量点登记表。"""

    def __init__(self, points: list[MeteringPoint] | None = None) -> None:
        self._points: dict[str, MeteringPoint] = {}
        for point in points or []:
            self.register(point)

    def register(self, point: MeteringPoint) -> MeteringPoint:
        """登记一个计量点；重复登记即报错，避免策略被悄悄覆盖。"""

        if point.point_id in self._points:
            raise CalibError(f"计量点已登记: {point.point_id}", component="calib")
        self._points[point.point_id] = point
        return point

    def resolve(self, point_id: str) -> MeteringPoint:
        """按计量点取登记信息。"""

        try:
            return self._points[point_id]
        except KeyError as error:
            raise CalibError(f"未登记计量点: {point_id}", component="calib") from error

    def points(self) -> list[MeteringPoint]:
        """返回全部计量点（按标识排序）。"""

        return [self._points[key] for key in sorted(self._points)]

    def describe(self) -> dict:
        """汇总计量点登记信息，供控制台展示。"""

        ordered = self.points()
        return {"count": len(ordered), "points": [point.as_dict() for point in ordered]}
