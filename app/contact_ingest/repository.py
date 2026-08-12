from __future__ import annotations

import time
from typing import Any

from psycopg.errors import DeadlockDetected, LockNotAvailable

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.case_contact_store import (
    ensure_case_contact_schema,
    observe_case_contact,
    upsert_unresolved_raw_record,
)
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


CONTACT_APPLY_MAX_ATTEMPTS = 4
_TRANSIENT_LOCK_ERRORS = (DeadlockDetected, LockNotAvailable)


def _new_metrics() -> dict[str, Any]:
    return {
        "version": CONTACT_INGEST_VERSION,
        "entities_touched": 0,
        "entities_created": 0,
        "people_touched": 0,
        "channels_touched": 0,
        "channel_observations_attempted": 0,
        "trademark_mentions_linked": 0,
        "raw_records_touched": 0,
        "case_contact_records_touched": 0,
        "unresolved_case_channels_touched": 0,
    }


def _apply_transaction(plan: ImportPlan) -> dict[str, Any]:
    from app.db import postgres_conn

    metrics = _new_metrics()
    with postgres_conn() as conn:
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

                for record in table.case_contacts:
                    raw_record_id = upsert_unresolved_raw_record(
                        cur,
                        source_id=source_id,
                        source_member=table.source_member,
                        sheet_name=table.sheet_name,
                        profile=table.profile,
                        record=record,
                    )
                    metrics["raw_records_touched"] += 1
                    metrics["case_contact_records_touched"] += 1
                    for channel in record.channels:
                        observe_case_contact(
                            cur,
                            plan=plan,
                            source_id=source_id,
                            raw_record_id=raw_record_id,
                            source_member=table.source_member,
                            sheet_name=table.sheet_name,
                            record=record,
                            channel=channel,
                        )
                        metrics["unresolved_case_channels_touched"] += 1
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
    return {"status": "SUCCESS", **metrics}


def _record_failure(plan: ImportPlan, exc: Exception) -> None:
    """Best-effort durable failure evidence after all retry attempts are exhausted."""
    from app.db import postgres_conn

    try:
        with postgres_conn() as conn:
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
        # Failure evidence must never replace the original exception.
        return


def apply_plan(plan: ImportPlan) -> dict[str, Any]:
    """Apply a deterministic plan; lock/deadlock aborts retry from a clean transaction."""
    # Schema installation is an additive, independently committed prerequisite so
    # a row-level import failure can still record durable failure evidence.
    ensure_contact_schema()
    ensure_case_contact_schema()

    last_error: Exception | None = None
    for attempt in range(1, CONTACT_APPLY_MAX_ATTEMPTS + 1):
        try:
            return _apply_transaction(plan)
        except _TRANSIENT_LOCK_ERRORS as exc:
            last_error = exc
            if attempt >= CONTACT_APPLY_MAX_ATTEMPTS:
                break
            # PostgreSQL has rolled the whole transaction back. A short bounded
            # backoff is safe because source SHA/idempotence contracts are unchanged.
            time.sleep(0.5 * (2 ** (attempt - 1)))
        except Exception as exc:
            _record_failure(plan, exc)
            raise

    if last_error is None:  # pragma: no cover - defensive guard
        raise RuntimeError("Contact import retry loop ended without a result")
    _record_failure(plan, last_error)
    raise last_error
