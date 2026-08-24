from __future__ import annotations

import json

from app.contact_ingest.country_inference_work import (
    CHECKPOINT_VERSION,
    run_country_inference_resumable,
)
from app.contact_ingest.country_inference_work_guard import (
    ensure_country_inference_work_membership_guard,
)
from app.contact_ingest.validate_country_inference_fixture import seed_fixture
from app.db import postgres_conn


def validate() -> dict[str, object]:
    seed_fixture()
    ensure_country_inference_work_membership_guard()

    first = run_country_inference_resumable(
        apply=False,
        batch_size=10,
        max_entities=2,
    )
    assert first["status"] == "SUCCESS"
    assert first["evaluated"] == 2
    assert first["batches"] == 1
    assert first["work_engine"]["work_units"] == {
        "RUNNING": 0,
        "SUCCESS": 1,
        "FAILED": 0,
    }
    run_id = str(first["run_id"])

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT task_key, status, attempts, item_count,
                       range_lower::text, range_upper::text, member_fingerprint
                FROM contact.country_inference_work_unit
                WHERE run_id = %s::uuid AND checkpoint_version = %s
                """,
                (run_id, CHECKPOINT_VERSION),
            )
            before = dict(cur.fetchone())
            assert before["status"] == "SUCCESS"
            assert int(before["attempts"]) == 1
            assert int(before["item_count"]) == 2
            assert len(str(before["member_fingerprint"])) == 32

            cur.execute(
                """
                SELECT count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                  AND entity_id >= %s::uuid
                  AND entity_id <= %s::uuid
                """,
                (run_id, before["range_lower"], before["range_upper"]),
            )
            assert int(cur.fetchone()["row_count"] or 0) == 2

            # Reproduce the only ambiguous crash window: the batch result transaction
            # committed, but the durable work-unit SUCCESS transition was not retained.
            cur.execute(
                """
                UPDATE contact.country_inference_work_unit
                SET status = 'FAILED',
                    completed_at = NULL,
                    last_error = 'fixture: success transition lost',
                    updated_at = now()
                WHERE run_id = %s::uuid AND checkpoint_version = %s
                """,
                (run_id, CHECKPOINT_VERSION),
            )
            cur.execute(
                """
                UPDATE contact.country_inference_run
                SET status = 'FAILED',
                    error_message = 'fixture: success transition lost',
                    finished_at = now()
                WHERE run_id = %s::uuid
                """,
                (run_id,),
            )
        conn.commit()

    resumed = run_country_inference_resumable(resume_run_id=run_id)
    assert resumed["status"] == "SUCCESS"
    assert resumed["run_id"] == run_id
    assert resumed["evaluated"] == 2
    assert resumed["batches"] == 1
    assert resumed["work_engine"]["reconciled_committed_units"] == 1
    assert resumed["work_engine"]["work_units"] == {
        "RUNNING": 0,
        "SUCCESS": 1,
        "FAILED": 0,
    }

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, attempts, last_error, member_fingerprint
                FROM contact.country_inference_work_unit
                WHERE run_id = %s::uuid AND checkpoint_version = %s
                """,
                (run_id, CHECKPOINT_VERSION),
            )
            after = dict(cur.fetchone())
            assert after["status"] == "SUCCESS"
            # Reconciliation proves the result transaction rather than re-running it.
            assert int(after["attempts"]) == 1
            assert after["last_error"] == ""
            assert after["member_fingerprint"] == before["member_fingerprint"]

            cur.execute(
                """
                SELECT count(*) AS row_count
                FROM contact.entity_country_inference
                WHERE last_run_id = %s::uuid
                """,
                (run_id,),
            )
            assert int(cur.fetchone()["row_count"] or 0) == 2

    return {
        "status": "PASS",
        "run_id": run_id,
        "evaluated": resumed["evaluated"],
        "work_units": resumed["work_engine"]["work_units"],
        "reconciled_committed_units": resumed["work_engine"][
            "reconciled_committed_units"
        ],
        "attempts_after_reconcile": int(after["attempts"]),
        "member_fingerprint": after["member_fingerprint"],
    }


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
