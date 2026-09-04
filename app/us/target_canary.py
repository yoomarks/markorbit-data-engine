from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Sequence
import uuid

from app.scanner import sha256_file
from app.us.change_history import CASE_OBSERVATION_TABLE
from app.us.ingest import OUTPUT_PACKAGE_COLUMNS, _iter_package_bundles
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


TARGET_DISTRO = "MarkOrbit-ClickHouse"
TARGET_NATIVE_HOST = "127.0.0.1"
TARGET_NATIVE_PORT = 29000
TARGET_STORAGE_POLICY = "hot_us_only"
TARGET_DATABASE = "markorbit_facts"
STAGE_DATABASE = "markorbit_canary_stage"
CANARY_RECEIPT_VERSION = "US_TARGET_CANARY_RECEIPT_V1"

APPLICATION_CANARY_TABLES = tuple(OUTPUT_PACKAGE_COLUMNS)

_FORBIDDEN_MUTATION = re.compile(
    r"\b(ALTER|DELETE|DROP|TRUNCATE|OPTIMIZE|MOVE|ATTACH|DETACH|RENAME|TTL)\b",
    re.IGNORECASE,
)
_CREATE_TABLE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:`?markorbit_facts`?\.)?`?([A-Za-z0-9_]+)`?",
    re.IGNORECASE,
)
_UUID_CLAUSE = re.compile(r"\s+UUID\s+'[^']+'", re.IGNORECASE)
_STORAGE_POLICY = re.compile(r"storage_policy\s*=\s*'[^']+'", re.IGNORECASE)
_SETTINGS = re.compile(r"\bSETTINGS\b", re.IGNORECASE)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class FrozenCanaryPackage:
    path: Path
    file_name: str
    size_bytes: int
    sha256: str
    package_kind: str
    source_rank: int
    source_effective_date: date | None
    package_id: uuid.UUID

    def as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "file_name": self.file_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "package_kind": self.package_kind,
            "source_rank": self.source_rank,
            "source_effective_date": (
                self.source_effective_date.isoformat()
                if self.source_effective_date is not None
                else None
            ),
            "package_id": str(self.package_id),
        }


@dataclass(frozen=True)
class QueryRows:
    result_rows: list[list[Any]]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe ClickHouse identifier: {value!r}")
    return value


def _short_table(table: str) -> str:
    prefix = f"{TARGET_DATABASE}."
    if not table.startswith(prefix):
        raise ValueError(f"table is outside target database: {table}")
    return _validate_identifier(table[len(prefix) :])


def _manifest_list(manifest: dict[str, object], field: str) -> list[object]:
    value = manifest.get(field)
    if not isinstance(value, list):
        raise ValueError(f"target schema manifest {field} must be a list")
    return list(value)


def deterministic_package_id(sha256: str) -> uuid.UUID:
    digest = sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("package SHA-256 must be 64 lowercase/uppercase hex characters")
    return uuid.uuid5(uuid.NAMESPACE_URL, f"markorbit:us:package:{digest}")


def freeze_package(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    package_kind: str,
    source_rank: int,
    source_effective_date: date | None,
    package_id: uuid.UUID | None = None,
) -> FrozenCanaryPackage:
    resolved = path.resolve(strict=True)
    before = resolved.stat()
    if not resolved.is_file():
        raise RuntimeError(f"US canary package is not a file: {resolved}")
    if resolved.suffix.lower() != ".zip":
        raise RuntimeError(f"US canary package must be a ZIP: {resolved.name}")
    if before.st_size != expected_size:
        raise RuntimeError(
            "US canary package size mismatch: "
            f"expected={expected_size} actual={before.st_size}"
        )

    actual_sha = sha256_file(resolved).lower()
    expected_sha = expected_sha256.lower()
    after = resolved.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise RuntimeError("US canary package changed while hashing")
    if actual_sha != expected_sha:
        raise RuntimeError(
            "US canary package SHA-256 mismatch: "
            f"expected={expected_sha} actual={actual_sha}"
        )
    if source_rank <= 0:
        raise ValueError("source_rank must be positive")

    return FrozenCanaryPackage(
        path=resolved,
        file_name=resolved.name,
        size_bytes=before.st_size,
        sha256=actual_sha,
        package_kind=package_kind,
        source_rank=source_rank,
        source_effective_date=source_effective_date,
        package_id=package_id or deterministic_package_id(actual_sha),
    )


