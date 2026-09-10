from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path

import pytest

from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    STAGE_DATABASE,
    TARGET_DATABASE,
    TARGET_HTTP_PORT,
    TARGET_STORAGE_POLICY,
    WslNativeClickHouseClient,
    build_target_schema_manifest,
    commit_statements,
    freeze_package,
    normalize_show_create_for_hot_us,
    stage_ddl_from_manifest,
    stage_table_map,
    validate_target_schema_manifest,
)


class FakeHttpResponse:
    def __init__(self, body=b"", *, status=200, headers=None):
        self._body = body
        self.status = status
        self._headers = dict(headers or {})

    def read(self):
        return self._body

    def getheader(self, name):
        return self._headers.get(name)


class FakeHttpConnection:
    def __init__(self, response=None, *, request_error=None):
        self.response = response or FakeHttpResponse()
        self.request_error = request_error
        self.requests = []
        self.closed = False

    def request(self, method, path, *, body, headers):
        self.requests.append((method, path, body, dict(headers)))
        if self.request_error is not None:
            raise self.request_error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def _full_table(short_name: str) -> str:
    return f"{TARGET_DATABASE}.{short_name}"


def _show_create(table: str, *, settings: str = "") -> str:
    full_table = table if table.startswith(f"{TARGET_DATABASE}.") else _full_table(table)
    suffix = f" SETTINGS {settings}" if settings else ""
    return (
        f"CREATE TABLE {full_table} "
        "(id String, source_package_id UUID) "
        "ENGINE = MergeTree ORDER BY id"
        f"{suffix}"
    )


def _manifest() -> dict[str, object]:
    return build_target_schema_manifest(
        {table: _show_create(table) for table in APPLICATION_CANARY_TABLES}
    )


def _frozen_package(tmp_path: Path):
    package = tmp_path / "apc260102.zip"
    package.write_bytes(b"frozen-canary")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    return freeze_package(
        package,
        expected_size=package.stat().st_size,
        expected_sha256=digest,
        package_kind="APPLICATION_DAILY",
        source_rank=200,
        source_effective_date=date(2026, 1, 2),
    )


def test_show_create_is_normalized_to_hot_us_only_without_alter() -> None:
    table = _full_table("us_case_current")
    sql = normalize_show_create_for_hot_us(
        _show_create(table, settings="index_granularity = 8192"),
        expected_table=table,
    )

    assert sql.startswith("CREATE TABLE IF NOT EXISTS markorbit_facts.us_case_current")
    assert "ALTER TABLE" not in sql.upper()
    assert "DROP TABLE" not in sql.upper()
    assert "DELETE WHERE" not in sql.upper()
    assert " TTL " not in f" {sql.upper()} "
    assert f"storage_policy = '{TARGET_STORAGE_POLICY}'" in sql
    assert "index_granularity = 8192" in sql


def test_existing_storage_policy_is_replaced_not_duplicated() -> None:
    table = _full_table("us_case_current")
    sql = normalize_show_create_for_hot_us(
        _show_create(table, settings="storage_policy = 'default'"),
        expected_table=table,
    )

    assert sql.count("storage_policy") == 1
    assert f"storage_policy = '{TARGET_STORAGE_POLICY}'" in sql
    assert "'default'" not in sql


def test_forbidden_or_wrong_show_create_fails_closed() -> None:
    table = _full_table("us_case_current")
    with pytest.raises(ValueError, match="forbidden mutation"):
        normalize_show_create_for_hot_us(
            "ALTER TABLE markorbit_facts.us_case_current DELETE WHERE 1",
            expected_table=table,
        )

    with pytest.raises(ValueError, match="mismatch"):
        normalize_show_create_for_hot_us(
            _show_create(_full_table("us_owner_current")),
            expected_table=table,
        )


def test_ttl_show_create_fails_closed() -> None:
    table = _full_table("us_case_current")
    with pytest.raises(ValueError, match="forbidden mutation"):
        normalize_show_create_for_hot_us(
            _show_create(table) + " TTL now() + INTERVAL 7 DAY",
            expected_table=table,
        )


