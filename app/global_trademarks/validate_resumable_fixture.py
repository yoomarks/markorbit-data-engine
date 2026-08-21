from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks import ca_st96
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core


def _source(root: Path) -> Path:
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


def _run_state(object_key: str) -> dict:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.status, r.checkpoint, r.rows_committed
                FROM acquisition.global_trademark_ingest_run AS r
                JOIN acquisition.global_trademark_source_object AS o
                  ON o.object_id = r.source_object_id
                WHERE o.source_id = 'CIPO_GLOBAL_2025_06_14'
                  AND o.object_key = %s
                  AND r.pipeline_id = 'CIPO_ST96_CORE_V1'
                """,
                (object_key,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("missing resumable fixture ingest run")
    return row


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="global-trademark-resume-") as temporary:
        path = _source(Path(temporary))
        original_iter = ca_st96.iter_cipo_records

        def interrupted_iter(source_path: Path):
            iterator = original_iter(source_path)
            yield next(iterator)
            raise RuntimeError("intentional fixture interruption")

        ca_st96.iter_cipo_records = interrupted_iter
        try:
            try:
                ingest_cipo_st96_core(path, batch_size=1)
            except RuntimeError as exc:
                assert str(exc) == "intentional fixture interruption"
            else:
                raise AssertionError("fixture ingestion should have been interrupted")
        finally:
            ca_st96.iter_cipo_records = original_iter

        failed = _run_state(path.name)
        assert failed == {"status": "FAILED", "checkpoint": 1, "rows_committed": 1}

        assert ingest_cipo_st96_core(path, batch_size=1) == 3
        complete = _run_state(path.name)
        assert complete == {"status": "COMPLETE", "checkpoint": 3, "rows_committed": 3}

        # A completed run is a no-op and returns the durable total.
        assert ingest_cipo_st96_core(path, batch_size=1) == 3

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

    print(
        {
            "status": "PASS",
            "durable_batch_commit": True,
            "resume_from_checkpoint": True,
            "completed_run_noop": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
