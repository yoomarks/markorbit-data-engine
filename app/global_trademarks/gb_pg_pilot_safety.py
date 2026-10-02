"""Shared fail-closed PostgreSQL safety for governed GB pilots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

POSTGRES_DATA_DESTINATION = "/var/lib/postgresql/data"
POSTGRES_E_ROOT = PureWindowsPath(r"E:\MarkOrbitData")
COMPOSE_PROJECT = "markorbit-data-engine"
EXECUTION_EVIDENCE_SQL = """
CREATE SCHEMA IF NOT EXISTS trademark_gb;
CREATE TABLE IF NOT EXISTS trademark_gb.governed_pilot_execution_v1 (
 operation_key text PRIMARY KEY,
 operation_kind text NOT NULL,
 plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
 plan_payload jsonb NOT NULL,
 execution_main_sha text NOT NULL CHECK (execution_main_sha ~ '^[0-9a-f]{40}$'),
 postgres_topology jsonb NOT NULL,
 receipt_payload jsonb NOT NULL,
 receipt_sha256 text NOT NULL CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
 committed_at timestamptz NOT NULL DEFAULT now()
);
"""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def _docker_json(*args: str) -> Any:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    require(completed.returncode == 0, "unable to inspect PostgreSQL Docker topology")
    try:
        if args and args[0] == "ps":
            return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid PostgreSQL Docker topology response") from exc


def _e_host_path(value: str) -> str:
    path = PureWindowsPath(value)
    require(path.is_absolute(), "PostgreSQL data mount source is not absolute")
    require(
        path.drive.upper() == "E:" and POSTGRES_E_ROOT in (path, *path.parents),
        "PostgreSQL data mount is not E-backed under MarkOrbitData",
    )
    return str(path)


def _postgres_database_identity(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_database() AS database,
                   inet_server_addr()::text AS server_address,
                   inet_server_port() AS server_port,
                   current_setting('data_directory') AS data_directory
            """
        )
        endpoint = dict(cur.fetchone())
        cur.execute("SELECT system_identifier::text FROM pg_control_system()")
        system_identifier = str(cur.fetchone()["system_identifier"])
    require(endpoint["database"] == "markorbit", "GB pilot target must be markorbit")
    require(
        endpoint["data_directory"] == POSTGRES_DATA_DESTINATION,
        "PostgreSQL data_directory does not match governed container mount",
    )
    require(re.fullmatch(r"[0-9]+", system_identifier) is not None, "invalid cluster identity")
    return {**endpoint, "system_identifier": system_identifier}


def capture_e_postgres_topology(conn: Any) -> dict[str, Any]:
    """Bind the live DB connection to one E-backed Compose PostgreSQL container."""
    from app.db import get_settings

    settings = get_settings()
    host = str(settings.postgres_host).strip().lower()
    port = int(settings.postgres_port)
    require(host in {"localhost", "127.0.0.1", "::1"}, "GB pilot requires loopback Postgres")
    require(str(settings.postgres_db) == "markorbit", "GB pilot DSN database must be markorbit")
    container_ids = _docker_json(
        "ps",
        "--filter",
        f"label=com.docker.compose.project={COMPOSE_PROJECT}",
        "--filter",
        "label=com.docker.compose.service=postgres",
        "--filter",
        "status=running",
        "--format",
        "json",
    )
    require(
        isinstance(container_ids, list) and len(container_ids) == 1,
        "ambiguous PostgreSQL container",
    )
    container_id = str(container_ids[0].get("ID", ""))
    require(
        re.fullmatch(r"[0-9a-f]{12,64}", container_id) is not None,
        "invalid container identity",
    )
    inspected = _docker_json("inspect", container_id)
    require(isinstance(inspected, list) and len(inspected) == 1, "ambiguous Docker inspect result")
    item = inspected[0]
    labels = item.get("Config", {}).get("Labels", {}) or {}
    require(
        labels.get("com.docker.compose.project") == COMPOSE_PROJECT
        and labels.get("com.docker.compose.service") == "postgres",
        "PostgreSQL container labels do not match governed service",
    )
    compose_files = str(labels.get("com.docker.compose.project.config_files", ""))
    require(
        "docker-compose.external-storage.yml" in compose_files,
        "PostgreSQL container was not created with the external-storage topology",
    )
    mounts = [
        mount
        for mount in item.get("Mounts", [])
        if mount.get("Destination") == POSTGRES_DATA_DESTINATION
    ]
    require(len(mounts) == 1, "PostgreSQL data mount is missing or ambiguous")
    mount = mounts[0]
    require(
        mount.get("Type") == "bind" and mount.get("RW") is True,
        "PostgreSQL data mount must be one writable bind mount",
    )
    source = _e_host_path(str(mount.get("Source", "")))
    bindings = item.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
    require(
        any(int(binding.get("HostPort", 0)) == port for binding in bindings),
        "PostgreSQL configured port does not match the governed container",
    )
    addresses = {
        str(network.get("IPAddress", ""))
        for network in item.get("NetworkSettings", {}).get("Networks", {}).values()
    }
    db = _postgres_database_identity(conn)
    require(db["server_address"] in addresses, "DSN is not connected to the governed container")
    image = str(item.get("Image", ""))
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "invalid image identity")
    return {
        "database": db["database"],
        "system_identifier": db["system_identifier"],
        "server_address": db["server_address"],
        "server_port": db["server_port"],
        "configured_host": host,
        "configured_port": port,
        "container_id": str(item.get("Id", "")),
        "container_image_sha256": image.removeprefix("sha256:"),
        "compose_project": COMPOSE_PROJECT,
        "compose_service": "postgres",
        "compose_external_storage": True,
        "data_directory": db["data_directory"],
        "data_mount_type": "bind",
        "data_mount_source": source,
        "data_mount_host_drive": "E",
    }


