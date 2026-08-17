from __future__ import annotations

import json

from app.contact_ingest.country_inference import run_country_inference
from app.contact_ingest.country_inference_run_activate import activate_persisted_run
from app.contact_ingest.country_inference_run_audit import audit_persisted_run
from app.contact_ingest.validate_country_inference_fixture import ENTITY_IDS, seed_fixture
from app.db import postgres_conn


def _source_countries() -> dict[str, str | None]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id::text, country_code
                FROM entity.entity
                WHERE entity_id = ANY(%s::uuid[])
                """,
                (list(ENTITY_IDS.values()),),
            )
            return {str(row["entity_id"]): row["country_code"] for row in cur.fetchall()}


def validate() -> dict[str, object]:
    seed_fixture()
    preview = run_country_inference(apply=False, batch_size=50)
    run_id = str(preview["run_id"])
    assert preview["status"] == "SUCCESS"
    assert preview["apply"] is False
    assert preview["accepted"] == 4

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            # Simulate an authoritative import landing after preview. Activation must
            # not override it and must leave this persisted inference inactive.
            cur.execute(
                "UPDATE entity.entity SET country_code = 'FR' WHERE entity_id = %s",
                (ENTITY_IDS["explicit_gb"],),
            )

            # A possible-but-not-valid international phone can legitimately sit at
            # the V1 acceptance floor (0.86). Persisted activation deliberately defers
            # this narrow confidence band until stronger corroboration arrives.
            cur.execute(
                """
                UPDATE contact.entity_country_inference
                SET confidence = 0.8600
                WHERE last_run_id = %s::uuid AND entity_id = %s
                """,
                (run_id, ENTITY_IDS["phone_gb"]),
            )

            # City-corpus evidence is useful for ranking, but multiple city tokens are
            # correlated rather than independent. Even with a high combined persisted
            # score, city-only evidence must remain inactive.
            city_only_evidence = [
                {
                    "country_code": "AU",
                    "kind": "CITY_CORPUS_MODEL",
                    "weight": 0.78,
                    "value": "Sydney",
                    "source": "fixture:city",
                },
                {
                    "country_code": "AU",
                    "kind": "CITY_CORPUS_MODEL",
                    "weight": 0.76,
                    "value": "New South Wales",
                    "source": "fixture:region",
                },
            ]
            cur.execute(
                """
                UPDATE contact.entity_country_inference
                SET confidence = 0.9500, evidence = %s::jsonb
                WHERE last_run_id = %s::uuid AND entity_id = %s
                """,
                (json.dumps(city_only_evidence), run_id, ENTITY_IDS["city_au"]),
            )
        conn.commit()

    source_before_activation = _source_countries()
    preflight = audit_persisted_run(run_id)
    assert preflight["activation_integrity_ready"] is True
    assert preflight["integrity_checks"]["accepted_with_source_country_now"] == 1
    assert preflight["activation_candidate_rows"] == preview["accepted"] - 1

    activated = activate_persisted_run(run_id)
    assert activated["status"] == "SUCCESS"
    assert activated["audited_candidate_rows_before"] == preview["accepted"] - 1
    assert activated["audited_candidates"] == preview["accepted"] - 1
    assert activated["deferred_low_confidence"] == 1
    assert activated["deferred_city_only"] == 1
    assert activated["safe_candidates"] == 1
    assert activated["newly_applied_rows"] == 1
    assert activated["applied_rows_after"] == 1
    assert activated["source_country_rows_after"] == 1
    assert activated["remaining_audited_candidates"] == 2
    assert activated["remaining_safe_candidates"] == 0
    assert activated["unknown_after"] == activated["unknown_before"] - 1
    assert activated["source_country_fields_mutated"] is False
    assert _source_countries() == source_before_activation

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id::text, applied_at
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                  AND status = 'ACCEPTED'
                ORDER BY entity_id
                """,
                (run_id,),
            )
            applied_by_entity = {
                str(row["entity_id"]): row["applied_at"] for row in cur.fetchall()
            }
            assert applied_by_entity[str(ENTITY_IDS["explicit_gb"])] is None
            assert applied_by_entity[str(ENTITY_IDS["phone_gb"])] is None
            assert applied_by_entity[str(ENTITY_IDS["domain_au"])] is not None
            assert applied_by_entity[str(ENTITY_IDS["city_au"])] is None

            cur.execute(
                "SELECT metrics->'activation' AS activation FROM contact.country_inference_run WHERE run_id = %s::uuid",
                (run_id,),
            )
            activation_metrics = cur.fetchone()["activation"]
            assert activation_metrics["newly_applied_rows"] == 1
            assert activation_metrics["deferred_low_confidence"] == 1
            assert activation_metrics["deferred_city_only"] == 1
            assert activation_metrics["source_country_fields_mutated"] is False

    after = audit_persisted_run(run_id)
    assert after["integrity_checks"]["already_applied_rows"] == 1
    assert after["integrity_checks"]["accepted_with_source_country_now"] == 1
    assert after["activation_candidate_rows"] == 2
    assert after["activation_integrity_ready"] is True

    # Idempotency: the two intentionally deferred rows remain visible for later V2
    # enrichment, but a second activation does no writes and still protects sources.
    source_before_second = _source_countries()
    second = activate_persisted_run(run_id)
    assert second["status"] == "SUCCESS"
    assert second["audited_candidate_rows_before"] == 2
    assert second["safe_candidates"] == 0
    assert second["deferred_low_confidence"] == 1
    assert second["deferred_city_only"] == 1
    assert second["newly_applied_rows"] == 0
    assert second["applied_rows_after"] == 1
    assert second["remaining_audited_candidates"] == 2
    assert second["remaining_safe_candidates"] == 0
    assert second["unknown_after"] == second["unknown_before"]
    assert _source_countries() == source_before_second

    return {
        "status": "PASS",
        "run_id": run_id,
        "preview_accepted": preview["accepted"],
        "preflight_candidates": preflight["activation_candidate_rows"],
        "first_activation": activated,
        "second_activation": second,
    }


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