def test_schema_manifest_requires_exact_application_table_set() -> None:
    show_create = {table: _show_create(table) for table in APPLICATION_CANARY_TABLES}
    show_create.pop(next(iter(show_create)))

    with pytest.raises(ValueError, match="schema set mismatch"):
        build_target_schema_manifest(show_create)


def test_schema_manifest_is_sha_bound_and_all_tables_are_hot_us_only() -> None:
    manifest = _manifest()
    validate_target_schema_manifest(manifest)

    assert manifest["tables"] == list(APPLICATION_CANARY_TABLES)
    assert manifest["storage_policy"] == TARGET_STORAGE_POLICY
    assert len(manifest["sha256"]) == 64
    for statement in list(manifest["statements"])[1:]:
        assert f"storage_policy = '{TARGET_STORAGE_POLICY}'" in statement
        assert "ALTER TABLE" not in statement.upper()
        assert " TTL " not in f" {statement.upper()} "

    tampered = dict(manifest)
    tampered["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_target_schema_manifest(tampered)


def test_freeze_package_fails_before_write_on_size_or_hash_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "apc260102.zip"
    package.write_bytes(b"immutable")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="size mismatch"):
        freeze_package(
            package,
            expected_size=package.stat().st_size + 1,
            expected_sha256=digest,
            package_kind="APPLICATION_DAILY",
            source_rank=200,
            source_effective_date=None,
        )

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        freeze_package(
            package,
            expected_size=package.stat().st_size,
            expected_sha256="0" * 64,
            package_kind="APPLICATION_DAILY",
            source_rank=200,
            source_effective_date=None,
        )


def test_native_client_is_pinned_to_target_runtime_and_ports() -> None:
    with pytest.raises(ValueError, match="distro"):
        WslNativeClickHouseClient(distro="docker-desktop")
    with pytest.raises(ValueError, match="native port"):
        WslNativeClickHouseClient(port=28123)
    with pytest.raises(ValueError, match="HTTP port"):
        WslNativeClickHouseClient(http_port=29000)


def test_target_client_uses_persistent_host_http_transport() -> None:
    connection = FakeHttpConnection(FakeHttpResponse(b"[1]\n"))
    client = WslNativeClickHouseClient(connection=connection)

    assert client.query("SELECT 1").result_rows == [[1]]
    assert client.query("SELECT 1").result_rows == [[1]]

    assert len(connection.requests) == 2
    for method, path, body, headers in connection.requests:
        assert method == "POST"
        assert path == "/?wait_end_of_query=1"
        assert body == b"SELECT 1 FORMAT JSONCompactEachRow"
        assert headers["Content-Type"] == "application/octet-stream"
    assert client.http_port == TARGET_HTTP_PORT

def test_native_client_query_preserves_unicode_line_separator_inside_json_string() -> None:
    payload = "[\"BASS ADDICTION You Wouldn't Understand\u0085\"]\n"
    connection = FakeHttpConnection(FakeHttpResponse(payload.encode("utf-8")))
    client = WslNativeClickHouseClient(connection=connection)

    result = client.query("SELECT statement_text")

    assert result.result_rows == [["BASS ADDICTION You Wouldn't Understand\u0085"]]


def test_native_client_insert_pins_utf8_for_non_gbk_payload() -> None:
    connection = FakeHttpConnection()
    client = WslNativeClickHouseClient(connection=connection)
    client.insert(
        f"{STAGE_DATABASE}.utf8_transport_probe",
        [["orbit-😀"]],
        column_names=["mark_name"],
    )

    assert len(connection.requests) == 1
    body = connection.requests[0][2]
    assert body.startswith(
        b"INSERT INTO markorbit_canary_stage.utf8_transport_probe (mark_name) FORMAT JSONEachRow\n"
    )
    assert body.endswith(b'{"mark_name":"orbit-\xf0\x9f\x98\x80"}\n')

def test_native_client_reports_invalid_utf8_after_binary_http_capture() -> None:
    connection = FakeHttpConnection(FakeHttpResponse(b'["0"]\n\x8e'))
    client = WslNativeClickHouseClient(connection=connection)

    with pytest.raises(RuntimeError, match="invalid UTF-8 on stdout"):
        client.query("SELECT count()")


