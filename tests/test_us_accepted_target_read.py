from types import SimpleNamespace

import pytest

import app.us.accepted_target_read as target_read
from app.us.accepted_target_read import AcceptedUSTargetReadClient


class FakeBase:
    def __init__(self):
        self.calls = []

    def query(self, sql, settings=None):
        self.calls.append((sql, settings))
        return SimpleNamespace(column_names=["n"], result_rows=[[1]])


def test_accepted_target_read_client_allows_only_read_query():
    base = FakeBase()
    client = AcceptedUSTargetReadClient(base=base)
    result = client.query("SELECT 1 AS n", settings={"max_threads": 1})
    assert result.result_rows == [[1]]
    assert base.calls == [("SELECT 1 AS n", {"max_threads": 1})]


@pytest.mark.parametrize("sql", ["INSERT INTO x VALUES (1)", "ALTER TABLE x DELETE WHERE 1", "DROP TABLE x"])
def test_accepted_target_read_client_rejects_mutating_query(sql):
    client = AcceptedUSTargetReadClient(base=FakeBase())
    with pytest.raises(RuntimeError, match="read-only SQL"):
        client.query(sql)


def test_accepted_target_read_client_has_no_mutation_surface():
    client = AcceptedUSTargetReadClient(base=FakeBase())
    with pytest.raises(RuntimeError, match="does not permit commands"):
        client.command("CREATE TABLE x")
    with pytest.raises(RuntimeError, match="does not permit inserts"):
        client.insert("x", [[1]], column_names=["n"])

def test_accepted_target_read_client_decodes_fixed_string_bytes():
    class BytesBase:
        def query(self, _sql, settings=None):
            return SimpleNamespace(
                column_names=["candidate_key", "nested"],
                result_rows=[(b"a" * 64, [b"009", b"035"])],
            )

    result = AcceptedUSTargetReadClient(base=BytesBase()).query("SELECT 1")
    assert result.result_rows == [["a" * 64, ["009", "035"]]]

def test_accepted_target_host_uses_localhost_outside_container(monkeypatch):
    monkeypatch.setattr(target_read, "_in_container", lambda: False)
    assert target_read._accepted_target_host() == "127.0.0.1"


def test_accepted_target_host_uses_stable_docker_endpoint_in_container(monkeypatch):
    monkeypatch.setattr(target_read, "_in_container", lambda: True)
    assert target_read._accepted_target_host() == "host.docker.internal"


def test_factory_pins_accepted_target_connection(monkeypatch):
    captured = {}

    class DummyBase:
        pass

    def fake_get_client(**kwargs):
        captured.update(kwargs)
        return DummyBase()

    monkeypatch.setattr(target_read, "_in_container", lambda: True)
    monkeypatch.setattr(target_read.clickhouse_connect, "get_client", fake_get_client)
    target_read.AcceptedUSTargetReadClient()
    assert captured == {
        "host": "host.docker.internal",
        "port": 28123,
        "username": "default",
        "password": "",
        "database": "markorbit_facts",
    }
