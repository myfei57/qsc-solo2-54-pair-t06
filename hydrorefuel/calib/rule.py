"""校验规则版本。

校验方式在首版**定死**为"按同量走一遍"：同一批量同时流经被校流量计与参照
路，取两路对同一批量的累计总量做一次比对。方法码
``same_quantity_single_pass`` 与偏差、合格判据冻结在 ``calib-v1.0`` 上，
现场不能在不换版的情况下改方法或改口径。

"分段比对"（按流量分段分别核对）是另一种方法，启用它必须新建规则版本并走
迁移：每条历史结论都嵌着出具时的版本与参数，永远按当时那一版解释；门禁只
认当前激活版本下的结论，换版后旧结论对加注门禁失效，需重新校验。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import CalibError

# 定死：首版唯一允许的校验方法。其它方法只能随新版本引入。
METHOD_SAME_QUANTITY = "same_quantity_single_pass"
KNOWN_METHODS = (METHOD_SAME_QUANTITY,)

CONCLUSION_PASS = "pass"
CONCLUSION_FAIL = "fail"
# 参照路异常等部分失败不出结论，历史与门禁保持上一条有效结论。
CONCLUSION_NONE = "inconclusive"


@dataclass(frozen=True)
class CalibRule:
    """一个校验规则版本：方法、数值口径全部冻结。"""

    version: str
    method: str
    mpe_pct: float
    min_charge_kg: float
    valid_ttl_s: float

    @classmethod
    def create(
        cls,
        version: str,
        method: str,
        mpe_pct: float,
        min_charge_kg: float,
        valid_ttl_s: float,
    ) -> "CalibRule":
        if not version:
            raise CalibError("规则版本号不能为空", component="calib")
        if method not in KNOWN_METHODS:
            raise CalibError(
                f"未知校验方法 {method!r}；首版仅允许 {METHOD_SAME_QUANTITY!r}，换方法必须新建版本",
                component="calib",
            )
        if mpe_pct <= 0 or mpe_pct > 100.0:
            raise CalibError("最大允许偏差必须落在 (0, 100]% 内", component="calib")
        if min_charge_kg <= 0:
            raise CalibError("最低校验量必须为正", component="calib")
        if valid_ttl_s <= 0:
            raise CalibError("结论有效期必须为正", component="calib")
        return cls(version, method, float(mpe_pct), float(min_charge_kg), float(valid_ttl_s))

    @classmethod
    def from_dict(cls, raw: dict) -> "CalibRule":
        return cls.create(
            version=str(raw["version"]),
            method=str(raw["method"]),
            mpe_pct=float(raw["mpe_pct"]),
            min_charge_kg=float(raw["min_charge_kg"]),
            valid_ttl_s=float(raw["valid_ttl_s"]),
        )

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "method": self.method,
            "mpe_pct": self.mpe_pct,
            "min_charge_kg": self.min_charge_kg,
            "valid_ttl_s": self.valid_ttl_s,
        }

    def evaluate(self, dut_kg: float, ref_kg: float) -> dict:
        """按同量比对判读两路读数，返回偏差与结论。

        偏差以参照路为基准：``(被校表 - 参照路) / 参照路``。参照读数不大于
        零或低于本版最低校验量时无法判读，按不出结论处理，而不是硬算一个
        偏差。
        """

        if ref_kg <= 0 or ref_kg < self.min_charge_kg or dut_kg <= 0:
            return {
                "method": self.method,
                "dut_kg": round(float(dut_kg), 3),
                "ref_kg": round(float(ref_kg), 3),
                "deviation_pct": None,
                "mpe_pct": self.mpe_pct,
                "conclusion": CONCLUSION_NONE,
                "reason": "charge_below_minimum" if ref_kg < self.min_charge_kg else "invalid_reading",
            }
        deviation_pct = (float(dut_kg) - float(ref_kg)) / float(ref_kg) * 100.0
        conclusion = CONCLUSION_PASS if abs(deviation_pct) <= self.mpe_pct else CONCLUSION_FAIL
        return {
            "method": self.method,
            "dut_kg": round(float(dut_kg), 3),
            "ref_kg": round(float(ref_kg), 3),
            "deviation_pct": round(deviation_pct, 4),
            "mpe_pct": self.mpe_pct,
            "conclusion": conclusion,
            "reason": "within_mpe" if conclusion == CONCLUSION_PASS else "exceeds_mpe",
        }


class RuleBook:
    """规则版本登记与当前激活版本管理。

    规则不可就地修改：数值或方法变化只能新建版本再激活，激活动作即一次
    迁移，迁移留痕。历史结论自带出具时的规则快照，因此旧规则即使不再激活
    也必须保留在版本表里供解释历史。
    """

    def __init__(self, rules: list[CalibRule] | None = None, active_version: str | None = None) -> None:
        self._rules: dict[str, CalibRule] = {}
        self._active_version = ""
        for rule in rules or []:
            self.register(rule)
        if active_version is not None:
            self.activate(active_version)

    @property
    def active_version(self) -> str:
        return self._active_version

    def register(self, rule: CalibRule) -> None:
        """登记一个新版本；版本号重复即报错，避免换版被静默覆盖。"""

        if rule.version in self._rules:
            raise CalibError(f"规则版本已存在: {rule.version}", component="calib")
        self._rules[rule.version] = rule
        if not self._active_version:
            self._active_version = rule.version

    def activate(self, version: str) -> CalibRule:
        """把某版本设为当前生效版本，返回该版本规则。"""

        if version not in self._rules:
            raise CalibError(f"未知规则版本，无法激活: {version}", component="calib")
        self._active_version = version
        return self._rules[version]

    def get(self, version: str) -> CalibRule:
        """按版本号取规则。"""

        return self._rules[version]

    def active(self) -> CalibRule:
        """返回当前生效规则。"""

        return self._rules[self._active_version]

    def versions(self) -> list[str]:
        """返回全部已登记版本号（登记先后）。"""

        return list(self._rules)

    def describe(self) -> dict:
        """汇总规则版本，供控制台展示。"""

        return {
            "active_version": self._active_version,
            "active": self.active().as_dict(),
            "versions": [self._rules[version].as_dict() for version in self._rules],
        }
