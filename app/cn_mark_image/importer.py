from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import uuid
from zipfile import ZipFile, ZipInfo

from app.cn_mark_image import NORMALIZATION_VERSION, SOURCE_VERSION
from app.cn_mark_image.migrations import ensure_cn_mark_image_schema
from app.cn_mark_image.storage import (
    MAX_IMAGE_BYTES,
    CanonicalJpeg,
    RawJpegAnalysis,
    analyze_jpeg,
    canonicalize_jpeg,
    content_addressed_key,
    store_content_addressed,
)
from app.config import get_settings
from app.db import postgres_conn


_APPLICATION_NUMBER_RE = re.compile(r"^[0-9]{4,20}$")


@dataclass(frozen=True)
class ImportResult:
    package_id: str
    package_name: str
    package_sha256: str
    package_kind: str
    source_rank: int
    state: str
    zip_entry_count: int
    jpeg_entry_count: int
    processed_jpeg_count: int
    mapped_application_count: int
    unmapped_subject_count: int
    new_raw_asset_count: int
    reused_raw_asset_count: int
    new_canonical_asset_count: int
    reused_canonical_asset_count: int
    unique_raw_asset_count: int
    unique_canonical_asset_count: int
    source_deleted: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_subject_key(entry_path: str) -> str:
    return PurePosixPath(entry_path).stem.strip()


def infer_application_number(source_subject_key: str) -> str | None:
    if _APPLICATION_NUMBER_RE.fullmatch(source_subject_key):
        return source_subject_key
    return None


def _is_jpeg_entry(info: ZipInfo) -> bool:
    if info.is_dir():
        return False
    suffix = PurePosixPath(info.filename).suffix.lower()
    return suffix in {".jpg", ".jpeg"}


def _read_zip_entry(archive: ZipFile, info: ZipInfo) -> bytes:
    if info.file_size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"CN mark image entry exceeds {MAX_IMAGE_BYTES} bytes: {info.filename}"
        )
    with archive.open(info, "r") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"CN mark image entry exceeds {MAX_IMAGE_BYTES} bytes: {info.filename}"
        )
    return data


def _prepare_package(
    *,
    package_sha256: str,
    package_name: str,
    package_kind: str,
    source_rank: int,
    compressed_bytes: int,
    zip_entry_count: int,
    jpeg_entry_count: int,
) -> tuple[uuid.UUID, bool]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, package_kind, source_rank, state
                FROM acquisition.cn_mark_image_package
                WHERE source_package_sha256 = %s
                """,
                (package_sha256,),
            )
            row = cur.fetchone()
            if row:
                if row["package_kind"] != package_kind or int(row["source_rank"]) != source_rank:
                    raise ValueError(
                        "same CN mark-image package SHA was registered with different "
                        "package_kind/source_rank"
                    )
                package_id = row["package_id"]
                if row["state"] == "ACCEPTED":
                    return package_id, True
                cur.execute(
                    """
                    UPDATE acquisition.cn_mark_image_package
                    SET state = 'PROCESSING', last_error = NULL,
                        compressed_bytes = %s, zip_entry_count = %s,
                        jpeg_entry_count = %s, updated_at = now()
                    WHERE package_id = %s
                    """,
                    (compressed_bytes, zip_entry_count, jpeg_entry_count, package_id),
                )
            else:
                package_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO acquisition.cn_mark_image_package (
                        package_id, source_package_sha256, source_package_name,
                        package_kind, source_rank, state, compressed_bytes,
                        zip_entry_count, jpeg_entry_count
                    ) VALUES (%s, %s, %s, %s, %s, 'PROCESSING', %s, %s, %s)
                    """,
                    (
                        package_id,
                        package_sha256,
                        package_name,
                        package_kind,
                        source_rank,
                        compressed_bytes,
                        zip_entry_count,
                        jpeg_entry_count,
                    ),
                )
        conn.commit()
    return package_id, False


