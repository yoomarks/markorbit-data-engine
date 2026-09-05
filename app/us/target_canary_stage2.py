from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any
import uuid

from app.us.package_meta import infer_us_package_descriptor
from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    CANARY_RECEIPT_VERSION,
    TARGET_DATABASE,
    TARGET_STORAGE_POLICY,
    WslNativeClickHouseClient,
    assert_package_unchanged,
    build_target_schema_manifest,
    freeze_package,
    package_column_for_table,
    stage_ddl_from_manifest,
    stage_package_rows,
    validate_target_schema_manifest,
    write_receipt,
)
from app.us.target_canary_journal import (
    commit_staged_tables,
    initialize_canary_journal,
    load_canary_journal,
    mark_stage_complete,
    mark_stage_started,
)


STAGE2_EXECUTOR_VERSION = "US_APPLICATION_CANARY_STAGE2_PACKAGE2_V1"
STAGE2_AUTHORITY_TOKEN = "GO #526 Stage 2 bounded US Application canary"
STAGE2_ACCEPTED_DECISION = "BOUNDED_US_APPLICATION_CANARY_STAGE2_PACKAGE2_ACCEPTED"
STAGE1_ACCEPTED_MAIN = "d92f430913ef0684c386c2d7bcb767aa2d3284f8"
STAGE1_READY_DECISION = "BOUNDED_US_APPLICATION_CANARY_REVIEW_READY_FOR_OPERATOR_GO"
STAGE1_REVIEW_DECISION = "US_APPLICATION_CANARY_SOURCE_AND_SCHEMA_FROZEN"
ACCEPTED_PILOT_EVIDENCE_REF = "issue#340:5482170174"
EXPECTED_SEQUENCE = 2
EXPECTED_FILE_NAME = "apc18840407-20251231-02.zip"
EXPECTED_SOURCE_PATH = r"F:\MarkOrbitData\raw\incoming\us\apc18840407-20251231-02.zip"
EXPECTED_SIZE_BYTES = 5_997_232
EXPECTED_SHA256 = "96555bf13b6e8c2f2ede3433c88e4c600b7115ef3e4d7d22f28c8263cada60c7"
EXPECTED_PACKAGE_ID = uuid.UUID("aec9c8b5-f680-5881-94fb-71a1f8e44152")
EXPECTED_PACKAGE_KIND = "HISTORICAL_APPLICATIONS"
EXPECTED_PARTITION_DIMENSION = "COVERAGE_RANGE_PART"
EXPECTED_PARTITION_VALUE = "1884-04-07/2025-12-31#002"
EXPECTED_SOURCE_RANK = 1_020_251_231_002_002
EXPECTED_SOURCE_EFFECTIVE_DATE = date(2025, 12, 31)
EXPECTED_SCHEMA_MANIFEST_SHA256 = "ff801dea29e5f4b146e5e7ca24507abf4d7d498f977af64e1bc2e14267f63795"
EXPECTED_SUCCESS_PREFIX_COUNT = 1
EXPECTED_REMAINING_COUNT = 309


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Stage 2 expected object: {field}")
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} root must be an object")
    return payload


def _same_windows_path(actual: str, expected: str) -> bool:
    return actual.replace("/", "\\").rstrip("\\").lower() == expected.rstrip("\\").lower()


