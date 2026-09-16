from __future__ import annotations

import json
from pathlib import Path
import uuid

from app.config import get_settings
from app.db import clickhouse_client, postgres_conn
from app.us_ttab.corpus_audit import build_manifest_acceptance
from app.us_ttab.corpus_manifest import MANIFEST_VERSION
from app.us_ttab.corpus_replay import execute_replay
from app.us_ttab.ingest import cleanup_ttab_package_outputs
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.publisher import TABLE_COLUMNS
from app.us_ttab.repository import list_ttab_packages


FILES = (
    "ci_ttab_manifest_historical.xml",
    "ci_ttab_manifest_daily.xml",
)
MANIFEST_RELATIVE = Path("manifests/us_ttab/ci_corpus.json")
HISTORICAL_PROCEEDING = "97658985"
DAILY_PROCEEDING = "79412016"
_FIXTURE_PROCEEDINGS = (HISTORICAL_PROCEEDING, DAILY_PROCEEDING)


def _is_fixture_source_name(name: str) -> bool:
    for fixture in FILES:
        item = Path(fixture)
        if name == fixture or (name.startswith(f"{item.stem}_") and name.endswith(item.suffix)):
            return True
    return False


def _fixture_package_ids() -> list[str]:
    return [
        str(row["package_id"])
        for row in list_ttab_packages()
        if str(row.get("file_name") or "") in FILES
    ]


def _assert_fixture_scope_isolated(raw_root: Path) -> None:
    foreign_registry = [
        str(row.get("file_name") or "")
        for row in list_ttab_packages()
        if str(row.get("file_name") or "") not in FILES
    ]
    foreign_raw: list[str] = []
    for directory in (raw_root / "incoming" / "us_ttab", raw_root / "archive" / "us_ttab"):
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if (
                path.is_file()
                and path.suffix.lower() in {".xml", ".zip"}
                and not _is_fixture_source_name(path.name)
            ):
                foreign_raw.append(str(path))
    if foreign_registry or foreign_raw:
        raise RuntimeError(
            "Refusing US TTAB runtime fixture against a non-isolated target: "
            f"foreign_registry={foreign_registry[:5]} foreign_raw={foreign_raw[:5]}"
        )


def _assert_fixture_facts_isolated() -> None:
    identities = ", ".join(f"'{value}'" for value in _FIXTURE_PROCEEDINGS)
    client = clickhouse_client()
    foreign: dict[str, int] = {}
    for table in TABLE_COLUMNS:
        count = int(
            client.query(
                f"SELECT count() FROM {table} WHERE proceeding_number NOT IN ({identities})"
            ).result_rows[0][0]
        )
        if count:
            foreign[table] = count
    if foreign:
        raise RuntimeError(
            f"Refusing US TTAB runtime fixture against existing non-fixture facts: {foreign}"
        )


HISTORICAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ttab-proceedings><transaction-date>20260902</transaction-date><proceeding-information><proceeding-entry>
<number>97658985</number><type-code>EXA</type-code><filing-date>20250620</filing-date>
<location-code>845</location-code><day-in-location>20250621</day-in-location>
<status-update-date>20251222</status-update-date><status-code>3</status-code>
<party-information><party><identifier>1206405</identifier><role-code>P</role-code><name>Hanako design SA</name>
<property-information><property><identifier>1613341</identifier><serial-number>97658985</serial-number>
<registration-number>7220244</registration-number><mark-text>BAIA</mark-text>
<tma-proceeding><proceeding-number>2024-101552</proceeding-number><proceeding-type-code>R</proceeding-type-code></tma-proceeding>
</property></property-information></party></party-information>
<prosecution-history><prosecution-entry><identifier>10</identifier><code>793</code><type-code>E</type-code>
<date>20251222</date><history-text>SUBMITTED FOR FINAL DECISION</history-text></prosecution-entry></prosecution-history>
</proceeding-entry></proceeding-information></ttab-proceedings>"""

DAILY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ttab-proceedings><transaction-date>20260514</transaction-date><proceeding-information><proceeding-entry>
<number>79412016</number><type-code>EXA</type-code><filing-date>20260414</filing-date>
<location-code>845</location-code><day-in-location>20260414</day-in-location>
<status-update-date>20260610</status-update-date><status-code>2</status-code>
<party-information><party><identifier>1248285</identifier><role-code>P</role-code>
<name>Octopus Energy Group Limited</name><property-information><property><identifier>1691989</identifier>
<serial-number>79412016</serial-number><mark-text>OEGEN</mark-text></property></property-information>
<address-information><proceeding-address><identifier>2284247</identifier><type-code>C</type-code>
<name>DAVID A.W. WONG</name><orgname>BARNES &amp; THORNBURG LLP</orgname>
<address-1>11 S. MERIDIAN ST</address-1><city>INDIANAPOLIS</city><state>IN</state>
<country>US</country><postcode>46204-3535</postcode></proceeding-address></address-information>
</party></party-information><prosecution-history><prosecution-entry><identifier>1</identifier><code>158</code>
<type-code>X</type-code><date>20260414</date><history-text>APPEAL TO BOARD</history-text>
</prosecution-entry></prosecution-history></proceeding-entry></proceeding-information></ttab-proceedings>"""


