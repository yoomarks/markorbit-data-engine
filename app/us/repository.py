from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from typing import Any

from app.db import postgres_conn
from app.domain import DiscoveredPackage
from app.us.migrations import US_SCHEMA_VERSION
from app.us.package_meta import infer_us_package_descriptor


_DOCKER_COMPOSE_PROJECT = "markorbit-data-engine"
_DOCKER_POSTGRES_SERVICE = "postgres"
_DOCKER_POSTGRES_DATA_DESTINATION = "/var/lib/postgresql/data"


def register_us_package(package: DiscoveredPackage) -> tuple[str, bool]:
    descriptor = infer_us_package_descriptor(package.path)
    if descriptor.package_kind == "UNKNOWN":
        raise ValueError(f"Unknown USPTO package precedence: {package.file_name}")

    sql = """
    INSERT INTO control.source_package (
        jurisdiction, file_name, file_path, file_size, sha256,
        source_modified_at, package_kind, partition_dimension,
        partition_value, source_period_start, source_period_end,
        source_sequence, status, schema_version
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'REGISTERED', %s)
    ON CONFLICT (sha256)
    DO UPDATE SET
        file_name = EXCLUDED.file_name,
        file_path = EXCLUDED.file_path,
        file_size = EXCLUDED.file_size,
        source_modified_at = EXCLUDED.source_modified_at,
        package_kind = EXCLUDED.package_kind,
        partition_dimension = EXCLUDED.partition_dimension,
        partition_value = EXCLUDED.partition_value,
        source_period_start = EXCLUDED.source_period_start,
        source_period_end = EXCLUDED.source_period_end,
        source_sequence = EXCLUDED.source_sequence,
        schema_version = EXCLUDED.schema_version,
        status = CASE
            WHEN control.source_package.status = 'MISSING_FILE' THEN 'FAILED'
            ELSE control.source_package.status
        END,
        last_seen_at = now()
    RETURNING package_id, package_sequence, (xmax = 0) AS inserted
    """
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    package.jurisdiction,
                    package.file_name,
                    str(package.path),
                    package.file_size,
                    package.sha256,
                    package.modified_at,
                    descriptor.package_kind,
                    descriptor.partition_dimension,
                    descriptor.partition_value,
                    descriptor.source_period_start,
                    descriptor.source_period_end,
                    descriptor.source_sequence,
                    US_SCHEMA_VERSION,
                ),
            )
            row = cur.fetchone()
            source_rank = descriptor.source_rank(int(row["package_sequence"]))
            cur.execute(
                "UPDATE control.source_package SET source_rank = %s WHERE package_id = %s",
                (source_rank, row["package_id"]),
            )
        conn.commit()
    return str(row["package_id"]), bool(row["inserted"])


def list_us_packages() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, file_name, sha256, partition_dimension,
                       partition_value, source_rank, status
                FROM control.source_package
                WHERE jurisdiction = 'US'
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]


def _runtime_registry_dependencies_available() -> bool:
    if os.environ.get("MARKORBIT_FORCE_DOCKER_READONLY_REGISTRY") == "1":
        return False
    return (
        importlib.util.find_spec("psycopg") is not None
        and importlib.util.find_spec("pydantic_settings") is not None
    )


