from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import shutil
from typing import Iterator
import uuid
import zipfile

from app.db import clickhouse_client, postgres_conn
from app.repository import create_job_run, finish_job_run, get_package, update_package_status
from app.scanner import sha256_file
from app.us_ttab import TTAB_JURISDICTION, TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.migrations import ensure_ttab_schema
from app.us_ttab.model import TTABProceedingBundle
from app.us_ttab.parser import iter_ttab_bundles
from app.us.publisher import stable_hash
from app.us.ttab_correspondent_history import (
    READY_VERSION,
    TARGET_TABLE,
    WATERMARK_TABLE,
    advance_serving_watermark,
    current_serving_watermark,
    history_ready,
    next_serving_generation,
)
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

    target_exists = client.query(
        """
        SELECT count()
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name = 'us_ttab_correspondent_mark_history'
        """
    ).result_rows
    if (
        target_exists
        and int(target_exists[0][0]) == 1
        and history_ready(client)
    ):
        watermark = current_serving_watermark(client)
        if watermark is not None:
            current_generation = int(watermark["serving_generation"])
            current_package = str(watermark["source_package_id"])
            if current_package == package:
                client.command(
                    f"ALTER TABLE {WATERMARK_TABLE} DELETE "
                    f"WHERE ready_version = '{READY_VERSION}' "
                    f"AND serving_generation = {current_generation} "
                    f"AND source_package_id = toUUID('{package}') "
                    "SETTINGS mutations_sync = 1"
                )
                client.command(
                    f"ALTER TABLE {TARGET_TABLE} DELETE "
                    f"WHERE source_package_id = toUUID('{package}') "
                    f"AND serving_generation = {current_generation} "
                    "SETTINGS mutations_sync = 1"
                )
                rolled_back = current_serving_watermark(client)
                if (
                    rolled_back is None
                    or int(rolled_back["serving_generation"])
                    != current_generation - 1
                ):
                    raise RuntimeError(
                        "US TTAB correspondent watermark rollback failed"
                    )
            else:
                client.command(
                    f"ALTER TABLE {TARGET_TABLE} DELETE "
                    f"WHERE source_package_id = toUUID('{package}') "
                    f"AND serving_generation > {current_generation} "
                    "SETTINGS mutations_sync = 1"
                )

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


SNAPSHOT_SLOT_BATCH_SIZE = 500
_HISTORICAL_SOURCE_KIND = "TTAB_BULK_HISTORICAL_XML"


def _bundle_signature(bundle: TTABProceedingBundle) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = [("P", stable_hash(asdict(bundle.proceeding)))]
    values.extend(("A", stable_hash(asdict(item))) for item in bundle.parties)
    values.extend(("R", stable_hash(asdict(item))) for item in bundle.properties)
    values.extend(("D", stable_hash(asdict(item))) for item in bundle.docket_entries)
    return tuple(sorted(values))


