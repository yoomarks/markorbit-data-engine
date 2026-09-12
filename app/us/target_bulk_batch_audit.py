from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

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
    _expected_counts_from_canary,
    _frozen_from_plan,
    _read_target_manifest,
    _verify_hot_us_headroom,
    _verify_storage,
)
from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    STAGE_DATABASE,
    assert_package_unchanged,
    package_column_for_table,
    write_receipt,
)
from app.us.target_canary_journal import REPLACING_VISIBLE_KEYS, load_canary_journal


BATCH_FINAL_AUDIT_VERSION = "US_APPLICATION_TARGET_BULK_BATCH_AUDIT_V2"
_CANARY_JOURNAL_RE = re.compile(
    r"^package_(?P<sequence>\d{3})_(?P<sha>[0-9a-f]{16})\.canary\.json$"
)


def _query_single(client: BulkTargetClient, sql: str) -> int:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("US target full-corpus audit scalar query returned unexpected shape")
    return int(rows[0][0])


def _verify_frozen_package2_anchor(
    client: BulkTargetClient,
    *,
    master_plan: dict[str, Any],
) -> dict[str, Any]:
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

    expected_counts = {
        table: int(anchor["expected_row_counts"][table])
        for table in APPLICATION_CANARY_TABLES
    }
    source = master_plan.get("accepted_package2_source")
    if not isinstance(source, dict):
        raise RuntimeError("US target bulk batch audit Package 2 source identity is missing")
    package = _frozen_from_plan(source)
    assert_package_unchanged(package)
    return {
        "sequence": 2,
        "package_id": str(package.package_id),
        "file_name": package.file_name,
        "sha256": package.sha256,
        "accepted_counts": expected_counts,
    }


def _discover_full_corpus_evidence(
    *,
    state_dir: Path,
    accepted_source_count: int = 310,
) -> dict[int, dict[str, Any]]:
    full_sequences = frozenset([1, *range(3, accepted_source_count + 1)])
    paths_by_sequence: dict[int, Path] = {}
    for path in sorted(state_dir.glob("package_*.canary.json")):
        match = _CANARY_JOURNAL_RE.fullmatch(path.name)
        if match is None:
            continue
        sequence = int(match.group("sequence"))
        if sequence in paths_by_sequence:
            raise RuntimeError(
                f"US target full-corpus audit found duplicate canary journal: {sequence}"
            )
        paths_by_sequence[sequence] = path

    observed_sequences = set(paths_by_sequence)
    missing = sorted(full_sequences - observed_sequences)
    unexpected = sorted(observed_sequences - full_sequences)
    if missing or unexpected:
        raise RuntimeError(
            "US target full-corpus canary coverage drifted: "
            f"missing={missing} unexpected={unexpected}"
        )

    evidence: dict[int, dict[str, Any]] = {}
    for sequence in sorted(full_sequences):
        path = paths_by_sequence[sequence]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"US target full-corpus canary journal is unreadable: {sequence}"
            ) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("package"), dict):
            raise RuntimeError(
                f"US target full-corpus canary journal package binding is malformed: {sequence}"
            )
        package_binding = dict(raw["package"])
        sha = str(package_binding.get("sha256") or "").lower()
        match = _CANARY_JOURNAL_RE.fullmatch(path.name)
        assert match is not None
        if sha[:16] != match.group("sha"):
            raise RuntimeError(
                f"US target full-corpus canary filename SHA binding drifted: {sequence}"
            )
        package = _frozen_from_plan(package_binding)
        journal = load_canary_journal(
            path,
            package=package,
            schema_manifest_sha256=ACCEPTED_SCHEMA_MANIFEST_SHA256,
        )
        if journal.get("state") != "COMPLETE":
            raise RuntimeError(
                f"US target full-corpus canary journal is not COMPLETE: {sequence}"
            )
        evidence[sequence] = {
            "sequence": sequence,
            "package_id": str(package.package_id),
            "file_name": package.file_name,
            "sha256": package.sha256,
            "source_rank": package.source_rank,
            "journal_path": str(path),
            "accepted_counts": _expected_counts_from_canary(journal),
        }
    return evidence


