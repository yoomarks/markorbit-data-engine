from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from app.us.target_bulk_batch import validate_batch_manifest
from app.us.target_bulk_journal import load_bulk_journal
from app.us.target_bulk_plan import (
    ACCEPTED_PACKAGE2_DECISION,
    ACCEPTED_PACKAGE2_FILE,
    ACCEPTED_PACKAGE2_ID,
    ACCEPTED_PACKAGE2_SHA256,
    ACCEPTED_SCHEMA_MANIFEST_SHA256,
    validate_bulk_plan,
)
from app.us.target_bulk_replay import (
    BulkTargetClient,
    _final_package_counts,
    _frozen_from_plan,
    _read_target_manifest,
    _stage_table_count,
    _validated_final_counts,
    _verify_complete_canary,
    _verify_hot_us_headroom,
    _verify_storage,
)
from app.us.target_canary import assert_package_unchanged, write_receipt


BATCH_FINAL_AUDIT_VERSION = "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V1"


def _canary_journal_path(state_dir: Path, item: dict[str, Any]) -> Path:
    sequence = int(item["sequence"])
    token = str(item["sha256"])[:16]
    return state_dir / f"package_{sequence:03d}_{token}.canary.json"


def _verify_frozen_package2_anchor(
    client: BulkTargetClient,
    *,
    master_plan: dict[str, Any],
) -> dict[str, int]:
    anchor = master_plan.get("accepted_package2_anchor")
    if not isinstance(anchor, dict):
        raise RuntimeError("US target bulk batch audit Package 2 anchor is missing")
    expected_identity = {
        "decision": ACCEPTED_PACKAGE2_DECISION,
        "sequence": 2,
        "file_name": ACCEPTED_PACKAGE2_FILE,
        "sha256": ACCEPTED_PACKAGE2_SHA256,
        "package_id": ACCEPTED_PACKAGE2_ID,
        "schema_manifest_sha256": ACCEPTED_SCHEMA_MANIFEST_SHA256,
    }
    for field, expected in expected_identity.items():
        actual = anchor.get(field)
        if field in {"sha256", "schema_manifest_sha256"}:
            actual = str(actual or "").lower()
        if actual != expected:
            raise RuntimeError(f"US target bulk batch audit Package 2 anchor drifted: {field}")

    expected_counts = _validated_final_counts(anchor.get("expected_row_counts"))
    observed_counts = _final_package_counts(client, uuid.UUID(ACCEPTED_PACKAGE2_ID))
    if observed_counts != expected_counts:
        raise RuntimeError(
            "US target bulk batch audit Package 2 target counts drifted: "
            f"expected={expected_counts} observed={observed_counts}"
        )

    source = master_plan.get("accepted_package2_source")
    if not isinstance(source, dict):
        raise RuntimeError("US target bulk batch audit Package 2 source identity is missing")
    package = _frozen_from_plan(source)
    assert_package_unchanged(package)
    return expected_counts


