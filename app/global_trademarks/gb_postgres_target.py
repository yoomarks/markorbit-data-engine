"""Fail-closed accepted E-backed PostgreSQL target evidence for governed GB writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from app.storage_topology_v2 import CONTRACT_VERSION as STORAGE_TOPOLOGY_VERSION
from app.storage_topology_v2 import build_storage_topology, placement_for

ACCEPTED_DOCKER_E_RECEIPT = Path(
    r"D:\yoomarks\governed-plans\837\phase-b-docker-relocation"
    r"\docker-relocation-final-receipt-r2.json"
)
ACCEPTED_DOCKER_E_RECEIPT_SHA = "7a2cf5df887eaf7751dcea2ff22bb329bf7fd3fe476bb40c09b9c5a62a7f8f74"
DOCKER_E_ROOT = Path(r"E:\DockerData\DockerDesktopWSL")
DOCKER_E_DATA_VHDX = DOCKER_E_ROOT / "disk" / "docker_data.vhdx"
POSTGRES_DATA_DIRECTORY = "/var/lib/postgresql/data"


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    require(path.is_file() and not path.is_symlink(), f"{label} missing or symlinked")
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    value = json.loads(payload.decode("utf-8-sig"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value, digest


def _canonical_json_sha(value: dict[str, Any]) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


def _docker_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    require(completed.returncode == 0, f"docker {' '.join(args)} failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid Docker JSON response") from exc


def _docker_lines(args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    require(completed.returncode == 0, f"docker {' '.join(args)} failed")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def verify_accepted_storage_topology() -> dict[str, Any]:
    topology = build_storage_topology()
    placement = placement_for("GB", "hot")
    require(
        topology.get("contract_version") == STORAGE_TOPOLOGY_VERSION
        and topology.get("read_only") is True
        and topology.get("constraints", {}).get("production_mutation_authorized") is False
        and placement == {"drive": "E", "placement": "hot_global"},
        "accepted GB hot storage topology drift",
    )

    accepted, accepted_sha = _load_json(
        ACCEPTED_DOCKER_E_RECEIPT, "accepted Docker E topology receipt"
    )
    require(
        accepted_sha == ACCEPTED_DOCKER_E_RECEIPT_SHA
        and accepted.get("version") == "DOCKER_DESKTOP_DURABLE_DATA_RELOCATION_RECEIPT_V2"
        and accepted.get("status") == "PASS"
        and accepted.get("source_released") is True
        and accepted.get("target_root") == str(DOCKER_E_ROOT)
        and accepted.get("custom_wsl_distro_dir") == str(DOCKER_E_ROOT)
        and accepted.get("docker_desktop_wsl_base_path") == str(DOCKER_E_ROOT / "main")
        and accepted.get("container_identity_match") is True
        and accepted.get("volume_identity_match") is True
        and accepted.get("api_health") == "api/postgres/clickhouse=ok",
        "accepted Docker E topology receipt drift",
    )

    appdata = os.environ.get("APPDATA", "").strip()
    require(bool(appdata), "APPDATA required for Docker E topology verification")
    settings, _ = _load_json(
        Path(appdata) / "Docker" / "settings-store.json",
        "current Docker Desktop settings",
    )
    require(
        settings.get("CustomWslDistroDir") == str(DOCKER_E_ROOT),
        "current Docker Desktop durable root is not accepted E topology",
    )
    require(
        DOCKER_E_DATA_VHDX.is_file() and not DOCKER_E_DATA_VHDX.is_symlink(),
        "accepted E Docker durable VHDX missing or symlinked",
    )
    docker = _docker_json(["info", "--format", "{{json .}}"])
    require(
        docker.get("DockerRootDir") == "/var/lib/docker"
        and docker.get("OperatingSystem") == "Docker Desktop"
        and docker.get("ServerVersion") == accepted.get("docker_engine_version"),
        "live Docker runtime differs from accepted E topology",
    )
    return {
        "contract_version": STORAGE_TOPOLOGY_VERSION,
        "contract_sha256": _canonical_json_sha(topology),
        "gb_hot_placement": placement,
        "accepted_docker_e_receipt_sha256": accepted_sha,
        "docker_desktop_data_root": str(DOCKER_E_ROOT),
        "docker_data_vhdx": str(DOCKER_E_DATA_VHDX),
        "docker_engine_version": docker["ServerVersion"],
        "docker_root_dir": docker["DockerRootDir"],
    }


def _postgres_database_identity(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT current_database() AS database,
                   current_setting('data_directory') AS data_directory,
                   current_setting('server_version_num')::integer AS server_version_num,
                   current_setting('port')::integer AS server_port,
                   host(inet_server_addr()) AS server_address,
                   inet_server_port() AS observed_server_port,
                   (SELECT system_identifier::text FROM pg_control_system()) AS system_identifier
            """
        )
        return dict(cur.fetchone())


