"""Governed UKIPO journal 2026/033 PostgreSQL pilot.

The operator consumes only an accepted E-resident journal stage and verifies
every referenced F original visual before an issue-atomic pilot. Journal rows
are observations, never current registry truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_domestic_full_resume as domestic
from app.global_trademarks import gb_journal_e_stage as stage
from app.storage_topology_v2 import CONTRACT_VERSION as STORAGE_TOPOLOGY_VERSION
from app.storage_topology_v2 import build_storage_topology, placement_for

PILOT_ISSUE = "2026-033"
PILOT_SOURCE_SHA = "63690a48858778b01e2eb06edc31a5b8bb72dd89c02b30133234014004741814"
PILOT_RECORDS_SHA = "565545f3ef823348127c27297cf446c95ad20e013ecab2101324070625946832"
PILOT_ORDERED_SHA = "6b5d3dc8484cefaf29f48c299273eb329462c61a6d8d905407d7200c7baf15e2"
PILOT_NOTICES = 3351
PILOT_GOODS = 7427
PILOT_PARTIES = 5182
PILOT_VISUALS = 1462
GOV = Path(r"D:\yoomarks\governed-plans\855")
RECEIPT = GOV / "gb-journal-2026-033-pg-pilot-r1.json"
ACCEPTED_DOCKER_E_RECEIPT = Path(
    r"D:\yoomarks\governed-plans\837\phase-b-docker-relocation"
    r"\docker-relocation-final-receipt-r2.json"
)
ACCEPTED_DOCKER_E_RECEIPT_SHA = "7a2cf5df887eaf7751dcea2ff22bb329bf7fd3fe476bb40c09b9c5a62a7f8f74"
DOCKER_E_ROOT = Path(r"E:\DockerData\DockerDesktopWSL")
DOCKER_E_DATA_VHDX = DOCKER_E_ROOT / "disk" / "docker_data.vhdx"
POSTGRES_DATA_DIRECTORY = "/var/lib/postgresql/data"

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS trademark_gb;
CREATE TABLE trademark_gb.journal_issue_ingest_run_v1 (
 issue text PRIMARY KEY CHECK (issue ~ '^[0-9]{4}-[0-9]{3}$'),
 source_zip_sha256 text NOT NULL CHECK (source_zip_sha256 ~ '^[0-9a-f]{64}$'),
 stage_records_sha256 text NOT NULL CHECK (stage_records_sha256 ~ '^[0-9a-f]{64}$'),
 expected_notices integer NOT NULL CHECK (expected_notices > 0),
 expected_goods integer NOT NULL CHECK (expected_goods >= 0),
 expected_parties integer NOT NULL CHECK (expected_parties >= 0),
 expected_visuals integer NOT NULL CHECK (expected_visuals >= 0),
 expected_missing_visuals integer NOT NULL CHECK (expected_missing_visuals >= 0),
 plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
 execution_main_sha text NOT NULL CHECK (execution_main_sha ~ '^[0-9a-f]{40}$'),
 plan_evidence jsonb NOT NULL,
 storage_topology_evidence jsonb NOT NULL,
 postgres_target_evidence jsonb NOT NULL,
 receipt_evidence jsonb NOT NULL,
 receipt_sha256 text NOT NULL CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
 status text NOT NULL CHECK (status IN ('RUNNING','COMPLETE')),
 checkpoint_notice_ordinal integer NOT NULL DEFAULT 0 CHECK (checkpoint_notice_ordinal >= 0),
 started_at timestamptz NOT NULL DEFAULT now(),
 completed_at timestamptz
);
CREATE TABLE trademark_gb.journal_notice_v1 (
 issue text NOT NULL REFERENCES trademark_gb.journal_issue_ingest_run_v1(issue),
 notice_ordinal integer NOT NULL CHECK (notice_ordinal > 0),
 source_zip_sha256 text NOT NULL CHECK (source_zip_sha256 ~ '^[0-9a-f]{64}$'),
 source_member text NOT NULL,
 detail_html_sha256 text NOT NULL CHECK (detail_html_sha256 ~ '^[0-9a-f]{64}$'),
 source_row_sha256 text NOT NULL CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
 mark_family text NOT NULL CHECK (mark_family IN ('UK','WO')),
 mark_id text NOT NULL,
 journal_title_raw text NOT NULL,
 registration_date_raw text,
 mark_text jsonb NOT NULL,
 source_payload jsonb NOT NULL,
 journal_observation_only boolean NOT NULL DEFAULT true CHECK (journal_observation_only),
 current_state_verified boolean NOT NULL DEFAULT false CHECK (NOT current_state_verified),
 ingested_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (issue,notice_ordinal),
 UNIQUE (issue,source_member)
);
CREATE INDEX idx_gb_journal_notice_mark_v1
 ON trademark_gb.journal_notice_v1(mark_family,mark_id,issue);
CREATE TABLE trademark_gb.journal_goods_v1 (
 issue text NOT NULL,
 notice_ordinal integer NOT NULL,
 class_ordinal integer NOT NULL CHECK (class_ordinal > 0),
 goods_ordinal integer NOT NULL CHECK (goods_ordinal > 0),
 class_number smallint NOT NULL CHECK (class_number BETWEEN 1 AND 45),
 goods_text text NOT NULL,
 PRIMARY KEY (issue,notice_ordinal,class_ordinal,goods_ordinal),
 FOREIGN KEY (issue,notice_ordinal)
  REFERENCES trademark_gb.journal_notice_v1(issue,notice_ordinal)
);
CREATE TABLE trademark_gb.journal_party_v1 (
 issue text NOT NULL,
 notice_ordinal integer NOT NULL,
 party_role text NOT NULL CHECK (party_role IN ('APPLICANT','REPRESENTATIVE')),
 party_ordinal integer NOT NULL CHECK (party_ordinal > 0),
 party_name_raw text NOT NULL,
 PRIMARY KEY (issue,notice_ordinal,party_role,party_ordinal),
 FOREIGN KEY (issue,notice_ordinal)
  REFERENCES trademark_gb.journal_notice_v1(issue,notice_ordinal)
);
CREATE TABLE trademark_gb.journal_visual_v1 (
 issue text NOT NULL,
 notice_ordinal integer NOT NULL,
 visual_ordinal integer NOT NULL CHECK (visual_ordinal > 0),
 source_member text,
 declared_src text,
 original_sha256 text CHECK (original_sha256 IS NULL OR original_sha256 ~ '^[0-9a-f]{64}$'),
 original_bytes bigint CHECK (original_bytes IS NULL OR original_bytes > 0),
 asset_relative_path text,
 source_member_resolution text,
 thumbnail_present boolean NOT NULL,
 evidence_complete boolean NOT NULL,
 PRIMARY KEY (issue,notice_ordinal,visual_ordinal),
 FOREIGN KEY (issue,notice_ordinal)
  REFERENCES trademark_gb.journal_notice_v1(issue,notice_ordinal),
 CHECK ((evidence_complete AND original_sha256 IS NOT NULL AND original_bytes IS NOT NULL
         AND asset_relative_path IS NOT NULL AND source_member IS NOT NULL)
     OR (NOT evidence_complete AND original_sha256 IS NULL AND original_bytes IS NULL
         AND asset_relative_path IS NULL AND source_member IS NULL))
);
"""