def validate_stage1_evidence(
    stage1_report: dict[str, Any],
    stage1_review: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind Stage 2 to the accepted Stage 1 evidence and nothing else."""
    _require(
        str(stage1_report.get("report_version") or "") == "PRODUCTION_US_APPLICATION_CANARY_STAGE1_V1",
        "Stage 1 report version mismatch",
    )
    _require(str(stage1_report.get("mode") or "") == "READ_ONLY", "Stage 1 report is not read-only")
    _require(
        str(stage1_report.get("decision") or "") == STAGE1_READY_DECISION,
        "Stage 1 report is not the accepted READY decision",
    )
    _require(
        str(stage1_report.get("expected_main") or "").lower() == STAGE1_ACCEPTED_MAIN,
        "Stage 1 accepted main SHA drifted",
    )

    report_canary = _object(stage1_report.get("canary"), "stage1_report.canary")
    report_target = _object(stage1_report.get("target"), "stage1_report.target")
    report_safety = _object(stage1_report.get("safety"), "stage1_report.safety")
    _require(int(report_canary.get("package_sequence") or 0) == EXPECTED_SEQUENCE, "Stage 1 package sequence drifted")
    _require(str(report_canary.get("package_file_name") or "") == EXPECTED_FILE_NAME, "Stage 1 package file drifted")
    _require(_same_windows_path(str(report_canary.get("package_path") or ""), EXPECTED_SOURCE_PATH), "Stage 1 package path drifted")
    _require(int(report_canary.get("package_size_bytes") or -1) == EXPECTED_SIZE_BYTES, "Stage 1 package size drifted")
    _require(str(report_canary.get("package_sha256") or "").lower() == EXPECTED_SHA256, "Stage 1 package SHA-256 drifted")
    _require(str(report_canary.get("package_id") or "") == str(EXPECTED_PACKAGE_ID), "Stage 1 package id drifted")
    _require(str(report_canary.get("package_kind") or "") == EXPECTED_PACKAGE_KIND, "Stage 1 package kind drifted")
    _require(int(report_canary.get("source_rank") or 0) == EXPECTED_SOURCE_RANK, "Stage 1 source rank drifted")
    _require(
        str(report_canary.get("schema_manifest_sha256") or "").lower() == EXPECTED_SCHEMA_MANIFEST_SHA256,
        "Stage 1 schema manifest SHA-256 drifted",
    )
    _require(str(report_canary.get("storage_policy") or "") == TARGET_STORAGE_POLICY, "Stage 1 target storage policy drifted")
    _require(str(report_canary.get("accepted_pilot_evidence_reference") or "") == ACCEPTED_PILOT_EVIDENCE_REF, "Stage 1 accepted pilot evidence drifted")
    _require(bool(report_canary.get("live_registry_read")) is False, "Stage 1 unexpectedly used live registry state")

    _require(int(report_target.get("required_application_tables_existing") or 0) == 0, "Stage 1 target was not empty")
    _require(int(report_target.get("hot_us_active_parts_before") or 0) == 0, "Stage 1 hot_us was not empty before review")
    _require(int(report_target.get("hot_us_active_parts_after") or 0) == 0, "Stage 1 hot_us changed during review")
    _require(int(report_target.get("warm_cn_active_parts_before") or 0) == 0, "Stage 1 warm_cn was not empty before review")
    _require(int(report_target.get("warm_cn_active_parts_after") or 0) == 0, "Stage 1 warm_cn changed during review")
    _require(bool(report_safety.get("read_only")), "Stage 1 safety report is not read_only=true")
    _require(not bool(report_safety.get("target_write_performed")), "Stage 1 unexpectedly wrote target data")
    _require(not bool(report_safety.get("source_data_write_performed")), "Stage 1 unexpectedly wrote source data")
    _require(not bool(report_safety.get("registry_write_performed")), "Stage 1 unexpectedly wrote registry data")
    _require(not bool(report_safety.get("cn_write_performed")), "Stage 1 unexpectedly wrote CN data")
    _require(not bool(report_safety.get("package_2_executed")), "Stage 1 already reports Package 2 execution")
    _require(not bool(report_safety.get("stage2_go_consumed")), "Stage 1 already reports Stage 2 GO consumed")

    _require(
        str(stage1_review.get("review_version") or "") == "US_TARGET_CANARY_STAGE1_REVIEW_V1",
        "Stage 1 source/schema review version mismatch",
    )
    _require(str(stage1_review.get("decision") or "") == STAGE1_REVIEW_DECISION, "Stage 1 source/schema review is not frozen")
    package = _object(stage1_review.get("package"), "stage1_review.package")
    continuity = _object(stage1_review.get("continuity"), "stage1_review.continuity")
    target = _object(stage1_review.get("target"), "stage1_review.target")
    manifest = _object(stage1_review.get("schema_manifest"), "stage1_review.schema_manifest")

    _require(int(continuity.get("success_prefix_count") or -1) == EXPECTED_SUCCESS_PREFIX_COUNT, "Stage 1 success prefix drifted")
    _require(int(continuity.get("remaining_count") or -1) == EXPECTED_REMAINING_COUNT, "Stage 1 remaining count drifted")
    _require(int(continuity.get("next_sequence") or 0) == EXPECTED_SEQUENCE, "Stage 1 next sequence drifted")
    _require(str(continuity.get("next_action") or "") == "REGISTER_AND_INGEST", "Stage 1 next action drifted")
    accepted = _object(continuity.get("accepted_pilot_evidence"), "stage1_review.continuity.accepted_pilot_evidence")
    _require(str(accepted.get("reference") or "") == ACCEPTED_PILOT_EVIDENCE_REF, "Stage 1 review pilot evidence drifted")

    _require(int(package.get("sequence") or 0) == EXPECTED_SEQUENCE, "Stage 1 review package sequence drifted")
    _require(str(package.get("file_name") or "") == EXPECTED_FILE_NAME, "Stage 1 review package file drifted")
    _require(_same_windows_path(str(package.get("path") or ""), EXPECTED_SOURCE_PATH), "Stage 1 review package path drifted")
    _require(int(package.get("size_bytes") or -1) == EXPECTED_SIZE_BYTES, "Stage 1 review package size drifted")
    _require(str(package.get("sha256") or "").lower() == EXPECTED_SHA256, "Stage 1 review package SHA-256 drifted")
    _require(str(package.get("package_id") or "") == str(EXPECTED_PACKAGE_ID), "Stage 1 review package id drifted")
    _require(str(package.get("package_kind") or "") == EXPECTED_PACKAGE_KIND, "Stage 1 review package kind drifted")
    _require(str(package.get("partition_dimension") or "") == EXPECTED_PARTITION_DIMENSION, "Stage 1 review partition dimension drifted")
    _require(str(package.get("partition_value") or "") == EXPECTED_PARTITION_VALUE, "Stage 1 review partition value drifted")
    _require(str(package.get("source_period_end") or "") == EXPECTED_SOURCE_EFFECTIVE_DATE.isoformat(), "Stage 1 review source effective date drifted")
    _require(int(package.get("source_rank") or 0) == EXPECTED_SOURCE_RANK, "Stage 1 review source rank drifted")

    _require(str(target.get("storage_policy") or "") == TARGET_STORAGE_POLICY, "Stage 1 review target policy drifted")
    _require(list(target.get("required_tables") or []) == list(APPLICATION_CANARY_TABLES), "Stage 1 review target table contract drifted")
    validate_target_schema_manifest(manifest)
    _require(str(manifest.get("schema_version") or "") == "US_M1.4_TARGET_HOT_US_V1", "Stage 1 schema version drifted")
    _require(str(manifest.get("sha256") or "").lower() == EXPECTED_SCHEMA_MANIFEST_SHA256, "Stage 1 review schema manifest SHA-256 drifted")

    _require(str(report_canary.get("package_sha256") or "").lower() == str(package.get("sha256") or "").lower(), "Stage 1 report/review package SHA disagreement")
    _require(str(report_canary.get("schema_manifest_sha256") or "").lower() == str(manifest.get("sha256") or "").lower(), "Stage 1 report/review schema SHA disagreement")
    return package, manifest


def _query_single(client: WslNativeClickHouseClient, sql: str) -> int:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("Stage 2 target query returned unexpected single-value shape")
    return int(rows[0][0])


def _table_names_sql() -> str:
    return ",".join("'" + table.split(".", 1)[1] + "'" for table in APPLICATION_CANARY_TABLES)


def _target_required_table_count(client: WslNativeClickHouseClient) -> int:
    return _query_single(
        client,
        f"SELECT count() FROM system.tables WHERE database='{TARGET_DATABASE}' AND name IN ({_table_names_sql()})",
    )


def _read_target_manifest(client: WslNativeClickHouseClient) -> dict[str, object]:
    rows = client.query(
        f"SELECT name,create_table_query FROM system.tables WHERE database='{TARGET_DATABASE}' "
        f"AND name IN ({_table_names_sql()}) ORDER BY name"
    ).result_rows
    if len(rows) != len(APPLICATION_CANARY_TABLES):
        raise RuntimeError(
            "Stage 2 target schema table count mismatch: "
            f"expected={len(APPLICATION_CANARY_TABLES)} actual={len(rows)}"
        )
    show_create: dict[str, str] = {}
    for row in rows:
        if len(row) != 2:
            raise RuntimeError("Stage 2 target schema query returned unexpected row shape")
        show_create[f"{TARGET_DATABASE}.{row[0]}"] = str(row[1])
    manifest = build_target_schema_manifest(show_create)
    validate_target_schema_manifest(manifest)
    if str(manifest.get("sha256") or "").lower() != EXPECTED_SCHEMA_MANIFEST_SHA256:
        raise RuntimeError(
            "Stage 2 target schema manifest differs from accepted Stage 1 manifest: "
            f"actual={manifest.get('sha256')}"
        )
    return manifest


def _final_package_counts(client: WslNativeClickHouseClient, package_id: uuid.UUID) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in APPLICATION_CANARY_TABLES:
        package_column = package_column_for_table(table)
        result[table] = _query_single(
            client,
            f"SELECT count() FROM {table} WHERE {package_column}=toUUID('{package_id}')",
        )
    return result


def _require_zero_final_package_rows(client: WslNativeClickHouseClient, package_id: uuid.UUID) -> None:
    counts = _final_package_counts(client, package_id)
    nonzero = {table: count for table, count in counts.items() if count != 0}
    if nonzero:
        raise RuntimeError(f"Stage 2 found pre-existing Package 2 target rows: {nonzero}")


def _verify_storage_after_commit(client: WslNativeClickHouseClient) -> dict[str, int]:
    non_hot = _query_single(
        client,
        f"SELECT count() FROM system.parts WHERE active AND database='{TARGET_DATABASE}' "
        f"AND table IN ({_table_names_sql()}) AND disk_name!='hot_us'",
    )
    warm = _query_single(client, "SELECT count() FROM system.parts WHERE active AND disk_name='warm_cn'")
    _require(non_hot == 0, f"Stage 2 Application target parts escaped hot_us: {non_hot}")
    _require(warm == 0, f"Stage 2 wrote or observed unexpected active warm_cn parts: {warm}")
    return {"application_non_hot_active_parts": non_hot, "warm_cn_active_parts": warm}


def execute_stage2(
    *,
    stage1_report_path: Path,
    stage1_review_path: Path,
    journal_path: Path,
    receipt_path: Path,
    authority_token: str,
    client: WslNativeClickHouseClient | None = None,
) -> dict[str, Any]:
    _require(authority_token == STAGE2_AUTHORITY_TOKEN, "Stage 2 explicit authority token mismatch")
    report = _load_json(stage1_report_path, "Stage 1 production report")
    review = _load_json(stage1_review_path, "Stage 1 source/schema review")
    package_evidence, manifest = validate_stage1_evidence(report, review)

    source_path = Path(str(package_evidence["path"]))
    descriptor = infer_us_package_descriptor(source_path)
    _require(descriptor.package_kind == EXPECTED_PACKAGE_KIND, "Package 2 descriptor kind drifted at execution")
    _require(descriptor.partition_dimension == EXPECTED_PARTITION_DIMENSION, "Package 2 descriptor dimension drifted at execution")
    _require(descriptor.partition_value == EXPECTED_PARTITION_VALUE, "Package 2 descriptor partition drifted at execution")
    _require(descriptor.source_period_end == EXPECTED_SOURCE_EFFECTIVE_DATE, "Package 2 descriptor effective date drifted at execution")
    _require(descriptor.source_rank(EXPECTED_SEQUENCE) == EXPECTED_SOURCE_RANK, "Package 2 descriptor source rank drifted at execution")

    package = freeze_package(
        source_path,
        expected_size=EXPECTED_SIZE_BYTES,
        expected_sha256=EXPECTED_SHA256,
        package_kind=EXPECTED_PACKAGE_KIND,
        source_rank=EXPECTED_SOURCE_RANK,
        source_effective_date=EXPECTED_SOURCE_EFFECTIVE_DATE,
        package_id=EXPECTED_PACKAGE_ID,
    )
    _require(package.file_name == EXPECTED_FILE_NAME, "Package 2 filename drifted at execution")
    assert_package_unchanged(package)

    target = client or WslNativeClickHouseClient()
    schema_sha = str(manifest["sha256"]).lower()

    if journal_path.exists():
        journal = load_canary_journal(
            journal_path,
            package=package,
            schema_manifest_sha256=schema_sha,
        )
        state = str(journal.get("state") or "")
        if state == "STAGING":
            raise RuntimeError(
                "Stage 2 journal is STAGING after an interrupted stage write; "
                "explicit read-only stage reconciliation is required before any retry"
            )
        if state == "PREPARED":
            for statement in manifest["statements"]:
                target.command(str(statement))
            _read_target_manifest(target)
            _require_zero_final_package_rows(target, package.package_id)
            mark_stage_started(
                journal_path,
                package=package,
                schema_manifest_sha256=schema_sha,
            )
            for statement in stage_ddl_from_manifest(manifest, package):
                target.command(statement)
            staged_counts = stage_package_rows(target, package)
            mark_stage_complete(
                target,
                journal_path,
                package=package,
                schema_manifest_sha256=schema_sha,
                expected_row_counts=staged_counts,
            )
        elif state not in {"STAGED", "COMMITTING", "COMPLETE"}:
            raise RuntimeError(f"Stage 2 journal state is not resumable: {state}")
    else:
        _require(
            _target_required_table_count(target) == 0,
            "First Stage 2 target mutation requires all Application final tables absent",
        )
        initialize_canary_journal(
            journal_path,
            package=package,
            schema_manifest_sha256=schema_sha,
        )
        for statement in manifest["statements"]:
            target.command(str(statement))
        _read_target_manifest(target)
        _require_zero_final_package_rows(target, package.package_id)
        mark_stage_started(
            journal_path,
            package=package,
            schema_manifest_sha256=schema_sha,
        )
        for statement in stage_ddl_from_manifest(manifest, package):
            target.command(statement)
        staged_counts = stage_package_rows(target, package)
        mark_stage_complete(
            target,
            journal_path,
            package=package,
            schema_manifest_sha256=schema_sha,
            expected_row_counts=staged_counts,
        )

    assert_package_unchanged(package)
    _read_target_manifest(target)
    journal = commit_staged_tables(
        target,
        journal_path,
        package=package,
        schema_manifest_sha256=schema_sha,
    )
    _require(str(journal.get("state") or "") == "COMPLETE", "Stage 2 journal did not reach COMPLETE")

    expected_counts = {
        table: int(_object(journal["commits"][table], f"journal.commits.{table}").get("expected_rows") or 0)
        for table in APPLICATION_CANARY_TABLES
    }
    observed_counts = _final_package_counts(target, package.package_id)
    _require(observed_counts == expected_counts, "Stage 2 final Package 2 counts differ from journal")
    storage = _verify_storage_after_commit(target)
    assert_package_unchanged(package)

    receipt: dict[str, Any] = {
        "receipt_version": CANARY_RECEIPT_VERSION,
        "stage2_executor_version": STAGE2_EXECUTOR_VERSION,
        "decision": STAGE2_ACCEPTED_DECISION,
        "authority": {
            "issue": 526,
            "stage": 2,
            "bounded_package_sequence": EXPECTED_SEQUENCE,
            "consumed": True,
        },
        "stage1": {
            "accepted_main": STAGE1_ACCEPTED_MAIN,
            "decision": STAGE1_READY_DECISION,
            "accepted_pilot_evidence_reference": ACCEPTED_PILOT_EVIDENCE_REF,
            "report_path": str(stage1_report_path),
            "review_path": str(stage1_review_path),
        },
        "package": {
            **package.as_dict(),
            "sequence": EXPECTED_SEQUENCE,
            "partition_dimension": EXPECTED_PARTITION_DIMENSION,
            "partition_value": EXPECTED_PARTITION_VALUE,
        },
        "schema": {
            "version": manifest["schema_version"],
            "manifest_sha256": schema_sha,
            "storage_policy": TARGET_STORAGE_POLICY,
            "tables": list(APPLICATION_CANARY_TABLES),
        },
        "journal": {
            "path": str(journal_path),
            "state": journal["state"],
            "revision": journal["revision"],
            "expected_row_counts": expected_counts,
            "observed_row_counts": observed_counts,
        },
        "storage": storage,
        "safety": {
            "source_file_preserved": True,
            "registry_write_performed": False,
            "cn_write_performed": False,
            "package_3_executed": False,
            "full_corpus_executed": False,
            "automatic_next_package": False,
        },
    }
    write_receipt(receipt_path, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute #526 Stage 2 exact Package 2 target canary")
    parser.add_argument("--stage1-report", type=Path, required=True)
    parser.add_argument("--stage1-review", type=Path, required=True)
    parser.add_argument("--journal-json", type=Path, required=True)
    parser.add_argument("--receipt-json", type=Path, required=True)
    parser.add_argument("--authority-token", required=True)
    args = parser.parse_args()

    receipt = execute_stage2(
        stage1_report_path=args.stage1_report,
        stage1_review_path=args.stage1_review,
        journal_path=args.journal_json,
        receipt_path=args.receipt_json,
        authority_token=args.authority_token,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