def audit_target_bulk_batch(
    *,
    master_plan: dict[str, Any],
    batch_manifest: dict[str, Any],
    state_dir: Path,
    client: BulkTargetClient | None = None,
) -> dict[str, Any]:
    """Read-only final audit across the complete approved master batch."""
    validate_bulk_plan(master_plan)
    validate_batch_manifest(batch_manifest, master_plan=master_plan)
    state_dir = state_dir.resolve()
    target = client or BulkTargetClient()

    start = int(master_plan["start_sequence"])
    end = int(master_plan["end_sequence"])
    expected_suffix = list(range(start, end + 1))
    children = batch_manifest["children"]
    observed_suffix = [int(item["sequence"]) for item in children]
    if observed_suffix != expected_suffix:
        raise RuntimeError("US target bulk batch audit child coverage is not contiguous")

    child_journals: dict[str, str] = {}
    for child in children:
        sequence = int(child["sequence"])
        child_path = Path(str(child.get("plan_path") or ""))
        if not child_path.is_file():
            raise RuntimeError(f"US target bulk batch audit child plan is missing: {sequence}")
        import json

        try:
            child_plan = json.loads(child_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"US target bulk batch audit child plan is unreadable: {sequence}"
            ) from exc
        if not isinstance(child_plan, dict):
            raise RuntimeError(f"US target bulk batch audit child plan is malformed: {sequence}")
        validate_bulk_plan(child_plan)
        if child_plan["plan_sha256"] != child["plan_sha256"]:
            raise RuntimeError(f"US target bulk batch audit child plan SHA drifted: {sequence}")
        journal_path = state_dir / f"bulk_{child['plan_sha256']}.journal.json"
        if not journal_path.is_file():
            raise RuntimeError(f"US target bulk batch audit child journal is missing: {sequence}")
        journal = load_bulk_journal(journal_path, plan=child_plan)
        if journal.get("state") != "COMPLETE":
            raise RuntimeError(
                f"US target bulk batch audit child journal is not COMPLETE: {sequence}"
            )
        for package_sequence in (1, sequence):
            package_state = journal["packages"].get(str(package_sequence))
            if not isinstance(package_state, dict) or package_state.get("status") != "COMPLETE":
                raise RuntimeError(
                    "US target bulk batch audit child package checkpoint is incomplete: "
                    f"child={sequence} package={package_sequence}"
                )
            if not bool(package_state.get("stage_cleanup_complete")):
                raise RuntimeError(
                    "US target bulk batch audit child staging cleanup is incomplete: "
                    f"child={sequence} package={package_sequence}"
                )
        child_journals[str(sequence)] = str(journal_path)

    package_rows: dict[str, int] = {}
    package_table_rows: dict[str, dict[str, int]] = {}
    for item in master_plan["packages"]:
        sequence = int(item["sequence"])
        package = _frozen_from_plan(item)
        journal_path = _canary_journal_path(state_dir, item)
        if not journal_path.is_file():
            raise RuntimeError(
                f"US target bulk batch audit package canary journal is missing: {sequence}"
            )
        counts = _verify_complete_canary(
            target,
            journal_path=journal_path,
            package=package,
        )
        if _stage_table_count(target, package) != 0:
            raise RuntimeError(
                f"US target bulk batch audit found stale staging tables: {sequence}"
            )
        package_table_rows[str(sequence)] = counts
        package_rows[str(sequence)] = sum(counts.values())

    package2_counts = _verify_frozen_package2_anchor(target, master_plan=master_plan)
    storage = _verify_storage(target)
    schema = _read_target_manifest(target)
    headroom = _verify_hot_us_headroom(target)

    verified_sequences = [1, 2, *expected_suffix]
    if start == 3 and end == 310 and verified_sequences != list(range(1, 311)):
        raise RuntimeError("US target bulk batch audit full-corpus sequence coverage drifted")

    return {
        "audit_version": BATCH_FINAL_AUDIT_VERSION,
        "master_plan_sha256": master_plan["plan_sha256"],
        "batch_manifest_sha256": batch_manifest["manifest_sha256"],
        "inventory_sha256": master_plan["inventory_sha256"],
        "execution_main": master_plan["execution_main"],
        "verified_sequences": verified_sequences,
        "verified_suffix_sequences": expected_suffix,
        "child_journals": child_journals,
        "package_total_rows": package_rows,
        "package_table_rows": package_table_rows,
        "package2_table_rows": package2_counts,
        "selected_total_rows": sum(package_rows.values()) + sum(package2_counts.values()),
        "storage": storage,
        "schema_manifest_sha256": schema["sha256"],
        "hot_us_headroom": headroom,
        "source_files_preserved": True,
        "staging_cleanup_complete": True,
        "full_accepted_source_corpus_on_target": start == 3 and end == 310,
        "automatic_next_package": False,
    }


def write_target_bulk_batch_audit(path: Path, audit: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_receipt(path, audit)