def assert_package_unchanged(package: FrozenCanaryPackage) -> None:
    current = package.path.stat()
    if current.st_size != package.size_bytes:
        raise RuntimeError("US canary source size changed after freeze")
    current_sha = sha256_file(package.path).lower()
    if current_sha != package.sha256:
        raise RuntimeError("US canary source SHA-256 changed after freeze")


def normalize_show_create_for_hot_us(
    statement: str,
    *,
    expected_table: str,
) -> str:
    if expected_table not in APPLICATION_CANARY_TABLES:
        raise ValueError(f"table is outside US Application canary scope: {expected_table}")
    expected_short = _short_table(expected_table)
    sql = statement.strip().rstrip(";")
    if _FORBIDDEN_MUTATION.search(sql):
        raise ValueError("source SHOW CREATE contains forbidden mutation SQL")
    match = _CREATE_TABLE.match(sql)
    if match is None or match.group(1).lower() != expected_short.lower():
        raise ValueError(f"SHOW CREATE table mismatch: expected={expected_table}")
    if not re.search(r"\b[A-Za-z]*MergeTree\b", sql, re.IGNORECASE):
        raise ValueError(f"US canary table is not MergeTree: {expected_table}")

    sql = _UUID_CLAUSE.sub("", sql, count=1)
    sql = re.sub(
        r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:`?markorbit_facts`?\.)?`?[A-Za-z0-9_]+`?",
        f"CREATE TABLE IF NOT EXISTS {expected_table}",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if _STORAGE_POLICY.search(sql):
        sql = _STORAGE_POLICY.sub(
            f"storage_policy = '{TARGET_STORAGE_POLICY}'", sql, count=1
        )
    else:
        settings = _SETTINGS.search(sql)
        if settings is not None:
            insert_at = settings.end()
            sql = (
                sql[:insert_at]
                + f" storage_policy = '{TARGET_STORAGE_POLICY}',"
                + sql[insert_at:]
            )
        else:
            sql += f"\nSETTINGS storage_policy = '{TARGET_STORAGE_POLICY}'"
    validate_direct_target_ddl(sql, expected_table=expected_table)
    return sql


def validate_direct_target_ddl(statement: str, *, expected_table: str) -> None:
    expected_short = _short_table(expected_table)
    sql = statement.strip().rstrip(";")
    if _FORBIDDEN_MUTATION.search(sql):
        raise ValueError("target schema contains forbidden mutation SQL")
    match = _CREATE_TABLE.match(sql)
    if match is None or match.group(1).lower() != expected_short.lower():
        raise ValueError(f"target DDL table mismatch: expected={expected_table}")
    policy_matches = _STORAGE_POLICY.findall(sql)
    if policy_matches != [f"storage_policy = '{TARGET_STORAGE_POLICY}'"]:
        raise ValueError(
            f"target DDL must contain exactly storage_policy='{TARGET_STORAGE_POLICY}'"
        )


def build_target_schema_manifest(
    show_create_by_table: dict[str, str],
) -> dict[str, object]:
    expected = set(APPLICATION_CANARY_TABLES)
    supplied = set(show_create_by_table)
    if supplied != expected:
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        raise ValueError(f"US canary schema set mismatch: missing={missing} extra={extra}")

    statements = [
        f"CREATE DATABASE IF NOT EXISTS {TARGET_DATABASE}",
        *[
            normalize_show_create_for_hot_us(
                show_create_by_table[table], expected_table=table
            )
            for table in APPLICATION_CANARY_TABLES
        ],
    ]
    canonical = "\n;\n".join(statements) + ";\n"
    return {
        "schema_version": "US_M1.4_TARGET_HOT_US_V1",
        "storage_policy": TARGET_STORAGE_POLICY,
        "tables": list(APPLICATION_CANARY_TABLES),
        "statements": statements,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "canonical_sql": canonical,
    }


