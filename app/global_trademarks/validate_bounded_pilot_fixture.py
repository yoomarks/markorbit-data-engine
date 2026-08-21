from __future__ import annotations

import csv
import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.acceptance import evaluate_manifest_acceptance
from app.global_trademarks.gb_open_data import UK_FIELDS, ingest_ukipo_2018
from app.global_trademarks.ingest_runs import get_ingest_run_state
from app.global_trademarks.manifest import attach_manifest_object, upsert_source_manifest
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object


_PIPELINE_ID = "UKIPO_2018_DOMESTIC_V1"


def _fixture(path: Path) -> None:
    fields = [*UK_FIELDS, *[f"Class{number}" for number in range(1, 46)]]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        for index in range(1, 4):
            row = {field: "" for field in fields}
            row.update(
                {
                    "Trade Mark": f"UKBOUNDEDPILOT{index:04d}",
                    "Mark Text": f"BOUNDED PILOT {index}",
                    "Name": "Bounded Pilot Owner Limited",
                    "Status": "Registered",
                    "Filed": "2018-01-01",
                    "Registered": "2018-06-01",
                    "Renewal Due Date": "2028-06-01",
                    "Class9": "1",
                }
            )
            writer.writerow(row)


def _record_count() -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM trademark_gb.historical_record
                WHERE application_number LIKE 'UKBOUNDEDPILOT%'
                """
            )
            return int(cur.fetchone()["count"])


def main() -> int:
    assert migrate_global_trademark_schema().ready

    with tempfile.TemporaryDirectory(prefix="global-bounded-pilot-") as temporary:
        path = Path(temporary) / "OpenDataDomestic2018Bounded.txt"
        _fixture(path)
        source_object_id = register_source_object(
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            path=path,
            source_period_start=date(2018, 1, 1),
            source_period_end=date(2018, 12, 31),
        )
        manifest = upsert_source_manifest(
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            manifest_key="UKIPO_2018_BOUNDED_PILOT_FIXTURE",
            source_period_start=date(2018, 1, 1),
            source_period_end=date(2018, 12, 31),
            source_sequence=90,
            source_precedence=10,
            expected_objects=1,
            parser_version="UKIPO_2018_V1",
            mapping_version="COUNTRY_NATIVE_V1",
        )
        manifest = attach_manifest_object(
            manifest_id=manifest.manifest_id,
            source_object_id=source_object_id,
            part_sequence=1,
        )

        assert _record_count() == 0
        assert ingest_ukipo_2018(
            path,
            source_stream="DOMESTIC",
            batch_size=10,
            max_records=1,
        ) == 1
        first = get_ingest_run_state(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
        )
        assert first is not None
        assert first.status == "RUNNING"
        assert first.checkpoint == 1
        assert first.rows_committed == 1
        assert _record_count() == 1

        assert ingest_ukipo_2018(
            path,
            source_stream="DOMESTIC",
            batch_size=10,
            max_records=1,
        ) == 2
        second = get_ingest_run_state(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
        )
        assert second is not None
        assert second.status == "RUNNING"
        assert second.checkpoint == 2
        assert second.rows_committed == 2
        assert _record_count() == 2

        partial_acceptance = evaluate_manifest_acceptance(manifest.manifest_id)
        assert partial_acceptance.release_accepted is False
        assert "INGEST_RUN_STILL_RUNNING" in partial_acceptance.reason_codes
        assert "SOURCE_OBJECT_WITHOUT_COMPLETE_INGEST_RUN" in partial_acceptance.reason_codes

        assert ingest_ukipo_2018(
            path,
            source_stream="DOMESTIC",
            batch_size=10,
        ) == 3
        complete = get_ingest_run_state(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
        )
        assert complete is not None
        assert complete.status == "COMPLETE"
        assert complete.checkpoint == 3
        assert complete.rows_committed == 3
        assert _record_count() == 3

        accepted = evaluate_manifest_acceptance(manifest.manifest_id)
        assert accepted.release_accepted is True

        assert ingest_ukipo_2018(
            path,
            source_stream="DOMESTIC",
            batch_size=10,
            max_records=1,
        ) == 3
        replay = get_ingest_run_state(
            source_object_id=source_object_id,
            pipeline_id=_PIPELINE_ID,
        )
        assert replay == complete
        assert _record_count() == 3

    print(
        {
            "status": "PASS",
            "bounded_apply_durable": True,
            "partial_not_complete": True,
            "resume_continues_checkpoint": True,
            "acceptance_blocks_partial": True,
            "unbounded_completion": True,
            "complete_replay_idempotent": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
