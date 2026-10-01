"""在线校验服务。

把规则版本、计量点、参照路、台架与两条只追加流水（校验、加注）装在一起，
对外提供四件事：

1. **跑校验**：按同量走一遍，出结论并按计量点改变其计量状态；参照路异常等
   部分失败不出结论、不改状态。
2. **加注门禁**：下次加注前先查当前有效结论，按计量点策略当场拦下或给
   提示；无有效结论（从未校验、过期、规则换版）默认拦下。
3. **历史与迁移**：结论带规则版本，换版登记为迁移；历史结论按当时版本
   解释，门禁只认当前版本。
4. **对账与展示**：校验/加注两条流水与审计三方对账，并给控制台出快照。

所有业务流水与审计事件在同一把服务锁内写完，``record_id`` 全流程一致。
"""

from __future__ import annotations

import threading

from ..clock import Clock
from ..config import CalibLimits
from ..errors import CalibError, HydroError
from ..eventbus import Event, EventBus
from ..ids import IdFactory
from ..store.jsonfile import JsonStore
from ..store.journal import JsonlJournal
from .point import MeteringPoint, MeteringPointRegistry, POLICY_BLOCK, POLICY_WARN
from .reconcile import reconcile
from .record import CalibRecord, FillGateRecord, GATE_BLOCK, GATE_PASS, GATE_WARN
from .reference import ReferenceMeter
from .rig import VerificationRig
from .rule import (
    CONCLUSION_FAIL,
    CONCLUSION_NONE,
    CONCLUSION_PASS,
    CalibRule,
    METHOD_SAME_QUANTITY,
    RuleBook,
)
from .snapshot import CalibSnapshot

DEFAULT_RULE_VERSION = "calib-v1.0"