def validate_target_schema_manifest(manifest: dict[str, object]) -> None:
    if manifest.get("storage_policy") != TARGET_STORAGE_POLICY:
        raise ValueError("target schema manifest has wrong storage policy")
    tables = [str(item) for item in _manifest_list(manifest, "tables")]
    if tables != list(APPLICATION_CANARY_TABLES):
        raise ValueError("target schema manifest table order/set mismatch")
    statements = [str(item) for item in _manifest_list(manifest, "statements")]
    if len(statements) != len(APPLICATION_CANARY_TABLES) + 1:
        raise ValueError("target schema manifest statement count mismatch")
    if statements[0] != f"CREATE DATABASE IF NOT EXISTS {TARGET_DATABASE}":
        raise ValueError("target schema manifest database statement mismatch")
    for table, statement in zip(APPLICATION_CANARY_TABLES, statements[1:], strict=True):
        validate_direct_target_ddl(statement, expected_table=table)
    canonical = "\n;\n".join(statements) + ";\n"
    actual_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if manifest.get("sha256") != actual_sha:
        raise ValueError("target schema manifest SHA-256 mismatch")


def _json_default(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    raise TypeError(f"unsupported ClickHouse JSON value: {type(value).__name__}")


class WslNativeClickHouseClient:
    """Native-protocol client pinned to the accepted target WSL runtime.

    There is intentionally no Docker/source fallback. The only transport is
    clickhouse-client inside MarkOrbit-ClickHouse to localhost:29000.
    """

    def __init__(
        self,
        *,
        distro: str = TARGET_DISTRO,
        host: str = TARGET_NATIVE_HOST,
        port: int = TARGET_NATIVE_PORT,
        runner: Runner = subprocess.run,
    ) -> None:
        if distro != TARGET_DISTRO:
            raise ValueError(f"target distro must be exactly {TARGET_DISTRO}")
        if host != TARGET_NATIVE_HOST:
            raise ValueError(f"target native host must be exactly {TARGET_NATIVE_HOST}")
        if port != TARGET_NATIVE_PORT:
            raise ValueError(f"target native port must be exactly {TARGET_NATIVE_PORT}")
        self.distro = distro
        self.host = host
        self.port = port
        self._runner = runner

    def _exec(self, query: str, *, input_text: str | None = None) -> str:
        args = [
            "wsl.exe",
            "-d",
            self.distro,
            "-u",
            "root",
            "--",
            "clickhouse-client",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--query",
            query,
        ]
        completed = self._runner(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "target clickhouse-client failed: "
                f"exit={completed.returncode} stderr={completed.stderr.strip()}"
            )
        return completed.stdout

    def command(self, sql: str) -> str:
        if _FORBIDDEN_MUTATION.search(sql):
            raise RuntimeError("forbidden mutation SQL rejected by target canary adapter")
        if not re.match(r"^\s*(CREATE|INSERT)\b", sql, re.IGNORECASE):
            raise RuntimeError("target canary command permits only CREATE/INSERT")
        return self._exec(sql)

    def query(self, sql: str) -> QueryRows:
        if not re.match(r"^\s*(SELECT|SHOW|DESCRIBE|EXISTS)\b", sql, re.IGNORECASE):
            raise RuntimeError("target canary query permits only read-only SQL")
        output = self._exec(f"{sql.rstrip().rstrip(';')} FORMAT JSONCompactEachRow")
        rows = [json.loads(line) for line in output.splitlines() if line.strip()]
        return QueryRows(result_rows=rows)

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        *,
        column_names: Sequence[str],
    ) -> None:
        if not rows:
            return
        database, dot, short_table = table.partition(".")
        if not dot or database not in {TARGET_DATABASE, STAGE_DATABASE}:
            raise ValueError(f"table outside target canary scope: {table}")
        _validate_identifier(short_table)
        columns = [_validate_identifier(str(item)) for item in column_names]
        payload = "".join(
            json.dumps(
                dict(zip(columns, row, strict=True)),
                ensure_ascii=False,
                separators=(",", ":"),
                default=_json_default,
            )
            + "\n"
            for row in rows
        )
        query = f"INSERT INTO {table} ({', '.join(columns)}) FORMAT JSONEachRow"
        self._exec(query, input_text=payload)


