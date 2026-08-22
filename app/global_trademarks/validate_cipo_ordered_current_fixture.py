from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.ca_st96 import ingest_cipo_st96_core
from app.global_trademarks.manifest import attach_manifest_object, upsert_source_manifest
from app.global_trademarks.migrations import migrate_global_trademark_schema
from app.global_trademarks.source_objects import register_source_object


_RECORD_KEY = "777777:00"


def _update_xml(mark_text: str, owner: str, goods: str) -> str:
    return f"""<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark com:operationCategory="Update">
  <com:ST13ApplicationNumber>300000077777700</com:ST13ApplicationNumber>
  <com:RegistrationNumber>TMA777777</com:RegistrationNumber>
  <tmk:MarkSignificantVerbalElementText>{mark_text}</tmk:MarkSignificantVerbalElementText>
  <tmk:ApplicantBag><tmk:Applicant>
    <com:LegalEntityName>{owner}</com:LegalEntityName>
    <com:Contact com:languageCode="en"><com:Name><com:EntityName>{owner}</com:EntityName></com:Name></com:Contact>
  </tmk:Applicant></tmk:ApplicantBag>
  <tmk:GoodsServicesBag><tmk:GoodsServices><tmk:ClassDescriptionBag>
    <tmk:ClassDescription><com:ClassificationVersion>12</com:ClassificationVersion>
      <tmk:ClassNumber>9</tmk:ClassNumber>
      <tmk:GoodsServicesDescriptionText com:sequenceNumber="Goods1" com:languageCode="en">{goods}</tmk:GoodsServicesDescriptionText>
    </tmk:ClassDescription>
  </tmk:ClassDescriptionBag></tmk:GoodsServices></tmk:GoodsServicesBag>
</tmk:Trademark>
</root>"""


def _delete_xml() -> str:
    return """<root xmlns:tmk="urn:tmk" xmlns:com="urn:com">
<tmk:Trademark com:operationCategory="Delete">
  <com:ST13ApplicationNumber>300000077777700</com:ST13ApplicationNumber>
</tmk:Trademark>
</root>"""


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _attach_release(
    path: Path,
    *,
    source_id: str,
    manifest_key: str,
    period_end: date,
    precedence: int,
    sequence: int,
) -> None:
    source_object_id = register_source_object(
        jurisdiction="CA",
        source_id=source_id,
        path=path,
        source_period_start=period_end,
        source_period_end=period_end,
    )
    manifest = upsert_source_manifest(
        jurisdiction="CA",
        source_id=source_id,
        manifest_key=manifest_key,
        source_period_start=period_end,
        source_period_end=period_end,
        source_sequence=sequence,
        source_precedence=precedence,
        expected_objects=1,
        parser_version="CIPO_ST96_CORE_V1",
        mapping_version="COUNTRY_NATIVE_V1",
    )
    attach_manifest_object(
        manifest_id=manifest.manifest_id,
        source_object_id=source_object_id,
        part_sequence=1,
    )