NOTICE_SQL = """
INSERT INTO trademark_gb.journal_notice_v1(
 issue,notice_ordinal,source_zip_sha256,source_member,detail_html_sha256,
 source_row_sha256,mark_family,mark_id,journal_title_raw,registration_date_raw,
 mark_text,source_payload,journal_observation_only,current_state_verified
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,true,false)
"""
GOODS_SQL = """
INSERT INTO trademark_gb.journal_goods_v1(
 issue,notice_ordinal,class_ordinal,goods_ordinal,class_number,goods_text
) VALUES (%s,%s,%s,%s,%s,%s)
"""
PARTY_SQL = """
INSERT INTO trademark_gb.journal_party_v1(
 issue,notice_ordinal,party_role,party_ordinal,party_name_raw
) VALUES (%s,%s,%s,%s,%s)
"""
VISUAL_SQL = """
INSERT INTO trademark_gb.journal_visual_v1(
 issue,notice_ordinal,visual_ordinal,source_member,declared_src,original_sha256,
 original_bytes,asset_relative_path,source_member_resolution,thumbnail_present,
 evidence_complete
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    require(path.is_file() and not path.is_symlink(), f"{label} missing or symlinked")
    digest = stage.sha256_file(path)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value, digest


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _canonical_json_sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _docker_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["docker", *args],
        text=True,
        capture_output=True,
        timeout=30,
    )
    require(completed.returncode == 0, f"docker {' '.join(args)} failed")
    return json.loads(completed.stdout)


def _docker_lines(args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["docker", *args],
        text=True,
        capture_output=True,
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
    settings_path = Path(appdata) / "Docker" / "settings-store.json"
    settings, _ = _load_json(settings_path, "current Docker Desktop settings")
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


def _postgres_database_identity(cur: Any) -> dict[str, Any]:
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
        endpoint_host.lower() in {"localhost", "127.0.0.1", "::1"},
        "journal pilot requires the locally accepted PostgreSQL endpoint",
    )
    container_ids = _docker_lines(
        ["ps", "-q", "--filter", "label=com.docker.compose.service=postgres"]
    )
    candidates: list[dict[str, Any]] = []
    for container_id in container_ids:
        inspected = _docker_json(["inspect", container_id])[0]
        ports = inspected.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
        if not any(int(binding.get("HostPort", -1)) == endpoint_port for binding in ports):
            continue
        mounts = [
            mount
            for mount in inspected.get("Mounts", [])
            if mount.get("Destination") == POSTGRES_DATA_DIRECTORY
        ]
        networks = inspected.get("NetworkSettings", {}).get("Networks", {})
        addresses = sorted(
            network.get("IPAddress") for network in networks.values() if network.get("IPAddress")
        )
        labels = inspected.get("Config", {}).get("Labels", {})
        state = inspected.get("State", {})
        health = state.get("Health", {}).get("Status")
        require(
            state.get("Running") is True
            and health == "healthy"
            and len(mounts) == 1
            and mounts[0].get("Type") == "volume"
            and mounts[0].get("RW") is True
            and mounts[0].get("Source", "").startswith("/var/lib/docker/volumes/")
            and labels.get("com.docker.compose.service") == "postgres",
            "PostgreSQL Docker runtime topology is not accepted",
        )
        candidates.append(
            {
                "container_id": inspected["Id"],
                "container_image_id": inspected["Image"],
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


def read_live_postgres_target() -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            database = _postgres_database_identity(cur)
    return _docker_postgres_identity(database)


def require_plan_target_matches_live(
    plan: dict[str, Any], storage: dict[str, Any], postgres: dict[str, Any]
) -> None:
    require(
        plan.get("accepted_storage_topology_evidence") == storage
        and plan.get("postgres_target_evidence") == postgres
        and storage.get("contract_version") == STORAGE_TOPOLOGY_VERSION
        and storage.get("gb_hot_placement") == {"drive": "E", "placement": "hot_global"}
        and storage.get("accepted_docker_e_receipt_sha256") == ACCEPTED_DOCKER_E_RECEIPT_SHA
        and storage.get("docker_desktop_data_root") == str(DOCKER_E_ROOT)
        and storage.get("docker_root_dir") == "/var/lib/docker"
        and postgres.get("database") == "markorbit"
        and postgres.get("data_directory") == POSTGRES_DATA_DIRECTORY
        and postgres.get("volume_source", "").startswith("/var/lib/docker/volumes/")
        and type(postgres.get("configured_endpoint_port")) is int
        and postgres.get("configured_endpoint_port") > 0
        and postgres.get("server_port") == 5432
        and postgres.get("observed_server_port") == 5432,
        "frozen PostgreSQL cluster/endpoint/E-topology evidence drift",
    )


def verify_journal_stage(plan_path: Path, plan_sha: str) -> dict[str, Any]:
    require(
        plan_path.parent.resolve() == GOV.resolve()
        and re.fullmatch(r"[0-9a-f]{64}", plan_sha) is not None
        and stage.sha256_file(plan_path) == plan_sha,
        "exact governed journal E-stage plan identity required",
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    authority = f"GO #855 GB-JOURNAL-E-STAGE {plan_sha} ADDITIVE-COPY-VERIFY-NO-DELETE"
    stage.validate_plan(plan, plan_sha, authority)

    receipt, receipt_sha = _load_json(stage.RECEIPT, "journal E-stage receipt")
    manifest, manifest_sha = _load_json(stage.TARGET_MANIFEST, "journal E-stage manifest")
    require(
        receipt.get("kind") == "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_RECEIPT_V1"
        and receipt.get("status") == "E_JOURNAL_STAGE_ACCEPTED_D_AND_F_RETAINED"
        and receipt.get("plan_sha256") == plan_sha
        and receipt.get("target_manifest_sha256") == manifest_sha
        and receipt.get("target_root") == str(stage.TARGET_ROOT)
        and receipt.get("files_verified") == len(plan["stage_files"])
        and receipt.get("bytes_verified") == plan["stage_bytes"]
        and receipt.get("source_D_retained") is True
        and receipt.get("official_raw_F_retained") is True
        and receipt.get("original_visual_F_retained") is True
        and receipt.get("source_delete_authorized") is False
        and receipt.get("postgres_apply_authorized") is False
        and receipt.get("clickhouse_apply_authorized") is False
        and receipt.get("vhdx_operation_authorized") is False
        and receipt.get("serving_cutover_authorized") is False
        and receipt.get("journal_observation_only") is True
        and receipt.get("current_registry_state_verified") is False,
        "journal E-stage receipt contract drift",
    )
    require(
        manifest.get("kind") == "GB_UKIPO_JOURNAL_E_STRUCTURED_STAGE_MANIFEST_V1"
        and manifest.get("status") == "E_JOURNAL_STAGE_BYTE_IDENTICAL_OBSERVATION_ONLY"
        and manifest.get("plan_sha256") == plan_sha
        and manifest.get("target_root") == str(stage.TARGET_ROOT)
        and manifest.get("issues") == plan["issues"]
        and manifest.get("stage_files") == plan["stage_files"]
        and manifest.get("official_raw_files") == plan["official_raw_files"]
        and manifest.get("accepted_78_issue_audit_sha256") == stage.ACCEPTED_AUDIT_SHA
        and manifest.get("missing_image_audit_sha256") == stage.MISSING_AUDIT_SHA
        and manifest.get("original_visual_root") == str(stage.VISUAL_ROOT)
        and manifest.get("f_original_visual_receipt_sha256") == stage.VISUAL_RECEIPT_SHA
        and manifest.get("journal_observation_only") is True
        and manifest.get("current_registry_state_verified") is False
        and manifest.get("database_ingested") is False
        and manifest.get("serving_cutover_authorized") is False,
        "journal E-stage manifest contract drift",
    )
    stage_files = tuple(
        stage.FrozenFile(row["relative_path"], row["bytes"], row["sha256"])
        for row in plan["stage_files"]
    )
    raw_files = tuple(
        stage.FrozenFile(row["relative_path"], row["bytes"], row["sha256"])
        for row in plan["official_raw_files"]
    )
    stage.verify_files(stage.TARGET_ROOT, stage_files)
    stage.verify_files(stage.RAW_ROOT, raw_files)
    actual = {
        path.relative_to(stage.TARGET_ROOT).as_posix()
        for path in stage.TARGET_ROOT.rglob("*")
        if path.is_file()
    }
    expected = {item.relative for item in stage_files} | {stage.TARGET_MANIFEST.name}
    require(actual == expected, "journal E stage contains partial or unexpected files")
    issues = [row for row in plan["issues"] if row.get("issue") == PILOT_ISSUE]
    require(len(issues) == 1, "journal pilot issue missing from accepted stage")
    issue = issues[0]
    records = stage.TARGET_ROOT / (f"{PILOT_ISSUE}-{issue['source_sha256'][:12]}-details.jsonl")
    require(
        records.is_file()
        and not records.is_symlink()
        and stage.sha256_file(records) == issue["records_sha256"],
        "journal pilot records identity drift",
    )
    return {
        "plan_sha256": plan_sha,
        "receipt_sha256": receipt_sha,
        "manifest_sha256": manifest_sha,
        "issue": issue,
        "records": records,
    }


def issue_records(proof: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = proof["issue"]
    records: list[dict[str, Any]] = []
    ordered_digest = hashlib.sha256()
    counts = {"notices": 0, "goods": 0, "parties": 0, "visuals": 0, "missing_visuals": 0}
    seen_members: set[str] = set()
    with proof["records"].open("r", encoding="utf-8", newline="") as stream:
        for ordinal, line in enumerate(stream, 1):
            payload = json.loads(line)
            require(isinstance(payload, dict), "journal detail row must be an object")
            source_member = payload.get("source_member")
            require(
                payload.get("kind") == "UKIPO_JOURNAL_DETAIL_STAGE_V1"
                and payload.get("issue") == PILOT_ISSUE
                and payload.get("source_zip_sha256") == expected["source_sha256"]
                and payload.get("mark_family") in {"UK", "WO"}
                and isinstance(payload.get("mark_id"), str)
                and re.fullmatch(r"(?:UK|WO)[0-9A-Z]+", payload["mark_id"]) is not None
                and isinstance(source_member, str)
                and source_member.startswith(PILOT_ISSUE + "/")
                and source_member.endswith(".html")
                and source_member not in seen_members
                and re.fullmatch(r"[0-9a-f]{64}", payload.get("detail_html_sha256", "")) is not None
                and isinstance(payload.get("journal_title_raw"), str)
                and isinstance(payload.get("mark_text"), list)
                and isinstance(payload.get("goods_by_class"), list)
                and isinstance(payload.get("applicants"), list)
                and isinstance(payload.get("representatives"), list)
                and isinstance(payload.get("mark_images"), list),
                f"journal detail contract drift at ordinal {ordinal}",
            )
            seen_members.add(source_member)
            row_sha = hashlib.sha256(line.encode("utf-8")).hexdigest()
            ordered_digest.update(f"{ordinal}:{row_sha}\n".encode())
            goods_rows = []
            for class_ordinal, group in enumerate(payload["goods_by_class"], 1):
                require(
                    isinstance(group, dict)
                    and type(group.get("class")) is int
                    and 1 <= group["class"] <= 45
                    and isinstance(group.get("goods"), list)
                    and all(isinstance(text, str) and text for text in group["goods"]),
                    f"journal goods contract drift at ordinal {ordinal}",
                )
                goods_rows.extend(
                    (class_ordinal, goods_ordinal, group["class"], text)
                    for goods_ordinal, text in enumerate(group["goods"], 1)
                )
            party_rows = []
            for role, field in (("APPLICANT", "applicants"), ("REPRESENTATIVE", "representatives")):
                require(
                    all(isinstance(name, str) and name for name in payload[field]),
                    f"journal party contract drift at ordinal {ordinal}",
                )
                party_rows.extend(
                    (role, party_ordinal, name)
                    for party_ordinal, name in enumerate(payload[field], 1)
                )
            visual_rows = []
            for image in payload["mark_images"]:
                image_ordinal = image.get("ordinal")
                require(type(image_ordinal) is int and image_ordinal > 0, "invalid visual ordinal")
                digest = image.get("sha256")
                if digest is None:
                    require(
                        payload.get("image_evidence_complete") is False
                        and image.get("asset_relative_path") is None
                        and image.get("bytes") is None
                        and image.get("source_member") is None
                        and image.get("source_member_resolution") == "MISSING_SOURCE_MEMBER",
                        f"unapproved missing visual at ordinal {ordinal}",
                    )
                    complete = False
                    counts["missing_visuals"] += 1
                else:
                    relative = image.get("asset_relative_path")
                    visual_root = stage.VISUAL_ROOT.resolve()
                    require(
                        re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                        and isinstance(relative, str)
                        and "\\" not in relative
                        and not Path(relative).is_absolute()
                        and Path(relative).stem == digest
                        and type(image.get("bytes")) is int
                        and image["bytes"] > 0
                        and isinstance(image.get("source_member"), str),
                        f"journal visual identity invalid at ordinal {ordinal}",
                    )
                    candidate = visual_root / relative
                    visual = candidate.resolve()
                    require(
                        visual.is_relative_to(visual_root)
                        and candidate.is_file()
                        and not candidate.is_symlink()
                        and visual.stat().st_size == image["bytes"]
                        and stage.sha256_file(visual) == digest,
                        f"F original visual identity drift at ordinal {ordinal}",
                    )
                    complete = True
                visual_rows.append(
                    (
                        image_ordinal,
                        image.get("source_member"),
                        image.get("declared_src"),
                        digest,
                        image.get("bytes"),
                        image.get("asset_relative_path"),
                        image.get("source_member_resolution"),
                        image.get("thumbnail_present") is True,
                        complete,
                    )
                )
            records.append(
                {
                    "ordinal": ordinal,
                    "row_sha256": row_sha,
                    "payload": payload,
                    "goods": goods_rows,
                    "parties": party_rows,
                    "visuals": visual_rows,
                }
            )
            counts["notices"] += 1
            counts["goods"] += len(goods_rows)
            counts["parties"] += len(party_rows)
            counts["visuals"] += len(visual_rows)
    require(
        counts["notices"] == expected["detail_rows"]
        and counts["visuals"] == expected["image_links"]
        and counts["missing_visuals"] == expected["missing_image_links"],
        "journal pilot issue cardinality drift",
    )
    counts["ordered_row_identity_sha256"] = ordered_digest.hexdigest()
    return records, counts


PILOT_RELATIONS = (
    "journal_issue_ingest_run_v1",
    "journal_notice_v1",
    "journal_goods_v1",
    "journal_party_v1",
    "journal_visual_v1",
)


def _relation_presence(cur: Any) -> list[str | None]:
    relations: list[str | None] = []
    for name in PILOT_RELATIONS:
        cur.execute("SELECT to_regclass(%s) AS relation", (f"trademark_gb.{name}",))
        relations.append(cur.fetchone()["relation"])
    return relations


def _readonly_live_prestate() -> dict[str, Any]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_database() AS db")
            database = cur.fetchone()["db"]
            require(database == "markorbit", "journal preflight target must be markorbit")
            relations = _relation_presence(cur)
    return {"database": database, "relations": relations}


def verify_live_prestate() -> dict[str, Any]:
    state = _readonly_live_prestate()
    require(
        state["database"] == "markorbit" and all(value is None for value in state["relations"]),
        "journal pilot tables already exist; require independent state review",
    )
    return state


def make_plan(
    proof: dict[str, Any],
    counts: dict[str, Any],
    *,
    execution_main: str | None = None,
    storage_topology: dict[str, Any] | None = None,
    postgres_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reserve = domestic.require_e_disk_reserve()["E"]
    execution_main = domestic.current_git_head() if execution_main is None else execution_main
    require(re.fullmatch(r"[0-9a-f]{40}", execution_main) is not None, "invalid execution main")
    storage_topology = (
        verify_accepted_storage_topology() if storage_topology is None else storage_topology
    )
    postgres_target = read_live_postgres_target() if postgres_target is None else postgres_target
    return {
        "kind": "GB_UKIPO_JOURNAL_2026_033_PG_PILOT_PLAN_V2",
        "status": "FROZEN_NO_APPLY",
        "execution_main_sha": execution_main,
        "issue": PILOT_ISSUE,
        "source_zip_sha256": proof["issue"]["source_sha256"],
        "stage_records_sha256": proof["issue"]["records_sha256"],
        "journal_e_stage_plan_sha256": proof["plan_sha256"],
        "journal_e_stage_receipt_sha256": proof["receipt_sha256"],
        "journal_e_stage_manifest_sha256": proof["manifest_sha256"],
        "f_original_visual_receipt_sha256": stage.VISUAL_RECEIPT_SHA,
        "expected_notices": counts["notices"],
        "expected_goods": counts["goods"],
        "expected_parties": counts["parties"],
        "expected_visuals": counts["visuals"],
        "expected_missing_visuals": counts["missing_visuals"],
        "ordered_row_identity_sha256": counts["ordered_row_identity_sha256"],
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "accepted_storage_topology_evidence": storage_topology,
        "postgres_target_evidence": postgres_target,
        "original_visual_authority_drive": "F",
        "operator_sha256": domestic.canonical_text_sha(Path(__file__)),
        "schema_sql_sha256": hashlib.sha256(SCHEMA_SQL.encode()).hexdigest(),
        "disk_reserve_gate": {"E": {"reserve_bytes": int(reserve["reserve_bytes"])}},
        "issue_atomic_rollback": True,
        "journal_observation_only": True,
        "current_state_verified": False,
        "full_journal_ingest_authorized": False,
        "historical_stock_mutation_authorized": False,
        "serving_cutover_authorized": False,
        "clickhouse_apply_authorized": False,
        "source_cleanup_authorized": False,
    }


def authorize(plan: dict[str, Any], plan_sha: str, token: str) -> None:
    storage_evidence = plan.get("accepted_storage_topology_evidence", {})
    postgres_evidence = plan.get("postgres_target_evidence", {})
    require(
        plan.get("kind") == "GB_UKIPO_JOURNAL_2026_033_PG_PILOT_PLAN_V2"
        and plan.get("status") == "FROZEN_NO_APPLY"
        and plan.get("issue") == PILOT_ISSUE
        and plan.get("source_zip_sha256") == PILOT_SOURCE_SHA
        and plan.get("stage_records_sha256") == PILOT_RECORDS_SHA
        and plan.get("expected_notices") == PILOT_NOTICES
        and plan.get("expected_goods") == PILOT_GOODS
        and plan.get("expected_parties") == PILOT_PARTIES
        and plan.get("expected_visuals") == PILOT_VISUALS
        and plan.get("expected_missing_visuals") == 0
        and plan.get("ordered_row_identity_sha256") == PILOT_ORDERED_SHA
        and plan.get("target_database") == "markorbit"
        and plan.get("target_database_physical_drive") == "E"
        and plan.get("future_query_storage_placement") == "hot_global"
        and storage_evidence.get("contract_version") == STORAGE_TOPOLOGY_VERSION
        and storage_evidence.get("contract_sha256") == _canonical_json_sha(build_storage_topology())
        and storage_evidence.get("gb_hot_placement") == {"drive": "E", "placement": "hot_global"}
        and storage_evidence.get("accepted_docker_e_receipt_sha256")
        == ACCEPTED_DOCKER_E_RECEIPT_SHA
        and storage_evidence.get("docker_desktop_data_root") == str(DOCKER_E_ROOT)
        and storage_evidence.get("docker_data_vhdx") == str(DOCKER_E_DATA_VHDX)
        and storage_evidence.get("docker_root_dir") == "/var/lib/docker"
        and postgres_evidence.get("database") == "markorbit"
        and postgres_evidence.get("data_directory") == POSTGRES_DATA_DIRECTORY
        and postgres_evidence.get("server_port") == 5432
        and postgres_evidence.get("observed_server_port") == 5432
        and re.fullmatch(r"[0-9]+", str(postgres_evidence.get("system_identifier", ""))) is not None
        and postgres_evidence.get("volume_source", "").startswith("/var/lib/docker/volumes/")
        and type(postgres_evidence.get("configured_endpoint_port")) is int
        and postgres_evidence.get("configured_endpoint_port", 0) > 0
        and plan.get("original_visual_authority_drive") == "F"
        and plan.get("operator_sha256") == domestic.canonical_text_sha(Path(__file__))
        and plan.get("schema_sql_sha256") == hashlib.sha256(SCHEMA_SQL.encode()).hexdigest()
        and set(plan.get("disk_reserve_gate", {})) == {"E"}
        and plan.get("issue_atomic_rollback") is True
        and plan.get("journal_observation_only") is True
        and plan.get("current_state_verified") is False
        and plan.get("full_journal_ingest_authorized") is False
        and plan.get("historical_stock_mutation_authorized") is False
        and plan.get("serving_cutover_authorized") is False
        and plan.get("clickhouse_apply_authorized") is False
        and plan.get("source_cleanup_authorized") is False,
        "journal pilot frozen plan/operator mismatch",
    )
    expected = f"GO #855 GB-JOURNAL-PILOT-2026-033 {plan_sha} ISSUE-ONLY-ROLLBACK"
    require(token == expected, "exact GB journal pilot authority required")


def _pilot_rows(records: list[dict[str, Any]]) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
    notice_rows = []
    goods_rows = []
    party_rows = []
    visual_rows = []
    for record in records:
        ordinal = record["ordinal"]
        payload = record["payload"]
        notice_rows.append(
            (
                PILOT_ISSUE,
                ordinal,
                payload["source_zip_sha256"],
                payload["source_member"],
                payload["detail_html_sha256"],
                record["row_sha256"],
                payload["mark_family"],
                payload["mark_id"],
                payload["journal_title_raw"],
                payload.get("regdate_raw"),
                json.dumps(payload["mark_text"], ensure_ascii=False, sort_keys=True),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        )
        goods_rows.extend((PILOT_ISSUE, ordinal, *row) for row in record["goods"])
        party_rows.extend((PILOT_ISSUE, ordinal, *row) for row in record["parties"])
        visual_rows.extend((PILOT_ISSUE, ordinal, *row) for row in record["visuals"])
    return notice_rows, goods_rows, party_rows, visual_rows


def _receipt_payload(
    plan: dict[str, Any], plan_sha: str, reserve: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": "GB_UKIPO_JOURNAL_2026_033_PG_PILOT_RECEIPT_V2",
        "status": "ISSUE_ATOMIC_PILOT_COMPLETE_OBSERVATION_ONLY",
        "plan_sha256": plan_sha,
        "execution_main_sha": plan["execution_main_sha"],
        "issue": PILOT_ISSUE,
        "source_zip_sha256": plan["source_zip_sha256"],
        "stage_records_sha256": plan["stage_records_sha256"],
        "notices_committed": plan["expected_notices"],
        "goods_committed": plan["expected_goods"],
        "parties_committed": plan["expected_parties"],
        "visuals_committed": plan["expected_visuals"],
        "missing_visuals_committed": plan["expected_missing_visuals"],
        "ordered_row_identity_sha256": plan["ordered_row_identity_sha256"],
        "target_database": "markorbit",
        "target_database_physical_drive": "E",
        "future_query_storage_placement": "hot_global",
        "accepted_storage_topology_evidence": plan["accepted_storage_topology_evidence"],
        "postgres_target_evidence": plan["postgres_target_evidence"],
        "original_visual_authority_drive": "F",
        "journal_observation_only": True,
        "current_state_verified": False,
        "full_journal_ingest_authorized": False,
        "serving_cutover_authorized": False,
        "source_cleanup_authorized": False,
        "disk_reserve_apply_snapshot": reserve,
    }


def _verify_committed_rows(cur: Any, plan: dict[str, Any]) -> None:
    cur.execute(
        """
        SELECT
         (SELECT count(*) FROM trademark_gb.journal_notice_v1 WHERE issue=%s) AS notices,
         (SELECT count(*) FROM trademark_gb.journal_goods_v1 WHERE issue=%s) AS goods,
         (SELECT count(*) FROM trademark_gb.journal_party_v1 WHERE issue=%s) AS parties,
         (SELECT count(*) FROM trademark_gb.journal_visual_v1 WHERE issue=%s) AS visuals,
         (SELECT count(*) FROM trademark_gb.journal_visual_v1
           WHERE issue=%s AND NOT evidence_complete) AS missing_visuals,
         (SELECT count(*) FROM trademark_gb.journal_notice_v1
           WHERE issue=%s AND current_state_verified) AS unapproved_current,
         (SELECT count(*) FROM trademark_gb.journal_notice_v1
           WHERE issue=%s AND NOT journal_observation_only) AS nonobservation
        """,
        (PILOT_ISSUE,) * 7,
    )
    check = cur.fetchone()
    require(
        all(
            check[key] == plan[f"expected_{key}"]
            for key in ("notices", "goods", "parties", "visuals", "missing_visuals")
        )
        and check["unapproved_current"] == 0
        and check["nonobservation"] == 0,
        "journal pilot live count/currentness drift",
    )
    cur.execute(
        """
        SELECT notice_ordinal,source_row_sha256
        FROM trademark_gb.journal_notice_v1
        WHERE issue=%s
        ORDER BY notice_ordinal
        """,
        (PILOT_ISSUE,),
    )
    ordered = hashlib.sha256()
    for expected_ordinal, row in enumerate(cur.fetchall(), 1):
        require(row["notice_ordinal"] == expected_ordinal, "journal notice ordinal drift")
        ordered.update(f"{row['notice_ordinal']}:{row['source_row_sha256']}\n".encode("utf-8"))
    require(
        ordered.hexdigest() == plan["ordered_row_identity_sha256"],
        "journal pilot ordered row identity drift",
    )


def _insert_new_pilot(
    cur: Any,
    records: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_sha: str,
    payload: dict[str, Any],
) -> None:
    receipt_sha = _canonical_json_sha(payload)
    notice_rows, goods_rows, party_rows, visual_rows = _pilot_rows(records)
    cur.execute(SCHEMA_SQL)
    cur.execute(
        """
        INSERT INTO trademark_gb.journal_issue_ingest_run_v1(
         issue,source_zip_sha256,stage_records_sha256,expected_notices,
         expected_goods,expected_parties,expected_visuals,expected_missing_visuals,
         plan_sha256,execution_main_sha,plan_evidence,storage_topology_evidence,
         postgres_target_evidence,receipt_evidence,receipt_sha256,status
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                  %s::jsonb,%s,'RUNNING')
        """,
        (
            PILOT_ISSUE,
            plan["source_zip_sha256"],
            plan["stage_records_sha256"],
            plan["expected_notices"],
            plan["expected_goods"],
            plan["expected_parties"],
            plan["expected_visuals"],
            plan["expected_missing_visuals"],
            plan_sha,
            plan["execution_main_sha"],
            json.dumps(plan, ensure_ascii=False, sort_keys=True),
            json.dumps(plan["accepted_storage_topology_evidence"], sort_keys=True),
            json.dumps(plan["postgres_target_evidence"], sort_keys=True),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            receipt_sha,
        ),
    )
    cur.executemany(NOTICE_SQL, notice_rows)
    require(cur.rowcount == len(notice_rows), "journal notice insert count drift")
    cur.executemany(GOODS_SQL, goods_rows)
    require(cur.rowcount == len(goods_rows), "journal goods insert count drift")
    cur.executemany(PARTY_SQL, party_rows)
    require(cur.rowcount == len(party_rows), "journal party insert count drift")
    cur.executemany(VISUAL_SQL, visual_rows)
    require(cur.rowcount == len(visual_rows), "journal visual insert count drift")
    _verify_committed_rows(cur, plan)
    cur.execute(
        """
        UPDATE trademark_gb.journal_issue_ingest_run_v1
        SET status='COMPLETE',checkpoint_notice_ordinal=%s,completed_at=now()
        WHERE issue=%s AND status='RUNNING' AND checkpoint_notice_ordinal=0
        """,
        (plan["expected_notices"], PILOT_ISSUE),
    )
    require(cur.rowcount == 1, "journal pilot completion transition failed")


def _as_json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def _reconcile_committed_pilot(cur: Any, plan: dict[str, Any], plan_sha: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT source_zip_sha256,stage_records_sha256,expected_notices,expected_goods,
               expected_parties,expected_visuals,expected_missing_visuals,plan_sha256,
               execution_main_sha,plan_evidence,storage_topology_evidence,
               postgres_target_evidence,receipt_evidence,receipt_sha256,status,
               checkpoint_notice_ordinal
        FROM trademark_gb.journal_issue_ingest_run_v1
        WHERE issue=%s
        """,
        (PILOT_ISSUE,),
    )
    row = cur.fetchone()
    require(row is not None, "journal pilot committed run evidence missing")
    persisted_plan = _as_json_object(row["plan_evidence"], "persisted pilot plan")
    persisted_storage = _as_json_object(
        row["storage_topology_evidence"], "persisted storage topology"
    )
    persisted_target = _as_json_object(
        row["postgres_target_evidence"], "persisted PostgreSQL target"
    )
    payload = _as_json_object(row["receipt_evidence"], "persisted pilot receipt")
    reserve = _as_json_object(
        payload.get("disk_reserve_apply_snapshot"), "persisted disk reserve snapshot"
    )
    expected_payload = _receipt_payload(plan, plan_sha, reserve)
    require(
        row["source_zip_sha256"] == plan["source_zip_sha256"]
        and row["stage_records_sha256"] == plan["stage_records_sha256"]
        and all(
            row[f"expected_{key}"] == plan[f"expected_{key}"]
            for key in ("notices", "goods", "parties", "visuals", "missing_visuals")
        )
        and row["plan_sha256"] == plan_sha
        and row["execution_main_sha"] == plan["execution_main_sha"]
        and persisted_plan == plan
        and persisted_storage == plan["accepted_storage_topology_evidence"]
        and persisted_target == plan["postgres_target_evidence"]
        and row["status"] == "COMPLETE"
        and row["checkpoint_notice_ordinal"] == plan["expected_notices"]
        and payload == expected_payload
        and row["receipt_sha256"] == _canonical_json_sha(payload),
        "committed journal pilot evidence differs from exact frozen execution",
    )
    _verify_committed_rows(cur, plan)
    return payload