def _verify_master_plan_bindings(
    master_plan: dict[str, Any],
    *,
    evidence: dict[int, dict[str, Any]],
) -> None:
    for item in master_plan["packages"]:
        sequence = int(item["sequence"])
        observed = evidence.get(sequence)
        if observed is None:
            raise RuntimeError(
                f"US target full-corpus audit current master package is missing: {sequence}"
            )
        if str(item["sha256"]).lower() != str(observed["sha256"]).lower():
            raise RuntimeError(
                f"US target full-corpus audit current master package SHA drifted: {sequence}"
            )
        if str(item["package_id"]) != str(observed["package_id"]):
            raise RuntimeError(
                f"US target full-corpus audit current master package ID drifted: {sequence}"
            )


def _verify_stage_evidence(
    client: BulkTargetClient,
    *,
    package2: dict[str, Any],
) -> dict[str, int]:
    token = ACCEPTED_PACKAGE2_SHA256[:16]
    expected_names = {
        f"{table.split('.', 1)[1]}__{token}": table
        for table in APPLICATION_CANARY_TABLES
    }
    prefixes = [table.split(".", 1)[1] + "__" for table in APPLICATION_CANARY_TABLES]
    predicate = " OR ".join(f"startsWith(name, '{prefix}')" for prefix in prefixes)
    rows = client.query(
        f"SELECT name FROM system.tables WHERE database='{STAGE_DATABASE}' "
        f"AND ({predicate}) ORDER BY name"
    ).result_rows
    observed_names = {str(row[0]) for row in rows}
    if observed_names != set(expected_names):
        raise RuntimeError(
            "US target full-corpus staging evidence drifted: "
            f"expected={sorted(expected_names)} observed={sorted(observed_names)}"
        )

    counts: dict[str, int] = {}
    for stage_name, target_table in expected_names.items():
        observed = _query_single(
            client,
            f"SELECT count() FROM {STAGE_DATABASE}.{stage_name}",
        )
        expected = int(package2["accepted_counts"][target_table])
        if observed != expected:
            raise RuntimeError(
                "US target full-corpus Package 2 frozen stage count drifted: "
                f"table={target_table} expected={expected} observed={observed}"
            )
        counts[target_table] = observed
    return counts


def _grouped_current_package_counts(
    client: BulkTargetClient,
    *,
    table: str,
) -> dict[str, int]:
    package_column = package_column_for_table(table)
    final = " FINAL" if table in REPLACING_VISIBLE_KEYS else ""
    rows = client.query(
        f"SELECT toString({package_column}), count() FROM {table}{final} "
        f"GROUP BY {package_column}"
    ).result_rows
    result: dict[str, int] = {}
    for row in rows:
        if len(row) != 2:
            raise RuntimeError(
                f"US target full-corpus grouped count returned unexpected shape: {table}"
            )
        package_id = str(row[0])
        count = int(row[1])
        if package_id in result:
            raise RuntimeError(
                f"US target full-corpus grouped count duplicated package ID: {table}"
            )
        result[package_id] = count
    return result


def _table_has_deleted_column(
    client: BulkTargetClient,
    *,
    table: str,
) -> bool:
    database, short_table = table.split(".", 1)
    return (
        _query_single(
            client,
            "SELECT count() FROM system.columns "
            f"WHERE database='{database}' AND table='{short_table}' AND name='is_deleted'",
        )
        == 1
    )


