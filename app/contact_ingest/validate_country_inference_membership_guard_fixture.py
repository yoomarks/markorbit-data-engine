from __future__ import annotations

import json
import uuid

from app.contact_ingest import country_inference as engine
from app.contact_ingest.country_inference_work import (
    CHECKPOINT_VERSION,
    run_country_inference_resumable,
)
from app.contact_ingest.country_inference_work_guard import (
    ensure_country_inference_work_membership_guard,
)
from app.contact_ingest.validate_country_inference_fixture import ENTITY_IDS, seed_fixture
from app.db import postgres_conn


ENDPOINT_ID = uuid.UUID("c0164000-0000-0000-0000-000000000020")
REPLACEMENT_ID = uuid.UUID("c0164000-0000-0000-0000-000000000017")


def _entity_key(name: str) -> str:
    return (name.encode("utf-8").hex() + "0" * 64)[:64]


def _delete_custom_entities() -> None:
    ids = [ENDPOINT_ID, REPLACEMENT_ID]
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM contact.channel WHERE entity_id = ANY(%s::uuid[])", (ids,))
            cur.execute("DELETE FROM entity.entity WHERE entity_id = ANY(%s::uuid[])", (ids,))
        conn.commit()


def _insert_contact(entity_id: uuid.UUID, name: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO entity.entity(
                    entity_id, entity_key, entity_type, canonical_name,
                    normalized_name, normalized_address, country_code, city,
                    status, resolution_method, source_primary, confidence_score
                ) VALUES (
                    %s, %s, 'ORGANIZATION', %s, lower(%s), '', NULL, NULL,
                    'CANDIDATE', 'FIXTURE', 'FIXTURE', 1.0
                )
                """,
                (entity_id, _entity_key(name), name, name),
            )
            cur.execute(
                """
                INSERT INTO contact.channel(
                    entity_id, channel_type, channel_value, normalized_value
                ) VALUES (%s, 'EMAIL', %s, %s)
                """,
                (entity_id, f"{name}@fixture.example", f"{name}@fixture.example".casefold()),
            )
        conn.commit()


def validate() -> dict[str, object]:
    seed_fixture()
    _delete_custom_entities()
    _insert_contact(ENDPOINT_ID, "Membership Endpoint")
    ensure_country_inference_work_membership_guard()

    original_load_context = engine._load_context

    def _interrupt_after_ledger(entity_ids: list[str]):
        raise RuntimeError(f"fixture interruption after ledger for {len(entity_ids)} entities")

    engine._load_context = _interrupt_after_ledger
    try:
        try:
            run_country_inference_resumable(
                apply=False,
                batch_size=10,
                max_entities=6,
            )
        except RuntimeError as exc:
            assert "fixture interruption after ledger" in str(exc)
        else:
            raise AssertionError("fixture interruption did not fail the first attempt")
    finally:
        engine._load_context = original_load_context

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.run_id::text, w.task_key, w.status, w.attempts,
                       w.item_count, w.range_lower::text, w.range_upper::text,
                       w.member_fingerprint
                FROM contact.country_inference_run AS r
                JOIN contact.country_inference_work_unit AS w ON w.run_id = r.run_id
                WHERE r.status = 'FAILED' AND w.checkpoint_version = %s
                ORDER BY r.started_at DESC
                LIMIT 1
                """,
                (CHECKPOINT_VERSION,),
            )
            failed = dict(cur.fetchone())

    assert failed["status"] == "FAILED"
    assert int(failed["attempts"]) == 1
    assert int(failed["item_count"]) == 6
    assert failed["range_lower"] == str(ENTITY_IDS["explicit_gb"])
    assert failed["range_upper"] == str(ENDPOINT_ID)
    original_fingerprint = str(failed["member_fingerprint"])
    assert len(original_fingerprint) == 32

    # Preserve the same range endpoints and cardinality while swapping one interior
    # candidate. Range+count validation alone therefore passes; only the durable
    # membership fingerprint can distinguish this from the original work unit.
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE entity.entity SET country_code = 'AU' WHERE entity_id = %s",
                (ENTITY_IDS["domain_au"],),
            )
        conn.commit()
    _insert_contact(REPLACEMENT_ID, "Membership Replacement")

    try:
        run_country_inference_resumable(resume_run_id=str(failed["run_id"]))
    except Exception as exc:
        assert "membership drift" in str(exc).casefold()
        blocked_error = f"{type(exc).__name__}: {exc}"
    else:
        raise AssertionError("membership drift was not blocked")

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, attempts, member_fingerprint
                FROM contact.country_inference_work_unit
                WHERE run_id = %s::uuid AND checkpoint_version = %s
                """,
                (failed["run_id"], CHECKPOINT_VERSION),
            )
            after = dict(cur.fetchone())
            assert after["status"] == "FAILED"
            assert int(after["attempts"]) == 1
            assert str(after["member_fingerprint"]) == original_fingerprint

            cur.execute(
                """
                SELECT count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                """,
                (failed["run_id"],),
            )
            assert int(cur.fetchone()["row_count"] or 0) == 0

    _delete_custom_entities()
    return {
        "status": "PASS",
        "run_id": failed["run_id"],
        "range_lower": failed["range_lower"],
        "range_upper": failed["range_upper"],
        "item_count": int(failed["item_count"]),
        "attempts": int(after["attempts"]),
        "member_fingerprint": original_fingerprint,
        "blocked_error": blocked_error,
    }


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