def _database_apply_or_reconcile(
    records: list[dict[str, Any]],
    plan: dict[str, Any],
    plan_sha: str,
    storage: dict[str, Any],
    reserve: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    from app.db import postgres_conn

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            live_database = _postgres_database_identity(cur)
            live_target = _docker_postgres_identity(live_database)
            require_plan_target_matches_live(plan, storage, live_target)
            relations = _relation_presence(cur)
            if all(relation is None for relation in relations):
                require(reserve is not None, "fresh journal pilot requires live E reserve proof")
                payload = _receipt_payload(plan, plan_sha, reserve)
                _insert_new_pilot(cur, records, plan, plan_sha, payload)
                conn.commit()
                return payload, False
            require(
                all(relation is not None for relation in relations),
                "partial journal pilot schema requires independent state review",
            )
            payload = _reconcile_committed_pilot(cur, plan, plan_sha)
            conn.commit()
            return payload, True


def _atomic_publish_receipt(path: Path, payload: dict[str, Any]) -> str:
    expected = _canonical_json_bytes(payload)
    expected_sha = hashlib.sha256(expected).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        require(path.is_file() and not path.is_symlink(), "receipt path is not a regular file")
        actual = path.read_bytes()
        if actual == expected:
            return expected_sha
        require(
            len(actual) < len(expected) and expected.startswith(actual),
            "existing receipt differs from exact committed evidence",
        )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        require(
            temporary.is_file()
            and not temporary.is_symlink()
            and temporary.read_bytes() == expected,
            "receipt temporary file verification failed",
        )
        os.replace(temporary, path)
        require(
            path.is_file()
            and not path.is_symlink()
            and path.read_bytes() == expected
            and domestic.sha(path) == expected_sha,
            "atomic receipt publication verification failed",
        )
    finally:
        if temporary.exists():
            temporary.unlink()
    return expected_sha


def apply_pilot(
    proof: dict[str, Any], records: list[dict[str, Any]], plan: dict[str, Any], plan_sha: str
) -> dict[str, Any]:
    domestic.require_live_clean_main(plan["execution_main_sha"])
    storage = verify_accepted_storage_topology()
    postgres = read_live_postgres_target()
    require_plan_target_matches_live(plan, storage, postgres)
    state = _readonly_live_prestate()
    if all(relation is None for relation in state["relations"]):
        require(not RECEIPT.exists(), "receipt exists without committed journal pilot state")
        reserve: dict[str, Any] | None = domestic.verify_apply_disk_reserve(
            plan["disk_reserve_gate"]
        )
    else:
        reserve = None
    payload, reconciled = _database_apply_or_reconcile(records, plan, plan_sha, storage, reserve)
    receipt_sha = _atomic_publish_receipt(RECEIPT, payload)

    result = dict(payload)
    result["receipt_path"] = str(RECEIPT)
    result["receipt_sha256"] = receipt_sha
    result["receipt_reconciled_from_committed_state"] = reconciled
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-plan", type=Path, required=True)
    parser.add_argument("--stage-plan-sha", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--freeze-plan", type=Path)
    mode.add_argument("--apply-pilot", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha", default="")
    parser.add_argument("--authority-token", default="")
    args = parser.parse_args()

    execution_main = (
        domestic.require_live_clean_main()
        if args.freeze_plan is not None or args.apply_pilot
        else domestic.current_git_head()
    )
    proof = verify_journal_stage(args.stage_plan, args.stage_plan_sha)
    records, counts = issue_records(proof)
    if not args.apply_pilot:
        verify_live_prestate()
    proposed = make_plan(proof, counts, execution_main=execution_main)
    if args.preflight_only:
        require(not args.plan and not args.authority_token, "preflight accepts no Apply arguments")
        print(json.dumps(proposed, ensure_ascii=False, sort_keys=True), flush=True)
        return
    if args.freeze_plan is not None:
        require(
            not args.plan
            and not args.authority_token
            and args.freeze_plan.parent.resolve() == GOV.resolve(),
            "freeze plan must be immutable under GB governed plans",
        )
        with args.freeze_plan.open("x", encoding="utf-8") as stream:
            json.dump(proposed, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        print("GB_JOURNAL_PILOT_PLAN_SHA256=" + domestic.sha(args.freeze_plan), flush=True)
        print("GB_JOURNAL_PILOT_PLAN_STATUS=FROZEN_NO_APPLY", flush=True)
        return
    require(
        args.plan is not None
        and re.fullmatch(r"[0-9a-f]{64}", args.plan_sha)
        and domestic.sha(args.plan) == args.plan_sha,
        "exact frozen GB journal pilot plan SHA required",
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    require(plan == proposed, "journal pilot plan differs from live frozen state")
    authorize(plan, args.plan_sha, args.authority_token)
    result = apply_pilot(proof, records, plan, args.plan_sha)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
