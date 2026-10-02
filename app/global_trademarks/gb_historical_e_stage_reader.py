"""Fail-closed reader for the independently accepted UKIPO E structured stage."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.global_trademarks import gb_historical_source_row_pilot as pilot

STAGE_ROOT = Path(r"E:\MarkOrbitData\structured-stage\gb\ukipo\historical-stock-v2")
STAGE_MANIFEST = STAGE_ROOT / "gb-historical-e-stage-manifest-v1.json"
STAGE_MANIFEST_SHA = "cae1e3bade1d87630c0a91a5156ba2a1702460421499b7ea55fd4c090b8c4712"
GOV_ROOT = Path(r"D:\yoomarks\governed-plans\855")
SOURCE_AUDIT = GOV_ROOT / "gb-historical-stock-independent-audit-r1.json"
SOURCE_AUDIT_SHA = "320c40b1cea7d5593ee8dbb99dfcbc1f534b71bd682281ab42fd719873a9d0c5"
RELOCATION_PLAN_SHA = "5a6e6861b3c245fe9dc4d05a4871315e048b05c0f03868e1b786cb1edcafe7c9"
RELOCATION_RECEIPT = GOV_ROOT / "gb-historical-e-stage-relocation-r1.json"
RELOCATION_RECEIPT_SHA = "c51b52d2fc34868e244a199613d632c44bb974aeb99ef727223be8c0a28638d3"
INDEPENDENT_AUDIT = GOV_ROOT / "gb-historical-e-stage-independent-audit-r1.json"
INDEPENDENT_AUDIT_SHA = "c6caf1d2b55320bd38a7777005e8d6f1a806f4f1e4a08b30db28da6c002cac4c"
RAW_ROOT = Path(r"F:\MarkOrbitData\raw\incoming\uk")
RAW_FILES = {
    "opendatadomestic.zip": {
        "bytes": 63_238_846,
        "sha256": "3b6063bed36a78e8a04f10a5383f2881f4072a1be13e706a81ab097fb56ee571",
    },
    "opendataIR.zip": {
        "bytes": 6_155_443,
        "sha256": "2a93bb45f0c40c69813628b6f2ab3a6a443f6d0504d8edf009c51027f28f8c85",
    },
}


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_json(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    require(
        path.is_file() and not path.is_symlink() and sha256_file(path) == expected_sha,
        f"{label} SHA identity drift",
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{label} must be a JSON object")
    return value


def require_no_reparse_ancestors(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.exists():
            require(
                not ancestor.is_symlink()
                and not (os.name == "nt" and os.path.isjunction(ancestor)),
                f"E structured-stage path traverses symlink/junction: {ancestor}",
            )


def _file_map(payload: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    rows = payload.get(field)
    require(isinstance(rows, list) and rows, f"{field} evidence missing")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        require(
            isinstance(row, dict)
            and isinstance(row.get("relative_path"), str)
            and type(row.get("bytes")) is int
            and isinstance(row.get("sha256"), str),
            f"{field} evidence shape invalid",
        )
        relative = row["relative_path"]
        require(relative not in result, f"duplicate {field} path")
        result[relative] = row
    return result


def verify_e_stage(stream: str) -> dict[str, Any]:
    require(stream in pilot.SOURCE_SPECS, "unreviewed GB historical stream")
    require_no_reparse_ancestors(STAGE_ROOT)
    require_no_reparse_ancestors(RAW_ROOT)
    source_audit = verified_json(SOURCE_AUDIT, SOURCE_AUDIT_SHA, "accepted source audit")
    receipt = verified_json(
        RELOCATION_RECEIPT, RELOCATION_RECEIPT_SHA, "E-stage relocation receipt"
    )
    independent = verified_json(
        INDEPENDENT_AUDIT, INDEPENDENT_AUDIT_SHA, "E-stage independent audit"
    )
    manifest = verified_json(STAGE_MANIFEST, STAGE_MANIFEST_SHA, "E-stage manifest")
    require(
        source_audit.get("status") == "TWO_HISTORICAL_ZIPS_STAGED_NOT_DATABASE_INGESTED"
        and source_audit.get("data_engine_ingested") is False
        and source_audit.get("schema_migration_applied") is False,
        "accepted source audit is not historical-stage-only",
    )
    require(
        receipt.get("status") == "E_STRUCTURED_STAGE_ACCEPTED_D_AND_F_RETAINED"
        and receipt.get("plan_sha256") == RELOCATION_PLAN_SHA
        and receipt.get("target_manifest_sha256") == STAGE_MANIFEST_SHA
        and receipt.get("target_root") == str(STAGE_ROOT)
        and receipt.get("source_D_retained") is True
        and receipt.get("official_raw_F_retained") is True
        and receipt.get("source_delete_authorized") is False
        and receipt.get("postgres_apply_authorized") is False
        and receipt.get("clickhouse_apply_authorized") is False
        and receipt.get("vhdx_operation_authorized") is False
        and receipt.get("serving_cutover_authorized") is False
        and receipt.get("historical_source_only") is True
        and receipt.get("current_state_verified") is False,
        "E-stage relocation receipt contract drift",
    )
    require(
        independent.get("kind") == "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_INDEPENDENT_AUDIT_V1"
        and independent.get("status")
        == "E_STRUCTURED_STAGE_INDEPENDENTLY_ACCEPTED_D_AND_F_RETAINED"
        and independent.get("plan_sha256") == RELOCATION_PLAN_SHA
        and independent.get("accepted_stage_audit_sha256") == SOURCE_AUDIT_SHA
        and independent.get("relocation_receipt_sha256") == RELOCATION_RECEIPT_SHA
        and independent.get("target_manifest_sha256") == STAGE_MANIFEST_SHA
        and independent.get("target_root") == str(STAGE_ROOT)
        and independent.get("stage_bytes_verified") == 2_132_564_100
        and independent.get("partials_found") == 0
        and independent.get("unexpected_target_files") == 0
        and independent.get("postgres_applied") is False
        and independent.get("clickhouse_applied") is False
        and independent.get("vhdx_operated") is False
        and independent.get("serving_cutover_applied") is False
        and independent.get("source_D_retained") is True
        and independent.get("official_raw_F_retained") is True
        and independent.get("historical_source_only") is True
        and independent.get("current_state_verified") is False,
        "E-stage independent audit contract drift",
    )
    require(
        manifest.get("kind") == "GB_UKIPO_HISTORICAL_E_STRUCTURED_STAGE_MANIFEST_V1"
        and manifest.get("status") == "E_STRUCTURED_STAGE_BYTE_IDENTICAL_HISTORICAL_ONLY"
        and manifest.get("plan_sha256") == RELOCATION_PLAN_SHA
        and manifest.get("accepted_stage_audit_sha256") == SOURCE_AUDIT_SHA
        and manifest.get("target_root") == str(STAGE_ROOT)
        and manifest.get("official_raw_root") == str(RAW_ROOT)
        and manifest.get("historical_source_only") is True
        and manifest.get("current_state_verified") is False
        and manifest.get("database_ingested") is False
        and manifest.get("serving_cutover_authorized") is False,
        "E-stage manifest contract drift",
    )
    manifest_files = _file_map(manifest, "stage_files")
    audit_files = _file_map(independent, "stage_files")
    require(manifest_files == audit_files, "E-stage manifest/audit file set drift")
    expected_names = set(manifest_files) | {STAGE_MANIFEST.name}
    actual_names = {
        path.relative_to(STAGE_ROOT).as_posix() for path in STAGE_ROOT.rglob("*") if path.is_file()
    }
    require(actual_names == expected_names, "E-stage contains partial or unexpected files")

    raw_files = _file_map(independent, "official_raw_files")
    require(
        raw_files == _file_map(manifest, "official_raw_files"),
        "E-stage manifest/audit raw inventory drift",
    )
    raw_identities = {
        name: {"bytes": row["bytes"], "sha256": row["sha256"]} for name, row in raw_files.items()
    }
    require(raw_identities == RAW_FILES, "official F raw evidence inventory drift")
    for name, expected in RAW_FILES.items():
        path = RAW_ROOT / name
        require(
            path.is_file()
            and path.stat().st_size == expected["bytes"]
            and sha256_file(path) == expected["sha256"],
            f"official F raw evidence drift: {name}",
        )

    spec = pilot.SOURCE_SPECS[stream]
    source_match = [row for row in source_audit["streams"] if row.get("source_stream") == stream]
    require(
        len(source_match) == 1
        and source_match[0]["source_zip_sha256"] == spec["zip_sha"]
        and source_match[0]["source_rows"] == spec["total"]
        and source_match[0]["accepted_source_rows"] == spec["accepted"]
        and source_match[0]["quarantined_source_rows"] == spec["bad"]
        and source_match[0]["manifest_sha256"] == spec["manifest_sha"],
        "accepted source stream identity drift",
    )
    stem = stream.lower() + "-" + spec["zip_sha"][:12]
    rows = STAGE_ROOT / f"{stem}-rows.jsonl"
    bad = STAGE_ROOT / f"{stem}-quarantine.jsonl"
    rows_evidence = manifest_files[rows.name]
    bad_evidence = manifest_files[bad.name]
    require(
        rows.stat().st_size == rows_evidence["bytes"]
        and sha256_file(rows) == rows_evidence["sha256"] == source_match[0]["rows_sha256"]
        and bad.stat().st_size == bad_evidence["bytes"]
        and sha256_file(bad) == bad_evidence["sha256"] == source_match[0]["quarantine_sha256"],
        "E-stage accepted/quarantine JSONL identity drift",
    )
    return {
        "manifest": {
            "accepted_jsonl_sha256": rows_evidence["sha256"],
            "quarantine_jsonl_sha256": bad_evidence["sha256"],
        },
        "rows": rows,
        "bad": bad,
        "spec": spec,
        "e_stage": {
            "root": str(STAGE_ROOT),
            "manifest_sha256": STAGE_MANIFEST_SHA,
            "relocation_plan_sha256": RELOCATION_PLAN_SHA,
            "relocation_receipt_sha256": RELOCATION_RECEIPT_SHA,
            "independent_audit_sha256": INDEPENDENT_AUDIT_SHA,
        },
    }
