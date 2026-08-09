from __future__ import annotations

from pathlib import Path
import shutil
from typing import Iterator
import uuid
import zipfile

from app.db import clickhouse_client
from app.repository import create_job_run, finish_job_run, get_package, update_package_status
from app.scanner import sha256_file
from app.us.migrations import US_SCHEMA_VERSION, ensure_us_m1_schema
from app.us.model import USCaseBundle
from app.us.parser import iter_case_bundles
from app.us.publisher_m12 import SnapshotAwareUSBatchPublisher


OUTPUT_PACKAGE_COLUMNS = {
    "markorbit_facts.us_case_current": "last_source_package_id",
    "markorbit_facts.us_owner_current": "last_source_package_id",
    "markorbit_facts.us_classification_current": "last_source_package_id",
    "markorbit_facts.us_event_history": "source_package_id",
    "markorbit_facts.us_statement_current": "last_source_package_id",
}


def _iter_package_bundles(path: Path) -> Iterator[tuple[str, USCaseBundle]]:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        for bundle in iter_case_bundles(path, source_name=path.name):
            yield path.name, bundle
        return
    if suffix != ".zip":
        raise RuntimeError(f"Unsupported US M1 source package: {path.name}")

    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
        if not members:
            raise RuntimeError(f"USPTO package contains no XML members: {path.name}")
        for member in members:
            with archive.open(member, "r") as stream:
                for bundle in iter_case_bundles(stream, source_name=member.filename):
                    yield member.filename, bundle


def _cleanup_package_outputs(package_id: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_id)
    for table, column in OUTPUT_PACKAGE_COLUMNS.items():
        client.command(
            f"ALTER TABLE {table} DELETE WHERE {column} = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )


def _archive_package(path: Path, raw_root: Path) -> Path:
    archive_dir = raw_root / "archive" / "us"
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / path.name
    if path.resolve() == destination.resolve():
        return destination
    if destination.exists():
        if sha256_file(path) == sha256_file(destination):
            path.unlink()
            return destination
        digest = sha256_file(path)[:8]
        destination = archive_dir / f"{path.stem}_{digest}{path.suffix}"
    shutil.move(str(path), str(destination))
    return destination


def ingest_us_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    *,
    trigger_type: str = "MANUAL_US",
    retrying: bool = False,
) -> dict[str, object]:
    ensure_us_m1_schema()
    package_uuid = uuid.UUID(str(package_id))
    package_meta = get_package(str(package_uuid))
    expected_sha = str(package_meta.get("sha256") or "").lower()
    actual_sha = sha256_file(path).lower()

    run_id = create_job_run(
        job_type="US_PACKAGE_INGESTION",
        trigger_type=trigger_type,
        payload={
            "package_id": str(package_uuid),
            "path": str(path),
            "package_kind": package_meta["package_kind"],
            "source_rank": package_meta["source_rank"],
        },
    )
    publisher = SnapshotAwareUSBatchPublisher(
        clickhouse_client(),
        package_id=package_uuid,
        package_kind=str(package_meta["package_kind"]),
        source_effective_date=package_meta.get("source_period_end"),
        source_rank=int(package_meta["source_rank"]),
    )
    seen_serials: set[str] = set()
    source_files: set[str] = set()

    try:
        update_package_status(
            str(package_uuid),
            "PROCESSING",
            package_kind=str(package_meta["package_kind"]),
        )
        if not expected_sha or actual_sha != expected_sha:
            raise RuntimeError(
                f"USPTO source SHA-256 mismatch for {path.name}: "
                f"registered={expected_sha or '<missing>'} actual={actual_sha}"
            )
        if retrying:
            _cleanup_package_outputs(package_uuid)

        for source_file, bundle in _iter_package_bundles(path):
            serial = bundle.case.serial_number
            if serial in seen_serials:
                raise RuntimeError(
                    f"Duplicate USPTO serial number in one source package: {serial}"
                )
            seen_serials.add(serial)
            source_files.add(source_file)
            publisher.add(bundle, source_file)

        if not seen_serials:
            raise RuntimeError("USPTO source package produced no trademark case records")
        row_counts = publisher.close()
        totals: dict[str, object] = {
            "schema_version": US_SCHEMA_VERSION,
            "package_kind": package_meta["package_kind"],
            "partition_dimension": package_meta["partition_dimension"],
            "partition_value": package_meta["partition_value"],
            "source_rank": package_meta["source_rank"],
            "case_count": len(seen_serials),
            "xml_members": len(source_files),
            "row_counts": row_counts,
            "snapshot_tombstone_counts": dict(publisher.tombstone_counts),
        }
        profile = {
            "schema_version": US_SCHEMA_VERSION,
            "source_sha256": actual_sha,
            "source_files": sorted(source_files),
            "totals": totals,
        }
        archived = _archive_package(path, raw_root)
        update_package_status(
            str(package_uuid),
            "SUCCESS",
            package_kind=str(package_meta["package_kind"]),
            profile=profile,
            archived_path=str(archived),
        )
        finish_job_run(run_id, "SUCCESS", metrics=totals)
        return totals
    except Exception as exc:
        try:
            _cleanup_package_outputs(package_uuid)
        except Exception:
            pass
        update_package_status(
            str(package_uuid),
            "FAILED",
            package_kind=str(package_meta["package_kind"]),
            error_message=str(exc),
        )
        finish_job_run(run_id, "FAILED", error_message=str(exc))
        raise
