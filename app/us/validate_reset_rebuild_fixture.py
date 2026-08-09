from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid
import zipfile

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.audit_real_data import ALL_TABLE_KEYS
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import ensure_us_m1_schema
from app.us.replay_executor import build_replay_plan, execute_replay
from app.us.repository import list_us_replay_registry
from app.us.reset_rebuild import RESET_CONFIRMATION, apply_reset, build_reset_plan
from app.us.stage_sources import apply_staging


SERIAL = "88990007"
HISTORY_NAME = "apc18840407-20260131-01.zip"
DAILY_NAME = "apc260202.zip"


def _xml(status_code: str, status_date: str, owner_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<trademark-applications-daily>
  <application-information>
    <file-segments>
      <action-keys>
        <case-file>
          <serial-number>{SERIAL}</serial-number>
          <case-file-header>
            <filing-date>20200116</filing-date>
            <registration-number>7655000</registration-number>
            <status-code>{status_code}</status-code>
            <status-date>{status_date}</status-date>
            <mark-identification>MARKORBIT RESET FIXTURE</mark-identification>
            <mark-drawing-code>4000</mark-drawing-code>
          </case-file-header>
          <case-file-owners>
            <case-file-owner>
              <entry-number>1</entry-number>
              <party-type>10</party-type>
              <legal-entity-type-code>16</legal-entity-type-code>
              <party-name>{owner_name}</party-name>
              <country>US</country>
            </case-file-owner>
          </case-file-owners>
          <classifications>
            <classification>
              <primary-code>009</primary-code>
              <international-code>009</international-code>
              <status-code>6</status-code>
            </classification>
          </classifications>
          <case-file-events>
            <case-file-event>
              <code>NWAP</code>
              <date>20200116</date>
              <number>1</number>
              <type>A</type>
            </case-file-event>
          </case-file-events>
          <case-file-statements>
            <case-file-statement>
              <type-code>GS0091</type-code>
              <text>Reset fixture software.</text>
            </case-file-statement>
          </case-file-statements>
        </case-file>
      </action-keys>
    </file-segments>
  </application-information>
</trademark-applications-daily>
"""


def _write_zip(path: Path, xml: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path.with_suffix(".xml").name, xml)


def _fixture_registry_rows() -> list[dict]:
    return [
        row
        for row in list_us_replay_registry()
        if str(row.get("file_name") or "") in {HISTORY_NAME, DAILY_NAME}
    ]


def _remove_fixture_files(raw_root: Path) -> None:
    for directory in (raw_root / "incoming" / "us", raw_root / "archive" / "us"):
        if not directory.exists():
            continue
        for name in (HISTORY_NAME, DAILY_NAME):
            path = directory / name
            if path.exists():
                path.unlink()
            stem = Path(name).stem
            suffix = Path(name).suffix
            for candidate in directory.glob(f"{stem}_*{suffix}"):
                candidate.unlink()


def _remove_manifest(path: str | None) -> None:
    if not path:
        return
    manifest = Path(path)
    if manifest.exists():
        manifest.unlink()


def _cleanup_fixture_state(raw_root: Path) -> None:
    rows = _fixture_registry_rows()
    for row in rows:
        _cleanup_package_outputs(uuid.UUID(str(row["package_id"])))
    if rows:
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM control.source_package WHERE jurisdiction = 'US' "
                    "AND file_name IN (%s, %s)",
                    (HISTORY_NAME, DAILY_NAME),
                )
            conn.commit()
    _remove_fixture_files(raw_root)


def _all_us_table_counts() -> dict[str, int]:
    client = clickhouse_client()
    counts: dict[str, int] = {}
    for table in ALL_TABLE_KEYS:
        rows = client.query(f"SELECT count() FROM markorbit_facts.{table}").result_rows
        counts[table] = int(rows[0][0] if rows else 0)
    return counts


def main() -> None:
    ensure_us_m1_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us"
    incoming.mkdir(parents=True, exist_ok=True)
    manifest_path: str | None = None

    try:
        _cleanup_fixture_state(raw_root)
        _write_zip(
            incoming / HISTORY_NAME,
            _xml("630", "20260131", "Reset Fixture Historical LLC"),
        )
        _write_zip(
            incoming / DAILY_NAME,
            _xml("700", "20260202", "Reset Fixture Daily LLC"),
        )

        first_replay = execute_replay(
            raw_root,
            expected_history_parts=1,
            max_packages=None,
            trigger_type="CI_US_RESET_REBUILD_FIXTURE_INITIAL",
        )
        if first_replay["status"] != "COMPLETE" or first_replay["processed_count"] != 2:
            raise RuntimeError(f"Reset fixture initial replay failed: {first_replay}")

        before_registry = _fixture_registry_rows()
        if len(before_registry) != 2 or any(row["status"] != "SUCCESS" for row in before_registry):
            raise RuntimeError(f"Reset fixture initial registry mismatch: {before_registry}")
        package_ids_before = {
            row["file_name"]: str(row["package_id"]) for row in before_registry
        }
        counts_before = _all_us_table_counts()
        if not any(counts_before.values()):
            raise RuntimeError("Reset fixture initial replay produced no US fact rows")

        staged = apply_staging(raw_root, expected_history_parts=1)
        if staged["status"] != "APPLIED" or staged["copied_count"] != 2:
            raise RuntimeError(f"Reset fixture archive staging failed: {staged}")

        dry_run = build_reset_plan(raw_root, expected_history_parts=1)
        if dry_run["status"] != "READY":
            raise RuntimeError(f"Reset fixture dry-run not READY: {dry_run}")
        if dry_run["registered_package_count"] != 2:
            raise RuntimeError("Reset fixture dry-run registry count mismatch")
        if dry_run["total_fact_rows"] <= 0:
            raise RuntimeError("Reset fixture dry-run did not observe fact rows")

        reset = apply_reset(
            raw_root,
            expected_history_parts=1,
            confirmation=RESET_CONFIRMATION,
        )
        manifest_path = reset.get("manifest_path")
        if reset["status"] != "RESET_COMPLETE":
            raise RuntimeError(f"Reset fixture apply failed: {reset}")
        if not manifest_path:
            raise RuntimeError("Reset fixture did not persist pre-reset manifest")
        manifest = Path(manifest_path)
        if not manifest.is_file():
            raise RuntimeError(f"Reset fixture manifest missing: {manifest}")
        actual_manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if actual_manifest_sha != reset["manifest_sha256"]:
            raise RuntimeError("Reset fixture manifest SHA-256 mismatch")

        counts_after_reset = _all_us_table_counts()
        if any(counts_after_reset.values()):
            raise RuntimeError(f"Reset fixture left US facts: {counts_after_reset}")
        after_registry = _fixture_registry_rows()
        if len(after_registry) != 2 or any(row["status"] != "REGISTERED" for row in after_registry):
            raise RuntimeError(f"Reset fixture registry not reset: {after_registry}")
        package_ids_after = {
            row["file_name"]: str(row["package_id"]) for row in after_registry
        }
        if package_ids_after != package_ids_before:
            raise RuntimeError(
                f"Reset fixture changed package identities: before={package_ids_before} "
                f"after={package_ids_after}"
            )
        if any(row.get("profile") for row in after_registry):
            raise RuntimeError("Reset fixture did not clear package profiles")

        replay_plan = build_replay_plan(raw_root, expected_history_parts=1)
        if replay_plan["status"] != "READY":
            raise RuntimeError(f"Reset fixture post-reset replay plan not READY: {replay_plan}")
        if replay_plan["next_step"]["file_name"] != HISTORY_NAME:
            raise RuntimeError("Reset fixture post-reset replay did not restart from history")

        second_replay = execute_replay(
            raw_root,
            expected_history_parts=1,
            max_packages=None,
            trigger_type="CI_US_RESET_REBUILD_FIXTURE_REPLAY",
        )
        if second_replay["status"] != "COMPLETE" or second_replay["processed_count"] != 2:
            raise RuntimeError(f"Reset fixture rebuild replay failed: {second_replay}")
        rebuilt_registry = _fixture_registry_rows()
        package_ids_rebuilt = {
            row["file_name"]: str(row["package_id"]) for row in rebuilt_registry
        }
        if package_ids_rebuilt != package_ids_before:
            raise RuntimeError("Reset fixture replay did not preserve original package identities")
        if any(row["status"] != "SUCCESS" for row in rebuilt_registry):
            raise RuntimeError(f"Reset fixture rebuilt registry not SUCCESS: {rebuilt_registry}")

        case_rows = clickhouse_client().query(
            f"""
            SELECT status_code, toString(last_source_package_id)
            FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0 AND serial_number = '{SERIAL}'
            """
        ).result_rows
        if len(case_rows) != 1 or str(case_rows[0][0]) != "700":
            raise RuntimeError(f"Reset fixture daily current case did not win: {case_rows}")
        daily_package_id = package_ids_before[DAILY_NAME]
        if str(case_rows[0][1]) != daily_package_id:
            raise RuntimeError("Reset fixture current case lineage changed after rebuild")

        final_plan = build_replay_plan(raw_root, expected_history_parts=1)
        if final_plan["status"] != "COMPLETE":
            raise RuntimeError(f"Reset fixture final replay plan not COMPLETE: {final_plan}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_CLEAN_REBUILD_RESET_FIXTURE",
                    "initial_replay": "PASS",
                    "archive_staging": "PASS",
                    "reset_dry_run": "PASS",
                    "manifest_sha256": "PASS",
                    "all_11_tables_zero_after_reset": "PASS",
                    "package_identity_preserved": "PASS",
                    "registry_reset_to_registered": "PASS",
                    "post_reset_replay": "PASS",
                    "daily_current_case_after_rebuild": "PASS",
                    "final_plan": final_plan["status"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture_state(raw_root)
        _remove_manifest(manifest_path)
        residual = clickhouse_client().query(
            f"""
            SELECT count()
            FROM markorbit_facts.us_case_current FINAL
            WHERE serial_number = '{SERIAL}'
            """
        ).result_rows[0][0]
        if int(residual):
            raise RuntimeError(
                f"Reset rebuild fixture cleanup failed: residual_case_rows={residual}"
            )


if __name__ == "__main__":
    main()