class _StagingClient:
    def __init__(self, base: WslNativeClickHouseClient, stage_tables: dict[str, str]) -> None:
        self._base = base
        self._stage_tables = stage_tables

    def query(self, sql: str) -> QueryRows:
        return self._base.query(sql)

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        *,
        column_names: Sequence[str],
    ) -> None:
        try:
            stage_table = self._stage_tables[table]
        except KeyError as exc:
            raise RuntimeError(f"unexpected canary write table: {table}") from exc
        self._base.insert(stage_table, rows, column_names=column_names)


def stage_table_map(package: FrozenCanaryPackage) -> dict[str, str]:
    token = package.sha256[:16]
    return {
        table: f"{STAGE_DATABASE}.{_short_table(table)}__{token}"
        for table in APPLICATION_CANARY_TABLES
    }


def stage_ddl_from_manifest(
    manifest: dict[str, object], package: FrozenCanaryPackage
) -> list[str]:
    validate_target_schema_manifest(manifest)
    stage_map = stage_table_map(package)
    statements = [f"CREATE DATABASE IF NOT EXISTS {STAGE_DATABASE}"]
    table_statements = [
        str(item) for item in _manifest_list(manifest, "statements")
    ][1:]
    for table, statement in zip(
        APPLICATION_CANARY_TABLES, table_statements, strict=True
    ):
        stage_table = stage_map[table]
        rewritten = re.sub(
            rf"^CREATE TABLE IF NOT EXISTS {re.escape(table)}",
            f"CREATE TABLE {stage_table}",
            statement,
            count=1,
        )
        if rewritten == statement:
            raise ValueError(f"could not rewrite stage DDL for {table}")
        statements.append(rewritten)
    return statements


def stage_package_rows(
    client: WslNativeClickHouseClient,
    package: FrozenCanaryPackage,
    *,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Parse one immutable package and write only package-scoped stage tables."""
    assert_package_unchanged(package)
    stage_map = stage_table_map(package)
    publisher = SnapshotAwareUSBatchPublisher(
        _StagingClient(client, stage_map),
        package_id=package.package_id,
        package_kind=package.package_kind,
        source_effective_date=package.source_effective_date,
        source_rank=package.source_rank,
        batch_size=batch_size,
    )
    seen_serials: set[str] = set()
    for source_file, bundle in _iter_package_bundles(package.path):
        serial = bundle.case.serial_number
        if serial in seen_serials:
            raise RuntimeError(
                f"duplicate USPTO serial number in frozen canary package: {serial}"
            )
        seen_serials.add(serial)
        publisher.add(bundle, source_file)
    if not seen_serials:
        raise RuntimeError("frozen canary package produced no trademark case records")
    counts = publisher.close()
    assert_package_unchanged(package)
    return counts


def package_column_for_table(table: str) -> str:
    if table == CASE_OBSERVATION_TABLE:
        return "source_package_id"
    return OUTPUT_PACKAGE_COLUMNS[table]


def commit_statements(package: FrozenCanaryPackage) -> list[dict[str, str]]:
    stage_map = stage_table_map(package)
    result: list[dict[str, str]] = []
    for table in APPLICATION_CANARY_TABLES:
        result.append(
            {
                "table": table,
                "stage_table": stage_map[table],
                "package_column": package_column_for_table(table),
                "statement": f"INSERT INTO {table} SELECT * FROM {stage_map[table]}",
            }
        )
    return result


def write_receipt(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical, encoding="utf-8")
    temporary.replace(path)