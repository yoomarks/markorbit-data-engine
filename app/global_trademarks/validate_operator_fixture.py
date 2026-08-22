from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from app.db import postgres_conn
from app.global_trademarks.gb_open_data import UK_FIELDS
from app.global_trademarks.migrations import migrate_global_trademark_schema


def _source_count() -> int:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM acquisition.global_trademark_source_object
                WHERE source_id = 'UKIPO_OPEN_DATA_2018'
                """
            )
            return int(cur.fetchone()["count"])


def _fixture(path: Path) -> None:
    fields = [*UK_FIELDS, *[f"Class{number}" for number in range(1, 46)]]
    row = {field: "" for field in fields}
    row.update(
        {
            "Trade Mark": "UK00000009999",
            "Mark Text": "OPERATOR CONTRACT",
            "Name": "Example Owner Limited",
            "Status": "Registered",
            "Filed": "2018-01-01",
            "Registered": "2019-01-01",
            "Renewal Due Date": "2029-01-01",
            "Class9": "1",
        }
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="|")
        writer.writeheader()
        writer.writerow(row)


def _run(*arguments: str) -> tuple[int, dict[str, object]]:
    process = subprocess.run(
        [sys.executable, "-m", "app.global_trademarks.cli", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if not process.stdout.strip():
        raise RuntimeError(
            f"operator fixture produced no stdout: rc={process.returncode} stderr={process.stderr}"
        )
    return process.returncode, json.loads(process.stdout)


def main() -> int:
    assert migrate_global_trademark_schema().ready
    before = _source_count()

    with tempfile.TemporaryDirectory(prefix="global-operator-fixture-") as temporary:
        path = Path(temporary) / "OpenDataDomestic2018.txt"
        _fixture(path)
        manifest_controls = (
            "--manifest-key",
            "UKIPO_OPEN_DATA_2018_FIXTURE",
            "--source-period-start",
            "2018-01-01",
            "--source-period-end",
            "2018-12-31",
        )
        compatibility = (
            "ingest-gb-2018",
            "--path",
            str(path),
            "--stream",
            "DOMESTIC",
            *manifest_controls,
        )
        generic = (
            "ingest-source",
            "--jurisdiction",
            "GB",
            "--source-id",
            "UKIPO_OPEN_DATA_2018",
            "--path",
            str(path),
            "--selector",
            "source_stream=DOMESTIC",
            *manifest_controls,
        )

        returncode, compatibility_plan = _run(*compatibility)
        assert returncode == 0
        assert compatibility_plan["status"] == "READY_TO_APPLY"
        assert compatibility_plan["mutation"] is False
        assert compatibility_plan["apply_required"] is True
        assert compatibility_plan["runtime_adapter_id"] == "UKIPO_2018_RUNTIME_V1"
        assert _source_count() == before

        returncode, generic_plan = _run(*generic)
        assert returncode == 0
        assert generic_plan["status"] == "READY_TO_APPLY"
        assert generic_plan["mutation"] is False
        assert generic_plan["runtime_adapter_id"] == "UKIPO_2018_RUNTIME_V1"
        assert generic_plan["jurisdiction"] == compatibility_plan["jurisdiction"] == "GB"
        assert generic_plan["source_id"] == compatibility_plan["source_id"]
        assert generic_plan["runtime_metadata"] == compatibility_plan["runtime_metadata"]
        assert _source_count() == before

        returncode, applied = _run(*generic, "--apply")
        assert returncode == 0
        assert applied["status"] == "COMPLETE"
        assert applied["processed_rows"] == 1
        assert applied["net_inserted_rows"] is None
        assert applied["manifest"]["objects_complete"] is True
        assert applied["manifest"]["attached_objects"] == 1
        assert _source_count() == before + 1

        returncode, compatibility_replay = _run(*compatibility, "--apply")
        assert returncode == 0
        assert compatibility_replay["status"] == "COMPLETE"
        assert compatibility_replay["processed_rows"] == 0
        assert _source_count() == before + 1

    print(
        {
            "status": "PASS",
            "default_ingest_is_no_write": True,
            "explicit_apply_required": True,
            "generic_and_compatibility_runtime_match": True,
            "generic_apply_uses_existing_manifest_boundary": True,
            "compatibility_replay_is_idempotent": True,
            "manifest_attached_before_ingest": True,
            "processed_rows_not_net_inserts": True,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
