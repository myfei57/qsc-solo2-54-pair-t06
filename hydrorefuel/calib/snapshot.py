"""在线校验状态快照。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CalibSnapshot:
    """在线校验某一时刻的整体状态，供控制台与命令行读取。"""

    reference_healthy: bool
    reference_fault: str
    rules: dict
    points: dict
    statuses: list[dict]
    calib_records: int
    fill_records: int
    recent: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reference_healthy": self.reference_healthy,
            "reference_fault": self.reference_fault,
            "rules": self.rules,
            "points": self.points,
            "statuses": self.statuses,
            "calib_records": self.calib_records,
            "fill_records": self.fill_records,
            "recent": self.recent,
        }
