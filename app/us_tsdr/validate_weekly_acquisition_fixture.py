from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import postgres_conn
from app.us_tsdr.exporter import export_batch
from app.us_tsdr.incoming import ingest_result_package
from app.us_tsdr.migrations import ensure_tsdr_schema
from app.us_tsdr.planner import create_weekly_batch, planner_state
from app.us_tsdr.policy import Candidate


def _reset() -> None:
    ensure_tsdr_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE acquisition.us_tsdr_task, acquisition.us_tsdr_batch, acquisition.us_tsdr_case_coverage")
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_planner_state
                SET source_rank_watermark = 100,
                    source_serial_watermark = '',
                    last_completed_batch_id = NULL,
                    updated_at = now()
                WHERE state_key = 'US_TSDR_WEEKLY'
                """
            )
        conn.commit()


def main() -> None:
    _reset()
    now = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquisition.us_tsdr_case_coverage (
                    serial_number, first_fetched_at, last_fetched_at, last_result_status,
                    refresh_due_at, lifecycle_state, terminal_complete, successful_fetch_count,
                    last_source_attorney_fingerprint, last_source_attorney_present
                ) VALUES (%s, %s, %s, 'SUCCESS', %s, 'REFRESHABLE', false, 1, %s, true)
                """,
                ("70000001", now, now, now + timedelta(days=30), "a" * 64),
            )
            cur.execute(
                """
                INSERT INTO acquisition.us_tsdr_case_coverage (
                    serial_number, first_fetched_at, last_fetched_at, last_result_status,
                    lifecycle_state, terminal_complete, successful_fetch_count
                ) VALUES (%s, %s, %s, 'SUCCESS', 'TERMINAL_INVALID', true, 1)
                """,
                ("70000002", now, now),
            )
        conn.commit()

    candidates = [
        Candidate("99000001", 101, applicant_country="US", current_attorney_present=True, lifecycle_state="REFRESHABLE", is_new_application=True),
        Candidate("99000002", 102, applicant_country="CN", current_attorney_present=False, lifecycle_state="REFRESHABLE", is_new_application=True),
        Candidate("71000001", 60, applicant_country="CN", current_attorney_present=False, lifecycle_state="REFRESHABLE"),
        Candidate("72000001", 50, applicant_country="US", lifecycle_state="TERMINAL_INVALID"),
        Candidate("70000001", 40, applicant_country="US", current_attorney_present=False, source_attorney_fingerprint="b" * 64, lifecycle_state="REFRESHABLE"),
        Candidate("70000002", 30, applicant_country="US", lifecycle_state="TERMINAL_INVALID"),
    ]
    planned = create_weekly_batch(candidates, capacity=5, now=now, source_watermark_to=(102, "99000002"))
    assert planned["task_count"] == 5, planned
    assert planned["metrics"]["new_application_count"] == 2, planned
    assert planned["source_watermark_to"] == [102, "99000002"], planned

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        exported = export_batch(planned["batch_key"], outgoing_root=root / "outgoing")
        assert exported["task_count"] == 5, exported

        tasks = [json.loads(line) for line in Path(exported["tasks"]).read_text(encoding="utf-8").splitlines()]
        result_dir = root / "incoming" / planned["batch_key"]
        result_dir.mkdir(parents=True)
        (result_dir / "manifest.json").write_text(
            json.dumps({"contract": "US_TSDR_RESULT_V1", "batch_key": planned["batch_key"]}),
            encoding="utf-8",
        )
        rows = []
        omitted = None
        for task in tasks:
            if omitted is None and task["serial_number"] == "71000001":
                omitted = task
                continue
            raw_relative = Path("raw") / f"{task['serial_number']}.json"
            raw_path = result_dir / raw_relative
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_bytes = json.dumps({"serial_number": task["serial_number"], "source": "fixture"}).encode("utf-8")
            raw_path.write_bytes(raw_bytes)
            rows.append(
                {
                    "task_id": task["task_id"],
                    "serial_number": task["serial_number"],
                    "result_status": "SUCCESS",
                    "fetched_at": now.isoformat(),
                    "snapshot_hash": hashlib.sha256(raw_bytes).hexdigest(),
                    "raw_relative_path": raw_relative.as_posix(),
                }
            )
        (result_dir / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        ingested = ingest_result_package(result_dir)
        assert ingested["status"] == "COMPLETED", ingested
        assert ingested["completed_with_gaps"] is True, ingested
        assert ingested["result_counts"]["UNATTEMPTED"] == 1, ingested

    state = planner_state()
    assert int(state["source_rank_watermark"]) == 102, state
    assert state["source_serial_watermark"] == "99000002", state

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT terminal_complete FROM acquisition.us_tsdr_case_coverage WHERE serial_number = '72000001'"
            )
            terminal = cur.fetchone()
            assert terminal and terminal["terminal_complete"] is True, terminal
            cur.execute(
                "SELECT last_result_status, last_fetched_at FROM acquisition.us_tsdr_case_coverage WHERE serial_number = '71000001'"
            )
            omitted_row = cur.fetchone()
            assert omitted_row["last_result_status"] == "UNATTEMPTED", omitted_row
            assert omitted_row["last_fetched_at"] is None, omitted_row

    follow_up = create_weekly_batch(
        [
            Candidate("99000003", 103, applicant_country="US", lifecycle_state="REFRESHABLE", is_new_application=True),
            Candidate("71000001", 60, applicant_country="CN", current_attorney_present=False, lifecycle_state="REFRESHABLE"),
        ],
        capacity=2,
        now=now + timedelta(days=7),
        source_watermark_to=(103, "99000003"),
    )
    assert follow_up["task_count"] == 2, follow_up
    assert follow_up["metrics"]["reason_counts"]["RETRY_PREVIOUS_FAILURE"] == 1, follow_up
    print(json.dumps({"status": "PASS", "first_batch": planned, "follow_up": follow_up}, default=str))


if __name__ == "__main__":
    main()