def _historical_batch_package_ids(
    meta: dict[str, object], package_uuid: uuid.UUID
) -> set[uuid.UUID]:
    if str(meta.get("package_kind") or "") != _HISTORICAL_SOURCE_KIND:
        return set()
    start = meta.get("source_period_start")
    end = meta.get("source_period_end")
    if start is None or end is None or start != end:
        raise RuntimeError(
            "US TTAB historical package lacks one authoritative transaction-date batch"
        )
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id
                FROM control.source_package
                WHERE jurisdiction = %s
                  AND package_kind = %s
                  AND source_period_start = %s
                  AND source_period_end = %s
                  AND package_id != %s
                  AND status = 'SUCCESS'
                """,
                (TTAB_JURISDICTION, _HISTORICAL_SOURCE_KIND, start, end, package_uuid),
            )
            return {uuid.UUID(str(row["package_id"])) for row in cur.fetchall()}


def _existing_snapshot_signatures(
    proceeding_numbers: list[str],
    snapshot_at: datetime,
    package_uuid: uuid.UUID,
    same_historical_batch: set[uuid.UUID],
) -> dict[tuple[str, uuid.UUID], tuple[tuple[str, str], ...]]:
    if not proceeding_numbers:
        return {}
    if any(not number.isdigit() for number in proceeding_numbers):
        raise RuntimeError("US TTAB proceeding number batch contains non-numeric identity")
    timestamp = snapshot_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:23]
    identities = ", ".join(f"'{number}'" for number in sorted(set(proceeding_numbers)))
    scopes = [f"source_snapshot_at = toDateTime64('{timestamp}', 3, 'UTC')"]
    if same_historical_batch:
        package_ids = ", ".join(
            f"toUUID('{value}')" for value in sorted(same_historical_batch, key=str)
        )
        scopes.append(f"source_package_id IN ({package_ids})")
    scope = " OR ".join(scopes)
    families = (
        ("P", "us_ttab_proceeding_history"),
        ("A", "us_ttab_party_history"),
        ("R", "us_ttab_property_history"),
        ("D", "us_ttab_docket_history"),
    )
    selects = []
    for family, table in families:
        selects.append(
            f"""
            SELECT proceeding_number, source_package_id, '{family}' AS family, record_hash
            FROM markorbit_facts.{table}
            WHERE proceeding_number IN ({identities})
              AND source_package_id != toUUID('{package_uuid}')
              AND ({scope})
            """
        )
    rows = (
        clickhouse_client()
        .query(
            "SELECT proceeding_number, toString(source_package_id), family, record_hash "
            "FROM (" + " UNION ALL ".join(selects) + ") "
            "ORDER BY proceeding_number, source_package_id, family, record_hash"
        )
        .result_rows
    )
    grouped: dict[tuple[str, uuid.UUID], list[tuple[str, str]]] = {}
    for proceeding_number, package_id, family, record_hash in rows:
        key = (str(proceeding_number), uuid.UUID(str(package_id)))
        normalized_hash = (
            bytes(record_hash).decode("ascii")
            if isinstance(record_hash, (bytes, bytearray))
            else str(record_hash)
        )
        grouped.setdefault(key, []).append((str(family), normalized_hash))
    return {key: tuple(sorted(values)) for key, values in grouped.items()}


def _publishable_snapshot_batch(
    pending: list[tuple[str, TTABProceedingBundle]],
    snapshot_at: datetime,
    package_uuid: uuid.UUID,
    meta: dict[str, object],
    same_historical_batch: set[uuid.UUID],
) -> tuple[list[tuple[str, TTABProceedingBundle]], int]:
    signatures = _existing_snapshot_signatures(
        [bundle.proceeding.proceeding_number for _source_file, bundle in pending],
        snapshot_at,
        package_uuid,
        same_historical_batch,
    )
    by_proceeding: dict[str, list[tuple[uuid.UUID, tuple[tuple[str, str], ...]]]] = {}
    for (number, existing_package_id), signature in signatures.items():
        by_proceeding.setdefault(number, []).append((existing_package_id, signature))

    publishable: list[tuple[str, TTABProceedingBundle]] = []
    skipped = 0
    transaction_date = str(meta.get("source_period_start") or "")
    for source_file, bundle in pending:
        number = bundle.proceeding.proceeding_number
        collisions = by_proceeding.get(number, [])
        if not collisions:
            publishable.append((source_file, bundle))
            continue
        incoming_signature = _bundle_signature(bundle)
        matched_historical_overlap = False
        for existing_package_id, existing_signature in collisions:
            if existing_package_id in same_historical_batch:
                matched_historical_overlap = True
                if existing_signature != incoming_signature:
                    raise RuntimeError(
                        "A US TTAB historical split batch contains conflicting content for the same "
                        f"proceeding. Proceeding={number} transaction_date={transaction_date} "
                        f"existing_package_id={existing_package_id} current_package_id={package_uuid}. "
                        "Synthetic timestamp or split-order precedence is not permitted."
                    )
                continue
            raise RuntimeError(
                "A US TTAB snapshot for the same proceeding and millisecond is already present. "
                f"Proceeding={number} snapshot_at={snapshot_at.isoformat()}. "
                "The collision is outside one identical historical split batch; synthetic "
                "sub-millisecond or registration-order precedence is not permitted."
            )
        if matched_historical_overlap:
            skipped += 1
        else:
            publishable.append((source_file, bundle))
    return publishable, skipped


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
    target_client = clickhouse_client()
    correspondent_history_enabled = history_ready(target_client)
    correspondent_serving_generation: int | None = None
    publisher: TTABBatchPublisher | None = None
    seen: set[str] = set()
    source_files: set[str] = set()
    malformed_serials: set[str] = set()
    proceeding_types: dict[str, int] = {}
    empty_docket_count = 0
    historical_batch_duplicate_count = 0
    same_historical_batch = _historical_batch_package_ids(meta, package_uuid)
    pending: list[tuple[str, TTABProceedingBundle]] = []

    def flush_pending() -> None:
        nonlocal historical_batch_duplicate_count
        if not pending:
            return
        publishable, skipped = _publishable_snapshot_batch(
            pending, snapshot_at, package_uuid, meta, same_historical_batch
        )
        historical_batch_duplicate_count += skipped
        if publisher is None:
            raise RuntimeError("US TTAB publisher is not initialized")
        for source_file, bundle in publishable:
            publisher.add(bundle, source_file)
        pending.clear()

    try:
        update_package_status(str(package_uuid), "PROCESSING")
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"US TTAB SHA-256 mismatch: registered={expected_sha} actual={actual_sha}"
            )
        if retrying:
            cleanup_ttab_package_outputs(package_uuid)

        if correspondent_history_enabled:
            correspondent_serving_generation = next_serving_generation(
                target_client
            )
        publisher = TTABBatchPublisher(
            target_client,
            package_id=package_uuid,
            source_kind=str(meta["package_kind"]),
            source_snapshot_at=snapshot_at,
            source_rank=int(meta["source_rank"]),
            include_correspondent_history=correspondent_history_enabled,
            correspondent_serving_generation=correspondent_serving_generation,
        )

        for source_file, bundle in _iter_source(path):
            number = bundle.proceeding.proceeding_number
            if number in seen:
                raise RuntimeError(f"Duplicate proceeding in one US TTAB package: {number}")
            seen.add(number)
            source_files.add(source_file)
            # Prefer human-readable TTABVUE display text; official bulk provides only the
            # raw type code, which is retained as a code rather than interpreted.
            kind = (
                bundle.proceeding.proceeding_type
                or bundle.proceeding.proceeding_type_code
                or "UNSPECIFIED"
            )
            proceeding_types[kind] = proceeding_types.get(kind, 0) + 1
            if not bundle.docket_entries:
                empty_docket_count += 1
            for item in bundle.properties:
                serial = item.serial_number
                if serial and (len(serial) != 8 or not serial.isdigit()):
                    malformed_serials.add(serial)
            pending.append((source_file, bundle))
            if len(pending) >= SNAPSHOT_SLOT_BATCH_SIZE:
                flush_pending()

        flush_pending()
        if not seen:
            raise RuntimeError("US TTAB source produced no proceeding records")
        if publisher is None:
            raise RuntimeError("US TTAB publisher is not initialized")
        row_counts = publisher.close()
        totals: dict[str, object] = {
            "schema_version": TTAB_SCHEMA_VERSION,
            "proceeding_count": len(seen),
            "published_proceeding_count": len(seen) - historical_batch_duplicate_count,
            "historical_batch_duplicate_count": historical_batch_duplicate_count,
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
        if correspondent_history_enabled:
            advance_serving_watermark(
                target_client,
                serving_generation=int(correspondent_serving_generation or 0),
                source_rank=int(meta["source_rank"]),
                source_package_id=package_uuid,
            )
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
