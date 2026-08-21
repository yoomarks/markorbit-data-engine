from __future__ import annotations

import csv
import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.gb_open_data import UK_FIELDS, ingest_ukipo_2018
from app.global_trademarks.ingest_runs import get_ingest_run_state
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.operator import build_ingest_plan, register_plan_source
from app.global_trademarks.preflight import inspect_gb_2018


_PIPELINE_ID = "UKIPO_2018_DOMESTIC_V1"


def _fixture(path: Path, *, mark_text: str) -> None:
    fields = [*UK_FIELDS, *[f"Class{number}" for number in range(1, 46)]]
    row = {field: "" for field in fields}
    row.update(
        {
            "Trade Mark": "UKSOURCEPIN0001",
            "Mark Text": mark_text,
            "Name": "Source Pin Owner Limited",
            "Status": "Registered",
            "Filed": "2018-01-01",
            "Registered": "2018-06-01",
            "Renewal Due Date": "2028-06-01",
            "Class9": "1",
        }
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        writer.writerow(row)


def _record_count() -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM trademark_gb.historical_record
                WHERE application_number = 'UKSOURCEPIN0001'
                """
            )
            return int(cur.fetchone()["count"])


def _source_count(object_key: str) -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM acquisition.global_trademark_source_object
                WHERE source_id = 'UKIPO_OPEN_DATA_2018'
                  AND object_key = %s
                """,
                (object_key,),
            )
            return int(cur.fetchone()["count"])


def main() -> int:
    assert migrate_global_trademark_schema().ready

    with tempfile.TemporaryDirectory(prefix="global-source-pin-") as temporary:
        root = Path(temporary)
        path = root / "UKIPO-source-pin-fixture.txt"
        other_path = root / "UKIPO-other-preflight-fixture.txt"
        _fixture(path, mark_text="SOURCE PIN ORIGINAL")
        _fixture(other_path, mark_text="OTHER PREFLIGHT")

        preflight = inspect_gb_2018(path)
        plan = build_ingest_plan(
            command="ingest-gb-2018",
            jurisdiction="GB",
            source_id="UKIPO_OPEN_DATA_2018",
            path=path,
            preflight=preflight,
            manifest_key="UKIPO_SOURCE_PIN_FIXTURE",
            source_period_start=date(2018, 1, 1),
            source_period_end=date(2018, 12, 31),
            source_sequence=91,
            source_precedence=10,
            expected_objects=1,
            part_sequence=1,
            parser_version="UKIPO_2018_V1",
            max_records=1,
        )
        source_object_id, _manifest = register_plan_source(
            plan,
            metadata={"source_stream": "DOMESTIC"},
        )
        assert _source_count(path.name) == 1
        assert _record_count() == 0

        # Replace the file after preflight/registration but before the loader begins.
        # The one-shot pin must reject this rather than silently registering/ingesting
        # a second source object with different bytes.
        _fixture(path, mark_text="SOURCE PIN MUTATED")
        blocked = False
        try:
            ingest_ukipo_2018(
                path,
                source_stream="DOMESTIC",
                max_records=1,
            )
        except RuntimeError as exc:
            blocked = "source bytes changed after preflight" in str(exc)
        assert blocked is True
        assert _source_count(path.name) == 1
        assert _record_count() == 0
        assert (
            get_ingest_run_state(
                source_object_id=source_object_id,
                pipeline_id=_PIPELINE_ID,
            )
            is None
        )

        other_preflight = inspect_gb_2018(other_path)
        mismatch_blocked = False
        try:
            build_ingest_plan(
                command="ingest-gb-2018",
                jurisdiction="GB",
                source_id="UKIPO_OPEN_DATA_2018",
                path=path,
                preflight=other_preflight,
            )
        except ValueError as exc:
            mismatch_blocked = "preflight source path does not match" in str(exc)
        assert mismatch_blocked is True

    print(
        {
            "status": "PASS",
            "preflight_path_bound_to_plan": True,
            "registered_source_pinned_to_loader": True,
            "post_preflight_byte_change_blocked": True,
            "no_unplanned_source_object_created": True,
            "no_unplanned_country_rows_written": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
