from __future__ import annotations

import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us_assignment.corpus_audit import build_manifest_acceptance
from app.us_assignment.corpus_manifest import MANIFEST_VERSION
from app.us_assignment.corpus_replay import execute_replay
from app.us_assignment.ingest import cleanup_assignment_package_outputs
from app.us_assignment.migrations import ensure_assignment_schema
from app.us_assignment.repository import list_assignment_packages


FILES = (
    "ci_assignment_manifest_snapshot.xml",
    "ci_assignment_manifest_daily.xml",
)
MANIFEST_RELATIVE = Path("manifests/us_assignment/ci_corpus.json")


def _xml(*, reel: str, frame: str, assignee: str, serial: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<trademark-assignments><assignment-information><assignment-entry>
<assignment><reel-no>{reel}</reel-no><frame-no>{frame}</frame-no>
<date-recorded>20260514</date-recorded><last-update-date>20260514</last-update-date>
<conveyance-text>ASSIGNS THE ENTIRE INTEREST</conveyance-text></assignment>
<assignors><assignor><name>Manifest Alpha LLC</name></assignor></assignors>
<assignees><assignee><name>{assignee}</name></assignee></assignees>
<properties><property><serial-number>{serial}</serial-number></property></properties>
</assignment-entry></assignment-information></trademark-assignments>"""


def _delete_registry(package_ids: list[str]) -> None:
    if not package_ids:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for package_id in package_ids:
                cur.execute("DELETE FROM control.source_package WHERE package_id = %s", (package_id,))
        conn.commit()


def _remove_fixture_files(raw_root: Path) -> None:
    for directory in (
        raw_root / "incoming" / "us_assignment",
        raw_root / "archive" / "us_assignment",
    ):
        if not directory.exists():
            continue
        for name in FILES:
            path = directory / name
            if path.exists():
                path.unlink()
            for candidate in directory.glob(f"{Path(name).stem}_*{Path(name).suffix}"):
                candidate.unlink()
    manifest = raw_root / MANIFEST_RELATIVE
    if manifest.exists():
        manifest.unlink()


def main() -> None:
    ensure_assignment_schema()
    raw_root = get_settings().raw_data_root
    incoming = raw_root / "incoming" / "us_assignment"
    incoming.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_root / MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    package_ids: list[str] = []

    try:
        _remove_fixture_files(raw_root)
        snapshot = incoming / FILES[0]
        daily = incoming / FILES[1]
        snapshot.write_text(
            _xml(reel="8100", frame="0001", assignee="Manifest Beta Inc.", serial="88998100"),
            encoding="utf-8",
        )
        daily.write_text(
            _xml(reel="8101", frame="0001", assignee="Manifest Gamma Corp.", serial="88998101"),
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": MANIFEST_VERSION,
                    "expected_snapshot_packages": 1,
                    "expected_daily_packages": 1,
                    "daily_through": "2026-05-14",
                    "sources": [
                        {
                            "path": f"incoming/us_assignment/{FILES[0]}",
                            "source_kind": "ASSIGNMENT_SNAPSHOT_XML",
                            "effective_date": "2026-05-13",
                        },
                        {
                            "path": f"incoming/us_assignment/{FILES[1]}",
                            "source_kind": "DAILY_ASSIGNMENT_XML",
                            "effective_date": "2026-05-14",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        dry_run = execute_replay(manifest_path, raw_root, apply=False, all_packages=True)
        if dry_run["status"] != "READY" or dry_run["remaining_count"] != 2:
            raise RuntimeError(f"Assignment manifest dry-run mismatch: {dry_run}")

        applied = execute_replay(manifest_path, raw_root, apply=True, all_packages=True)
        if applied["status"] != "COMPLETE" or applied["processed_count"] != 2:
            raise RuntimeError(f"Assignment manifest replay mismatch: {applied}")
        package_ids = [str(row["package_id"]) for row in list_assignment_packages()]
        if len(package_ids) != 2:
            raise RuntimeError(f"Expected two manifest packages, found {package_ids}")

        acceptance = build_manifest_acceptance(manifest_path, raw_root)
        if acceptance["status"] != "PASS":
            raise RuntimeError(f"Assignment manifest acceptance mismatch: {acceptance}")
        if acceptance["manifest_registry"]["successful_manifest_source_count"] != 2:
            raise RuntimeError(f"Assignment manifest success coverage mismatch: {acceptance}")

        archived = [
            (raw_root / "archive" / "us_assignment" / name).is_file()
            for name in FILES
        ]
        if archived != [True, True]:
            raise RuntimeError(f"Assignment archive movement mismatch: {archived}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_ASSIGNMENT_MANIFEST_REPLAY_V1_RUNTIME_FIXTURE",
                    "dry_run": dry_run["status"],
                    "processed_count": applied["processed_count"],
                    "acceptance": acceptance["status"],
                    "archive_recovery": True,
                    "legal_ownership_conclusion": False,
                },
                indent=2,
            )
        )
    finally:
        if not package_ids:
            package_ids = [str(row["package_id"]) for row in list_assignment_packages()]
        for package_id in reversed(package_ids):
            cleanup_assignment_package_outputs(uuid.UUID(package_id))
        _delete_registry(package_ids)
        _remove_fixture_files(raw_root)
        residual = int(
            clickhouse_client().query(
                "SELECT count() FROM markorbit_facts.us_assignment_record_history "
                "WHERE reel_frame_id IN ('8100/0001', '8101/0001')"
            ).result_rows[0][0]
        )
        if residual:
            raise RuntimeError(f"Assignment manifest fixture cleanup failed: residual={residual}")


if __name__ == "__main__":
    main()
