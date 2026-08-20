from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import get_settings
from app.db import postgres_conn
from app.us_tsdr.migrations import ensure_tsdr_schema


def _default_outgoing_root() -> Path:
    return get_settings().raw_data_root / "outgoing" / "us" / "tsdr"


def export_batch(batch_key: str, *, outgoing_root: Path | None = None) -> dict[str, object]:
    """Export one planned weekly batch as a streaming JSONL task package."""
    ensure_tsdr_schema()
    outgoing_root = outgoing_root or _default_outgoing_root()

    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_id, batch_key, policy_version, backfill_bucket, status,
                       target_capacity, task_count, source_rank_from, source_serial_from,
                       source_rank_to, source_serial_to, planned_at, metrics
                FROM acquisition.us_tsdr_batch
                WHERE batch_key = %s
                FOR UPDATE
                """,
                (batch_key,),
            )
            batch = cur.fetchone()
            if batch is None:
                raise ValueError(f"unknown TSDR batch: {batch_key}")
            if batch["status"] not in {"PLANNED", "EXPORTED"}:
                raise ValueError(f"batch {batch_key} cannot be exported from {batch['status']}")

            cur.execute(
                """
                SELECT task_id, serial_number, task_type, priority_score, reason_codes,
                       applicant_country, source_rank, lifecycle_state,
                       source_attorney_fingerprint, source_attorney_present
                FROM acquisition.us_tsdr_task
                WHERE batch_id = %s
                ORDER BY priority_score DESC, source_rank DESC, serial_number, task_id
                """,
                (batch["batch_id"],),
            )
            tasks = cur.fetchall()

            batch_dir = outgoing_root / batch_key
            batch_dir.mkdir(parents=True, exist_ok=True)
            task_path = batch_dir / "tasks.jsonl"
            hasher = hashlib.sha256()
            with task_path.open("wb") as handle:
                for task in tasks:
                    payload = {
                        "task_id": str(task["task_id"]),
                        "serial_number": task["serial_number"],
                        "task_type": task["task_type"],
                        "priority_score": int(task["priority_score"]),
                        "reason_codes": list(task["reason_codes"] or []),
                        "applicant_country": task["applicant_country"],
                        "source_rank": int(task["source_rank"]),
                        "lifecycle_state": task["lifecycle_state"],
                        "source_attorney_fingerprint": task["source_attorney_fingerprint"],
                        "source_attorney_present": bool(task["source_attorney_present"]),
                    }
                    line = (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                    ).encode("utf-8")
                    hasher.update(line)
                    handle.write(line)
            digest = hasher.hexdigest()

            manifest = {
                "contract": "US_TSDR_WEEKLY_BATCH_V1",
                "batch_id": str(batch["batch_id"]),
                "batch_key": batch["batch_key"],
                "policy_version": batch["policy_version"],
                "backfill_bucket": int(batch["backfill_bucket"]),
                "task_count": int(batch["task_count"]),
                "target_capacity": int(batch["target_capacity"]),
                "source_watermark_from": [
                    int(batch["source_rank_from"]),
                    batch["source_serial_from"],
                ],
                "source_watermark_to": [
                    int(batch["source_rank_to"]),
                    batch["source_serial_to"],
                ],
                "task_file": "tasks.jsonl",
                "task_file_sha256": digest,
                "planned_at": batch["planned_at"].isoformat(),
                "metrics": batch["metrics"],
            }
            manifest_path = batch_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )

            cur.execute(
                """
                UPDATE acquisition.us_tsdr_task
                SET state = 'EXPORTED'
                WHERE batch_id = %s AND state = 'PLANNED'
                """,
                (batch["batch_id"],),
            )
            cur.execute(
                """
                UPDATE acquisition.us_tsdr_batch
                SET status = 'EXPORTED', exported_at = now(), export_path = %s,
                    export_sha256 = %s
                WHERE batch_id = %s
                """,
                (str(batch_dir), digest, batch["batch_id"]),
            )
        conn.commit()

    return {
        "batch_key": batch_key,
        "task_count": len(tasks),
        "directory": str(batch_dir),
        "manifest": str(manifest_path),
        "tasks": str(task_path),
        "tasks_sha256": digest,
        "status": "EXPORTED",
    }