def _existing_entries(package_id: uuid.UUID) -> set[str]:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_entry_path
                FROM visual.cn_mark_image_observation
                WHERE package_id = %s
                """,
                (package_id,),
            )
            return {str(row["source_entry_path"]) for row in cur.fetchall()}


def _get_or_create_raw_asset(
    cur,
    analysis: RawJpegAnalysis,
    raw_bytes: bytes,
    *,
    raw_root: Path,
) -> tuple[uuid.UUID, str, bool]:
    cur.execute(
        "SELECT asset_id, storage_key FROM visual.asset WHERE sha256 = %s",
        (analysis.raw_sha256,),
    )
    row = cur.fetchone()
    if row:
        return row["asset_id"], str(row["storage_key"]), False

    relative = content_addressed_key(tier="raw", sha256=analysis.raw_sha256)
    storage_key = store_content_addressed(raw_bytes, root=raw_root, relative_key=relative)
    asset_id = uuid.uuid4()
    bbox_json = json.dumps(list(analysis.content_bbox)) if analysis.content_bbox else None
    cur.execute(
        """
        INSERT INTO visual.asset (
            asset_id, sha256, mime_type, file_extension, byte_size,
            width, height, content_bbox, dhash64, storage_key
        ) VALUES (%s, %s, 'image/jpeg', '.jpg', %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (sha256) DO NOTHING
        RETURNING asset_id, storage_key
        """,
        (
            asset_id,
            analysis.raw_sha256,
            analysis.byte_size,
            analysis.width,
            analysis.height,
            bbox_json,
            analysis.dhash64,
            storage_key,
        ),
    )
    inserted = cur.fetchone()
    if inserted:
        return inserted["asset_id"], str(inserted["storage_key"]), True

    cur.execute(
        "SELECT asset_id, storage_key FROM visual.asset WHERE sha256 = %s",
        (analysis.raw_sha256,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("raw visual asset conflict did not resolve")
    return row["asset_id"], str(row["storage_key"]), False


def _get_or_create_canonical_asset(
    cur,
    canonical: CanonicalJpeg,
    *,
    processed_root: Path,
) -> tuple[uuid.UUID, str, bool]:
    cur.execute(
        """
        SELECT canonical_asset_id, storage_key
        FROM visual.canonical_asset
        WHERE sha256 = %s
        """,
        (canonical.sha256,),
    )
    row = cur.fetchone()
    if row:
        return row["canonical_asset_id"], str(row["storage_key"]), False

    relative = content_addressed_key(tier="processed", sha256=canonical.sha256)
    storage_key = store_content_addressed(
        canonical.data,
        root=processed_root,
        relative_key=relative,
    )
    canonical_asset_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO visual.canonical_asset (
            canonical_asset_id, sha256, mime_type, file_extension,
            byte_size, width, height, storage_key
        ) VALUES (%s, %s, 'image/jpeg', '.jpg', %s, %s, %s, %s)
        ON CONFLICT (sha256) DO NOTHING
        RETURNING canonical_asset_id, storage_key
        """,
        (
            canonical_asset_id,
            canonical.sha256,
            canonical.byte_size,
            canonical.width,
            canonical.height,
            storage_key,
        ),
    )
    inserted = cur.fetchone()
    if inserted:
        return inserted["canonical_asset_id"], str(inserted["storage_key"]), True

    cur.execute(
        """
        SELECT canonical_asset_id, storage_key
        FROM visual.canonical_asset
        WHERE sha256 = %s
        """,
        (canonical.sha256,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("canonical visual asset conflict did not resolve")
    return row["canonical_asset_id"], str(row["storage_key"]), False


def _persist_observation(
    cur,
    *,
    package_id: uuid.UUID,
    source_rank: int,
    source_entry_path: str,
    source_subject_key: str,
    application_number: str | None,
    raw_asset_id: uuid.UUID,
    canonical_asset_id: uuid.UUID,
    analysis: RawJpegAnalysis,
    canonical: CanonicalJpeg,
) -> bool:
    cur.execute(
        """
        INSERT INTO visual.asset_derivative (
            source_asset_id, canonical_asset_id, derivative_kind,
            transform_version, normalized_pixel_sha256, transformed
        ) VALUES (%s, %s, 'CANONICAL', %s, %s, %s)
        ON CONFLICT (source_asset_id, derivative_kind, transform_version)
        DO UPDATE SET
            canonical_asset_id = EXCLUDED.canonical_asset_id,
            normalized_pixel_sha256 = EXCLUDED.normalized_pixel_sha256,
            transformed = EXCLUDED.transformed
        """,
        (
            raw_asset_id,
            canonical_asset_id,
            NORMALIZATION_VERSION,
            analysis.pixel_sha256,
            canonical.transformed,
        ),
    )

    cur.execute(
        """
        INSERT INTO visual.cn_mark_image_observation (
            package_id, source_entry_path, source_subject_key,
            application_number, raw_asset_id, canonical_asset_id
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (package_id, source_entry_path) DO NOTHING
        """,
        (
            package_id,
            source_entry_path,
            source_subject_key,
            application_number,
            raw_asset_id,
            canonical_asset_id,
        ),
    )
    if cur.rowcount != 1:
        return False

    if application_number:
        cur.execute(
            """
            INSERT INTO visual.cn_trademark_visual_version (
                application_number, raw_asset_id, canonical_asset_id,
                first_package_id, last_package_id,
                first_source_rank, last_source_rank
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (application_number, raw_asset_id) DO UPDATE SET
                canonical_asset_id = EXCLUDED.canonical_asset_id,
                last_package_id = CASE
                    WHEN EXCLUDED.last_source_rank >=
                         visual.cn_trademark_visual_version.last_source_rank
                    THEN EXCLUDED.last_package_id
                    ELSE visual.cn_trademark_visual_version.last_package_id
                END,
                last_source_rank = GREATEST(
                    visual.cn_trademark_visual_version.last_source_rank,
                    EXCLUDED.last_source_rank
                ),
                last_observed_at = now()
            """,
            (
                application_number,
                raw_asset_id,
                canonical_asset_id,
                package_id,
                package_id,
                source_rank,
                source_rank,
            ),
        )
        cur.execute(
            """
            INSERT INTO visual.cn_trademark_visual_current (
                application_number, raw_asset_id, canonical_asset_id,
                source_package_id, source_rank
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (application_number) DO UPDATE SET
                raw_asset_id = EXCLUDED.raw_asset_id,
                canonical_asset_id = EXCLUDED.canonical_asset_id,
                source_package_id = EXCLUDED.source_package_id,
                source_rank = EXCLUDED.source_rank,
                first_observed_at = LEAST(
                    visual.cn_trademark_visual_current.first_observed_at,
                    EXCLUDED.first_observed_at
                ),
                last_observed_at = now()
            WHERE visual.cn_trademark_visual_current.source_rank <= EXCLUDED.source_rank
            """,
            (
                application_number,
                raw_asset_id,
                canonical_asset_id,
                package_id,
                source_rank,
            ),
        )
    return True


def _add_package_metrics(cur, package_id: uuid.UUID, metrics: dict[str, int]) -> None:
    cur.execute(
        """
        UPDATE acquisition.cn_mark_image_package
        SET processed_jpeg_count = processed_jpeg_count + %s,
            mapped_application_count = mapped_application_count + %s,
            unmapped_subject_count = unmapped_subject_count + %s,
            new_raw_asset_count = new_raw_asset_count + %s,
            reused_raw_asset_count = reused_raw_asset_count + %s,
            new_canonical_asset_count = new_canonical_asset_count + %s,
            reused_canonical_asset_count = reused_canonical_asset_count + %s,
            updated_at = now()
        WHERE package_id = %s
        """,
        (
            metrics["processed"],
            metrics["mapped"],
            metrics["unmapped"],
            metrics["new_raw"],
            metrics["reused_raw"],
            metrics["new_canonical"],
            metrics["reused_canonical"],
            package_id,
        ),
    )


def _accept_package(package_id: uuid.UUID) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT jpeg_entry_count,
                       (SELECT count(*) FROM visual.cn_mark_image_observation o
                        WHERE o.package_id = p.package_id) AS observed_count,
                       (SELECT count(DISTINCT raw_asset_id)
                        FROM visual.cn_mark_image_observation o
                        WHERE o.package_id = p.package_id) AS unique_raw,
                       (SELECT count(DISTINCT canonical_asset_id)
                        FROM visual.cn_mark_image_observation o
                        WHERE o.package_id = p.package_id) AS unique_canonical
                FROM acquisition.cn_mark_image_package p
                WHERE package_id = %s
                FOR UPDATE
                """,
                (package_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"unknown CN mark-image package: {package_id}")
            if int(row["observed_count"]) != int(row["jpeg_entry_count"]):
                raise ValueError(
                    "CN mark-image package cannot be accepted: "
                    f"observed={row['observed_count']} expected={row['jpeg_entry_count']}"
                )
            cur.execute(
                """
                UPDATE acquisition.cn_mark_image_package
                SET state = 'ACCEPTED', accepted_at = now(), updated_at = now(),
                    last_error = NULL,
                    processed_jpeg_count = %s,
                    unique_raw_asset_count = %s,
                    unique_canonical_asset_count = %s
                WHERE package_id = %s
                """,
                (
                    int(row["observed_count"]),
                    int(row["unique_raw"]),
                    int(row["unique_canonical"]),
                    package_id,
                ),
            )
        conn.commit()


def _mark_package_failed(package_id: uuid.UUID, error: Exception) -> None:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquisition.cn_mark_image_package
                SET state = 'FAILED', last_error = %s, updated_at = now()
                WHERE package_id = %s AND state != 'ACCEPTED'
                """,
                (str(error)[:4000], package_id),
            )
        conn.commit()


def _result(package_id: uuid.UUID, *, source_deleted: bool) -> ImportResult:
    with postgres_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM acquisition.cn_mark_image_package WHERE package_id = %s",
                (package_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"unknown CN mark-image package: {package_id}")
    return ImportResult(
        package_id=str(package_id),
        package_name=str(row["source_package_name"]),
        package_sha256=str(row["source_package_sha256"]),
        package_kind=str(row["package_kind"]),
        source_rank=int(row["source_rank"]),
        state=str(row["state"]),
        zip_entry_count=int(row["zip_entry_count"]),
        jpeg_entry_count=int(row["jpeg_entry_count"]),
        processed_jpeg_count=int(row["processed_jpeg_count"]),
        mapped_application_count=int(row["mapped_application_count"]),
        unmapped_subject_count=int(row["unmapped_subject_count"]),
        new_raw_asset_count=int(row["new_raw_asset_count"]),
        reused_raw_asset_count=int(row["reused_raw_asset_count"]),
        new_canonical_asset_count=int(row["new_canonical_asset_count"]),
        reused_canonical_asset_count=int(row["reused_canonical_asset_count"]),
        unique_raw_asset_count=int(row["unique_raw_asset_count"]),
        unique_canonical_asset_count=int(row["unique_canonical_asset_count"]),
        source_deleted=source_deleted,
    )


def import_zip_package(
    package_path: Path,
    *,
    package_kind: str,
    source_rank: int,
    raw_root: Path | None = None,
    processed_root: Path | None = None,
    delete_source_on_acceptance: bool = False,
    commit_interval: int = 250,
) -> ImportResult:
    package_path = package_path.resolve()
    if package_kind not in {"HISTORICAL", "UPDATE"}:
        raise ValueError("package_kind must be HISTORICAL or UPDATE")
    if source_rank < 0:
        raise ValueError("source_rank must be >= 0")
    if commit_interval < 1:
        raise ValueError("commit_interval must be >= 1")
    if not package_path.is_file():
        raise FileNotFoundError(package_path)

    ensure_cn_mark_image_schema()
    settings = get_settings()
    raw_root = (raw_root or settings.resolved_visual_raw_root).resolve()
    processed_root = (processed_root or settings.resolved_visual_processed_root).resolve()
    package_sha256 = _sha256_file(package_path)

    try:
        with ZipFile(package_path, "r") as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            jpeg_infos = [info for info in infos if _is_jpeg_entry(info)]
            jpeg_paths = [info.filename for info in jpeg_infos]
            if len(jpeg_paths) != len(set(jpeg_paths)):
                raise ValueError("CN mark-image package contains duplicate JPEG entry paths")
            if not jpeg_infos:
                raise ValueError("CN mark-image package contains no JPEG entries")

            package_id, already_accepted = _prepare_package(
                package_sha256=package_sha256,
                package_name=package_path.name,
                package_kind=package_kind,
                source_rank=source_rank,
                compressed_bytes=package_path.stat().st_size,
                zip_entry_count=len(infos),
                jpeg_entry_count=len(jpeg_infos),
            )
            if already_accepted:
                source_deleted = False
                if delete_source_on_acceptance and package_path.exists():
                    package_path.unlink()
                    source_deleted = True
                return _result(package_id, source_deleted=source_deleted)

            observed_entries = _existing_entries(package_id)
            delta = {
                "processed": 0,
                "mapped": 0,
                "unmapped": 0,
                "new_raw": 0,
                "reused_raw": 0,
                "new_canonical": 0,
                "reused_canonical": 0,
            }
            pending_since_commit = 0

            with postgres_conn() as conn:
                with conn.cursor() as cur:
                    for info in jpeg_infos:
                        if info.filename in observed_entries:
                            continue
                        raw_bytes = _read_zip_entry(archive, info)
                        analysis = analyze_jpeg(raw_bytes)
                        canonical = canonicalize_jpeg(raw_bytes)
                        source_subject_key = _source_subject_key(info.filename)
                        application_number = infer_application_number(source_subject_key)

                        raw_asset_id, _raw_key, raw_created = _get_or_create_raw_asset(
                            cur,
                            analysis,
                            raw_bytes,
                            raw_root=raw_root,
                        )
                        canonical_asset_id, _canonical_key, canonical_created = (
                            _get_or_create_canonical_asset(
                                cur,
                                canonical,
                                processed_root=processed_root,
                            )
                        )
                        inserted = _persist_observation(
                            cur,
                            package_id=package_id,
                            source_rank=source_rank,
                            source_entry_path=info.filename,
                            source_subject_key=source_subject_key,
                            application_number=application_number,
                            raw_asset_id=raw_asset_id,
                            canonical_asset_id=canonical_asset_id,
                            analysis=analysis,
                            canonical=canonical,
                        )
                        if not inserted:
                            continue
                        observed_entries.add(info.filename)

                        delta["processed"] += 1
                        delta["mapped" if application_number else "unmapped"] += 1
                        delta["new_raw" if raw_created else "reused_raw"] += 1
                        delta[
                            "new_canonical" if canonical_created else "reused_canonical"
                        ] += 1
                        pending_since_commit += 1

                        if pending_since_commit >= commit_interval:
                            _add_package_metrics(cur, package_id, delta)
                            conn.commit()
                            delta = {key: 0 for key in delta}
                            pending_since_commit = 0

                    if pending_since_commit:
                        _add_package_metrics(cur, package_id, delta)
                    conn.commit()

            _accept_package(package_id)
    except Exception as error:
        package_id_value = locals().get("package_id")
        if isinstance(package_id_value, uuid.UUID):
            _mark_package_failed(package_id_value, error)
        raise

    source_deleted = False
    if delete_source_on_acceptance:
        package_path.unlink()
        source_deleted = True
    return _result(package_id, source_deleted=source_deleted)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import a CN official mark-image ZIP as a resumable, content-addressed "
            "bulk package. ZIP is transport only and can be deleted after acceptance."
        )
    )
    parser.add_argument("package", type=Path)
    parser.add_argument(
        "--package-kind",
        choices=["HISTORICAL", "UPDATE"],
        required=True,
    )
    parser.add_argument("--source-rank", type=int, required=True)
    parser.add_argument("--delete-source-on-acceptance", action="store_true")
    parser.add_argument("--commit-interval", type=int, default=250)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = import_zip_package(
        args.package,
        package_kind=args.package_kind,
        source_rank=args.source_rank,
        delete_source_on_acceptance=args.delete_source_on_acceptance,
        commit_interval=args.commit_interval,
    )
    print(json.dumps({"source_version": SOURCE_VERSION, **asdict(result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
