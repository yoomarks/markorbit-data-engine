from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from app.us.package_meta import infer_us_package_descriptor
from app.us.source_preflight import build_preflight
from app.us.target_canary import deterministic_package_id


BULK_PLAN_VERSION = "US_APPLICATION_TARGET_BULK_PLAN_V1"
EXPECTED_HISTORY_PARTS = 91
EXPECTED_SOURCE_COUNT = 310
FIRST_BULK_SEQUENCE = 3
ACCEPTED_SCHEMA_MANIFEST_SHA256 = (
    "ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795"
)
ACCEPTED_PACKAGE1_FILE = "apc18840407-20251231-01.zip"
ACCEPTED_PACKAGE1_SHA256 = (
    "9b65bdcb80c2bdd6efa6869432771c30613bed6dc8efd3d4589e2fd8b334b062"
)
ACCEPTED_PACKAGE2_FILE = "apc18840407-20251231-02.zip"
ACCEPTED_PACKAGE2_SHA256 = (
    "96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7"
)
ACCEPTED_PACKAGE2_ID = "aec9c8b5-f680-5881-94fb-71a1f8e44152"
ACCEPTED_PACKAGE2_DECISION = "BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def validate_stage2_anchor(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate the immutable accepted Package 2 target-canary receipt."""
    _require(
        str(receipt.get("decision") or "") == ACCEPTED_PACKAGE2_DECISION,
        "accepted Package 2 decision mismatch",
    )
    authority = _as_object(receipt.get("authority"), "Package 2 authority")
    _require(int(authority.get("issue") or 0) == 526, "Package 2 authority issue mismatch")
    _require(int(authority.get("stage") or 0) == 2, "Package 2 authority stage mismatch")
    _require(
        int(authority.get("bounded_package_sequence") or 0) == 2,
        "Package 2 authority sequence mismatch",
    )
    _require(bool(authority.get("consumed")), "Package 2 authority is not consumed")

    package = _as_object(receipt.get("package"), "Package 2 package")
    _require(str(package.get("file_name") or "") == ACCEPTED_PACKAGE2_FILE, "Package 2 filename mismatch")
    _require(
        str(package.get("sha256") or "").lower() == ACCEPTED_PACKAGE2_SHA256,
        "Package 2 SHA-256 mismatch",
    )
    _require(str(package.get("package_id") or "") == ACCEPTED_PACKAGE2_ID, "Package 2 id mismatch")
    _require(int(package.get("sequence") or 0) == 2, "Package 2 sequence mismatch")

    schema = _as_object(receipt.get("schema"), "Package 2 schema")
    _require(
        str(schema.get("manifest_sha256") or "").lower()
        == ACCEPTED_SCHEMA_MANIFEST_SHA256,
        "Package 2 target schema manifest mismatch",
    )
    journal = _as_object(receipt.get("journal"), "Package 2 journal")
    _require(str(journal.get("state") or "") == "COMPLETE", "Package 2 journal is not COMPLETE")
    expected_counts = _as_object(
        journal.get("expected_row_counts"),
        "Package 2 expected row counts",
    )
    observed_counts = _as_object(
        journal.get("observed_row_counts"),
        "Package 2 observed row counts",
    )
    _require(expected_counts == observed_counts, "Package 2 accepted row counts disagree")
    safety = _as_object(receipt.get("safety"), "Package 2 safety")
    _require(bool(safety.get("source_file_preserved")), "Package 2 source was not preserved")
    _require(not bool(safety.get("package_3_executed")), "Package 3 already executed in Package 2 receipt")
    _require(not bool(safety.get("full_corpus_executed")), "full corpus already executed in Package 2 receipt")
    _require(not bool(safety.get("automatic_next_package")), "Package 2 receipt allowed automatic next package")
    return {
        "decision": ACCEPTED_PACKAGE2_DECISION,
        "sequence": 2,
        "file_name": ACCEPTED_PACKAGE2_FILE,
        "sha256": ACCEPTED_PACKAGE2_SHA256,
        "package_id": ACCEPTED_PACKAGE2_ID,
        "schema_manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
        "expected_row_counts": {str(k): int(v) for k, v in sorted(expected_counts.items())},
        "receipt_sha256": _canonical_sha256(receipt),
    }


