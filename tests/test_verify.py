"""流量计在线校验：分段比对、超差处置、版本迁移与历史留存。"""

from __future__ import annotations

import dataclasses

import pytest

from hydrorefuel.app import DEFAULT_POINT, build_platform
from hydrorefuel.config import default_config
from hydrorefuel.console.api import Api
from hydrorefuel.errors import FlowError, HydroError


def _warn_platform(tmp_path, clock):
    """装配一个计量点策略为"仅提示"的平台。"""

    config = default_config()
    verify = dataclasses.replace(config.verify, on_fail="warn", points={DEFAULT_POINT: "warn"})
    return build_platform(dataclasses.replace(config, verify=verify), clock=clock, storage_root=str(tmp_path / "state"))


def test_verify_pass_records_conclusion(platform):
    record = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=3.0)
    assert record["conclusive"] is True
    assert record["conclusion"] == "pass"
    assert record["operator"] == "op-zhang"
    assert record["point"] == DEFAULT_POINT
    assert record["criteria"]["version"] == "v1"
    assert record["criteria"]["method"] == "segmented"
    assert len(record["segments"]) == 3
    assert all(segment["within"] for segment in record["segments"])
    assert record["max_deviation_pct"] == 0.0
    assert record["status"]["qualification"] == "qualified"
    # 校验结论落审计，操作人与版本可追。
    entries = platform.audit.entries()
    passed = [entry for entry in entries if entry["action"] == "verify_pass"]
    assert passed and passed[0]["payload"]["operator"] == "op-zhang"
    assert passed[0]["payload"]["version"] == "v1"


def test_verify_segments_compare_per_segment(platform):
    record = platform.verify_run(operator="op-li", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.97)
    assert record["conclusion"] == "pass"
    for segment in record["segments"]:
        assert segment["meter_kg"] == pytest.approx(1.8, abs=1e-3)
        assert segment["reference_kg"] == pytest.approx(1.782, abs=1e-3)
        assert segment["deviation_pct"] == pytest.approx(1.01, abs=0.01)


def test_verify_fail_blocks_fill(platform):
    record = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.9)
    assert record["conclusion"] == "fail"
    assert record["status"]["qualification"] == "unqualified"

    fill = platform.fill(vehicle="vehicle01")
    assert fill["ok"] is False
    assert fill["violations"] == ["meter_unqualified"]
    assert fill["verification"]["run_id"] == record["run_id"]
    assert fill["verification"]["version"] == "v1"

    actions = [entry["action"] for entry in platform.audit.entries()]
    assert "verify_fail" in actions
    assert "fill_blocked_verify" in actions
    blocked = [entry for entry in platform.audit.entries() if entry["action"] == "fill_blocked_verify"]
    assert blocked[0]["severity"] == "alarm"
    assert blocked[0]["payload"]["run_id"] == record["run_id"]
    assert platform.readiness()["checks"]["flow_verify_clear"] is False


def test_verify_fail_warn_policy_only_prompts(tmp_path, clock):
    platform = _warn_platform(tmp_path, clock)
    record = platform.verify_run(operator="op-wang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.9)
    assert record["conclusion"] == "fail"

    fill = platform.fill(vehicle="vehicle01", steps=2)
    assert fill["ok"] is True
    assert fill["verification"]["conclusion"] == "fail"
    assert fill["verification"]["on_fail"] == "warn"

    entries = platform.audit.entries()
    assert any(entry["action"] == "fill_verify_warning" for entry in entries)
    assert not any(entry["action"] == "fill_blocked_verify" for entry in entries)
    # 加注记录的审计汇总携带校验状态，三处按 run_id 互查。
    completed = [entry for entry in entries if entry["action"] == "fill_complete"]
    assert completed[0]["payload"]["verification"]["run_id"] == record["run_id"]


def test_reference_fault_is_inconclusive(platform):
    platform.fault_reference("signal_lost")
    record = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=3.0)
    assert record["conclusive"] is False
    assert record["conclusion"] is None
    assert record["reason"] == "reference_leg_fault"
    # 部分失败不出结论，也不改变既有合格状态。
    assert record["status"]["qualification"] == "unverified"
    assert platform.verify_status()["reference"]["faulted"] is True

    fill = platform.fill(vehicle="vehicle01", steps=1)
    assert fill["ok"] is True
    assert any(entry["action"] == "verify_inconclusive" for entry in platform.audit.entries())


def test_reference_out_of_range_reading_is_inconclusive(platform):
    record = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=99.0)
    assert record["conclusive"] is False
    assert record["reason"] == "reference_leg_fault"


def test_inconclusive_run_keeps_previous_standing(platform):
    passed = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=3.0)
    assert passed["conclusion"] == "pass"

    platform.fault_reference("frozen_signal")
    inconclusive = platform.verify_run(operator="op-zhang")
    assert inconclusive["conclusive"] is False
    status = platform.verify_status()["points"][0]
    assert status["qualification"] == "qualified"
    assert status["standing"]["run_id"] == passed["run_id"]
    assert status["last_run"]["run_id"] == inconclusive["run_id"]

    platform.clear_reference_fault()
    assert platform.verify_status()["reference"]["faulted"] is False


