from contextlib import contextmanager

import pytest

from app.cn import validate_fixture_m16


def test_m16_fixture_uses_cn_resource_profile(monkeypatch):
    calls = {}

    def original_client():
        return object()

    monkeypatch.setattr(validate_fixture_m16.legacy, "clickhouse_client", original_client)

    def fake_resource_client(factory):
        calls["resource_factory"] = factory
        return "CN_RESOURCE_CLIENT"

    monkeypatch.setattr(validate_fixture_m16, "cn_resource_client", fake_resource_client)

    @contextmanager
    def fake_execution_settings(**kwargs):
        calls["execution_settings"] = kwargs
        yield

    monkeypatch.setattr(
        validate_fixture_m16,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fake_fixture_main():
        calls["runtime_client"] = validate_fixture_m16.legacy.clickhouse_client()

    monkeypatch.setattr(validate_fixture_m16, "fixture_main", fake_fixture_main)

    validate_fixture_m16.main()

    assert calls["resource_factory"] is original_client
    assert calls["runtime_client"] == "CN_RESOURCE_CLIENT"
    assert calls["execution_settings"] == {
        "join_algorithm": "grace_hash",
        "grace_hash_join_initial_buckets": 32,
        "send_receive_timeout": 3600,
    }
    assert validate_fixture_m16.legacy.clickhouse_client is original_client


def test_m16_fixture_restores_client_after_failure(monkeypatch):
    def original_client():
        return object()

    monkeypatch.setattr(validate_fixture_m16.legacy, "clickhouse_client", original_client)
    monkeypatch.setattr(
        validate_fixture_m16,
        "cn_resource_client",
        lambda factory: factory(),
    )

    @contextmanager
    def fake_execution_settings(**kwargs):
        yield

    monkeypatch.setattr(
        validate_fixture_m16,
        "clickhouse_execution_settings",
        fake_execution_settings,
    )

    def fail_fixture():
        raise RuntimeError("synthetic fixture failure")

    monkeypatch.setattr(validate_fixture_m16, "fixture_main", fail_fixture)

    with pytest.raises(RuntimeError, match="synthetic fixture failure"):
        validate_fixture_m16.main()

    assert validate_fixture_m16.legacy.clickhouse_client is original_client
