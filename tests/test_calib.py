"""流量计在线校验。

定死的口径：校验方式为"按同量走一遍"（``same_quantity_single_pass``），用
参照路做基准算相对偏差，超 MPE 判不合格；超差处置按计量点 ``block``/``warn``
定；结论带规则版本，换版即迁移、历史结论按当时版本解释；参照路异常是部分
失败，不出结论；校验记录、加注记录与审计三方对账必须一致。
"""

from __future__ import annotations

import pytest

from hydrorefuel.app import build_platform
from hydrorefuel.clock import ManualClock
from hydrorefuel.config import default_config
from hydrorefuel.errors import CalibError
from support import load_station

OPERATOR = "metrologist.li"


def _block_point(platform):
    return platform.register_metering_point(OPERATOR, "MP-BLOCK", "station.flowmeter", "block")


def _warn_point(platform):
    return platform.register_metering_point(OPERATOR, "MP-WARN", "station.flowmeter", "warn")


def test_same_quantity_pass_and_fail_are_recorded(platform):
    _block_point(platform)
    ok = platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    assert ok["method"] == "same_quantity_single_pass"
    assert ok["conclusion"] == "pass"
    assert ok["status"] == "complete"
    assert ok["deviation_pct"] == 0.0
    bad = platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0, dut_bias_pct=3.0)
    assert bad["conclusion"] == "fail"
    assert abs(bad["deviation_pct"] - 3.0) < 0.01
    assert bad["reason"] == "exceeds_mpe"
    history = platform.calib_history("MP-BLOCK")["records"]
    assert [item["conclusion"] for item in history] == ["pass", "fail"]
    assert all(item["operator"] == OPERATOR for item in history)


def test_deviation_uses_reference_as_baseline(platform):
    rule = platform.calib._rules.active()
    verdict = rule.evaluate(dut_kg=5.09, ref_kg=5.0)
    assert verdict["conclusion"] == "fail"
    assert abs(verdict["deviation_pct"] - 1.8) < 0.001


def test_fill_blocked_without_verification(platform):
    _block_point(platform)
    result = platform.fill(vehicle="v1", steps=1, point_id="MP-BLOCK", operator=OPERATOR)
    assert result["ok"] is False
    assert result["violations"] == ["calib_gate_blocked"]
    assert result["gate"]["reason"] == "no_verification"


def test_block_policy_stops_fill_after_fail(platform):
    _block_point(platform)
    platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    allowed = platform.calib.gate_fill(OPERATOR, "MP-BLOCK", vehicle="v1")
    assert allowed["decision"] == "allowed"
    platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0, dut_bias_pct=3.0)
    result = platform.fill(vehicle="v2", steps=1, point_id="MP-BLOCK", operator=OPERATOR)
    assert result["ok"] is False
    assert result["gate"]["decision"] == "blocked"
    assert result["gate"]["reason"] == "verification_fail_block"


def test_warn_policy_prompts_then_allows_on_ack(platform):
    _warn_point(platform)
    platform.verify_meter(OPERATOR, "MP-WARN", target_kg=5.0, dut_bias_pct=-2.0)
    prompt = platform.calib.gate_fill(OPERATOR, "MP-WARN", vehicle="v3")
    assert prompt["decision"] == "warned"
    assert prompt["acknowledged"] is False
    unacknowledged = platform.fill(vehicle="v3", steps=1, point_id="MP-WARN", operator=OPERATOR)
    assert unacknowledged["ok"] is False
    assert unacknowledged["gate"]["decision"] == "warned"
    # 确认后门禁放行但保留警告；平台据此真正执行加注。
    acknowledged = platform.calib.gate_fill(OPERATOR, "MP-WARN", vehicle="v3", acknowledged=True)
    assert acknowledged["decision"] == "allowed"
    assert acknowledged["warning"] is True
    assert acknowledged["acknowledged"] is True
    # 先建立瓶组压力等工艺条件，确认后的加注才真正放行并执行。
    load_station(platform)
    proceed = platform.fill(vehicle="v3", steps=1, point_id="MP-WARN", operator=OPERATOR, acknowledged=True)
    assert proceed["ok"] is True
    assert proceed["gate"]["decision"] == "allowed"
    assert proceed["gate"]["warning"] is True


