from __future__ import annotations

import json
from pathlib import Path
import uuid
import zipfile

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us.ingest import _cleanup_package_outputs
from app.us.migrations import ensure_us_m1_schema
from app.us.replay_executor import build_replay_plan, execute_replay
from app.us.repository import list_us_replay_registry


SERIAL = "88990006"
HISTORY_NAME = "apc18840407-20251231-01.zip"
DAILY_NAME = "apc260102.zip"


def _xml(status_code: str, status_date: str, owner_name: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<trademark-applications-daily>
  <application-information>
    <file-segments>
      <action-keys>
        <case-file>
          <serial-number>{SERIAL}</serial-number>
          <case-file-header>
            <filing-date>20200115</filing-date>
            <registration-number>7654999</registration-number>
            <status-code>{status_code}</status-code>
            <status-date>{status_date}</status-date>
            <mark-identification>MARKORBIT REPLAY FIXTURE</mark-identification>
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
              <date>20200115</date>
              <number>1</number>
              <type>A</type>
            </case-file-event>
          </case-file-events>
          <case-file-statements>
            <case-file-statement>
              <type-code>GS0091</type-code>
              <text>Replay fixture software.</text>
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


def main() -> None:
    ensure_us_m1_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us"
    archive = raw_root / "archive" / "us"
    incoming.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    try:
        _cleanup_fixture_state(raw_root)
        _write_zip(
            incoming / HISTORY_NAME,
            _xml("630", "20251231", "Replay Fixture Historical LLC"),
        )
        _write_zip(
            incoming / DAILY_NAME,
            _xml("700", "20260102", "Replay Fixture Daily LLC"),
        )

        initial_plan = build_replay_plan(raw_root, expected_history_parts=1)
        if initial_plan["status"] != "READY":
            raise RuntimeError(f"Replay fixture initial plan not READY: {initial_plan}")
        if initial_plan["next_step"]["file_name"] != HISTORY_NAME:
            raise RuntimeError("Replay fixture did not select historical package first")

        first = execute_replay(
            raw_root,
            expected_history_parts=1,
            max_packages=1,
            trigger_type="CI_US_REPLAY_EXECUTOR_FIXTURE",
        )
        if first["status"] != "PAUSED" or first["processed_count"] != 1:
            raise RuntimeError(f"Replay fixture first step mismatch: {first}")
        if first["processed"][0]["file_name"] != HISTORY_NAME:
            raise RuntimeError("Replay fixture processed the wrong first package")
        if not (archive / HISTORY_NAME).is_file():
            raise RuntimeError("Replay fixture historical package was not archived")
        if not (incoming / DAILY_NAME).is_file():
            raise RuntimeError("Replay fixture daily package disappeared before its turn")

        middle_plan = build_replay_plan(raw_root, expected_history_parts=1)
        if middle_plan["status"] != "READY":
            raise RuntimeError(f"Replay fixture middle plan not READY: {middle_plan}")
        if middle_plan["steps"][0]["action"] != "SKIP_SUCCESS":
            raise RuntimeError("Replay fixture did not preserve successful history prefix")
        if middle_plan["next_step"]["file_name"] != DAILY_NAME:
            raise RuntimeError("Replay fixture did not select daily package second")

        second = execute_replay(
            raw_root,
            expected_history_parts=1,
            max_packages=None,
            trigger_type="CI_US_REPLAY_EXECUTOR_FIXTURE",
        )
        if second["status"] != "COMPLETE" or second["processed_count"] != 1:
            raise RuntimeError(f"Replay fixture completion mismatch: {second}")
        if second["processed"][0]["file_name"] != DAILY_NAME:
            raise RuntimeError("Replay fixture processed the wrong second package")
        if not (archive / DAILY_NAME).is_file():
            raise RuntimeError("Replay fixture daily package was not archived")

        registry = _fixture_registry_rows()
        if len(registry) != 2 or any(row["status"] != "SUCCESS" for row in registry):
            raise RuntimeError(f"Replay fixture registry mismatch: {registry}")
        ranks = [int(row["source_rank"]) for row in registry]
        if ranks != sorted(ranks) or ranks[0] >= ranks[1]:
            raise RuntimeError(f"Replay fixture source rank order mismatch: {ranks}")

        case_rows = clickhouse_client().query(
            f"""
            SELECT status_code, toString(last_source_package_id)
            FROM markorbit_facts.us_case_current FINAL
            WHERE is_deleted = 0 AND serial_number = '{SERIAL}'
            """
        ).result_rows
        if len(case_rows) != 1 or str(case_rows[0][0]) != "700":
            raise RuntimeError(f"Replay fixture daily current case did not win: {case_rows}")
        daily_registry = next(row for row in registry if row["file_name"] == DAILY_NAME)
        if str(case_rows[0][1]) != str(daily_registry["package_id"]):
            raise RuntimeError("Replay fixture current case lineage is not the daily package")

        final_plan = build_replay_plan(raw_root, expected_history_parts=1)
        if final_plan["status"] != "COMPLETE" or final_plan["remaining_count"] != 0:
            raise RuntimeError(f"Replay fixture final plan not COMPLETE: {final_plan}")

        noop = execute_replay(
            raw_root,
            expected_history_parts=1,
            max_packages=1,
            trigger_type="CI_US_REPLAY_EXECUTOR_FIXTURE",
        )
        if noop["status"] != "COMPLETE" or noop["processed_count"] != 0:
            raise RuntimeError(f"Replay fixture idempotent completion failed: {noop}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_DETERMINISTIC_REPLAY_EXECUTOR_FIXTURE",
                    "first_run": {
                        "status": first["status"],
                        "processed": first["processed_count"],
                    },
                    "second_run": {
                        "status": second["status"],
                        "processed": second["processed_count"],
                    },
                    "final_plan": final_plan["status"],
                    "idempotent_noop": "PASS",
                    "daily_current_case": "PASS",
                    "source_rank_order": "PASS",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        _cleanup_fixture_state(raw_root)
        residual = clickhouse_client().query(
            f"""
            SELECT count()
            FROM markorbit_facts.us_case_current FINAL
            WHERE serial_number = '{SERIAL}'
            """
        ).result_rows[0][0]
        if int(residual):
            raise RuntimeError(
                f"Replay executor fixture cleanup failed: residual_case_rows={residual}"
            )


if __name__ == "__main__":
    main()
