from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from app.cn_qcc.planner import create_batch_from_candidates
from app.cn_qcc.preflight import production_preflight
from app.cn_qcc.validate_fixture import _candidate
from app.db import postgres_conn


def _delete_batch(batch_id: str) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acquisition.cn_qcc_task WHERE batch_id = %s", (batch_id,))
            cur.execute("DELETE FROM acquisition.cn_qcc_batch WHERE batch_id = %s", (batch_id,))
        conn.commit()


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        outgoing = root / "outgoing"
        incoming = root / "incoming"
        outgoing.mkdir()
        incoming.mkdir()

        clean = production_preflight(
            capacity=10,
            refresh_days=180,
            cycle_interval_seconds=3600,
            stale_batch_hours=168,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert clean.ready, clean.as_dict()
        assert clean.open_batch_id == "", clean.as_dict()

        plan = create_batch_from_candidates(
            [_candidate()],
            capacity=10,
            refresh_days=180,
            backfill_bucket=20,
            backfill_entity_from="",
            backfill_entity_to="preflight-cursor",
            backfill_bucket_exhausted=False,
        )
        stale_time = datetime.now(timezone.utc) - timedelta(hours=200)
        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE acquisition.cn_qcc_batch SET planned_at = %s WHERE batch_id = %s",
                    (stale_time, plan.batch_id),
                )
            conn.commit()

        stale = production_preflight(
            capacity=10,
            refresh_days=180,
            cycle_interval_seconds=3600,
            stale_batch_hours=168,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert not stale.ready, stale.as_dict()
        assert stale.open_batch_id == plan.batch_id, stale.as_dict()
        checks = {check.name: check for check in stale.checks}
        assert not checks["open_batch_not_stale"].ok, stale.as_dict()
        assert checks["open_batch_not_stale"].severity == "BLOCKER", stale.as_dict()

        invalid = production_preflight(
            capacity=0,
            refresh_days=180,
            cycle_interval_seconds=10,
            stale_batch_hours=168,
            outgoing_root=outgoing,
            incoming_root=incoming,
        )
        assert not invalid.ready, invalid.as_dict()
        invalid_checks = {check.name: check for check in invalid.checks}
        assert not invalid_checks["capacity"].ok
        assert not invalid_checks["cycle_interval_seconds"].ok

        _delete_batch(plan.batch_id)

    print(
        json.dumps(
            {
                "status": "PASS",
                "clean_enablement_ready": True,
                "stale_batch_blocks": True,
                "invalid_config_blocks": True,
                "read_only_preflight": True,
            }
        )
    )


if __name__ == "__main__":
    main()
