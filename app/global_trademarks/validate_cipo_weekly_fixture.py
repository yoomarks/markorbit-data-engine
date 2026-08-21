from __future__ import annotations

import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core


def _write(path: Path, *, operation: str, mark_text: str | None = None) -> None:
    mark = f"<tmk:MarkVerbalElementText>{mark_text}</tmk:MarkVerbalElementText>" if mark_text else ""
    path.write_text(
        f"""<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark com:operationCategory="{operation}">
  <com:ST13ApplicationNumber>300000055512301</com:ST13ApplicationNumber>
  {mark}
</tmk:Trademark>
</root>""",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cipo-weekly-tombstone-") as temporary:
        root = Path(temporary)
        baseline = root / "CA-TMK-GLOBAL-fixture.xml"
        deletion = root / "CA-TMK-DELETE-fixture.xml"
        _write(baseline, operation="Update", mark_text="CIPO TOMBSTONE FIXTURE")
        _write(deletion, operation="Delete")

        assert ingest_cipo_st96_core(
            baseline,
            source_id="CIPO_GLOBAL_2025_06_14",
            batch_size=1,
        ) == 1
        assert ingest_cipo_st96_core(
            deletion,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT mark_text
                    FROM trademark_ca.st96_record
                    WHERE record_key = '555123:01'
                    """
                )
                row = cur.fetchone()
                assert row == {"mark_text": "CIPO TOMBSTONE FIXTURE"}

                cur.execute(
                    """
                    SELECT source_present, last_operation_category
                    FROM trademark_ca.record_state
                    WHERE record_key = '555123:01'
                    """
                )
                assert cur.fetchone() == {
                    "source_present": False,
                    "last_operation_category": "Delete",
                }

                cur.execute(
                    """
                    SELECT operation_category
                    FROM trademark_ca.record_operation
                    WHERE record_key = '555123:01'
                    ORDER BY observed_at, operation_category
                    """
                )
                assert {row["operation_category"] for row in cur.fetchall()} == {"Update", "Delete"}

                cur.execute(
                    """
                    SELECT source_record_role
                    FROM acquisition.global_trademark_record_source
                    WHERE jurisdiction = 'CA'
                      AND source_record_key = '555123:01'
                    """
                )
                assert {row["source_record_role"] for row in cur.fetchall()} == {
                    "CIPO_ST96_UPDATE",
                    "CIPO_ST96_DELETE",
                }

    print(
        {
            "status": "PASS",
            "delete_is_source_tombstone": True,
            "prior_record_preserved": True,
            "operation_history_preserved": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