def _run_readonly_docker_command(args: list[str], *, label: str) -> str:
    completed = subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _source_postgres_container() -> tuple[str, dict[str, Any]]:
    output = _run_readonly_docker_command(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={_DOCKER_COMPOSE_PROJECT}",
            "--filter",
            f"label=com.docker.compose.service={_DOCKER_POSTGRES_SERVICE}",
            "--format",
            "{{.ID}}",
        ],
        label="source Postgres container discovery",
    )
    container_ids = [line.strip() for line in output.splitlines() if line.strip()]
    if len(container_ids) != 1:
        raise RuntimeError(
            "read-only US registry fallback requires exactly one running source Postgres "
            f"container; observed={len(container_ids)}"
        )
    container_id = container_ids[0]
    inspect_raw = _run_readonly_docker_command(
        ["docker", "inspect", container_id],
        label="source Postgres container inspect",
    )
    inspect_rows = json.loads(inspect_raw)
    if not isinstance(inspect_rows, list) or len(inspect_rows) != 1:
        raise RuntimeError("source Postgres inspect did not return exactly one container")
    row = inspect_rows[0]
    if not isinstance(row, dict):
        raise RuntimeError("source Postgres inspect row is not an object")

    labels = ((row.get("Config") or {}).get("Labels") or {})
    if labels.get("com.docker.compose.project") != _DOCKER_COMPOSE_PROJECT:
        raise RuntimeError("source Postgres compose project label drifted")
    if labels.get("com.docker.compose.service") != _DOCKER_POSTGRES_SERVICE:
        raise RuntimeError("source Postgres compose service label drifted")
    state = row.get("State") or {}
    if state.get("Status") != "running":
        raise RuntimeError("source Postgres container is not running")
    health = state.get("Health") or {}
    if health.get("Status") != "healthy":
        raise RuntimeError("source Postgres container is not healthy")
    mounts = [
        item
        for item in row.get("Mounts") or []
        if isinstance(item, dict)
        and item.get("Type") == "volume"
        and item.get("Destination") == _DOCKER_POSTGRES_DATA_DESTINATION
    ]
    if len(mounts) != 1:
        raise RuntimeError(
            "source Postgres data volume mount is not exact-one at "
            f"{_DOCKER_POSTGRES_DATA_DESTINATION}"
        )
    return container_id, row


def _container_env(inspect_row: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in (inspect_row.get("Config") or {}).get("Env") or []:
        text = str(item)
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key] = value
    return values


def _list_us_replay_registry_via_docker_psql() -> list[dict[str, Any]]:
    """Read the US replay registry without importing application DB drivers.

    This fallback exists for the Stage 1 READ-ONLY operator host. It performs
    only Docker metadata inspection plus one PostgreSQL SELECT, with the server
    session forced to default_transaction_read_only=on. It never starts,
    restarts, creates, or mutates a container or database object.
    """

    container_id, inspect_row = _source_postgres_container()
    env = _container_env(inspect_row)
    postgres_user = env.get("POSTGRES_USER", "").strip()
    postgres_db = env.get("POSTGRES_DB", "").strip()
    if not postgres_user or not postgres_db:
        raise RuntimeError("source Postgres container is missing POSTGRES_USER/POSTGRES_DB")

    sql = """
    SELECT COALESCE(
        json_agg(row_to_json(registry_row) ORDER BY source_rank, package_sequence),
        '[]'::json
    )::text
    FROM (
        SELECT package_id::text AS package_id,
               package_sequence,
               file_name,
               file_path,
               file_size,
               sha256,
               package_kind,
               partition_dimension,
               partition_value,
               source_period_start,
               source_period_end,
               source_sequence,
               source_rank,
               status,
               profile,
               schema_version,
               archived_path,
               processed_at,
               error_message
        FROM control.source_package
        WHERE jurisdiction = 'US'
    ) AS registry_row
    """.strip()
    output = _run_readonly_docker_command(
        [
            "docker",
            "exec",
            "-e",
            "PGOPTIONS=-c default_transaction_read_only=on",
            container_id,
            "psql",
            "-X",
            "-A",
            "-t",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            postgres_user,
            "-d",
            postgres_db,
            "-c",
            sql,
        ],
        label="read-only US replay registry SELECT",
    )
    payload = output.strip()
    if not payload:
        raise RuntimeError("read-only US replay registry SELECT returned no JSON")
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise RuntimeError("read-only US replay registry SELECT did not return a JSON array")
    if not all(isinstance(row, dict) for row in rows):
        raise RuntimeError("read-only US replay registry JSON contains a non-object row")
    return [dict(row) for row in rows]


def list_us_replay_registry() -> list[dict[str, Any]]:
    """Return the authoritative US registry state needed by ordered replay planning."""
    if not _runtime_registry_dependencies_available():
        return _list_us_replay_registry_via_docker_psql()

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, package_sequence, file_name, file_path, file_size,
                       sha256, package_kind, partition_dimension, partition_value,
                       source_period_start, source_period_end, source_sequence,
                       source_rank, status, profile, schema_version, archived_path,
                       processed_at, error_message
                FROM control.source_package
                WHERE jurisdiction = 'US'
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]


def list_us_blocking_failures() -> list[dict[str, Any]]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, file_name, source_rank, status
                FROM control.source_package
                WHERE jurisdiction = 'US'
                  AND status IN ('FAILED', 'MISSING_FILE')
                ORDER BY source_rank, package_sequence
                """
            )
            return [dict(row) for row in cur.fetchall()]