def test_target_http_error_is_fail_closed_with_clickhouse_diagnostic() -> None:
    response = FakeHttpResponse(
        b"Code: 241. Memory limit exceeded",
        status=500,
        headers={"X-ClickHouse-Exception-Code": "241"},
    )
    connection = FakeHttpConnection(response)
    client = WslNativeClickHouseClient(connection=connection)

    with pytest.raises(RuntimeError, match="status=500.*exception_code=241"):
        client.query("SELECT 1")
    assert len(connection.requests) == 1

def test_target_http_transport_failure_is_not_retried() -> None:
    connection = FakeHttpConnection(request_error=ConnectionResetError("channel closed"))
    client = WslNativeClickHouseClient(connection=connection)

    with pytest.raises(RuntimeError, match="failed without retry"):
        client.query("SELECT 1")
    assert len(connection.requests) == 1
    assert connection.closed is True


def test_native_client_rejects_destructive_commands_before_transport() -> None:
    connection = FakeHttpConnection()
    client = WslNativeClickHouseClient(connection=connection)

    with pytest.raises(RuntimeError, match="forbidden mutation"):
        client.command("ALTER TABLE markorbit_facts.us_case_current DELETE WHERE 1")
    assert connection.requests == []

def test_stage_tables_are_package_scoped_hot_us_and_not_idempotent_create(
    tmp_path: Path,
) -> None:
    package = _frozen_package(tmp_path)
    manifest = _manifest()
    stage_map = stage_table_map(package)
    statements = stage_ddl_from_manifest(manifest, package)

    assert statements[0] == f"CREATE DATABASE IF NOT EXISTS {STAGE_DATABASE}"
    assert len(stage_map) == len(APPLICATION_CANARY_TABLES)
    assert len(set(stage_map.values())) == len(APPLICATION_CANARY_TABLES)
    for statement in statements[1:]:
        assert statement.startswith(f"CREATE TABLE {STAGE_DATABASE}.")
        assert "CREATE TABLE IF NOT EXISTS markorbit_canary_stage" not in statement
        assert f"storage_policy = '{TARGET_STORAGE_POLICY}'" in statement
        assert "ALTER TABLE" not in statement.upper()
        assert " TTL " not in f" {statement.upper()} "


def test_stage_tables_neutralize_replacing_merge_engine_for_lossless_counts(tmp_path: Path) -> None:
    package = _frozen_package(tmp_path)
    show_create = {table: _show_create(table) for table in APPLICATION_CANARY_TABLES}
    show_create[_full_table("us_owner_current")] = show_create[_full_table("us_owner_current")].replace(
        "ENGINE = MergeTree", "ENGINE = ReplacingMergeTree(source_rank, is_deleted)"
    )
    show_create[_full_table("us_event_history")] = show_create[_full_table("us_event_history")].replace(
        "ENGINE = MergeTree", "ENGINE = ReplacingMergeTree(source_rank)"
    )
    statements = stage_ddl_from_manifest(build_target_schema_manifest(show_create), package)
    stage_sql = "\n".join(statements[1:])
    assert "ReplacingMergeTree" not in stage_sql
    assert stage_sql.count("ENGINE = MergeTree") == len(APPLICATION_CANARY_TABLES)


def test_commit_plan_is_exact_one_package_and_insert_only(tmp_path: Path) -> None:
    package = _frozen_package(tmp_path)
    plan = commit_statements(package)
    stage_map = stage_table_map(package)

    assert [item["table"] for item in plan] == list(APPLICATION_CANARY_TABLES)
    for item in plan:
        assert item["statement"] == (
            f"INSERT INTO {item['table']} SELECT * FROM {stage_map[item['table']]}"
        )
        upper = item["statement"].upper()
        assert "ALTER " not in upper
        assert "DELETE " not in upper
        assert "DROP " not in upper
        assert "TRUNCATE " not in upper
        assert " TTL " not in f" {upper} "
