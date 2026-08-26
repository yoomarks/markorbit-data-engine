from contextlib import contextmanager

import pytest

from app.cn import audit_acceptance_m16


def test_final_acceptance_uses_cn_resource_and_spill_profile(monkeypatch):
    calls = {}

    def audit_factory():
        return "AUDIT_RAW"

    def followup_factory():
        return "FOLLOWUP_RAW"

    monkeypatch.setattr(audit_acceptance_m16.audit_data, "clickhouse_client", audit_factory)
    monkeypatch.setattr(
        audit_acceptance_m16.audit_followup,
        "clickhouse_client",
        followup_factory,
    )

    def fake_resource_client(factory):
        calls.setdefault("resource_factories", []).append(factory)
        return f"RESOURCE:{factory()}"

    monkeypatch.setattr(
        audit_acceptance_m16,
        "cn_resource_client",
        fake_resource_client,
    )

    @contextmanager
    def fake_execution_settings(**kwargs):
        calls["execution_settings"] = kwargs
        calls["inside_profile"] = True
        try:
            yield
        finally:
            calls["inside_profile"] = False

    monkeypatch.setattr(
        audit_acceptance_m16,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fake_acceptance():
        assert calls["inside_profile"] is True
        calls["audit_client"] = audit_acceptance_m16.audit_data.clickhouse_client()
        calls["followup_client"] = audit_acceptance_m16.audit_followup.clickhouse_client()
        return {"status": "PASS"}

    monkeypatch.setattr(audit_acceptance_m16, "acceptance_main", fake_acceptance)

    assert audit_acceptance_m16.build_acceptance_audit_m16() == {"status": "PASS"}
    assert calls["execution_settings"] == {
        "join_algorithm": "grace_hash",
        "grace_hash_join_initial_buckets": 32,
        "send_receive_timeout": 3600,
    }
    assert calls["resource_factories"] == [audit_factory, followup_factory]
    assert calls["audit_client"] == "RESOURCE:AUDIT_RAW"
    assert calls["followup_client"] == "RESOURCE:FOLLOWUP_RAW"
    assert audit_acceptance_m16.audit_data.clickhouse_client is audit_factory
    assert audit_acceptance_m16.audit_followup.clickhouse_client is followup_factory


def test_final_acceptance_restores_clients_after_failure(monkeypatch):
    def audit_factory():
        return object()

    def followup_factory():
        return object()

    monkeypatch.setattr(audit_acceptance_m16.audit_data, "clickhouse_client", audit_factory)
    monkeypatch.setattr(
        audit_acceptance_m16.audit_followup,
        "clickhouse_client",
        followup_factory,
    )
    monkeypatch.setattr(
        audit_acceptance_m16,
        "cn_resource_client",
        lambda factory: factory(),
    )

    @contextmanager
    def fake_execution_settings(**kwargs):
        yield

    monkeypatch.setattr(
        audit_acceptance_m16,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fail_acceptance():
        raise RuntimeError("synthetic acceptance failure")

    monkeypatch.setattr(audit_acceptance_m16, "acceptance_main", fail_acceptance)

    with pytest.raises(RuntimeError, match="synthetic acceptance failure"):
        audit_acceptance_m16.build_acceptance_audit_m16()

    assert audit_acceptance_m16.audit_data.clickhouse_client is audit_factory
    assert audit_acceptance_m16.audit_followup.clickhouse_client is followup_factory