def _docker_postgres_identity(database: dict[str, Any]) -> dict[str, Any]:
    from app.config import get_settings

    settings = get_settings()
    endpoint_host = str(settings.postgres_host)
    endpoint_port = int(settings.postgres_port)
    require(
        endpoint_host.lower() in {"localhost", "127.0.0.1", "::1"}
        and str(settings.postgres_db) == "markorbit",
        "GB operator requires the locally accepted markorbit PostgreSQL endpoint",
    )
    candidates: list[dict[str, Any]] = []
    for container_id in _docker_lines(
        ["ps", "-q", "--filter", "label=com.docker.compose.service=postgres"]
    ):
        inspected = _docker_json(["inspect", container_id])
        require(
            isinstance(inspected, list) and len(inspected) == 1,
            "ambiguous Docker inspect result",
        )
        item = inspected[0]
        ports = item.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
        if not any(int(binding.get("HostPort", -1)) == endpoint_port for binding in ports):
            continue
        mounts = [
            mount
            for mount in item.get("Mounts", [])
            if mount.get("Destination") == POSTGRES_DATA_DIRECTORY
        ]
        networks = item.get("NetworkSettings", {}).get("Networks", {})
        addresses = sorted(
            network.get("IPAddress") for network in networks.values() if network.get("IPAddress")
        )
        labels = item.get("Config", {}).get("Labels", {}) or {}
        state = item.get("State", {})
        require(
            state.get("Running") is True
            and state.get("Health", {}).get("Status") == "healthy"
            and len(mounts) == 1
            and mounts[0].get("Type") == "volume"
            and mounts[0].get("RW") is True
            and str(mounts[0].get("Source", "")).startswith("/var/lib/docker/volumes/")
            and labels.get("com.docker.compose.service") == "postgres",
            "PostgreSQL Docker runtime topology is not accepted",
        )
        candidates.append(
            {
                "container_id": item["Id"],
                "container_image_id": item["Image"],
                "compose_project": labels.get("com.docker.compose.project"),
                "container_addresses": addresses,
                "volume_name": mounts[0].get("Name"),
                "volume_source": mounts[0].get("Source"),
            }
        )
    require(len(candidates) == 1, "configured PostgreSQL endpoint is not one exact container")
    candidate = candidates[0]
    require(
        database.get("database") == "markorbit"
        and database.get("data_directory") == POSTGRES_DATA_DIRECTORY
        and database.get("server_port") == 5432
        and database.get("observed_server_port") == 5432
        and database.get("server_address") in candidate["container_addresses"]
        and re.fullmatch(r"[0-9]+", str(database.get("system_identifier", ""))) is not None,
        "live PostgreSQL cluster identity does not match configured Docker endpoint",
    )
    return {
        **database,
        "configured_endpoint_host": endpoint_host,
        "configured_endpoint_port": endpoint_port,
        **candidate,
    }


def capture(conn: Any) -> dict[str, Any]:
    return {
        "accepted_storage_topology_evidence": verify_accepted_storage_topology(),
        "postgres_target_evidence": _docker_postgres_identity(_postgres_database_identity(conn)),
    }


def verify(conn: Any, expected: dict[str, Any]) -> dict[str, Any]:
    observed = capture(conn)
    require(observed == expected, "PostgreSQL E topology or cluster endpoint drifted")
    return observed


def validate(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    storage = value.get("accepted_storage_topology_evidence")
    target = value.get("postgres_target_evidence")
    if not isinstance(storage, dict) or not isinstance(target, dict):
        return False
    return (
        set(value) == {"accepted_storage_topology_evidence", "postgres_target_evidence"}
        and set(storage)
        == {
            "contract_version",
            "contract_sha256",
            "gb_hot_placement",
            "accepted_docker_e_receipt_sha256",
            "docker_desktop_data_root",
            "docker_data_vhdx",
            "docker_engine_version",
            "docker_root_dir",
        }
        and set(target)
        == {
            "database",
            "data_directory",
            "server_version_num",
            "server_port",
            "server_address",
            "observed_server_port",
            "system_identifier",
            "configured_endpoint_host",
            "configured_endpoint_port",
            "container_id",
            "container_image_id",
            "compose_project",
            "container_addresses",
            "volume_name",
            "volume_source",
        }
        and storage.get("contract_version") == STORAGE_TOPOLOGY_VERSION
        and re.fullmatch(r"[0-9a-f]{64}", str(storage.get("contract_sha256", ""))) is not None
        and storage.get("gb_hot_placement") == {"drive": "E", "placement": "hot_global"}
        and storage.get("accepted_docker_e_receipt_sha256") == ACCEPTED_DOCKER_E_RECEIPT_SHA
        and storage.get("docker_desktop_data_root") == str(DOCKER_E_ROOT)
        and storage.get("docker_data_vhdx") == str(DOCKER_E_DATA_VHDX)
        and storage.get("docker_root_dir") == "/var/lib/docker"
        and target.get("database") == "markorbit"
        and target.get("data_directory") == POSTGRES_DATA_DIRECTORY
        and re.fullmatch(r"[0-9]+", str(target.get("system_identifier", ""))) is not None
        and target.get("server_port") == 5432
        and target.get("observed_server_port") == 5432
        and str(target.get("configured_endpoint_host", "")).lower()
        in {"localhost", "127.0.0.1", "::1"}
        and type(target.get("configured_endpoint_port")) is int
        and target.get("configured_endpoint_port") > 0
        and re.fullmatch(r"[0-9a-f]{64}", str(target.get("container_id", ""))) is not None
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(target.get("container_image_id", "")))
        is not None
        and isinstance(target.get("compose_project"), str)
        and bool(target.get("compose_project"))
        and isinstance(target.get("container_addresses"), list)
        and target.get("server_address") in target.get("container_addresses")
        and isinstance(target.get("volume_name"), str)
        and bool(target.get("volume_name"))
        and str(target.get("volume_source", "")).startswith("/var/lib/docker/volumes/")
    )