def test_reference_fault_is_partial_failure_without_conclusion(platform):
    _block_point(platform)
    platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0, dut_bias_pct=3.0)
    platform.mark_reference_fault(OPERATOR, "sensor_drift")
    result = platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    assert result["conclusion"] == "inconclusive"
    assert result["status"] == "partial_failure"
    assert result["ref_healthy"] is False
    # 状态停在上一条有效结论（不合格），没有被部分失败改写成别的。
    status = {item["point_id"]: item for item in platform.calib.statuses()}["MP-BLOCK"]
    assert status["latest_conclusion"] == "fail"
    platform.recover_reference(OPERATOR)
    assert platform.calib.reference_healthy()["healthy"] is True


def test_rule_migration_invalidates_old_conclusions_but_keeps_history(platform):
    _block_point(platform)
    platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    platform.activate_calib_rule(OPERATOR, "calib-v2.0", mpe_pct=1.0)
    stale = platform.calib.gate_fill(OPERATOR, "MP-BLOCK", vehicle="v4")
    assert stale["decision"] == "blocked"
    assert stale["reason"] == "stale_rule_version"
    history = platform.calib_history("MP-BLOCK")["records"]
    assert all(item["rule_version"] == "calib-v1.0" for item in history)
    assert all(item["rule_snapshot"]["mpe_pct"] == 1.5 for item in history)
    rechecked = platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    assert rechecked["rule_version"] == "calib-v2.0"
    assert rechecked["conclusion"] == "pass"


def test_expired_conclusion_blocks(platform, clock):
    _block_point(platform)
    platform.verify_meter(OPERATOR, "MP-BLOCK", target_kg=5.0)
    clock.advance(default_config().calib.valid_ttl_s + 1)
    gate = platform.calib.gate_fill(OPERATOR, "MP-BLOCK", vehicle="v5")
    assert gate["decision"] == "blocked"
    assert gate["reason"] == "verification_expired"


def test_operator_is_required(platform):
    _block_point(platform)
    with pytest.raises(CalibError):
        platform.verify_meter("", "MP-BLOCK", target_kg=5.0)
    with pytest.raises(CalibError):
        platform.calib.gate_fill("", "MP-BLOCK")


def test_unknown_method_cannot_be_sneaked_in(platform):
    with pytest.raises(CalibError):
        platform.activate_calib_rule(OPERATOR, "calib-v9.9", method="segmented_comparison")


def test_duplicate_point_and_unknown_point_rejected(platform):
    _block_point(platform)
    with pytest.raises(CalibError):
        _block_point(platform)
    with pytest.raises(CalibError):
        platform.calib.gate_fill(OPERATOR, "NO-SUCH-POINT")


def test_three_way_reconciliation_is_consistent(platform):
    _warn_point(platform)
    platform.verify_meter(OPERATOR, "MP-WARN", target_kg=5.0, dut_bias_pct=2.0)
    platform.calib.gate_fill(OPERATOR, "MP-WARN", vehicle="v6")
    platform.calib.gate_fill(OPERATOR, "MP-WARN", vehicle="v6", acknowledged=True)
    report = platform.calib_reconciliation()
    assert report["consistent"] is True
    assert report["missing_in_audit"] == []
    assert report["field_mismatches"] == []
    assert report["calib_records"] == 1
    assert report["fill_records"] == 2


def test_state_and_history_restore_across_restart(tmp_path):
    root = str(tmp_path / "state")
    clock = ManualClock()
    first = build_platform(default_config(), clock=clock, storage_root=root)
    first.register_metering_point(OPERATOR, "MP-RESTORE", "station.flowmeter", "block")
    first.verify_meter(OPERATOR, "MP-RESTORE", target_kg=5.0)
    first.activate_calib_rule(OPERATOR, "calib-v2.0", mpe_pct=1.0)
    second = build_platform(default_config(), clock=clock, storage_root=root)
    status = {item["point_id"]: item for item in second.calib.statuses()}
    assert "MP-RESTORE" in status
    assert status["MP-RESTORE"]["latest_rule_version"] == "calib-v1.0"
    assert status["MP-RESTORE"]["state_reason"] == "stale_rule_version"
    assert second.calib_snapshot()["rules"]["active_version"] == "calib-v2.0"
    assert second.calib_reconciliation()["consistent"] is True
