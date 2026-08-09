from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from typing import Iterator
import uuid
import zipfile

from app.db import clickhouse_client
from app.repository import create_job_run, finish_job_run, get_package, update_package_status
from app.scanner import sha256_file
from app.us_ttab import TTAB_JURISDICTION, TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.model import TTABProceedingBundle
from app.us_ttab.parser import iter_ttab_bundles
from app.us_ttab.publisher import TABLE_COLUMNS, TTABBatchPublisher


def _iter_source(path: Path) -> Iterator[tuple[str, TTABProceedingBundle]]:
    if path.suffix.lower() == ".xml":
        for bundle in iter_ttab_bundles(path):
            yield path.name, bundle
        return
    if path.suffix.lower() != ".zip":
        raise RuntimeError(f"Unsupported US TTAB source: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
        if not members:
            raise RuntimeError(f"US TTAB ZIP contains no XML member: {path.name}")
        for member in members:
            with archive.open(member, "r") as stream:
                for bundle in iter_ttab_bundles(stream):
                    yield member.filename, bundle


def cleanup_ttab_package_outputs(package_id: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_id)
    for table in TABLE_COLUMNS:
        client.command(
            f"ALTER TABLE {table} DELETE WHERE source_package_id = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )


def _archive(path: Path, raw_root: Path) -> Path:
    archive_dir = raw_root / "archive" / "us_ttab"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / path.name
    if path.resolve() == destination.resolve():
        return destination
    if destination.exists():
        if sha256_file(path) == sha256_file(destination):
            path.unlink()
            return destination
        destination = archive_dir / f"{path.stem}_{sha256_file(path)[:8]}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def _snapshot_at(meta: dict[str, object]) -> datetime:
    value = str(meta.get("partition_value") or "")
    if not value:
        raise RuntimeError("US TTAB package lacks explicit snapshot_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Invalid US TTAB snapshot_at metadata: {value}") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("US TTAB snapshot_at metadata must be timezone-aware")
    return parsed


def _assert_snapshot_slot_available(
    proceeding_number: str,
    snapshot_at: datetime,
    package_uuid: uuid.UUID,
) -> None:
    timestamp = snapshot_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
    count = int(
        clickhouse_client().query(
            f"""
            SELECT count()
            FROM markorbit_facts.us_ttab_proceeding_history
            WHERE proceeding_number = '{proceeding_number}'
              AND source_snapshot_at = toDateTime64('{timestamp}', 3, 'UTC')
              AND source_package_id != toUUID('{package_uuid}')
            """
        ).result_rows[0][0]
    )
    if count:
        raise RuntimeError(
            "A US TTAB snapshot for the same proceeding and millisecond is already present. "
            f"Proceeding={proceeding_number} snapshot_at={snapshot_at.isoformat()}. "
            "Sub-millisecond or registration-order precedence is not modeled; materialize "
            "the authoritative snapshots with distinct source timestamps."
        )


def ingest_ttab_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    *,
    trigger_type: str = "MANUAL_US_TTAB",
    retrying: bool = False,
) -> dict[str, object]:
    ensure_ttab_schema()
    package_uuid = uuid.UUID(str(package_id))
    meta = get_package(str(package_uuid))
    if meta["jurisdiction"] != TTAB_JURISDICTION:
        raise RuntimeError("Refusing to ingest a non-US_TTAB package")
    snapshot_at = _snapshot_at(meta)
    expected_sha = str(meta.get("sha256") or "").lower()
    actual_sha = sha256_file(path).lower()

    run_id = create_job_run(
        job_type="US_TTAB_PACKAGE_INGESTION",
        trigger_type=trigger_type,
        payload={
            "package_id": str(package_uuid),
            "path": str(path),
            "source_kind": meta["package_kind"],
            "source_rank": meta["source_rank"],
            "snapshot_at": snapshot_at.isoformat(),
        },
    )
    publisher = TTABBatchPublisher(
        clickhouse_client(),
        package_id=package_uuid,
        source_kind=str(meta["package_kind"]),
        source_snapshot_at=snapshot_at,
        source_rank=int(meta["source_rank"]),
    )
    seen: set[str] = set()
    source_files: set[str] = set()
    malformed_serials: set[str] = set()
    proceeding_types: dict[str, int] = {}
    empty_docket_count = 0

    try:
        update_package_status(str(package_uuid), "PROCESSING")
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"US TTAB SHA-256 mismatch: registered={expected_sha} actual={actual_sha}"
            )
        if retrying:
            cleanup_ttab_package_outputs(package_uuid)

        for source_file, bundle in _iter_source(path):
            number = bundle.proceeding.proceeding_number
            if number in seen:
                raise RuntimeError(f"Duplicate proceeding in one US TTAB package: {number}")
            _assert_snapshot_slot_available(number, snapshot_at, package_uuid)
            seen.add(number)
            source_files.add(source_file)
            kind = bundle.proceeding.proceeding_type or "UNSPECIFIED"
            proceeding_types[kind] = proceeding_types.get(kind, 0) + 1
            if not bundle.docket_entries:
                empty_docket_count += 1
            for item in bundle.properties:
                serial = item.serial_number
                if serial and (len(serial) != 8 or not serial.isdigit()):
                    malformed_serials.add(serial)
            publisher.add(bundle, source_file)

        if not seen:
            raise RuntimeError("US TTAB source produced no proceeding records")
        row_counts = publisher.close()
        totals: dict[str, object] = {
            "schema_version": TTAB_SCHEMA_VERSION,
            "proceeding_count": len(seen),
            "xml_members": len(source_files),
            "row_counts": row_counts,
            "proceeding_types": dict(sorted(proceeding_types.items())),
            "malformed_property_serial_count": len(malformed_serials),
            "malformed_property_serial_examples": sorted(malformed_serials)[:20],
            "proceedings_without_docket_count": empty_docket_count,
        }
        profile = {
            "schema_version": TTAB_SCHEMA_VERSION,
            "source_sha256": actual_sha,
            "source_files": sorted(source_files),
            "snapshot_at": snapshot_at.isoformat(),
            "totals": totals,
            "semantics": TTAB_SEMANTICS,
        }
        archived = _archive(path, raw_root)
        update_package_status(
            str(package_uuid),
            "SUCCESS",
            profile=profile,
            archived_path=str(archived),
        )
        finish_job_run(run_id, "SUCCESS", metrics=totals)
        return totals
    except Exception as exc:
        try:
            cleanup_ttab_package_outputs(package_uuid)
        except Exception:
            pass
        update_package_status(str(package_uuid), "FAILED", error_message=str(exc))
        finish_job_run(run_id, "FAILED", error_message=str(exc))
        raise