def _final_package_replacing_counts(
    client: BulkTargetClient,
    *,
    table: str,
    package_id: str,
) -> dict[str, int | bool]:
    keys = REPLACING_VISIBLE_KEYS.get(table)
    if not keys:
        raise ValueError(f"table is not a replacing Application table: {table}")
    package_column = package_column_for_table(table)
    expression = "tuple(" + ", ".join(keys) + ")"
    predicate = f"{package_column}=toUUID('{package_id}')"
    all_unique = _query_single(
        client,
        f"SELECT uniqExact({expression}) FROM {table} WHERE {predicate}",
    )
    has_deleted = _table_has_deleted_column(client, table=table)
    deleted_unique = 0
    if has_deleted:
        deleted_unique = _query_single(
            client,
            f"SELECT uniqExactIf({expression}, is_deleted=1) FROM {table} WHERE {predicate}",
        )
    return {
        "all_unique": all_unique,
        "deleted_unique": deleted_unique,
        "has_deleted": has_deleted,
    }



def _verify_target_full_corpus_attribution(
    client: BulkTargetClient,
    *,
    evidence: dict[int, dict[str, Any]],
    package2: dict[str, Any],
    accepted_source_count: int = 310,
) -> dict[str, Any]:
    all_evidence = {**evidence, 2: package2}
    expected_by_id: dict[str, dict[str, Any]] = {}
    for sequence in range(1, accepted_source_count + 1):
        item = all_evidence.get(sequence)
        if item is None:
            raise RuntimeError(
                f"US target full-corpus accepted package evidence is missing: {sequence}"
            )
        package_id = str(item["package_id"])
        if package_id in expected_by_id:
            raise RuntimeError(
                f"US target full-corpus accepted package ID is duplicated: {package_id}"
            )
        expected_by_id[package_id] = item

    package_current_rows: dict[str, dict[str, int]] = {
        str(sequence): {} for sequence in range(1, accepted_source_count + 1)
    }
    table_summary: dict[str, dict[str, Any]] = {}
    final_package_state: dict[str, dict[str, int | bool]] = {}
    final_item = all_evidence[accepted_source_count]
    final_package_id = str(final_item["package_id"])

    for table in APPLICATION_CANARY_TABLES:
        replacing = table in REPLACING_VISIBLE_KEYS
        observed_by_id = _grouped_current_package_counts(client, table=table)
        unknown = sorted(set(observed_by_id) - set(expected_by_id))
        if unknown:
            raise RuntimeError(
                "US target full-corpus target rows reference unaccepted package IDs: "
                f"table={table} package_ids={unknown[:10]}"
            )

        expected_total = 0
        live_total = 0
        for package_id, item in expected_by_id.items():
            sequence = int(item["sequence"])
            expected = int(item["accepted_counts"][table])
            live = int(observed_by_id.get(package_id, 0))
            expected_total += expected
            live_total += live
            package_current_rows[str(sequence)][table] = live

            if replacing:
                if live > expected:
                    raise RuntimeError(
                        "US target full-corpus replacement attribution exceeds durable "
                        f"acceptance: table={table} sequence={sequence} "
                        f"expected_max={expected} observed_live={live}"
                    )
            elif live != expected:
                raise RuntimeError(
                    "US target full-corpus append-only attribution drifted: "
                    f"table={table} sequence={sequence} "
                    f"expected={expected} observed={live}"
                )

        final_deleted = 0
        if replacing:
            expected_final = int(final_item["accepted_counts"][table])
            final_live = int(observed_by_id.get(final_package_id, 0))
            final_state = _final_package_replacing_counts(
                client,
                table=table,
                package_id=final_package_id,
            )
            final_unique = int(final_state["all_unique"])
            final_deleted = int(final_state["deleted_unique"])
            has_deleted = bool(final_state["has_deleted"])
            if final_unique != expected_final:
                raise RuntimeError(
                    "US target full-corpus final package logical-key drifted: "
                    f"table={table} expected={expected_final} observed_unique={final_unique}"
                )
            if has_deleted:
                if final_live + final_deleted != expected_final:
                    raise RuntimeError(
                        "US target full-corpus final package live/tombstone drifted: "
                        f"table={table} expected={expected_final} live={final_live} "
                        f"deleted={final_deleted}"
                    )
            elif final_live != expected_final:
                raise RuntimeError(
                    "US target full-corpus final package replacement drifted: "
                    f"table={table} expected={expected_final} observed={final_live}"
                )
            final_package_state[table] = {
                "accepted_unique": expected_final,
                "observed_unique": final_unique,
                "live": final_live,
                "deleted": final_deleted,
                "has_deleted": has_deleted,
            }

        table_summary[table] = {
            "replacing": replacing,
            "accepted_rows_across_packages": expected_total,
            "current_live_rows_attributed_to_accepted_packages": live_total,
            "superseded_or_deleted_accepted_rows": (
                expected_total - live_total if replacing else 0
            ),
            "final_package_deleted_rows": final_deleted,
        }

    raw_current_serials = _query_single(
        client,
        "SELECT uniqExact(serial_number) FROM markorbit_facts.us_case_current",
    )
    observed_serials = _query_single(
        client,
        "SELECT uniqExact(serial_number) FROM markorbit_facts.us_case_observation_history",
    )
    if raw_current_serials != observed_serials:
        raise RuntimeError(
            "US target full-corpus case serial coverage drifted: "
            f"raw_current={raw_current_serials} observed={observed_serials}"
        )

    return {
        "table_summary": table_summary,
        "package_current_rows": package_current_rows,
        "final_package_state": final_package_state,
        "case_serial_coverage": {
            "raw_current_unique_serials": raw_current_serials,
            "observed_unique_serials": observed_serials,
        },
    }


