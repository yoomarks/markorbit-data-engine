from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    STAGE_DATABASE,
    TARGET_DATABASE,
    WslNativeClickHouseClient,
    assert_package_unchanged,
    freeze_package,
)
from app.us.target_canary_journal import load_canary_journal
from app.us.target_canary_stage2 import (
    EXPECTED_PACKAGE_ID,
    EXPECTED_PACKAGE_KIND,
    EXPECTED_SCHEMA_MANIFEST_SHA256,
    EXPECTED_SHA256,
    EXPECTED_SIZE_BYTES,
    EXPECTED_SOURCE_EFFECTIVE_DATE,
    EXPECTED_SOURCE_PATH,
    EXPECTED_SOURCE_RANK,
)


RECONCILE_DECISION = "BOUNDED_US_APPLICATION_CANARY_STAGE2_PREPARED_RECONCILED"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _query_single(client: WslNativeClickHouseClient, sql: str) -> int:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("Stage 2 PREPARED reconciliation query returned unexpected shape")
    return int(rows[0][0])


def _short_names() -> set[str]:
    return {table.split(".", 1)[1] for table in APPLICATION_CANARY_TABLES}


def reconcile_prepared_state(
    journal: dict[str, Any],
    client: WslNativeClickHouseClient,
) -> dict[str, Any]:
    state = str(journal.get("state") or "")
    stage = journal.get("stage")
    commits = journal.get("commits")
    _require(state == "PREPARED", f"journal is not PREPARED: {state}")
    _require(isinstance(stage, dict), "journal stage block is invalid")
    _require(stage.get("status") == "NOT_STARTED", "journal stage has already started")
    _require(stage.get("row_counts") is None, "journal stage already records row counts")
    _require(isinstance(commits, dict), "journal commits block is invalid")
    for table in APPLICATION_CANARY_TABLES:
        item = commits.get(table)
        _require(isinstance(item, dict), f"journal commit block missing: {table}")
        _require(item.get("status") == "PENDING", f"journal commit is not PENDING: {table}")
        _require(item.get("expected_rows") is None, f"journal commit already has expected rows: {table}")
        _require(item.get("observed_rows") is None, f"journal commit already has observed rows: {table}")

    table_rows = client.query(
        f"SELECT name FROM system.tables WHERE database='{TARGET_DATABASE}' ORDER BY name"
    ).result_rows
    observed_names = []
    for row in table_rows:
        _require(len(row) == 1, "target table inventory returned unexpected row shape")
        observed_names.append(str(row[0]))
    extras = sorted(set(observed_names) - _short_names())
    _require(not extras, f"unexpected tables exist in {TARGET_DATABASE}: {extras}")

    total_rows = 0
    for short_name in observed_names:
        rows = _query_single(client, f"SELECT count() FROM {TARGET_DATABASE}.{short_name}")
        _require(rows == 0, f"PREPARED target table is not empty: {short_name} rows={rows}")
        total_rows += rows

    stage_tables = _query_single(
        client,
        f"SELECT count() FROM system.tables WHERE database='{STAGE_DATABASE}'",
    )
    _require(stage_tables == 0, f"PREPARED reconciliation found stage tables: {stage_tables}")

    app_parts = _query_single(
        client,
        f"SELECT count() FROM system.parts WHERE active AND database='{TARGET_DATABASE}'",
    )
    _require(app_parts == 0, f"PREPARED reconciliation found target active parts: {app_parts}")

    warm_parts = _query_single(
        client,
        "SELECT count() FROM system.parts WHERE active AND disk_name='warm_cn'",
    )
    _require(warm_parts == 0, f"PREPARED reconciliation found warm_cn active parts: {warm_parts}")

    return {
        "decision": RECONCILE_DECISION,
        "journal_state": state,
        "stage_status": stage.get("status"),
        "existing_application_tables": len(observed_names),
        "existing_application_table_names": observed_names,
        "target_rows": total_rows,
        "stage_tables": stage_tables,
        "application_active_parts": app_parts,
        "warm_cn_active_parts": warm_parts,
        "safe_to_resume": True,
    }


def reconcile_prepared_stage2(
    *,
    journal_path: Path,
    source_path: Path = Path(EXPECTED_SOURCE_PATH),
    client: WslNativeClickHouseClient | None = None,
) -> dict[str, Any]:
    package = freeze_package(
        source_path,
        expected_size=EXPECTED_SIZE_BYTES,
        expected_sha256=EXPECTED_SHA256,
        package_kind=EXPECTED_PACKAGE_KIND,
        source_rank=EXPECTED_SOURCE_RANK,
        source_effective_date=EXPECTED_SOURCE_EFFECTIVE_DATE,
        package_id=EXPECTED_PACKAGE_ID,
    )
    journal = load_canary_journal(
        journal_path,
        package=package,
        schema_manifest_sha256=EXPECTED_SCHEMA_MANIFEST_SHA256,
    )
    assert_package_unchanged(package)
    result = reconcile_prepared_state(journal, client or WslNativeClickHouseClient())
    assert_package_unchanged(package)
    result["journal_path"] = str(journal_path)
    result["source_path"] = str(package.path)
    result["source_sha256"] = package.sha256
    return result


def _print_result(payload: dict[str, Any]) -> None:
    for key in (
        "decision",
        "journal_state",
        "stage_status",
        "existing_application_tables",
        "target_rows",
        "stage_tables",
        "application_active_parts",
        "warm_cn_active_parts",
        "source_sha256",
        "safe_to_resume",
    ):
        if key in payload:
            print(f"{key}={payload[key]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only reconciliation for #526 Stage 2 PREPARED journal")
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = reconcile_prepared_stage2(journal_path=args.journal)
    except Exception as exc:
        print("decision=BLOCKED")
        print("safe_to_resume=False")
        print(f"error={exc}")
        raise SystemExit(1) from exc
    _print_result(result)


if __name__ == "__main__":
    main()