def _delete_registry(package_ids: list[str]) -> None:
    if not package_ids:
        return
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            for package_id in package_ids:
                cur.execute(
                    "DELETE FROM control.source_package WHERE package_id = %s", (package_id,)
                )
        conn.commit()


def _remove_fixture_files(raw_root: Path) -> None:
    for directory in (
        raw_root / "incoming" / "us_ttab",
        raw_root / "archive" / "us_ttab",
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
    raw_root = get_settings().raw_data_root
    _assert_fixture_scope_isolated(raw_root)
    ensure_ttab_schema()
    _assert_fixture_facts_isolated()
    incoming = raw_root / "incoming" / "us_ttab"
    incoming.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_root / MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    package_ids: list[str] = []

    try:
        _remove_fixture_files(raw_root)
        historical = incoming / FILES[0]
        daily = incoming / FILES[1]
        historical.write_text(HISTORICAL_XML, encoding="utf-8")
        daily.write_text(DAILY_XML, encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": MANIFEST_VERSION,
                    "expected_historical_packages": 1,
                    "expected_daily_packages": 1,
                    "daily_through": "2026-05-14",
                    "sources": [
                        {
                            "path": f"incoming/us_ttab/{FILES[0]}",
                            "source_kind": "TTAB_BULK_HISTORICAL_XML",
                            "snapshot_at": "2026-09-03T17:00:53Z",
                        },
                        {
                            "path": f"incoming/us_ttab/{FILES[1]}",
                            "source_kind": "TTAB_BULK_DAILY_XML",
                            "snapshot_at": "2026-05-14T12:00:00Z",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        dry_run = execute_replay(manifest_path, raw_root, apply=False, all_packages=True)
        if dry_run["status"] != "READY" or dry_run["remaining_count"] != 2:
            raise RuntimeError(f"TTAB manifest dry-run mismatch: {dry_run}")

        applied = execute_replay(manifest_path, raw_root, apply=True, all_packages=True)
        if applied["status"] != "COMPLETE" or applied["processed_count"] != 2:
            raise RuntimeError(f"TTAB manifest replay mismatch: {applied}")
        package_ids = _fixture_package_ids()
        if len(package_ids) != 2:
            raise RuntimeError(f"Expected two TTAB manifest packages, found {package_ids}")

        acceptance = build_manifest_acceptance(manifest_path, raw_root)
        if acceptance["status"] not in {"PASS", "PASS_WITH_WARNINGS"}:
            raise RuntimeError(f"TTAB manifest acceptance mismatch: {acceptance}")
        if acceptance["manifest_registry"]["successful_manifest_source_count"] != 2:
            raise RuntimeError(f"TTAB manifest success coverage mismatch: {acceptance}")
        if acceptance["legal_outcome_conclusion"] is not False:
            raise RuntimeError("TTAB manifest acceptance must not claim legal outcome")

        archived = [(raw_root / "archive" / "us_ttab" / name).is_file() for name in FILES]
        if archived != [True, True]:
            raise RuntimeError(f"TTAB archive movement mismatch: {archived}")

        counts = (
            clickhouse_client()
            .query(
                "SELECT count(), uniqExact(proceeding_number), uniqExact(source_package_id) "
                "FROM markorbit_facts.us_ttab_proceeding_history "
                f"WHERE proceeding_number IN ('{HISTORICAL_PROCEEDING}', '{DAILY_PROCEEDING}')"
            )
            .result_rows[0]
        )
        if tuple(int(item) for item in counts) != (2, 2, 2):
            raise RuntimeError(f"TTAB manifest history coverage mismatch: {counts}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "contract": "US_TTAB_MANIFEST_REPLAY_V1_RUNTIME_FIXTURE",
                    "dry_run": dry_run["status"],
                    "processed_count": applied["processed_count"],
                    "acceptance": acceptance["status"],
                    "archive_recovery": True,
                    "deadline_validity_inference": False,
                    "legal_outcome_conclusion": False,
                    "substantive_rights_conclusion": False,
                },
                indent=2,
            )
        )
    finally:
        fixture_package_ids = _fixture_package_ids()
        for package_id in reversed(fixture_package_ids):
            cleanup_ttab_package_outputs(uuid.UUID(package_id))
        _delete_registry(fixture_package_ids)
        _remove_fixture_files(raw_root)
        residual = int(
            clickhouse_client()
            .query(
                "SELECT count() FROM markorbit_facts.us_ttab_proceeding_history "
                f"WHERE proceeding_number IN ('{HISTORICAL_PROCEEDING}', '{DAILY_PROCEEDING}')"
            )
            .result_rows[0][0]
        )
        if residual:
            raise RuntimeError(f"TTAB manifest fixture cleanup failed: residual={residual}")


if __name__ == "__main__":
    main()
