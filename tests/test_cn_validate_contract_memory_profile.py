from contextlib import contextmanager

import pytest

from app.cn import validate_contract


def test_empty_publish_contract_uses_cn_spill_profile(monkeypatch):
    calls = {}

    def original_client():
        return object()

    monkeypatch.setattr(validate_contract.legacy, "clickhouse_client", original_client)

    def fake_resource_client(factory):
        calls["resource_factory"] = factory
        return "CN_RESOURCE_CLIENT"

    monkeypatch.setattr(validate_contract, "cn_resource_client", fake_resource_client)

    @contextmanager
    def fake_execution_settings(**kwargs):
        calls["execution_settings"] = kwargs
        yield

    monkeypatch.setattr(
        validate_contract,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fake_publish(package_uuid, package_meta):
        calls["runtime_client"] = validate_contract.legacy.clickhouse_client()
        calls["package_meta"] = package_meta
        return {"case_rows": 0, "scope_rows": 0}

    monkeypatch.setattr(validate_contract.legacy, "_publish", fake_publish)

    metrics = validate_contract._assert_empty_publish_compiles()

    assert metrics == {"case_rows": 0, "scope_rows": 0}
    assert calls["resource_factory"] is original_client
    assert calls["runtime_client"] == "CN_RESOURCE_CLIENT"
    assert calls["execution_settings"] == {
        "join_algorithm": "grace_hash",
        "grace_hash_join_initial_buckets": 32,
        "send_receive_timeout": 3600,
    }
    assert calls["package_meta"] == {
        "package_kind": "CONTRACT_PREFLIGHT",
        "source_rank": 1,
        "source_period_end": None,
    }
    assert validate_contract.legacy.clickhouse_client is original_client


def test_empty_publish_contract_restores_client_after_failure(monkeypatch):
    def original_client():
        return object()

    monkeypatch.setattr(validate_contract.legacy, "clickhouse_client", original_client)
    monkeypatch.setattr(
        validate_contract,
        "cn_resource_client",
        lambda factory: factory(),
    )

    @contextmanager
    def fake_execution_settings(**kwargs):
        yield

    monkeypatch.setattr(
        validate_contract,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fail_publish(package_uuid, package_meta):
        raise RuntimeError("synthetic contract failure")

    monkeypatch.setattr(validate_contract.legacy, "_publish", fail_publish)

    with pytest.raises(RuntimeError, match="synthetic contract failure"):
        validate_contract._assert_empty_publish_compiles()

    assert validate_contract.legacy.clickhouse_client is original_client