def _source_entry(source: dict[str, Any]) -> dict[str, Any]:
    sequence = int(source.get("sequence") or 0)
    _require(sequence > 0, "source replay sequence must be positive")
    path = Path(str(source.get("path") or ""))
    _require(path.is_file(), f"planned source file is missing: {path}")
    stat = path.stat()
    descriptor = infer_us_package_descriptor(path)
    if descriptor.package_kind == "UNKNOWN":
        stem = path.stem
        if len(stem) > 9 and stem[-9] == "_" and all(
            ch in "0123456789abcdefABCDEF" for ch in stem[-8:]
        ):
            descriptor = infer_us_package_descriptor(path.with_name(stem[:-9] + path.suffix))
    _require(descriptor.package_kind != "UNKNOWN", f"unknown package descriptor: {path.name}")
    _require(
        descriptor.package_kind == str(source.get("package_kind") or ""),
        f"source descriptor kind drifted: {path.name}",
    )
    _require(
        descriptor.partition_value == str(source.get("partition_value") or ""),
        f"source descriptor partition drifted: {path.name}",
    )
    digest = str(source.get("sha256") or "").lower()
    _require(len(digest) == 64, f"invalid source SHA-256 in preflight: {path.name}")
    effective = descriptor.source_period_end
    return {
        "sequence": sequence,
        "file_name": path.name,
        "path": str(path),
        "location": str(source.get("location") or ""),
        "size_bytes": int(stat.st_size),
        "sha256": digest,
        "package_id": str(deterministic_package_id(digest)),
        "package_kind": descriptor.package_kind,
        "partition_dimension": descriptor.partition_dimension,
        "partition_value": descriptor.partition_value,
        "source_effective_date": effective.isoformat() if isinstance(effective, date) else None,
        "source_rank": descriptor.source_rank(sequence),
    }


def _validate_source_inventory(entries: list[dict[str, Any]]) -> None:
    _require(len(entries) == EXPECTED_SOURCE_COUNT, "accepted US source corpus count drifted")
    sequences = [int(item["sequence"]) for item in entries]
    _require(
        sequences == list(range(1, EXPECTED_SOURCE_COUNT + 1)),
        "US source replay sequence is not exactly contiguous 1..310",
    )
    shas = [str(item["sha256"]) for item in entries]
    _require(len(shas) == len(set(shas)), "US source inventory contains duplicate SHA-256 identities")
    package_ids = [str(item["package_id"]) for item in entries]
    _require(len(package_ids) == len(set(package_ids)), "US source inventory contains duplicate package ids")

    first = entries[0]
    second = entries[1]
    _require(first["sha256"] == ACCEPTED_PACKAGE1_SHA256, "accepted Package 1 SHA-256 drifted")
    _require(first["partition_value"].endswith("#001"), "accepted Package 1 partition drifted")
    _require(second["file_name"] == ACCEPTED_PACKAGE2_FILE, "accepted Package 2 filename drifted")
    _require(second["sha256"] == ACCEPTED_PACKAGE2_SHA256, "accepted Package 2 SHA-256 drifted")
    _require(second["package_id"] == ACCEPTED_PACKAGE2_ID, "accepted Package 2 deterministic id drifted")