def test_migration_versions_and_history(platform):
    first = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.97)
    assert first["conclusion"] == "pass"

    migration = platform.verify_migrate(operator="op-admin", version="v2", tolerance_pct=0.5, reason="tighten")
    assert migration["from_version"] == "v1"
    assert migration["to_version"] == "v2"
    assert migration["criteria"]["tolerance_pct"] == 0.5

    # 同一偏差在 v1 合格、在 v2 超差；历史结论仍按 v1 解释，不做重判。
    second = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.97)
    assert second["conclusion"] == "fail"
    assert second["criteria"]["version"] == "v2"

    history = platform.verify_history()
    runs = {record["run_id"]: record for record in history if record["kind"] == "run"}
    assert runs[first["run_id"]]["conclusion"] == "pass"
    assert runs[first["run_id"]]["criteria"]["version"] == "v1"
    assert runs[second["run_id"]]["conclusion"] == "fail"
    migrations = [record for record in history if record["kind"] == "migration"]
    assert migrations[0]["operator"] == "op-admin"
    assert any(entry["action"] == "verify_migrated" for entry in platform.audit.entries())


def test_migration_requires_new_version_and_operator(platform):
    with pytest.raises(HydroError):
        platform.verify_migrate(operator="op-admin", version="v1")
    with pytest.raises(HydroError):
        platform.verify_migrate(operator="", version="v2")
    with pytest.raises(HydroError):
        platform.verify_migrate(operator="op-admin", version="v2", tolerance_pct=0.0)


def test_verify_requires_operator_and_known_point(platform):
    with pytest.raises(HydroError):
        platform.verify_run(operator="  ")
    with pytest.raises(HydroError):
        platform.verify_run(operator="op-zhang", point="station.elsewhere")
    assert platform.verify_history() == []


def test_meter_rate_out_of_range_is_rejected(platform):
    with pytest.raises(FlowError):
        platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=99.0)
    assert platform.verify_history() == []


def test_history_survives_restart(tmp_path, clock):
    storage = str(tmp_path / "state")
    platform = build_platform(default_config(), clock=clock, storage_root=storage)
    record = platform.verify_run(operator="op-zhang", meter_rate_kg_per_min=3.0, reference_rate_kg_per_min=2.9)
    assert record["conclusion"] == "fail"

    clock.advance(10.0)
    restored = build_platform(default_config(), clock=clock, storage_root=storage)
    status = restored.verify_status()["points"][0]
    assert status["qualification"] == "unqualified"
    assert status["standing"]["run_id"] == record["run_id"]
    assert status["standing"]["operator"] == "op-zhang"
    fill = restored.fill(vehicle="vehicle01")
    assert fill["ok"] is False
    assert fill["violations"] == ["meter_unqualified"]
    history = restored.verify_history()
    assert any(item["run_id"] == record["run_id"] for item in history)


def test_console_verify_endpoints(platform):
    api = Api(platform)
    status, payload = api.handle("POST", "/api/verify/run", {}, {"operator": "op-console", "reference_rate_kg_per_min": 2.9})
    assert status == 200
    assert payload["conclusion"] == "fail"
    run_id = payload["run_id"]

    status, payload = api.handle("GET", "/api/verify", {}, {})
    assert status == 200
    assert payload["verify"]["points"][0]["qualification"] == "unqualified"
    assert payload["verify"]["active_criteria"]["version"] == "v1"

    status, payload = api.handle("GET", "/api/verify/history", {"point": DEFAULT_POINT}, {})
    assert status == 200
    assert payload["records"][0]["run_id"] == run_id

    # 校验历史与审计按同一 run_id 互查。
    status, payload = api.handle("GET", "/api/audit", {}, {})
    audited = [entry for entry in payload["entries"] if entry["action"] == "verify_fail"]
    assert audited and audited[0]["payload"]["op_id"] == run_id

    status, payload = api.handle("POST", "/api/verify/migrate", {}, {"operator": "op-admin", "version": "v2"})
    assert status == 200
    assert payload["to_version"] == "v2"

    status, payload = api.handle("POST", "/api/verify/reference", {}, {"fault": True, "reason": "drill"})
    assert status == 200
    assert payload["faulted"] is True
    status, payload = api.handle("POST", "/api/verify/reference", {}, {"fault": False})
    assert payload["faulted"] is False

    # 缺少操作人按平台异常处理并留痕。
    status, payload = api.handle("POST", "/api/verify/run", {}, {})
    assert status == 409
    assert payload["component"] == "verify"


def test_status_and_readiness_expose_verify(platform):
    platform.start()
    status = platform.status()
    assert status["verify"]["active_criteria"]["version"] == "v1"
    assert status["verify"]["points"][0]["qualification"] == "unverified"
    assert platform.readiness()["checks"]["flow_verify_clear"] is True