def _current_snapshot() -> dict[str, object]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.mark_text, s.source_present, s.last_operation_category,
                       s.last_source_object_id,
                       o.source_period_end, o.source_precedence, o.source_sequence,
                       o.manifest_key
                FROM trademark_ca.st96_record AS r
                JOIN trademark_ca.record_state AS s ON s.record_key = r.record_key
                LEFT JOIN trademark_ca.current_source_order AS o ON o.record_key = r.record_key
                WHERE r.record_key = %s
                """,
                (_RECORD_KEY,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError("missing ordered-current fixture record")
    return row


def _view_count(view_name: str) -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS count FROM trademark_ca.{view_name} WHERE record_key = %s",
                (_RECORD_KEY,),
            )
            return int(cur.fetchone()["count"])


def _history_count(table_name: str) -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS count FROM trademark_ca.{table_name} WHERE record_key = %s",
                (_RECORD_KEY,),
            )
            return int(cur.fetchone()["count"])


def main() -> int:
    migration = migrate_global_trademark_schema()
    assert migration.ready
    assert migration.ca_current_source_order_ready

    with tempfile.TemporaryDirectory(prefix="cipo-ordered-current-") as temporary:
        root = Path(temporary)
        baseline = root / "CA-TMK-GLOBAL-ordered.xml"
        newest = root / "CA-TMK-UPDATE-20250625.xml"
        stale = root / "CA-TMK-UPDATE-20250618.xml"
        deletion = root / "CA-TMK-DELETE-20250702.xml"
        unordered = root / "CA-TMK-UPDATE-unordered.xml"

        _write(baseline, _update_xml("BASELINE MARK", "Baseline Owner", "baseline software"))
        _write(newest, _update_xml("NEWEST MARK", "Newest Owner", "newest software"))
        _write(stale, _update_xml("STALE MARK", "Stale Owner", "stale software"))
        _write(deletion, _delete_xml())
        _write(unordered, _update_xml("UNORDERED MARK", "Unordered Owner", "unordered software"))

        _attach_release(
            baseline,
            source_id="CIPO_GLOBAL_2025_06_14",
            manifest_key="CIPO_GLOBAL_ORDERED_FIXTURE",
            period_end=date(2025, 6, 14),
            precedence=100,
            sequence=1,
        )
        _attach_release(
            newest,
            source_id="CIPO_WEEKLY",
            manifest_key="CIPO_WEEKLY_2025_06_25_FIXTURE",
            period_end=date(2025, 6, 25),
            precedence=200,
            sequence=11,
        )
        _attach_release(
            stale,
            source_id="CIPO_WEEKLY",
            manifest_key="CIPO_WEEKLY_2025_06_18_FIXTURE",
            period_end=date(2025, 6, 18),
            precedence=200,
            sequence=10,
        )
        _attach_release(
            deletion,
            source_id="CIPO_WEEKLY",
            manifest_key="CIPO_WEEKLY_2025_07_02_DELETE_FIXTURE",
            period_end=date(2025, 7, 2),
            precedence=200,
            sequence=12,
        )

        assert ingest_cipo_st96_core(
            baseline,
            source_id="CIPO_GLOBAL_2025_06_14",
            batch_size=1,
        ) == 1
        baseline_state = _current_snapshot()
        assert baseline_state["mark_text"] == "BASELINE MARK"
        assert baseline_state["source_present"] is True
        assert baseline_state["source_period_end"] == date(2025, 6, 14)
        assert _view_count("party_current") == 1
        assert _view_count("goods_service_current") == 1

        assert ingest_cipo_st96_core(
            newest,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1
        newest_state = _current_snapshot()
        newest_source_object = newest_state["last_source_object_id"]
        assert newest_state["mark_text"] == "NEWEST MARK"
        assert newest_state["source_period_end"] == date(2025, 6, 25)
        assert newest_state["manifest_key"] == "CIPO_WEEKLY_2025_06_25_FIXTURE"
        assert _view_count("party_current") == 1
        assert _view_count("goods_service_current") == 1

        party_history_before_stale = _history_count("party")
        goods_history_before_stale = _history_count("goods_service")
        assert ingest_cipo_st96_core(
            stale,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1
        stale_attempt_state = _current_snapshot()
        assert stale_attempt_state["mark_text"] == "NEWEST MARK"
        assert stale_attempt_state["last_source_object_id"] == newest_source_object
        assert stale_attempt_state["source_period_end"] == date(2025, 6, 25)
        assert _history_count("party") == party_history_before_stale + 1
        assert _history_count("goods_service") == goods_history_before_stale + 1

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT party_name FROM trademark_ca.party_current WHERE record_key = %s",
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["party_name"] == "Newest Owner"
                cur.execute(
                    "SELECT text_value FROM trademark_ca.goods_service_current WHERE record_key = %s",
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["text_value"] == "newest software"
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM trademark_ca.record_operation
                    WHERE record_key = %s AND operation_category = 'Update'
                    """,
                    (_RECORD_KEY,),
                )
                assert cur.fetchone()["count"] == 3

        assert ingest_cipo_st96_core(
            deletion,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1
        deleted_state = _current_snapshot()
        assert deleted_state["mark_text"] == "NEWEST MARK"
        assert deleted_state["source_present"] is False
        assert deleted_state["last_operation_category"] == "Delete"
        assert deleted_state["source_period_end"] == date(2025, 7, 2)
        assert _view_count("record_current") == 0
        assert _view_count("party_current") == 0
        assert _view_count("goods_service_current") == 0

        party_history_before_unordered = _history_count("party")
        assert ingest_cipo_st96_core(
            unordered,
            source_id="CIPO_WEEKLY",
            batch_size=1,
        ) == 1
        unordered_attempt_state = _current_snapshot()
        assert unordered_attempt_state["source_present"] is False
        assert unordered_attempt_state["last_operation_category"] == "Delete"
        assert unordered_attempt_state["source_period_end"] == date(2025, 7, 2)
        assert _history_count("party") == party_history_before_unordered + 1
        assert _view_count("party_current") == 0

    print(
        {
            "status": "PASS",
            "ordered_by_source_coverage_not_ingest_time": True,
            "stale_weekly_cannot_regress_current": True,
            "stale_observation_history_preserved": True,
            "newer_delete_tombstone_wins": True,
            "unordered_legacy_source_cannot_regress_ordered_current": True,
            "current_child_views_follow_winning_source_object": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
