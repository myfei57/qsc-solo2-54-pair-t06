"""校验记录、加注记录与审计的三方对账。

三条线必须口径一致：每出具一条校验结论、每做一次加注定夺，审计里都要有同
``record_id`` 的事件，且计量点、操作员、结论/处置等字段一致。对账函数以
两条业务流水（校验流水、加注流水）为准，逐条去审计流水里找对应事件，报出
缺失与字段不符——任何一项不一致都明确置 ``consistent = False``，控制台
据此判断能否信任当前计量状态。
"""

from __future__ import annotations

from .record import CalibRecord, FillGateRecord

CALIB_FIELDS = ("point_id", "operator", "rule_version", "conclusion", "status")
FILL_FIELDS = ("point_id", "operator", "decision", "reason")


def _audit_index(audit_entries: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in audit_entries:
        payload = entry.get("payload", {})
        record_id = payload.get("record_id")
        action = entry.get("action", "")
        # 只有"出具/定夺"主事件进对账索引；fill_gate_attached 这类后续关联
        # 事件携带的是部分字段，不能覆盖同 record_id 的主事件。
        if record_id and action in ("verification_run", "fill_gate"):
            index[str(record_id)] = {"entry": entry, "payload": payload}
    return index


def _check_fields(record_id: str, expected: dict, payload: dict, fields: tuple[str, ...]) -> list[str]:
    mismatches: list[str] = []
    for field in fields:
        if field in expected and str(expected[field]) != str(payload.get(field)):
            mismatches.append(f"{record_id}:{field}")
    return mismatches


def reconcile(
    calib_records: list[CalibRecord],
    fill_records: list[FillGateRecord],
    audit_entries: list[dict],
) -> dict:
    """对三条线做一致性对账，返回对账报告。"""

    index = _audit_index(audit_entries)
    missing: list[str] = []
    mismatches: list[str] = []
    checked = 0

    for record in calib_records:
        found = index.get(record.record_id)
        if found is None:
            missing.append(record.record_id)
            continue
        checked += 1
        expected = record.as_dict()
        mismatches.extend(_check_fields(record.record_id, expected, found["payload"], CALIB_FIELDS))

    for record in fill_records:
        found = index.get(record.record_id)
        if found is None:
            missing.append(record.record_id)
            continue
        checked += 1
        expected = record.as_dict()
        mismatches.extend(_check_fields(record.record_id, expected, found["payload"], FILL_FIELDS))

    return {
        "consistent": not missing and not mismatches,
        "checked": checked,
        "calib_records": len(calib_records),
        "fill_records": len(fill_records),
        "audit_events": len(audit_entries),
        "missing_in_audit": missing,
        "field_mismatches": mismatches,
    }