def build_bulk_plan(
    raw_root: Path,
    *,
    execution_main: str,
    stage2_receipt: dict[str, Any],
    start_sequence: int = FIRST_BULK_SEQUENCE,
    end_sequence: int | None = None,
    max_packages: int | None = None,
    source_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fully frozen, read-only bounded target replay plan."""
    if bool(end_sequence is None) == bool(max_packages is None):
        raise ValueError("provide exactly one of end_sequence or max_packages")
    if len(execution_main) != 40 or any(
        ch not in "0123456789abcdefABCDEF" for ch in execution_main
    ):
        raise ValueError("execution_main must be a 40-character git SHA")
    if start_sequence < FIRST_BULK_SEQUENCE:
        raise ValueError(f"bulk replay cannot start before sequence {FIRST_BULK_SEQUENCE}")
    if start_sequence > EXPECTED_SOURCE_COUNT:
        raise ValueError("start_sequence exceeds accepted source corpus")
    if end_sequence is not None:
        if end_sequence < start_sequence or end_sequence > EXPECTED_SOURCE_COUNT:
            raise ValueError("end_sequence is outside the accepted bounded suffix")
        resolved_end = end_sequence
    else:
        assert max_packages is not None
        if max_packages < 1:
            raise ValueError("max_packages must be at least 1")
        resolved_end = start_sequence + max_packages - 1
        if resolved_end > EXPECTED_SOURCE_COUNT:
            raise ValueError("max_packages exceeds the accepted source corpus suffix")

    anchor = validate_stage2_anchor(stage2_receipt)
    preflight = source_preflight or build_preflight(
        raw_root,
        expected_history_parts=EXPECTED_HISTORY_PARTS,
        deep_source_test=False,
    )
    _require(bool(preflight.get("safe_to_replay")), "US source preflight is not safe to replay")
    _require(
        int(
            _as_object(
                preflight.get("source_inventory"),
                "source preflight inventory",
            ).get("history_source_count")
            or 0
        )
        == EXPECTED_HISTORY_PARTS,
        "historical source part count drifted",
    )
    raw_steps = preflight.get("replay_plan")
    _require(isinstance(raw_steps, list), "source preflight replay_plan must be a list")
    entries = [_source_entry(_as_object(item, "source replay step")) for item in raw_steps]
    _validate_source_inventory(entries)

    inventory_sha = _canonical_sha256(entries)
    suffix = [
        dict(item)
        for item in entries
        if start_sequence <= int(item["sequence"]) <= resolved_end
    ]
    _require(
        [int(item["sequence"]) for item in suffix]
        == list(range(start_sequence, resolved_end + 1)),
        "selected bulk replay suffix is not contiguous",
    )
    bridge = dict(entries[0])
    bridge["role"] = "PACKAGE1_TARGET_BRIDGE_REQUIRE_OR_ADOPT"
    for item in suffix:
        item["role"] = "BOUNDED_SUFFIX"
    execution_packages = [bridge, *suffix]

    root = str(raw_root.resolve())
    contract = {
        "plan_version": BULK_PLAN_VERSION,
        "read_only": True,
        "production_mutation_authorized": False,
        "execution_main": execution_main.lower(),
        "raw_root": root,
        "expected_history_parts": EXPECTED_HISTORY_PARTS,
        "accepted_source_count": EXPECTED_SOURCE_COUNT,
        "accepted_schema_manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
        "accepted_package2_anchor": anchor,
        "inventory_sha256": inventory_sha,
        "bridge_sequence": 1,
        "accepted_existing_target_sequence": 2,
        "start_sequence": start_sequence,
        "end_sequence": resolved_end,
        "suffix_package_count": len(suffix),
        "package_count": len(execution_packages),
        "packages": execution_packages,
    }
    plan_sha = _canonical_sha256(contract)
    return {
        **contract,
        "plan_sha256": plan_sha,
        "required_authority_token": f"GO #545 bounded US Application bulk replay {plan_sha}",
    }


def validate_bulk_plan(plan: dict[str, Any]) -> None:
    if str(plan.get("plan_version") or "") != BULK_PLAN_VERSION:
        raise RuntimeError("unsupported US target bulk plan version")
    if not bool(plan.get("read_only")) or bool(plan.get("production_mutation_authorized")):
        raise RuntimeError("US target bulk plan must remain read-only before explicit execution authority")
    packages = plan.get("packages")
    if not isinstance(packages, list) or not packages:
        raise RuntimeError("US target bulk plan packages are missing")
    start = int(plan.get("start_sequence") or 0)
    end = int(plan.get("end_sequence") or 0)
    if start < FIRST_BULK_SEQUENCE or end < start:
        raise RuntimeError("US target bulk plan range is invalid")
    expected_order = [1, *range(start, end + 1)]
    if [int(item.get("sequence") or 0) for item in packages if isinstance(item, dict)] != expected_order:
        raise RuntimeError("US target bulk plan execution order drifted")
    if str(packages[0].get("role") or "") != "PACKAGE1_TARGET_BRIDGE_REQUIRE_OR_ADOPT":
        raise RuntimeError("US target bulk plan Package 1 bridge role drifted")
    if str(plan.get("accepted_schema_manifest_sha256") or "").lower() != ACCEPTED_SCHEMA_MANIFEST_SHA256:
        raise RuntimeError("US target bulk plan schema manifest drifted")
    contract = {
        key: value
        for key, value in plan.items()
        if key not in {"plan_sha256", "required_authority_token"}
    }
    expected = _canonical_sha256(contract)
    if str(plan.get("plan_sha256") or "").lower() != expected:
        raise RuntimeError("US target bulk plan integrity SHA-256 mismatch")
    required = f"GO #545 bounded US Application bulk replay {expected}"
    if str(plan.get("required_authority_token") or "") != required:
        raise RuntimeError("US target bulk plan authority token binding drifted")
