from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.us.target_canary import (
    APPLICATION_CANARY_TABLES,
    FrozenCanaryPackage,
    WslNativeClickHouseClient,
    assert_package_unchanged,
    commit_statements,
    stage_table_map,
)


CANARY_JOURNAL_VERSION = "US_TARGET_CANARY_JOURNAL_V1"


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    material = deepcopy(payload)
    material.pop("integrity_sha256", None)
    return (
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(payload)
    sealed["integrity_sha256"] = hashlib.sha256(_canonical_payload(sealed)).hexdigest()
    return sealed


def _validate_integrity(payload: dict[str, Any]) -> None:
    expected = str(payload.get("integrity_sha256") or "")
    actual = hashlib.sha256(_canonical_payload(payload)).hexdigest()
    if expected != actual:
        raise RuntimeError(
            "US target canary journal integrity mismatch: "
            f"expected={expected or '<missing>'} actual={actual}"
        )


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sealed = _seal(payload)
    body = json.dumps(sealed, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"US target canary journal not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("US target canary journal root must be an object")
    _validate_integrity(payload)
    return payload


def _statement_sha256(statement: str) -> str:
    return hashlib.sha256((statement.rstrip() + "\n").encode("utf-8")).hexdigest()


def _expected_commit_plan(package: FrozenCanaryPackage) -> dict[str, dict[str, str]]:
    return {
        str(item["table"]): {
            "stage_table": str(item["stage_table"]),
            "package_column": str(item["package_column"]),
            "statement": str(item["statement"]),
            "statement_sha256": _statement_sha256(str(item["statement"])),
        }
        for item in commit_statements(package)
    }


def initialize_canary_journal(
    path: Path,
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
) -> dict[str, Any]:
    if path.exists():
        raise RuntimeError(
            "US target canary journal already exists; refusing blind overwrite: "
            f"{path}"
        )
    if len(schema_manifest_sha256) != 64:
        raise ValueError("target schema manifest SHA-256 must be 64 hex characters")
    try:
        int(schema_manifest_sha256, 16)
    except ValueError as exc:
        raise ValueError("target schema manifest SHA-256 must be hexadecimal") from exc

    plan = _expected_commit_plan(package)
    payload: dict[str, Any] = {
        "journal_version": CANARY_JOURNAL_VERSION,
        "revision": 1,
        "state": "PREPARED",
        "package": package.as_dict(),
        "schema_manifest_sha256": schema_manifest_sha256.lower(),
        "stage": {
            "status": "NOT_STARTED",
            "tables": stage_table_map(package),
            "row_counts": None,
        },
        "commits": {
            table: {
                **plan[table],
                "status": "PENDING",
                "expected_rows": None,
                "observed_rows": None,
                "recovered_after_uncertain_insert": False,
            }
            for table in APPLICATION_CANARY_TABLES
        },
    }
    _atomic_write(path, payload)
    return _load(path)


def validate_canary_journal_binding(
    payload: dict[str, Any],
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
) -> None:
    _validate_integrity(payload)
    if payload.get("journal_version") != CANARY_JOURNAL_VERSION:
        raise RuntimeError("US target canary journal version mismatch")
    if payload.get("package") != package.as_dict():
        raise RuntimeError("US target canary journal package binding mismatch")
    if payload.get("schema_manifest_sha256") != schema_manifest_sha256.lower():
        raise RuntimeError("US target canary journal schema binding mismatch")

    stage = payload.get("stage")
    if not isinstance(stage, dict):
        raise RuntimeError("US target canary journal stage block is invalid")
    if stage.get("tables") != stage_table_map(package):
        raise RuntimeError("US target canary journal stage table binding mismatch")

    commits = payload.get("commits")
    if not isinstance(commits, dict):
        raise RuntimeError("US target canary journal commits block is invalid")
    if set(commits) != set(APPLICATION_CANARY_TABLES):
        raise RuntimeError("US target canary journal commit table set mismatch")

    expected_plan = _expected_commit_plan(package)
    for table in APPLICATION_CANARY_TABLES:
        item = commits.get(table)
        if not isinstance(item, dict):
            raise RuntimeError(f"US target canary journal commit block invalid: {table}")
        for field in ("stage_table", "package_column", "statement", "statement_sha256"):
            if item.get(field) != expected_plan[table][field]:
                raise RuntimeError(
                    "US target canary journal commit binding mismatch: "
                    f"table={table} field={field}"
                )


def load_canary_journal(
    path: Path,
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
) -> dict[str, Any]:
    payload = _load(path)
    validate_canary_journal_binding(
        payload,
        package=package,
        schema_manifest_sha256=schema_manifest_sha256,
    )
    return payload


def _persist_revision(path: Path, payload: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
    current = _load(path)
    current_revision = int(current.get("revision") or 0)
    if current_revision != expected_revision:
        raise RuntimeError(
            "US target canary journal concurrent revision mismatch: "
            f"expected={expected_revision} actual={current_revision}"
        )
    updated = deepcopy(payload)
    updated["revision"] = expected_revision + 1
    _atomic_write(path, updated)
    return _load(path)


def mark_stage_started(
    path: Path,
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
) -> dict[str, Any]:
    payload = load_canary_journal(
        path,
        package=package,
        schema_manifest_sha256=schema_manifest_sha256,
    )
    stage = payload["stage"]
    if payload.get("state") != "PREPARED" or stage.get("status") != "NOT_STARTED":
        raise RuntimeError(
            "US target canary staging cannot be restarted blindly: "
            f"state={payload.get('state')} stage={stage.get('status')}"
        )
    revision = int(payload["revision"])
    payload["state"] = "STAGING"
    stage["status"] = "STARTED"
    return _persist_revision(path, payload, expected_revision=revision)


def _query_pair(client: WslNativeClickHouseClient, sql: str) -> tuple[int, int]:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 2:
        raise RuntimeError("US target canary count query returned unexpected shape")
    return int(rows[0][0]), int(rows[0][1])


def _query_single(client: WslNativeClickHouseClient, sql: str) -> int:
    rows = client.query(sql).result_rows
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("US target canary count query returned unexpected shape")
    return int(rows[0][0])


def _stage_counts_for_table(
    client: WslNativeClickHouseClient,
    *,
    stage_table: str,
    package_column: str,
    package_id: str,
) -> tuple[int, int]:
    return _query_pair(
        client,
        (
            "SELECT count(), "
            f"countIf({package_column} = toUUID('{package_id}')) "
            f"FROM {stage_table}"
        ),
    )


def _final_package_count(
    client: WslNativeClickHouseClient,
    *,
    table: str,
    package_column: str,
    package_id: str,
) -> int:
    return _query_single(
        client,
        (
            "SELECT count() "
            f"FROM {table} "
            f"WHERE {package_column} = toUUID('{package_id}')"
        ),
    )


def mark_stage_complete(
    client: WslNativeClickHouseClient,
    path: Path,
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
    expected_row_counts: dict[str, int],
) -> dict[str, Any]:
    payload = load_canary_journal(
        path,
        package=package,
        schema_manifest_sha256=schema_manifest_sha256,
    )
    stage = payload["stage"]
    if payload.get("state") != "STAGING" or stage.get("status") != "STARTED":
        raise RuntimeError("US target canary stage completion requires STAGING/STARTED journal state")
    if set(expected_row_counts) != set(APPLICATION_CANARY_TABLES):
        raise RuntimeError("US target canary staged row-count table set mismatch")

    normalized: dict[str, int] = {}
    plan = _expected_commit_plan(package)
    package_id = str(package.package_id)
    for table in APPLICATION_CANARY_TABLES:
        expected = int(expected_row_counts[table])
        if expected < 0:
            raise RuntimeError(f"US target canary negative staged row count: {table}")
        total, matching = _stage_counts_for_table(
            client,
            stage_table=plan[table]["stage_table"],
            package_column=plan[table]["package_column"],
            package_id=package_id,
        )
        if total != expected or matching != expected:
            raise RuntimeError(
                "US target canary stage verification failed closed: "
                f"table={table} expected={expected} total={total} package_rows={matching}"
            )
        normalized[table] = expected

    assert_package_unchanged(package)
    revision = int(payload["revision"])
    stage["status"] = "COMPLETE"
    stage["row_counts"] = normalized
    payload["state"] = "STAGED"
    for table in APPLICATION_CANARY_TABLES:
        payload["commits"][table]["expected_rows"] = normalized[table]
    return _persist_revision(path, payload, expected_revision=revision)


def _verify_stage_again(
    client: WslNativeClickHouseClient,
    payload: dict[str, Any],
    *,
    package: FrozenCanaryPackage,
) -> None:
    stage = payload["stage"]
    if stage.get("status") != "COMPLETE":
        raise RuntimeError("US target canary commit requires a COMPLETE stage")
    row_counts = stage.get("row_counts")
    if not isinstance(row_counts, dict):
        raise RuntimeError("US target canary stage row counts are missing")

    package_id = str(package.package_id)
    for table in APPLICATION_CANARY_TABLES:
        commit = payload["commits"][table]
        expected = int(commit.get("expected_rows"))
        total, matching = _stage_counts_for_table(
            client,
            stage_table=str(commit["stage_table"]),
            package_column=str(commit["package_column"]),
            package_id=package_id,
        )
        if total != expected or matching != expected:
            raise RuntimeError(
                "US target canary staged data changed after checkpoint: "
                f"table={table} expected={expected} total={total} package_rows={matching}"
            )


def commit_staged_tables(
    client: WslNativeClickHouseClient,
    path: Path,
    *,
    package: FrozenCanaryPackage,
    schema_manifest_sha256: str,
) -> dict[str, Any]:
    payload = load_canary_journal(
        path,
        package=package,
        schema_manifest_sha256=schema_manifest_sha256,
    )
    if payload.get("state") not in {"STAGED", "COMMITTING", "COMPLETE"}:
        raise RuntimeError(
            "US target canary final commit is not resumable from this journal state: "
            f"{payload.get('state')}"
        )

    assert_package_unchanged(package)
    _verify_stage_again(client, payload, package=package)
    package_id = str(package.package_id)

    for table in APPLICATION_CANARY_TABLES:
        commit = payload["commits"][table]
        expected = int(commit.get("expected_rows"))
        status = str(commit.get("status") or "")
        if status not in {"PENDING", "INSERT_STARTED", "COMMITTED"}:
            raise RuntimeError(f"US target canary invalid commit status: table={table} status={status}")

        observed = _final_package_count(
            client,
            table=table,
            package_column=str(commit["package_column"]),
            package_id=package_id,
        )

        if status == "COMMITTED":
            if observed != expected:
                raise RuntimeError(
                    "US target canary committed-table count drift: "
                    f"table={table} expected={expected} observed={observed}"
                )
            continue

        if status == "INSERT_STARTED" and observed == expected:
            revision = int(payload["revision"])
            commit["status"] = "COMMITTED"
            commit["observed_rows"] = observed
            commit["recovered_after_uncertain_insert"] = True
            payload["state"] = "COMMITTING"
            payload = _persist_revision(path, payload, expected_revision=revision)
            continue

        if status == "INSERT_STARTED" and observed == 0:
            raise RuntimeError(
                "US target canary INSERT_STARTED has zero visible rows; "
                "explicit read-only in-flight reconciliation is required before retry: "
                f"table={table} expected={expected}"
            )

        if observed != 0:
            if status == "PENDING":
                raise RuntimeError(
                    "US target canary found pre-existing package rows before INSERT boundary; "
                    f"refusing adoption: table={table} expected={expected} observed={observed}"
                )
            raise RuntimeError(
                "US target canary partial final-table state detected; refusing replay: "
                f"table={table} expected={expected} observed={observed} status={status}"
            )

        if expected == 0:
            revision = int(payload["revision"])
            commit["status"] = "COMMITTED"
            commit["observed_rows"] = 0
            commit["recovered_after_uncertain_insert"] = False
            payload["state"] = "COMMITTING"
            payload = _persist_revision(path, payload, expected_revision=revision)
            continue

        if status == "PENDING":
            revision = int(payload["revision"])
            commit["status"] = "INSERT_STARTED"
            commit["observed_rows"] = 0
            payload["state"] = "COMMITTING"
            payload = _persist_revision(path, payload, expected_revision=revision)
            commit = payload["commits"][table]

        assert_package_unchanged(package)
        client.command(str(commit["statement"]))
        assert_package_unchanged(package)

        observed_after = _final_package_count(
            client,
            table=table,
            package_column=str(commit["package_column"]),
            package_id=package_id,
        )
        if observed_after != expected:
            raise RuntimeError(
                "US target canary final-table insert did not reach exact expected count: "
                f"table={table} expected={expected} observed={observed_after}"
            )

        revision = int(payload["revision"])
        commit = payload["commits"][table]
        commit["status"] = "COMMITTED"
        commit["observed_rows"] = observed_after
        payload["state"] = "COMMITTING"
        payload = _persist_revision(path, payload, expected_revision=revision)

    assert_package_unchanged(package)
    for table in APPLICATION_CANARY_TABLES:
        commit = payload["commits"][table]
        expected = int(commit.get("expected_rows"))
        observed = _final_package_count(
            client,
            table=table,
            package_column=str(commit["package_column"]),
            package_id=package_id,
        )
        if commit.get("status") != "COMMITTED" or observed != expected:
            raise RuntimeError(
                "US target canary final acceptance count mismatch: "
                f"table={table} expected={expected} observed={observed} "
                f"status={commit.get('status')}"
            )

    if payload.get("state") != "COMPLETE":
        revision = int(payload["revision"])
        payload["state"] = "COMPLETE"
        payload = _persist_revision(path, payload, expected_revision=revision)
    return payload