class CalibService:
    """流量计在线校验与加注门禁。"""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        flow_limits,
        limits: CalibLimits,
        store: JsonStore,
        journal: JsonlJournal,
        fill_journal: JsonlJournal,
        audit_journal: JsonlJournal,
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._store = store
        self._default_limits = limits
        self._audit_journal = audit_journal
        self._reference = ReferenceMeter()
        self._rig = VerificationRig(flow_limits, self._reference)
        self._id_factory = IdFactory("calib")
        self._fill_id_factory = IdFactory("fillgate")
        self._journal = journal
        self._fill_journal = fill_journal
        self._rules = RuleBook()
        self._points = MeteringPointRegistry()
        self._calib_records: list[CalibRecord] = []
        self._fill_records: list[FillGateRecord] = []
        self._lock = threading.RLock()
        self._restore()

    def _restore(self) -> None:
        """从状态文档与两条流水恢复计量点、规则版本与计量状态。"""

        self._rules.register(self.default_rule())
        state = self._store.read("calib_state")
        if isinstance(state, dict):
            for raw in state.get("rules", []):
                rule = CalibRule.from_dict(raw)
                if rule.version not in self._rules.versions():
                    self._rules.register(rule)
            active = str(state.get("active_version", ""))
            if active in self._rules.versions():
                self._rules.activate(active)
            for raw in state.get("points", []):
                point = MeteringPoint.from_dict(raw)
                if point.point_id not in {item.point_id for item in self._points.points()}:
                    self._points.register(point)
        for raw in self._journal.read_all():
            self._calib_records.append(CalibRecord.from_dict(raw))
            rule_snapshot = raw.get("rule_snapshot", {})
            version = str(raw.get("rule_version", ""))
            if version and rule_snapshot and version not in self._rules.versions():
                self._rules.register(CalibRule.from_dict(rule_snapshot))
        for raw in self._fill_journal.read_all():
            self._fill_records.append(FillGateRecord.from_dict(raw))

    def _persist_state(self) -> None:
        """把规则版本与计量点登记落状态文档，供跨重启恢复。"""

        self._store.write(
            "calib_state",
            {
                "active_version": self._rules.active_version,
                "rules": [self._rules.get(version).as_dict() for version in self._rules.versions()],
                "points": [point.as_dict() for point in self._points.points()],
            },
        )

    @property
    def reference(self) -> ReferenceMeter:
        return self._reference

    @property
    def journal(self) -> JsonlJournal:
        return self._journal

    @property
    def fill_journal(self) -> JsonlJournal:
        return self._fill_journal

    def reference_healthy(self) -> dict:
        """返回参照路健康状态。"""

        return {"healthy": self._reference.healthy(), "fault": self._reference.fault()}

    def mark_reference_fault(self, operator: str, reason: str = "reference_fault") -> dict:
        """置参照路异常（现场诊断/人工置位），并落审计。"""

        self._require_operator(operator)
        with self._lock:
            self._reference.mark_fault(reason)
            result = self.reference_healthy()
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    "reference_fault",
                    station="",
                    severity="alarm",
                    message=f"参照路异常：{result['fault']}",
                    operator=operator,
                    **result,
                )
            )
            return result

    def recover_reference(self, operator: str) -> dict:
        """显式恢复参照路并留痕；恢复后才能再出结论。"""

        self._require_operator(operator)
        with self._lock:
            self._reference.recover()
            result = self.reference_healthy()
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    "reference_recovered",
                    station="",
                    severity="notice",
                    message="参照路恢复正常",
                    operator=operator,
                    **result,
                )
            )
            return result

    def register_point(self, operator: str, point_id: str, flowmeter: str, fail_policy: str = POLICY_BLOCK) -> dict:
        """登记一个计量点及其超差处置策略，并落审计。"""

        self._require_operator(operator)
        with self._lock:
            point = self._points.register(MeteringPoint.create(point_id, flowmeter, fail_policy))
            self._persist_state()
            result = point.as_dict()
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    "point_registered",
                    station="",
                    severity="notice",
                    message=f"计量点 {point_id} 登记，超差处置={fail_policy}",
                    operator=operator,
                    **result,
                )
            )
            return result

    def activate_rule(
        self,
        operator: str,
        version: str,
        method: str = METHOD_SAME_QUANTITY,
        mpe_pct: float | None = None,
        min_charge_kg: float | None = None,
        valid_ttl_s: float | None = None,
    ) -> dict:
        """登记并激活一个规则版本。

        激活与当前不同的版本即一次**迁移**：历史结论保留各自的规则快照、
        仍按旧版解释，但对加注门禁立即失效，须在新版下重新校验。
        """

        self._require_operator(operator)
        with self._lock:
            if version in self._rules.versions():
                rule = self._rules.activate(version)
                action = "rule_selected"
            else:
                rule = self._new_rule(version, method, mpe_pct, min_charge_kg, valid_ttl_s, self._rules.active())
                action = "rule_migrated"
            self._persist_state()
            result = self._rules.describe()
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    action,
                    station="",
                    severity="warning" if action == "rule_migrated" else "notice",
                    message=f"校验规则切换到 {version}（历史结论按各自版本解释，门禁以新版重新校验）",
                    operator=operator,
                    active_version=version,
                )
            )
            return result

    def _new_rule(
        self,
        version: str,
        method: str,
        mpe_pct: float | None,
        min_charge_kg: float | None,
        valid_ttl_s: float | None,
        current: CalibRule,
    ) -> CalibRule:
        rule = CalibRule.create(
            version=version,
            method=method,
            mpe_pct=current.mpe_pct if mpe_pct is None else mpe_pct,
            min_charge_kg=current.min_charge_kg if min_charge_kg is None else min_charge_kg,
            valid_ttl_s=current.valid_ttl_s if valid_ttl_s is None else valid_ttl_s,
        )
        self._rules.register(rule)
        return self._rules.activate(version)

    def run_verification(
        self,
        operator: str,
        point_id: str,
        target_kg: float,
        dut_bias_pct: float = 0.0,
    ) -> dict:
        """对某计量点跑一遍同量校验，落校验流水与审计，返回记录。

        参照路异常属于部分失败：记 ``inconclusive``、不出结论，计量点状态
        停在上一条有效结论。
        """

        self._require_operator(operator)
        with self._lock:
            point = self._points.resolve(point_id)
            rule = self._rules.active()
            record_id = self._id_factory.next()
            ts = self._clock.now()
            try:
                reading = self._rig.run(target_kg, dut_bias_pct=dut_bias_pct)
            except HydroError as error:
                # 参照路异常、被校表读数越界等可归因于现场设备的异常，按
                # 部分失败处理、不出结论；其余编程错误直接抛出，不伪装成故障。
                record = CalibRecord(
                    record_id=record_id,
                    ts=ts,
                    point_id=point.point_id,
                    operator=operator,
                    rule_version=rule.version,
                    rule_snapshot=rule.as_dict(),
                    method=rule.method,
                    target_kg=float(target_kg),
                    dut_kg=0.0,
                    ref_kg=0.0,
                    deviation_pct=None,
                    conclusion=CONCLUSION_NONE,
                    status="partial_failure",
                    reason=error.message,
                    ref_healthy=self._reference.healthy(),
                )
                self._append_calib(record, severity="warning", message="参照路/读数异常，本次校验不出结论")
                return record.as_dict()

            verdict = rule.evaluate(reading["dut_kg"], reading["ref_kg"])
            complete = verdict["conclusion"] in (CONCLUSION_PASS, CONCLUSION_FAIL)
            record = CalibRecord(
                record_id=record_id,
                ts=ts,
                point_id=point.point_id,
                operator=operator,
                rule_version=rule.version,
                rule_snapshot=rule.as_dict(),
                method=verdict["method"],
                target_kg=reading["target_kg"],
                dut_kg=verdict["dut_kg"],
                ref_kg=verdict["ref_kg"],
                deviation_pct=verdict["deviation_pct"],
                conclusion=verdict["conclusion"],
                status="complete" if complete else "partial_failure",
                reason=verdict["reason"],
                ref_healthy=reading["ref_healthy"],
            )
            severity = "notice"
            message = "校验合格"
            if record.conclusion == CONCLUSION_FAIL:
                severity = "alarm"
                message = f"校验超差：偏差 {record.deviation_pct}% 超出 MPE {rule.mpe_pct}%，判不合格"
            elif not complete:
                severity = "warning"
                message = "本次校验不完整，不出结论"
            self._append_calib(record, severity=severity, message=message, fail_policy=point.fail_policy)
            return record.as_dict()

    def gate_fill(
        self,
        operator: str,
        point_id: str,
        vehicle: str = "",
        acknowledged: bool = False,
    ) -> dict:
        """加注前门禁：查当前有效结论并按计量点策略定夺，返回判定记录。

        返回里 ``decision`` 为 ``blocked`` 时调用方不得发起加注；``warned``
        时必须带操作员确认（``acknowledged=True``）再调一次才放行；
        ``allowed`` 为放行。
        """

        self._require_operator(operator)
        with self._lock:
            point = self._points.resolve(point_id)
            now = self._clock.now()
            active_version = self._rules.active_version
            record_id = self._fill_id_factory.next()

            basis = self._latest_conclusion(point_id)
            decision, reason, confirmed = GATE_BLOCK, "", bool(acknowledged)
            warning = False
            if basis is None:
                reason = "no_verification"
            elif basis.rule_version != active_version:
                reason = "stale_rule_version"
            elif now - basis.ts > basis.rule_snapshot.get("valid_ttl_s", 0.0):
                reason = "verification_expired"
            elif basis.conclusion == CONCLUSION_PASS:
                decision, reason = GATE_PASS, "verification_pass"
            elif point.fail_policy == POLICY_BLOCK:
                reason = "verification_fail_block"
            elif not acknowledged:
                # warn 策略：首次只给提示并拦下，要求操作员确认后再发起。
                decision, reason = GATE_WARN, "verification_fail_warn"
            else:
                # 操作员已确认风险：放行，但保留警告与确认留痕。
                decision, reason, warning = GATE_PASS, "verification_fail_acknowledged", True

            gate = FillGateRecord(
                record_id=record_id,
                ts=now,
                point_id=point.point_id,
                vehicle=vehicle,
                operator=operator,
                decision=decision,
                fail_policy=point.fail_policy,
                reason=reason,
                basis_record_id=basis.record_id if basis else "",
                rule_version=basis.rule_version if basis else active_version,
                conclusion=basis.conclusion if basis else "",
                deviation_pct=basis.deviation_pct if basis else None,
                acknowledged=warning and confirmed,
                warning=warning,
            )
            self._fill_records.append(gate)
            self._fill_journal.append(gate.as_dict())

            if decision == GATE_BLOCK:
                severity = "alarm"
            elif decision == GATE_WARN or warning:
                severity = "warning"
            else:
                severity = "notice"
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    "fill_gate",
                    station="",
                    severity=severity,
                    message=self._gate_message(gate),
                    record_id=record_id,
                    point_id=point.point_id,
                    vehicle=vehicle,
                    operator=operator,
                    decision=decision,
                    reason=reason,
                    fail_policy=point.fail_policy,
                    basis_record_id=gate.basis_record_id,
                    rule_version=gate.rule_version,
                    conclusion=gate.conclusion,
                    acknowledged=gate.acknowledged,
                    warning=gate.warning,
                )
            )
            return gate.as_dict()

    def attach_fill_op(self, gate_record_id: str, fill_op_id: str) -> None:
        """把真实加注操作标识关联到放行的门禁记录上，做三方串证。

        流水不可改写，关联只写一条新的审计事件，由 ``fill_op_id`` 把两条
        记录串起来。
        """

        with self._lock:
            if not any(gate.record_id == gate_record_id for gate in self._fill_records):
                return
            self._bus.publish(
                Event.of(
                    "calib",
                    "calib",
                    "fill_gate_attached",
                    station="",
                    severity="notice",
                    message="门禁记录关联加注操作",
                    record_id=gate_record_id,
                    fill_op_id=fill_op_id,
                )
            )

    def history(self, point_id: str | None = None, limit: int = 100) -> list[dict]:
        """返回校验历史（时间升序的最近若干条），可按计量点过滤。"""

        with self._lock:
            selected = [record for record in self._calib_records if point_id is None or record.point_id == point_id]
            return [record.as_dict() for record in selected[-limit:]]

    def fill_history(self, point_id: str | None = None, limit: int = 100) -> list[dict]:
        """返回加注门禁历史（时间升序的最近若干条），可按计量点过滤。"""

        with self._lock:
            selected = [record for record in self._fill_records if point_id is None or record.point_id == point_id]
            return [record.as_dict() for record in selected[-limit:]]

    def statuses(self) -> list[dict]:
        """返回每个计量点的当前计量状态（最近有效结论 + 是否可加注）。"""

        now = self._clock.now()
        active_version = self._rules.active_version
        result: list[dict] = []
        for point in self._points.points():
            basis = self._latest_conclusion(point.point_id)
            state = {
                **point.as_dict(),
                "active_rule_version": active_version,
                "latest_record_id": basis.record_id if basis else None,
                "latest_conclusion": basis.conclusion if basis else "",
                "latest_deviation_pct": basis.deviation_pct if basis else None,
                "latest_ts": basis.ts if basis else None,
                "latest_operator": basis.operator if basis else "",
                "latest_rule_version": basis.rule_version if basis else "",
            }
            if basis is None:
                state["valid"] = False
                state["state_reason"] = "no_verification"
            elif basis.rule_version != active_version:
                state["valid"] = False
                state["state_reason"] = "stale_rule_version"
            elif now - basis.ts > basis.rule_snapshot.get("valid_ttl_s", 0.0):
                state["valid"] = False
                state["state_reason"] = "verification_expired"
            else:
                state["valid"] = basis.conclusion == CONCLUSION_PASS
                state["state_reason"] = "verification_pass" if state["valid"] else "verification_fail"
            result.append(state)
        return result

    def reconciliation(self) -> dict:
        """校验流水、加注流水与审计做三方对账。

        审计以**落盘流水**为准而不是内存环形副本——后者有容量上限，会淘汰
        早期事件；对账是一致性保证，必须覆盖全部历史记录。
        """

        with self._lock:
            return reconcile(
                list(self._calib_records),
                list(self._fill_records),
                self._audit_journal.read_all(),
            )

    def snapshot(self, limit: int = 10) -> CalibSnapshot:
        """返回在线校验整体快照，供控制台展示。"""

        with self._lock:
            return CalibSnapshot(
                reference_healthy=self._reference.healthy(),
                reference_fault=self._reference.fault(),
                rules=self._rules.describe(),
                points=self._points.describe(),
                statuses=self.statuses(),
                calib_records=len(self._calib_records),
                fill_records=len(self._fill_records),
                recent=[record.as_dict() for record in self._calib_records[-limit:][::-1]],
            )

    def default_rule(self) -> CalibRule:
        """按当前配置口径构造出厂默认规则（首版定死的同量走一遍）。"""

        return CalibRule.create(
            DEFAULT_RULE_VERSION,
            METHOD_SAME_QUANTITY,
            self._default_limits.mpe_pct,
            self._default_limits.min_charge_kg,
            self._default_limits.valid_ttl_s,
        )

    def reset(self) -> None:
        """清掉两条流水、计量点与规则迁移，回到出厂默认规则。

        参照路的现场诊断状态保留，避免重置把现场真实故障也一并抹掉。
        """

        with self._lock:
            self._calib_records.clear()
            self._fill_records.clear()
            self._reference.reset()
            self._rules = RuleBook([self.default_rule()])
            self._points = MeteringPointRegistry()
            if self._store.exists("calib_state"):
                self._store.remove("calib_state")

    def _latest_conclusion(self, point_id: str) -> CalibRecord | None:
        for record in reversed(self._calib_records):
            if record.point_id == point_id and record.is_conclusive():
                return record
        return None

    def _append_calib(self, record: CalibRecord, *, severity: str, message: str, fail_policy: str = "") -> None:
        self._calib_records.append(record)
        self._journal.append(record.as_dict())
        self._bus.publish(
            Event.of(
                "calib",
                "calib",
                "verification_run",
                station="",
                severity=severity,
                message=message,
                record_id=record.record_id,
                point_id=record.point_id,
                operator=record.operator,
                rule_version=record.rule_version,
                method=record.method,
                target_kg=record.target_kg,
                dut_kg=record.dut_kg,
                ref_kg=record.ref_kg,
                deviation_pct=record.deviation_pct,
                conclusion=record.conclusion,
                status=record.status,
                reason=record.reason,
                ref_healthy=record.ref_healthy,
                fail_policy=fail_policy,
            )
        )

    def _gate_message(self, gate: FillGateRecord) -> str:
        if gate.decision == GATE_BLOCK:
            return f"加注被拦下：{gate.reason}"
        if gate.decision == GATE_WARN:
            return f"加注提示：计量点校验不合格（{gate.deviation_pct}%），需操作员确认"
        if gate.warning:
            return f"加注放行（操作员已确认风险，校验偏差 {gate.deviation_pct}%）"
        return "门禁通过，允许加注"

    @staticmethod
    def _require_operator(operator: str) -> None:
        if not operator or not str(operator).strip():
            raise CalibError("校验与加注操作必须带操作员标识", component="calib")