def verify_e_postgres_topology(conn: Any, expected: dict[str, Any]) -> dict[str, Any]:
    observed = capture_e_postgres_topology(conn)
    require(observed == expected, "PostgreSQL E topology or cluster endpoint drifted")
    return observed


def validate_frozen_topology(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    expected_keys = {
        "database",
        "system_identifier",
        "server_address",
        "server_port",
        "configured_host",
        "configured_port",
        "container_id",
        "container_image_sha256",
        "compose_project",
        "compose_service",
        "compose_external_storage",
        "data_directory",
        "data_mount_type",
        "data_mount_source",
        "data_mount_host_drive",
    }
    try:
        source = _e_host_path(str(value.get("data_mount_source", "")))
    except RuntimeError:
        return False
    return (
        set(value) == expected_keys
        and value.get("database") == "markorbit"
        and re.fullmatch(r"[0-9]+", str(value.get("system_identifier", ""))) is not None
        and isinstance(value.get("server_address"), str)
        and type(value.get("server_port")) is int
        and value.get("configured_host") in {"localhost", "127.0.0.1", "::1"}
        and value.get("configured_port") == value.get("server_port")
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("container_id", ""))) is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("container_image_sha256", ""))) is not None
        and value.get("compose_project") == COMPOSE_PROJECT
        and value.get("compose_service") == "postgres"
        and value.get("compose_external_storage") is True
        and value.get("data_directory") == POSTGRES_DATA_DESTINATION
        and value.get("data_mount_type") == "bind"
        and value.get("data_mount_host_drive") == "E"
        and source == value.get("data_mount_source")
    )


def receipt_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def receipt_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(receipt_bytes(payload)).hexdigest()


def atomic_publish_receipt(
    path: Path, payload: dict[str, Any], *, repair_from_db_evidence: bool = False
) -> str:
    """Publish exact bytes, optionally repairing a partial post-commit file."""
    expected = receipt_bytes(payload)
    digest = hashlib.sha256(expected).hexdigest()
    if path.exists():
        if path.is_file() and path.read_bytes() == expected:
            return digest
        require(repair_from_db_evidence, "receipt file conflicts with DB evidence")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        if repair_from_db_evidence:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    except FileExistsError:
        require(
            path.is_file() and path.read_bytes() == expected,
            "receipt file conflicts with DB evidence",
        )
    finally:
        temporary.unlink(missing_ok=True)
    require(
        path.is_file()
        and path.read_bytes() == expected
        and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
        "published receipt bytes do not match DB evidence",
    )
    return digest


def atomic_freeze_plan(path: Path, payload: dict[str, Any]) -> str:
    """Create one immutable plan through a same-directory atomic hard link."""
    require(not path.exists(), "governed plan already exists; refuse reuse")
    expected = receipt_bytes(payload)
    digest = hashlib.sha256(expected).hexdigest()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    require(
        path.is_file()
        and path.read_bytes() == expected
        and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
        "frozen plan bytes failed verification",
    )
    return digest


def insert_execution_evidence(
    cur: Any,
    *,
    operation_key: str,
    operation_kind: str,
    plan: dict[str, Any],
    plan_sha: str,
    receipt: dict[str, Any],
) -> str:
    digest = receipt_sha256(receipt)
    cur.execute(EXECUTION_EVIDENCE_SQL)
    cur.execute(
        """
        INSERT INTO trademark_gb.governed_pilot_execution_v1(
          operation_key,operation_kind,plan_sha256,plan_payload,
          execution_main_sha,postgres_topology,receipt_payload,receipt_sha256
        ) VALUES (%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s)
        """,
        (
            operation_key,
            operation_kind,
            plan_sha,
            json.dumps(plan, ensure_ascii=False, sort_keys=True),
            plan["execution_main_sha"],
            json.dumps(plan["postgres_topology"], ensure_ascii=False, sort_keys=True),
            json.dumps(receipt, ensure_ascii=False, sort_keys=True),
            digest,
        ),
    )
    require(cur.rowcount == 1, "GB pilot execution evidence insert failed")
    return digest


def read_execution_evidence(cur: Any, operation_key: str) -> dict[str, Any] | None:
    cur.execute("SELECT to_regclass('trademark_gb.governed_pilot_execution_v1') AS relation")
    if cur.fetchone()["relation"] is None:
        return None
    cur.execute(
        """
        SELECT operation_key,operation_kind,plan_sha256,plan_payload,
               execution_main_sha,postgres_topology,receipt_payload,receipt_sha256
        FROM trademark_gb.governed_pilot_execution_v1
        WHERE operation_key=%s
        """,
        (operation_key,),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None


def validate_execution_evidence(
    evidence: dict[str, Any],
    *,
    operation_key: str,
    operation_kind: str,
    plan: dict[str, Any],
    plan_sha: str,
) -> dict[str, Any]:
    receipt = evidence.get("receipt_payload")
    require(
        evidence.get("operation_key") == operation_key
        and evidence.get("operation_kind") == operation_kind
        and evidence.get("plan_sha256") == plan_sha
        and evidence.get("plan_payload") == plan
        and evidence.get("execution_main_sha") == plan.get("execution_main_sha")
        and evidence.get("postgres_topology") == plan.get("postgres_topology")
        and isinstance(receipt, dict)
        and evidence.get("receipt_sha256") == receipt_sha256(receipt),
        "committed GB pilot execution evidence does not match frozen authority",
    )
    return receipt
