from __future__ import annotations

from typing import Any

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.contact_store import (
    _create_run,
    _json,
    _observe_channel,
    _upsert_channel,
    _upsert_person,
    _upsert_raw_record,
    _upsert_source,
)
from app.contact_ingest.entity_store import _create_or_update_entity, _link_trademark_mentions
from app.contact_ingest.migrations import ensure_contact_schema
from app.contact_ingest.models import ImportPlan


def apply_plan(plan: ImportPlan) -> dict[str, Any]:
    """Apply a deterministic plan to PostgreSQL; repeated source files are idempotent."""
    from app.db import postgres_conn

    metrics = {
        "version": CONTACT_INGEST_VERSION,
        "entities_touched": 0,
        "entities_created": 0,
        "people_touched": 0,
        "channels_touched": 0,
        "channel_observations_attempted": 0,
        "trademark_mentions_linked": 0,
        "raw_records_touched": 0,
    }
    # Schema installation is an additive, independently committed prerequisite so
    # a row-level import failure can still record durable failure evidence.
    ensure_contact_schema()
    with postgres_conn() as conn:
        try:
            with conn.cursor() as cur:
                source_id = _upsert_source(cur, plan)
                run_id = _create_run(cur, source_id)
                for table in plan.tables:
                    for entity in table.entities:
                        entity_id, match_method, match_confidence, created = _create_or_update_entity(
                            cur, entity, table.profile
                        )
                        metrics["entities_touched"] += 1
                        metrics["entities_created"] += int(created)
                        metrics["trademark_mentions_linked"] += _link_trademark_mentions(
                            cur, entity_id, entity, match_method
                        )
                        raw_record_id = _upsert_raw_record(
                            cur,
                            source_id=source_id,
                            source_member=table.source_member,
                            sheet_name=table.sheet_name,
                            profile=table.profile,
                            entity=entity,
                            entity_id=entity_id,
                            match_method=match_method,
                            match_confidence=match_confidence,
                        )
                        metrics["raw_records_touched"] += 1

                        for channel in entity.channels:
                            channel_id = _upsert_channel(
                                cur, entity_id=entity_id, person_id=None, channel=channel
                            )
                            metrics["channels_touched"] += 1
                            _observe_channel(
                                cur,
                                channel_id=channel_id,
                                source_id=source_id,
                                raw_record_id=raw_record_id,
                                source_member=table.source_member,
                                sheet_name=table.sheet_name,
                                channel=channel,
                            )
                            metrics["channel_observations_attempted"] += 1

                        for person in entity.people:
                            if not person.normalized_name:
                                continue
                            person_id = _upsert_person(
                                cur,
                                entity_id=entity_id,
                                person=person,
                                source_id=source_id,
                                country_code=entity.country_code,
                            )
                            metrics["people_touched"] += 1
                            for channel in person.channels:
                                channel_id = _upsert_channel(
                                    cur, entity_id=None, person_id=person_id, channel=channel
                                )
                                metrics["channels_touched"] += 1
                                _observe_channel(
                                    cur,
                                    channel_id=channel_id,
                                    source_id=source_id,
                                    raw_record_id=raw_record_id,
                                    source_member=table.source_member,
                                    sheet_name=table.sheet_name,
                                    channel=channel,
                                )
                                metrics["channel_observations_attempted"] += 1

                cur.execute(
                    """
                    UPDATE contact.import_run
                    SET status = 'SUCCESS', finished_at = now(), metrics = %s::jsonb
                    WHERE run_id = %s
                    """,
                    (_json(metrics), run_id),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            # Best-effort failure evidence in a fresh transaction.
            try:
                with conn.cursor() as cur:
                    source_id = _upsert_source(cur, plan)
                    cur.execute(
                        """
                        INSERT INTO contact.import_run(
                            source_id, status, apply_mode, error_message, finished_at
                        ) VALUES (%s, 'FAILED', true, %s, now())
                        """,
                        (source_id, f"{type(exc).__name__}: {exc}"),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
            raise

    return {"status": "SUCCESS", **metrics}
