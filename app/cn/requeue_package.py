from __future__ import annotations

import argparse
import json

from app.cn.run_guard import cn_ingestion_guard
from app.db import postgres_conn
from app.repository import update_package_status


def requeue_package(file_name: str) -> dict[str, object]:
    with cn_ingestion_guard() as acquired:
        if not acquired:
            raise RuntimeError("CN ingestion is currently busy; package was not requeued")

        with postgres_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT package_id, file_name, status, source_rank
                    FROM control.source_package
                    WHERE jurisdiction = 'CN' AND file_name = %s
                    ORDER BY source_rank DESC
                    LIMIT 1
                    """,
                    (file_name,),
                )
                row = cur.fetchone()

        if not row:
            raise KeyError(f"CN source package not registered: {file_name}")

        status = str(row["status"])
        if status == "PROCESSING":
            raise RuntimeError(
                f"{file_name} is PROCESSING; stop the active ingestion before requeueing"
            )

        package_id = str(row["package_id"])
        update_package_status(
            package_id,
            "INTERRUPTED",
            error_message=(
                "Manually requeued for deterministic full-package replay after "
                "ingestion-contract correction. Existing package outputs will be "
                "cleaned by retry before authoritative ZIP replay."
            ),
        )
        return {
            "status": "REQUEUED",
            "file_name": str(row["file_name"]),
            "package_id": package_id,
            "previous_status": status,
            "source_rank": int(row["source_rank"] or 0),
            "recovery_mode": "PACKAGE_REPLAY",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file_name")
    args = parser.parse_args()
    print(
        json.dumps(
            requeue_package(args.file_name),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
