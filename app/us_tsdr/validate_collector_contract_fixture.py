from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.db import postgres_conn
from app.us_tsdr.collector_incoming import (
    ensure_collector_observation_schema,
    ingest_collector_csv_directory,
)
from app.us_tsdr.exporter import export_batch
from app.us_tsdr.planner import create_weekly_batch
from app.us_tsdr.policy import Candidate


def _reset() -> None:
    ensure_collector_observation_schema()
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE acquisition.us_tsdr_contact_observation, acquisition.us_tsdr_task, acquisition.us_tsdr_batch, acquisition.us_tsdr_case_coverage"
            )
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_planner_state
                SET source_rank_watermark = 0,
                    source_serial_watermark = '',
                    last_completed_batch_id = NULL,
                    updated_at = now()
                WHERE state_key = 'US_TSDR_WEEKLY'
                """
            )
        conn.commit()


def main() -> None:
    _reset()
    now = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    planned = create_weekly_batch(
        [
            Candidate(
                "90817045",
                1,
                applicant_country="CN",
                current_attorney_present=True,
                lifecycle_state="REFRESHABLE",
                is_new_application=True,
            ),
            Candidate(
                "99000001",
                2,
                applicant_country="US",
                current_attorney_present=False,
                lifecycle_state="REFRESHABLE",
                is_new_application=True,
            ),
            Candidate(
                "99000002",
                3,
                applicant_country="US",
                current_attorney_present=True,
                lifecycle_state="REFRESHABLE",
                is_new_application=True,
            ),
        ],
        capacity=3,
        now=now,
        source_watermark_to=(3, "99000002"),
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        exported = export_batch(planned["batch_key"], outgoing_root=root / "outgoing")
        task_lines = Path(exported["tasks"]).read_text(encoding="utf-8").splitlines()
        assert task_lines == [
            "https://tsdr.uspto.gov/statusview/sn90817045",
            "https://tsdr.uspto.gov/statusview/sn99000001",
            "https://tsdr.uspto.gov/statusview/sn99000002",
        ], task_lines

        incoming = root / "incoming" / planned["batch_key"]
        incoming.mkdir(parents=True)
        (incoming / "90817045.csv").write_text(
            """Attorney Name:Adriano Pacifici
Docket Number:00989
Attorney Primary Email Address:apacifici@iplawconsulting.com
Attorney Email Authorized:Yes
Correspondent Name/Address:
ADRIANO PACIFICI
INTELLECTUAL PROPERTY CONSULTING, LLC
400 POYDRAS STREET
SUITE 1400
NEW ORLEANS, LOUISIANA UNITED STATES 70130
Phone:504-323-6600
Correspondent e-mail:apacifici@iplawconsulting.com dmintlsz@yeah.net creid@iplawconsulting.com
Correspondent e-mail Authorized:Yes
""",
            encoding="utf-8",
        )
        (incoming / "99000001.csv").write_text(
            """Attorney Name:
Attorney Primary Email Address:
Attorney Email Authorized:No
Correspondent Name/Address:
DIRECT APPLICANT
123 MAIN STREET
AUSTIN, TEXAS UNITED STATES 78701
Phone:512-555-0100
Correspondent e-mail:owner@example.com
Correspondent e-mail Authorized:Yes
""",
            encoding="utf-8",
        )

        ingested = ingest_collector_csv_directory(
            planned["batch_key"], incoming, ingested_at=now
        )
        assert ingested["success"] == 2, ingested
        assert ingested["unattempted"] == 1, ingested

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT attorney_name, docket_number, attorney_primary_email,
                       correspondent_name_address_raw, correspondent_name_address_lines,
                       phone, correspondent_emails, collected_at_evidence
                FROM acquisition.us_tsdr_contact_observation
                WHERE serial_number = '90817045'
                """
            )
            row = cur.fetchone()
            assert row, row
            assert row["attorney_name"] == "Adriano Pacifici", row
            assert row["docket_number"] == "00989", row
            assert row["attorney_primary_email"] == "apacifici@iplawconsulting.com", row
            assert row["phone"] == "504-323-6600", row
            assert row["correspondent_emails"] == [
                "apacifici@iplawconsulting.com",
                "dmintlsz@yeah.net",
                "creid@iplawconsulting.com",
            ], row
            assert "INTELLECTUAL PROPERTY CONSULTING, LLC" in row[
                "correspondent_name_address_raw"
            ], row
            assert row["correspondent_name_address_lines"][0] == "ADRIANO PACIFICI", row
            assert row["collected_at_evidence"] == "INGESTED_AT_FALLBACK", row

            cur.execute(
                """
                SELECT serial_number, state, result_status
                FROM acquisition.us_tsdr_task
                ORDER BY serial_number
                """
            )
            task_rows = cur.fetchall()
            task_state = {
                row["serial_number"]: (row["state"], row["result_status"])
                for row in task_rows
            }
            assert task_state["90817045"] == ("SUCCESS", "SUCCESS"), task_state
            assert task_state["99000001"] == ("SUCCESS", "SUCCESS"), task_state
            assert task_state["99000002"] == ("UNATTEMPTED", "UNATTEMPTED"), task_state

    print(
        json.dumps(
            {
                "status": "PASS",
                "collector_contract": "US_TSDR_COLLECTOR_TXT_CSV_V1",
                "batch_key": planned["batch_key"],
            }
        )
    )


if __name__ == "__main__":
    main()
