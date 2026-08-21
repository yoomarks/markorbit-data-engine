from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks import ca_st96, gb_open_data, tm_link_seed
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core
from app.global_trademarks.gb_open_data import ingest_ukipo_2018
from app.global_trademarks.tm_link_seed import ingest_tm_link_applications


def _ca_source(root: Path) -> Path:
    path = root / "CA-TMK-resume-fixture.xml"
    path.write_text(
        """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark><com:ST13ApplicationNumber>300000020000000</com:ST13ApplicationNumber>
<tmk:MarkVerbalElementText>RESUME ONE</tmk:MarkVerbalElementText></tmk:Trademark>
<tmk:Trademark><com:ST13ApplicationNumber>300000020000100</com:ST13ApplicationNumber>
<tmk:MarkVerbalElementText>RESUME TWO</tmk:MarkVerbalElementText></tmk:Trademark>
<tmk:Trademark><com:ST13ApplicationNumber>300000020000200</com:ST13ApplicationNumber>
<tmk:MarkVerbalElementText>RESUME THREE</tmk:MarkVerbalElementText></tmk:Trademark>
</root>""",
        encoding="utf-8",
    )
    return path


def _gb_source(root: Path) -> Path:
    path = root / "UKIPO-resume-fixture.txt"
    fields = [
        "Trade Mark",
        "Mark Text",
        "Name",
        "Postcode",
        "Region",
        "Country",
        "Status",
        "Category of Mark",
        "Mark Type",
        "Series",
        "No of Marks in Series",
        "Filed",
        "Published",
        "Registered",
        "Expired",
        "Renewal Due Date",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        for number in range(1, 4):
            writer.writerow(
                {
                    "Trade Mark": f"UKRESUME{number:04d}",
                    "Mark Text": f"GB RESUME {number}",
                    "Name": "Resume Example Ltd",
                    "Status": "Registered",
                    "Filed": f"201{number}-01-01",
                    "Registered": f"201{number}-06-01",
                }
            )
    return path


def _tm_link_source(root: Path) -> Path:
    path = root / "EM-resume-applications.csv"
    fields = [
        "application_number",
        "application_country",
        "madrid_number",
        "current_status",
        "filing_date",
        "registration_date",
        "renewal_due_date",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for number in range(1, 4):
            writer.writerow(
                {
                    "application_number": f"9900000{number}",
                    "application_country": "EM",
                    "current_status": "Registered",
                    "filing_date": f"201{number}-02-01",
                    "registration_date": f"201{number}-09-01",
                    "renewal_due_date": f"202{number}-09-01",
                }
            )
    return path


def _run_state(*, source_id: str, object_key: str, pipeline_id: str) -> dict:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.status, r.checkpoint, r.rows_committed
                FROM acquisition.global_trademark_ingest_run AS r
                JOIN acquisition.global_trademark_source_object AS o
                  ON o.object_id = r.source_object_id
                WHERE o.source_id = %s
                  AND o.object_key = %s
                  AND r.pipeline_id = %s
                """,
                (source_id, object_key, pipeline_id),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"missing resumable fixture ingest run: {pipeline_id}")
    return row


def _assert_interrupted(callable_, expected_message: str) -> None:
    try:
        callable_()
    except RuntimeError as exc:
        assert str(exc) == expected_message
    else:
        raise AssertionError("fixture ingestion should have been interrupted")


def _validate_ca(root: Path) -> None:
    path = _ca_source(root)
    original_iter = ca_st96.iter_cipo_records

    def interrupted_iter(source_path: Path):
        iterator = original_iter(source_path)
        yield next(iterator)
        raise RuntimeError("intentional CIPO fixture interruption")

    ca_st96.iter_cipo_records = interrupted_iter
    try:
        _assert_interrupted(
            lambda: ingest_cipo_st96_core(path, batch_size=1),
            "intentional CIPO fixture interruption",
        )
    finally:
        ca_st96.iter_cipo_records = original_iter

    assert _run_state(
        source_id="CIPO_GLOBAL_2025_06_14",
        object_key=path.name,
        pipeline_id="CIPO_ST96_CORE_V1",
    ) == {"status": "FAILED", "checkpoint": 1, "rows_committed": 1}
    assert ingest_cipo_st96_core(path, batch_size=1) == 3
    assert _run_state(
        source_id="CIPO_GLOBAL_2025_06_14",
        object_key=path.name,
        pipeline_id="CIPO_ST96_CORE_V1",
    ) == {"status": "COMPLETE", "checkpoint": 3, "rows_committed": 3}
    assert ingest_cipo_st96_core(path, batch_size=1) == 3


def _validate_gb(root: Path) -> None:
    path = _gb_source(root)
    original_iter = gb_open_data.iter_ukipo_2018

    def interrupted_iter(source_path: Path):
        iterator = original_iter(source_path)
        yield next(iterator)
        raise RuntimeError("intentional UKIPO fixture interruption")

    gb_open_data.iter_ukipo_2018 = interrupted_iter
    try:
        _assert_interrupted(
            lambda: ingest_ukipo_2018(path, source_stream="DOMESTIC", batch_size=1),
            "intentional UKIPO fixture interruption",
        )
    finally:
        gb_open_data.iter_ukipo_2018 = original_iter

    assert _run_state(
        source_id="UKIPO_OPEN_DATA_2018",
        object_key=path.name,
        pipeline_id="UKIPO_2018_DOMESTIC_V1",
    ) == {"status": "FAILED", "checkpoint": 1, "rows_committed": 1}
    assert ingest_ukipo_2018(path, source_stream="DOMESTIC", batch_size=1) == 3
    assert _run_state(
        source_id="UKIPO_OPEN_DATA_2018",
        object_key=path.name,
        pipeline_id="UKIPO_2018_DOMESTIC_V1",
    ) == {"status": "COMPLETE", "checkpoint": 3, "rows_committed": 3}
    assert ingest_ukipo_2018(path, source_stream="DOMESTIC", batch_size=1) == 3


def _validate_tm_link(root: Path) -> None:
    path = _tm_link_source(root)
    original_iter = tm_link_seed._iter_csv

    def interrupted_iter(source_path: Path):
        iterator = original_iter(source_path)
        yield next(iterator)
        raise RuntimeError("intentional TM-Link fixture interruption")

    tm_link_seed._iter_csv = interrupted_iter
    try:
        _assert_interrupted(
            lambda: ingest_tm_link_applications(path, jurisdiction="EU", batch_size=1),
            "intentional TM-Link fixture interruption",
        )
    finally:
        tm_link_seed._iter_csv = original_iter

    assert _run_state(
        source_id="TM_LINK_EU",
        object_key=path.name,
        pipeline_id="TM_LINK_EU_APPLICATIONS_V1",
    ) == {"status": "FAILED", "checkpoint": 1, "rows_committed": 1}
    assert ingest_tm_link_applications(path, jurisdiction="EU", batch_size=1) == 3
    assert _run_state(
        source_id="TM_LINK_EU",
        object_key=path.name,
        pipeline_id="TM_LINK_EU_APPLICATIONS_V1",
    ) == {"status": "COMPLETE", "checkpoint": 3, "rows_committed": 3}
    assert ingest_tm_link_applications(path, jurisdiction="EU", batch_size=1) == 3


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="global-trademark-resume-") as temporary:
        root = Path(temporary)
        _validate_ca(root)
        _validate_gb(root)
        _validate_tm_link(root)

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM trademark_ca.st96_record
                    WHERE application_number IN ('200000', '200001', '200002')
                    """
                )
                assert cur.fetchone()["count"] == 3
                cur.execute(
                    "SELECT COUNT(*) AS count FROM trademark_gb.historical_record WHERE application_number LIKE 'UKRESUME%'"
                )
                assert cur.fetchone()["count"] == 3
                cur.execute(
                    "SELECT COUNT(*) AS count FROM trademark_eu.tm_link_seed WHERE application_number LIKE '9900000%'"
                )
                assert cur.fetchone()["count"] == 3

    print(
        {
            "status": "PASS",
            "durable_batch_commit": True,
            "resume_from_checkpoint": True,
            "completed_run_noop": True,
            "pipelines": ["CIPO_ST96_CORE_V1", "UKIPO_2018_DOMESTIC_V1", "TM_LINK_EU_APPLICATIONS_V1"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