def audit_target_bulk_batch(
    *,
    master_plan: dict[str, Any],
    batch_manifest: dict[str, Any],
    state_dir: Path,
    client: BulkTargetClient | None = None,
) -> dict[str, Any]:
    """Read-only final audit across all accepted US Application source packages."""
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

    accepted_source_count = int(master_plan["accepted_source_count"])
    evidence = _discover_full_corpus_evidence(state_dir=state_dir, accepted_source_count=accepted_source_count)
    _verify_master_plan_bindings(master_plan, evidence=evidence)
    package2 = _verify_frozen_package2_anchor(target, master_plan=master_plan)
    package2_stage_counts = _verify_stage_evidence(target, package2=package2)
    attribution = _verify_target_full_corpus_attribution(
        target,
        evidence=evidence,
        package2=package2,
        accepted_source_count=accepted_source_count,
    )

    storage = _verify_storage(target)
    schema = _read_target_manifest(target)
    headroom = _verify_hot_us_headroom(target)

    verified_sequences = list(range(1, accepted_source_count + 1))
    package_table_rows = {
        str(sequence): evidence[sequence]["accepted_counts"]
        for sequence in sorted(evidence)
    }
    package_table_rows["2"] = package2["accepted_counts"]
    package_rows = {
        sequence: sum(counts.values())
        for sequence, counts in package_table_rows.items()
    }

    return {
        "audit_version": BATCH_FINAL_AUDIT_VERSION,
        "master_plan_sha256": master_plan["plan_sha256"],
        "batch_manifest_sha256": batch_manifest["manifest_sha256"],
        "inventory_sha256": master_plan["inventory_sha256"],
        "execution_main": master_plan["execution_main"],
        "verified_sequences": verified_sequences,
        "verified_suffix_sequences": expected_suffix,
        "child_journals": child_journals,
        "full_corpus_canary_journals": {
            str(sequence): evidence[sequence]["journal_path"]
            for sequence in sorted(evidence)
        },
        "package_total_rows": package_rows,
        "package_table_rows": package_table_rows,
        "package_current_rows": attribution["package_current_rows"],
        "target_table_summary": attribution["table_summary"],
        "case_serial_coverage": attribution["case_serial_coverage"],
        "package2_frozen_stage_rows": package2_stage_counts,
        "package2_frozen_stage_preserved": True,
        "storage": storage,
        "schema_manifest_sha256": schema["sha256"],
        "hot_us_headroom": headroom,
        "source_files_preserved": True,
        "staging_cleanup_complete": True,
        "full_accepted_source_corpus_on_target": True,
        "automatic_next_package": False,
    }


def write_target_bulk_batch_audit(path: Path, audit: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_receipt(path, audit)
