from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
import shutil
from typing import Iterator
import uuid
import zipfile

from app.db import clickhouse_client
from app.repository import create_job_run, finish_job_run, get_package, update_package_status
from app.scanner import sha256_file
from app.us_assignment import ASSIGNMENT_JURISDICTION, ASSIGNMENT_SCHEMA_VERSION
from app.us_assignment.migrations import ensure_assignment_schema
from app.us_assignment.model import AssignmentBundle
from app.us_assignment.parser import iter_assignment_bundles
from app.us.publisher import stable_hash
from app.us_assignment.publisher import AssignmentBatchPublisher, TABLE_COLUMNS


def _source_xml_member_names(path: Path) -> list[str]:
    if path.suffix.lower() == ".xml":
        return [path.name]
    if path.suffix.lower() != ".zip":
        raise RuntimeError(f"Unsupported US assignment source: {path.name}")
    with zipfile.ZipFile(path) as archive:
        members = sorted(
            item.filename
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        )
    if not members:
        raise RuntimeError(f"US assignment ZIP contains no XML member: {path.name}")
    return members


def _iter_source(path: Path) -> Iterator[tuple[str, AssignmentBundle]]:
    if path.suffix.lower() == ".xml":
        for bundle in iter_assignment_bundles(path):
            yield path.name, bundle
        return
    member_names = _source_xml_member_names(path)
    with zipfile.ZipFile(path) as archive:
        for member_name in member_names:
            with archive.open(member_name, "r") as stream:
                for bundle in iter_assignment_bundles(stream):
                    yield member_name, bundle


_DAILY_ASSIGNMENT_KIND = "DAILY_ASSIGNMENT_XML"
_NO_DATA_CODE = re.compile(
    rb"<data-available-code>\s*N\s*</data-available-code>",
    re.IGNORECASE,
)
_NO_DATA_SCAN_LIMIT = 1024 * 1024


def _stream_declares_no_data(stream) -> bool:
    payload = stream.read(_NO_DATA_SCAN_LIMIT)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return _NO_DATA_CODE.search(payload) is not None


def _package_declares_no_data(path: Path) -> bool:
    if path.suffix.lower() == ".xml":
        with path.open("rb") as stream:
            return _stream_declares_no_data(stream)
    if path.suffix.lower() != ".zip":
        return False
    with zipfile.ZipFile(path) as archive:
        members = [
            item
            for item in archive.infolist()
            if not item.is_dir() and item.filename.lower().endswith(".xml")
        ]
        if not members:
            return False
        for member in members:
            with archive.open(member, "r") as stream:
                if not _stream_declares_no_data(stream):
                    return False
    return True


def _empty_assignment_package_is_acceptable(path: Path, source_kind: str) -> bool:
    return source_kind == _DAILY_ASSIGNMENT_KIND and _package_declares_no_data(path)


def _bundle_persistence_fingerprint(source_file: str, bundle: AssignmentBundle) -> bytes:
    return bytes.fromhex(stable_hash({"source_file": source_file, "bundle": asdict(bundle)}))


def _coalesce_assignment_bundles(
    items: Iterator[tuple[str, AssignmentBundle]],
) -> Iterator[tuple[str, AssignmentBundle]]:
    """Coalesce only exact package-local source overlap; conflicting facts fail closed."""
    seen: dict[str, bytes] = {}
    for source_file, bundle in items:
        reel_frame = bundle.assignment.reel_frame_id
        fingerprint = _bundle_persistence_fingerprint(source_file, bundle)
        prior = seen.get(reel_frame)
        if prior is not None:
            if prior != fingerprint:
                raise RuntimeError(
                    f"Conflicting duplicate reel/frame in one US assignment package: {reel_frame}"
                )
            continue
        seen[reel_frame] = fingerprint
        yield source_file, bundle


def cleanup_assignment_package_outputs(package_id: uuid.UUID) -> None:
    client = clickhouse_client()
    package = str(package_id)
    for table in TABLE_COLUMNS:
        client.command(
            f"ALTER TABLE {table} DELETE WHERE source_package_id = toUUID('{package}') "
            "SETTINGS mutations_sync = 1"
        )


def _archive(path: Path, raw_root: Path) -> Path:
    archive_dir = raw_root / "archive" / "us_assignment"
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


def ingest_assignment_package(
    package_id: str,
    path: Path,
    raw_root: Path,
    *,
    trigger_type: str = "MANUAL_US_ASSIGNMENT",
    retrying: bool = False,
) -> dict[str, object]:
    ensure_assignment_schema()
    package_uuid = uuid.UUID(str(package_id))
    meta = get_package(str(package_uuid))
    if meta["jurisdiction"] != ASSIGNMENT_JURISDICTION:
        raise RuntimeError("Refusing to ingest a non-US_ASSIGNMENT package")
    effective_date = meta.get("source_period_end")
    if effective_date is None:
        raise RuntimeError("US assignment package lacks explicit effective date")
    expected_sha = str(meta.get("sha256") or "").lower()
    actual_sha = sha256_file(path).lower()

    run_id = create_job_run(
        job_type="US_ASSIGNMENT_PACKAGE_INGESTION",
        trigger_type=trigger_type,
        payload={
            "package_id": str(package_uuid),
            "path": str(path),
            "source_kind": meta["package_kind"],
            "source_rank": meta["source_rank"],
        },
    )
    publisher = AssignmentBatchPublisher(
        clickhouse_client(),
        package_id=package_uuid,
        source_kind=str(meta["package_kind"]),
        source_effective_date=effective_date,
        source_rank=int(meta["source_rank"]),
    )
    assignment_count = 0
    source_files: set[str] = set()
    malformed_property_serials: set[str] = set()

    try:
        source_files.update(_source_xml_member_names(path))
        update_package_status(str(package_uuid), "PROCESSING")
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"US assignment SHA-256 mismatch: registered={expected_sha} actual={actual_sha}"
            )
        if retrying:
            cleanup_assignment_package_outputs(package_uuid)

        for source_file, bundle in _coalesce_assignment_bundles(_iter_source(path)):
            assignment_count += 1
            source_files.add(source_file)
            for item in bundle.properties:
                if item.serial_number and (
                    len(item.serial_number) != 8 or not item.serial_number.isdigit()
                ):
                    malformed_property_serials.add(item.serial_number)
            publisher.add(bundle, source_file)

        declared_no_data = False
        if not assignment_count:
            declared_no_data = _empty_assignment_package_is_acceptable(
                path, str(meta["package_kind"])
            )
            if not declared_no_data:
                raise RuntimeError("US assignment source produced no assignment records")
        row_counts = publisher.close()
        totals: dict[str, object] = {
            "schema_version": ASSIGNMENT_SCHEMA_VERSION,
            "assignment_count": assignment_count,
            "declared_no_data": declared_no_data,
            "xml_members": len(source_files),
            "row_counts": row_counts,
            "malformed_property_serial_count": len(malformed_property_serials),
            "malformed_property_serial_examples": sorted(malformed_property_serials)[:20],
        }
        profile = {
            "schema_version": ASSIGNMENT_SCHEMA_VERSION,
            "source_sha256": actual_sha,
            "source_files": sorted(source_files),
            "totals": totals,
            "semantics": "USPTO_RECORDED_ASSIGNMENT_FACTS_NOT_LEGAL_TITLE_CONCLUSION",
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
            cleanup_assignment_package_outputs(package_uuid)
        except Exception:
            pass
        update_package_status(str(package_uuid), "FAILED", error_message=str(exc))
        finish_job_run(run_id, "FAILED", error_message=str(exc))
        raise
