from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from app.cn_qcc.migrations import ensure_qcc_schema
from app.db import postgres_conn


EXPORT_COLUMNS = (
    "task_id",
    "entity_id",
    "applicant_name",
    "normalized_name",
    "applicant_address",
    "country_code",
    "region_code",
    "city",
    "trademark_count",
    "latest_application_number",
    "source_rank",
    "priority_score",
    "reason_codes",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_batch(batch_id: str, output_path: Path) -> dict[str, object]:
    ensure_qcc_schema()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, batch_key FROM acquisition.cn_qcc_batch WHERE batch_id = %s FOR UPDATE",
                (batch_id,),
            )
            batch = cur.fetchone()
            if not batch:
                raise ValueError(f"unknown CN QCC batch: {batch_id}")
            if batch["status"] not in {"PLANNED", "EXPORTED"}:
                raise ValueError(f"CN QCC batch is not exportable: {batch['status']}")
            cur.execute(
                """
                SELECT task_id, entity_id, applicant_name, normalized_name,
                       applicant_address, country_code, region_code, city,
                       trademark_count, latest_application_number, source_rank,
                       priority_score, reason_codes
                FROM acquisition.cn_qcc_task
                WHERE batch_id = %s
                ORDER BY priority_score DESC, source_rank DESC, entity_id
                """,
                (batch_id,),
            )
            rows = cur.fetchall()

        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "task_id": row["task_id"],
                        "entity_id": row["entity_id"],
                        "applicant_name": row["applicant_name"],
                        "normalized_name": row["normalized_name"],
                        "applicant_address": row["applicant_address"],
                        "country_code": row["country_code"],
                        "region_code": row["region_code"],
                        "city": row["city"],
                        "trademark_count": row["trademark_count"],
                        "latest_application_number": row["latest_application_number"],
                        "source_rank": row["source_rank"],
                        "priority_score": row["priority_score"],
                        "reason_codes": ";".join(row["reason_codes"] or []),
                    }
                )

        export_sha256 = _sha256_file(output_path)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_batch
                SET status = 'EXPORTED', exported_at = COALESCE(exported_at, now()),
                    export_path = %s, export_sha256 = %s
                WHERE batch_id = %s
                """,
                (str(output_path), export_sha256, batch_id),
            )
            cur.execute(
                """
                UPDATE acquisition.cn_qcc_task
                SET state = 'EXPORTED'
                WHERE batch_id = %s AND state = 'PLANNED'
                """,
                (batch_id,),
            )
        conn.commit()
    return {
        "batch_id": batch_id,
        "batch_key": str(batch["batch_key"]),
        "task_count": len(rows),
        "output_path": str(output_path),
        "sha256": export_sha256,
    }


__all__ = ["EXPORT_COLUMNS", "export_batch"]
