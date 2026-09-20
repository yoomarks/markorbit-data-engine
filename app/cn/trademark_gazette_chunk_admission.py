from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.cn.trademark_gazette_admission import (
    ANNOUNCEMENT_TABLE,
    ISSUE_TABLE,
    GazetteAdmissionError,
    NormalizedGazetteRow,
    _ALLOWED_ROW_FIELDS,
    _FORBIDDEN_ROW_FIELDS,
    _HEX64,
    _check_fields,
    _fingerprint,
    _optional_positive_int,
    _optional_text,
    _parse_date,
    _parse_instant,
    _positive_int,
    _required_text,
    _row_fingerprint,
)


CHUNK_ADMISSION_CONTRACT_VERSION = "CN_TRADEMARK_GAZETTE_ADMISSION_CHUNK_V1"
CHUNK_FINALIZE_CONTRACT_VERSION = "CN_TRADEMARK_GAZETTE_ADMISSION_FINALIZE_V1"
CHUNK_STAGE_TABLE = "markorbit_facts.cn_trademark_gazette_admission_chunk"

_ALLOWED_CHUNK_TOP_LEVEL = frozenset(
    {
        "contract_version",
        "source_authority",
        "query_scope",
        "announcement_issue",
        "announcement_date",
        "source_record_count",
        "source_page_count",
        "page_size",
        "range_start_page",
        "range_end_page",
        "page_row_counts",
        "chunk_row_count",
        "source_capture_schema",
        "source_dataset_sha256",
        "source_uri",
        "collected_at",
        "records",
    }
)

_ALLOWED_FINALIZE_TOP_LEVEL = frozenset(
    {
        "contract_version",
        "source_authority",
        "query_scope",
        "announcement_issue",
        "announcement_date",
        "record_count",
        "page_count",
        "page_size",
        "source_capture_schema",
        "source_dataset_sha256",
        "source_uri",
        "collected_at",
    }
)

_ALLOWED_QUERY_SCOPE = frozenset({"announcement_type_selection", "annc_type"})
_ALLOWED_PAGE_COUNT = frozenset({"page_index", "row_count"})


@dataclass(frozen=True)
class NormalizedGazetteChunk:
    announcement_issue: int
    announcement_date: Any
    source_record_count: int
    source_page_count: int
    page_size: int
    range_start_page: int
    range_end_page: int
    page_row_counts: tuple[tuple[int, int], ...]
    chunk_row_count: int
    source_capture_schema: str
    source_dataset_sha256: str
    source_uri: str
    collected_at: Any
    records: tuple[NormalizedGazetteRow, ...]
    chunk_fingerprint: str


@dataclass(frozen=True)
class GazetteChunkAdmissionReceipt:
    announcement_issue: int
    source_dataset_sha256: str
    range_start_page: int
    range_end_page: int
    chunk_row_count: int
    chunk_fingerprint: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "CHUNK_ADMITTED",
            "contract_version": CHUNK_ADMISSION_CONTRACT_VERSION,
            "announcement_issue": self.announcement_issue,
            "source_dataset_sha256": self.source_dataset_sha256,
            "range_start_page": self.range_start_page,
            "range_end_page": self.range_end_page,
            "chunk_row_count": self.chunk_row_count,
            "chunk_fingerprint": self.chunk_fingerprint,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class GazetteChunkFinalizeReceipt:
    announcement_issue: int
    source_dataset_sha256: str
    record_count: int
    page_count: int
    chunk_count: int
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "ADMITTED",
            "contract_version": CHUNK_FINALIZE_CONTRACT_VERSION,
            "announcement_issue": self.announcement_issue,
            "source_dataset_sha256": self.source_dataset_sha256,
            "record_count": self.record_count,
            "page_count": self.page_count,
            "chunk_count": self.chunk_count,
            "replayed": self.replayed,
        }


def _query_scope(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise GazetteAdmissionError("query_scope must be an object")
    _check_fields(value, _ALLOWED_QUERY_SCOPE, "query_scope")
    if value.get("announcement_type_selection") != "ALL" or value.get("annc_type") != "":
        raise GazetteAdmissionError(
            'query_scope must be {"announcement_type_selection":"ALL","annc_type":""}'
        )


def _dataset_sha(value: Any) -> str:
    dataset_sha = _required_text(value, "source_dataset_sha256", maximum=64).lower()
    if not _HEX64.fullmatch(dataset_sha):
        raise GazetteAdmissionError(
            "source_dataset_sha256 must be 64 hexadecimal characters"
        )
    return dataset_sha


def _normalize_rows(
    raw_records: Any,
    *,
    announcement_issue: int,
    expected_count: int,
) -> tuple[NormalizedGazetteRow, ...]:
    if not isinstance(raw_records, Sequence) or isinstance(
        raw_records, (str, bytes, bytearray)
    ):
        raise GazetteAdmissionError("records must be an array")
    if len(raw_records) != expected_count:
        raise GazetteAdmissionError("chunk_row_count does not match records length")

    seen_ids: set[str] = set()
    normalized_rows: list[NormalizedGazetteRow] = []
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping):
            raise GazetteAdmissionError(f"records[{index}] must be an object")
        forbidden = sorted(set(raw) & _FORBIDDEN_ROW_FIELDS)
        if forbidden:
            raise GazetteAdmissionError(
                f"records[{index}] duplicates trademark-entity fields: {', '.join(forbidden)}"
            )
        _check_fields(raw, _ALLOWED_ROW_FIELDS, f"records[{index}]")

        row_id = _required_text(
            raw.get("source_row_id"),
            f"records[{index}].source_row_id",
            maximum=256,
        )
        if row_id in seen_ids:
            raise GazetteAdmissionError(f"duplicate source_row_id: {row_id}")
        seen_ids.add(row_id)

        search_id = _optional_text(
            raw.get("source_search_id"),
            f"records[{index}].source_search_id",
            maximum=256,
        )
        if search_id and search_id != row_id:
            raise GazetteAdmissionError(
                f"records[{index}].source_search_id must match source_row_id"
            )

        row_issue = _positive_int(
            raw.get("announcement_issue"),
            f"records[{index}].announcement_issue",
        )
        if row_issue != announcement_issue:
            raise GazetteAdmissionError(
                f"records[{index}] belongs to announcement issue {row_issue}, "
                f"expected {announcement_issue}"
            )

        normalized_rows.append(
            NormalizedGazetteRow(
                source_row_id=row_id,
                source_search_id=search_id,
                registration_number=_required_text(
                    raw.get("registration_number"),
                    f"records[{index}].registration_number",
                    maximum=128,
                ),
                announcement_issue=row_issue,
                announcement_type_code=_required_text(
                    raw.get("announcement_type_code"),
                    f"records[{index}].announcement_type_code",
                    maximum=128,
                ),
                announcement_type_name=_optional_text(
                    raw.get("announcement_type_name"),
                    f"records[{index}].announcement_type_name",
                    maximum=512,
                ),
                detail_page_no=_optional_positive_int(
                    raw.get("detail_page_no"),
                    f"records[{index}].detail_page_no",
                ),
                announcement_page_count=_optional_positive_int(
                    raw.get("announcement_page_count"),
                    f"records[{index}].announcement_page_count",
                ),
                detail_file_id=_optional_text(
                    raw.get("detail_file_id"),
                    f"records[{index}].detail_file_id",
                    maximum=256,
                ),
                detail_asset_path=_optional_text(
                    raw.get("detail_asset_path"),
                    f"records[{index}].detail_asset_path",
                ),
                announcement_detail_url=_optional_text(
                    raw.get("announcement_detail_url"),
                    f"records[{index}].announcement_detail_url",
                ),
            )
        )

    return tuple(normalized_rows)


def _expected_page_rows(
    *,
    page_index: int,
    source_record_count: int,
    source_page_count: int,
    page_size: int,
) -> int:
    if source_record_count == 0:
        if page_index == 1 and source_page_count == 1:
            return 0
        raise GazetteAdmissionError(
            "zero-record Gazette source must contain exactly one empty page"
        )
    if page_index < source_page_count:
        return page_size
    if page_index == source_page_count:
        remainder = source_record_count % page_size
        return remainder or page_size
    raise GazetteAdmissionError(
        f"page_index {page_index} exceeds source_page_count {source_page_count}"
    )


def normalize_cn_trademark_gazette_chunk(
    package: Mapping[str, Any],
) -> NormalizedGazetteChunk:
    _check_fields(package, _ALLOWED_CHUNK_TOP_LEVEL, "package")
    if package.get("contract_version") != CHUNK_ADMISSION_CONTRACT_VERSION:
        raise GazetteAdmissionError(
            f"contract_version must be {CHUNK_ADMISSION_CONTRACT_VERSION}"
        )
    if package.get("source_authority") != "CNIPA":
        raise GazetteAdmissionError("source_authority must be CNIPA")
    _query_scope(package.get("query_scope"))

    issue = _positive_int(package.get("announcement_issue"), "announcement_issue")
    announcement_date = _parse_date(package.get("announcement_date"))
    if announcement_date is None:
        raise GazetteAdmissionError("announcement_date is required for Gazette chunks")

    source_record_count = _positive_int(
        package.get("source_record_count"),
        "source_record_count",
        allow_zero=True,
    )
    source_page_count = _positive_int(
        package.get("source_page_count"), "source_page_count"
    )
    page_size = _positive_int(package.get("page_size"), "page_size")
    if page_size != 100:
        raise GazetteAdmissionError("page_size must equal 100")

    expected_pages = max(1, (source_record_count + page_size - 1) // page_size)
    if source_page_count != expected_pages:
        raise GazetteAdmissionError(
            "source_page_count is inconsistent with source_record_count/page_size"
        )

    range_start = _positive_int(
        package.get("range_start_page"), "range_start_page"
    )
    range_end = _positive_int(package.get("range_end_page"), "range_end_page")
    if range_end < range_start:
        raise GazetteAdmissionError("range_end_page must be >= range_start_page")
    if range_end > source_page_count:
        raise GazetteAdmissionError("range_end_page exceeds source_page_count")
    if range_end - range_start + 1 > 100:
        raise GazetteAdmissionError("chunk range cannot exceed 100 pages")

    raw_page_counts = package.get("page_row_counts")
    if not isinstance(raw_page_counts, Sequence) or isinstance(
        raw_page_counts, (str, bytes, bytearray)
    ):
        raise GazetteAdmissionError("page_row_counts must be an array")
    expected_range_pages = range_end - range_start + 1
    if len(raw_page_counts) != expected_range_pages:
        raise GazetteAdmissionError(
            "page_row_counts does not fully cover the chunk range"
        )

    page_row_counts: list[tuple[int, int]] = []
    sum_rows = 0
    for offset, raw in enumerate(raw_page_counts):
        if not isinstance(raw, Mapping):
            raise GazetteAdmissionError(
                f"page_row_counts[{offset}] must be an object"
            )
        _check_fields(raw, _ALLOWED_PAGE_COUNT, f"page_row_counts[{offset}]")
        page_index = _positive_int(
            raw.get("page_index"), f"page_row_counts[{offset}].page_index"
        )
        expected_page = range_start + offset
        if page_index != expected_page:
            raise GazetteAdmissionError(
                f"page_row_counts sequence expected page {expected_page}, got {page_index}"
            )
        row_count = _positive_int(
            raw.get("row_count"),
            f"page_row_counts[{offset}].row_count",
            allow_zero=True,
        )
        expected_rows = _expected_page_rows(
            page_index=page_index,
            source_record_count=source_record_count,
            source_page_count=source_page_count,
            page_size=page_size,
        )
        if row_count != expected_rows:
            raise GazetteAdmissionError(
                f"page {page_index} row_count={row_count}, expected {expected_rows}"
            )
        page_row_counts.append((page_index, row_count))
        sum_rows += row_count

    chunk_row_count = _positive_int(
        package.get("chunk_row_count"), "chunk_row_count", allow_zero=True
    )
    if chunk_row_count != sum_rows:
        raise GazetteAdmissionError(
            "chunk_row_count does not match page_row_counts"
        )

    records = _normalize_rows(
        package.get("records"),
        announcement_issue=issue,
        expected_count=chunk_row_count,
    )
    source_dataset_sha256 = _dataset_sha(package.get("source_dataset_sha256"))
    source_capture_schema = _required_text(
        package.get("source_capture_schema"),
        "source_capture_schema",
        maximum=256,
    )
    source_uri = _required_text(package.get("source_uri"), "source_uri")
    collected_at = _parse_instant(package.get("collected_at"))

    chunk_fingerprint = _fingerprint(
        {
            "announcement_issue": issue,
            "announcement_date": announcement_date.isoformat(),
            "source_record_count": source_record_count,
            "source_page_count": source_page_count,
            "page_size": page_size,
            "range_start_page": range_start,
            "range_end_page": range_end,
            "page_row_counts": page_row_counts,
            "source_dataset_sha256": source_dataset_sha256,
            "source_capture_schema": source_capture_schema,
            "source_uri": source_uri,
            "rows": [_row_fingerprint(row) for row in records],
        }
    )

    return NormalizedGazetteChunk(
        announcement_issue=issue,
        announcement_date=announcement_date,
        source_record_count=source_record_count,
        source_page_count=source_page_count,
        page_size=page_size,
        range_start_page=range_start,
        range_end_page=range_end,
        page_row_counts=tuple(page_row_counts),
        chunk_row_count=chunk_row_count,
        source_capture_schema=source_capture_schema,
        source_dataset_sha256=source_dataset_sha256,
        source_uri=source_uri,
        collected_at=collected_at,
        records=records,
        chunk_fingerprint=chunk_fingerprint,
    )


def _announcement_insert_rows(
    normalized: NormalizedGazetteChunk,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row in normalized.records:
        row_fingerprint = _row_fingerprint(row)
        resolution_status = (
            "URL_OBSERVED"
            if row.announcement_detail_url
            else (
                "SOURCE_LOCATOR_OBSERVED"
                if row.detail_asset_path or row.detail_file_id
                else "UNRESOLVED"
            )
        )
        observation_fingerprint = _fingerprint(
            {
                "announcement_issue": normalized.announcement_issue,
                "source_row_id": row.source_row_id,
                "source_dataset_sha256": normalized.source_dataset_sha256,
                "source_row_fingerprint": row_fingerprint,
            }
        )
        rows.append(
            [
                row.source_row_id,
                row.source_search_id,
                row.announcement_issue,
                row.registration_number,
                row.announcement_type_code,
                row.announcement_type_name,
                row.detail_page_no,
                row.announcement_page_count,
                row.detail_file_id,
                row.detail_asset_path,
                row.announcement_detail_url,
                resolution_status,
                row_fingerprint,
                normalized.source_dataset_sha256,
                normalized.source_uri,
                normalized.collected_at,
                observation_fingerprint,
            ]
        )
    return rows


_ANNOUNCEMENT_COLUMNS = [
    "source_row_id",
    "source_search_id",
    "announcement_issue",
    "registration_number",
    "announcement_type_code",
    "announcement_type_name",
    "detail_page_no",
    "announcement_page_count",
    "detail_file_id",
    "detail_asset_path",
    "announcement_detail_url",
    "detail_resolution_status",
    "source_row_fingerprint",
    "source_dataset_sha256",
    "source_uri",
    "collected_at",
    "observation_fingerprint",
]


def admit_cn_trademark_gazette_chunk(
    package: Mapping[str, Any],
    *,
    client: Any,
) -> GazetteChunkAdmissionReceipt:
    normalized = normalize_cn_trademark_gazette_chunk(package)

    existing = client.query(
        f"""
        SELECT chunk_fingerprint, chunk_row_count
        FROM {CHUNK_STAGE_TABLE} FINAL
        WHERE announcement_issue = %(issue)s
          AND source_dataset_sha256 = %(dataset_sha)s
          AND range_start_page = %(range_start)s
          AND range_end_page = %(range_end)s
        LIMIT 1
        """,
        parameters={
            "issue": normalized.announcement_issue,
            "dataset_sha": normalized.source_dataset_sha256,
            "range_start": normalized.range_start_page,
            "range_end": normalized.range_end_page,
        },
    ).result_rows
    if existing:
        raw_fingerprint = existing[0][0]
        fingerprint = (
            raw_fingerprint.decode("ascii")
            if isinstance(raw_fingerprint, (bytes, bytearray))
            else str(raw_fingerprint)
        )
        row_count = int(existing[0][1])
        if (
            fingerprint != normalized.chunk_fingerprint
            or row_count != normalized.chunk_row_count
        ):
            raise GazetteAdmissionError(
                "existing Gazette chunk conflicts with this replay"
            )
        return GazetteChunkAdmissionReceipt(
            normalized.announcement_issue,
            normalized.source_dataset_sha256,
            normalized.range_start_page,
            normalized.range_end_page,
            normalized.chunk_row_count,
            normalized.chunk_fingerprint,
            True,
        )

    rows = _announcement_insert_rows(normalized)
    if rows:
        client.insert(
            ANNOUNCEMENT_TABLE,
            rows,
            column_names=_ANNOUNCEMENT_COLUMNS,
        )

    # The stage row is the chunk-level commit marker. It is written only after
    # the announcement rows have been accepted.
    client.insert(
        CHUNK_STAGE_TABLE,
        [[
            normalized.announcement_issue,
            normalized.announcement_date,
            normalized.source_dataset_sha256,
            normalized.range_start_page,
            normalized.range_end_page,
            normalized.source_record_count,
            normalized.source_page_count,
            normalized.page_size,
            normalized.chunk_row_count,
            "ALL",
            normalized.source_capture_schema,
            normalized.source_uri,
            normalized.collected_at,
            normalized.chunk_fingerprint,
        ]],
        column_names=[
            "announcement_issue",
            "announcement_date",
            "source_dataset_sha256",
            "range_start_page",
            "range_end_page",
            "source_record_count",
            "source_page_count",
            "page_size",
            "chunk_row_count",
            "query_scope",
            "source_capture_schema",
            "source_uri",
            "collected_at",
            "chunk_fingerprint",
        ],
    )

    return GazetteChunkAdmissionReceipt(
        normalized.announcement_issue,
        normalized.source_dataset_sha256,
        normalized.range_start_page,
        normalized.range_end_page,
        normalized.chunk_row_count,
        normalized.chunk_fingerprint,
        False,
    )


def _normalize_finalize_request(package: Mapping[str, Any]) -> dict[str, Any]:
    _check_fields(package, _ALLOWED_FINALIZE_TOP_LEVEL, "package")
    if package.get("contract_version") != CHUNK_FINALIZE_CONTRACT_VERSION:
        raise GazetteAdmissionError(
            f"contract_version must be {CHUNK_FINALIZE_CONTRACT_VERSION}"
        )
    if package.get("source_authority") != "CNIPA":
        raise GazetteAdmissionError("source_authority must be CNIPA")
    _query_scope(package.get("query_scope"))

    issue = _positive_int(package.get("announcement_issue"), "announcement_issue")
    announcement_date = _parse_date(package.get("announcement_date"))
    if announcement_date is None:
        raise GazetteAdmissionError("announcement_date is required")
    record_count = _positive_int(
        package.get("record_count"), "record_count", allow_zero=True
    )
    page_count = _positive_int(package.get("page_count"), "page_count")
    page_size = _positive_int(package.get("page_size"), "page_size")
    if page_size != 100:
        raise GazetteAdmissionError("page_size must equal 100")
    expected_pages = max(1, (record_count + page_size - 1) // page_size)
    if page_count != expected_pages:
        raise GazetteAdmissionError(
            "page_count is inconsistent with record_count/page_size"
        )

    return {
        "announcement_issue": issue,
        "announcement_date": announcement_date,
        "record_count": record_count,
        "page_count": page_count,
        "page_size": page_size,
        "source_capture_schema": _required_text(
            package.get("source_capture_schema"),
            "source_capture_schema",
            maximum=256,
        ),
        "source_dataset_sha256": _dataset_sha(
            package.get("source_dataset_sha256")
        ),
        "source_uri": _required_text(package.get("source_uri"), "source_uri"),
        "collected_at": _parse_instant(package.get("collected_at")),
    }


def finalize_cn_trademark_gazette_chunks(
    package: Mapping[str, Any],
    *,
    client: Any,
) -> GazetteChunkFinalizeReceipt:
    normalized = _normalize_finalize_request(package)
    issue = normalized["announcement_issue"]
    dataset_sha = normalized["source_dataset_sha256"]

    existing = client.query(
        f"""
        SELECT record_count
        FROM {ISSUE_TABLE} FINAL
        WHERE announcement_issue = %(issue)s
          AND source_dataset_sha256 = %(dataset_sha)s
        LIMIT 1
        """,
        parameters={"issue": issue, "dataset_sha": dataset_sha},
    ).result_rows
    if existing:
        if int(existing[0][0]) != normalized["record_count"]:
            raise GazetteAdmissionError(
                "existing issue observation conflicts with record_count"
            )
        chunk_rows = client.query(
            f"""
            SELECT count()
            FROM {CHUNK_STAGE_TABLE} FINAL
            WHERE announcement_issue = %(issue)s
              AND source_dataset_sha256 = %(dataset_sha)s
            """,
            parameters={"issue": issue, "dataset_sha": dataset_sha},
        ).result_rows
        chunk_count = int(chunk_rows[0][0]) if chunk_rows else 0
        return GazetteChunkFinalizeReceipt(
            issue,
            dataset_sha,
            normalized["record_count"],
            normalized["page_count"],
            chunk_count,
            True,
        )

    staged = client.query(
        f"""
        SELECT
            range_start_page,
            range_end_page,
            source_record_count,
            source_page_count,
            page_size,
            chunk_row_count,
            announcement_date,
            query_scope,
            source_capture_schema,
            source_uri
        FROM {CHUNK_STAGE_TABLE} FINAL
        WHERE announcement_issue = %(issue)s
          AND source_dataset_sha256 = %(dataset_sha)s
        ORDER BY range_start_page, range_end_page
        """,
        parameters={"issue": issue, "dataset_sha": dataset_sha},
    ).result_rows
    if not staged:
        raise GazetteAdmissionError("no committed Gazette chunks found")

    next_page = 1
    staged_row_count = 0
    for index, row in enumerate(staged):
        (
            range_start,
            range_end,
            source_record_count,
            source_page_count,
            page_size,
            chunk_row_count,
            announcement_date,
            query_scope,
            source_capture_schema,
            source_uri,
        ) = row
        range_start = int(range_start)
        range_end = int(range_end)
        if range_start != next_page:
            raise GazetteAdmissionError(
                f"Gazette chunk coverage gap/overlap: expected page {next_page}, "
                f"got {range_start}"
            )
        if range_end < range_start:
            raise GazetteAdmissionError(
                f"invalid staged range at chunk {index}"
            )
        if (
            int(source_record_count) != normalized["record_count"]
            or int(source_page_count) != normalized["page_count"]
            or int(page_size) != normalized["page_size"]
            or str(query_scope) != "ALL"
            or announcement_date != normalized["announcement_date"]
            or str(source_capture_schema) != normalized["source_capture_schema"]
            or str(source_uri) != normalized["source_uri"]
        ):
            raise GazetteAdmissionError(
                f"staged Gazette chunk {range_start}-{range_end} metadata drifted"
            )
        staged_row_count += int(chunk_row_count)
        next_page = range_end + 1

    if next_page != normalized["page_count"] + 1:
        raise GazetteAdmissionError(
            "Gazette staged chunk coverage does not reach the final page"
        )
    if staged_row_count != normalized["record_count"]:
        raise GazetteAdmissionError(
            "Gazette staged chunk row counts do not match record_count"
        )

    unique_rows_result = client.query(
        f"""
        SELECT uniqExact(source_row_id)
        FROM {ANNOUNCEMENT_TABLE}
        WHERE announcement_issue = %(issue)s
          AND source_dataset_sha256 = %(dataset_sha)s
        """,
        parameters={"issue": issue, "dataset_sha": dataset_sha},
    ).result_rows
    unique_rows = int(unique_rows_result[0][0]) if unique_rows_result else 0
    if unique_rows != normalized["record_count"]:
        raise GazetteAdmissionError(
            f"Gazette unique admitted row count {unique_rows} does not match "
            f"record_count {normalized['record_count']}"
        )

    issue_observation_fingerprint = _fingerprint(
        {
            "announcement_issue": issue,
            "announcement_date": normalized["announcement_date"].isoformat(),
            "record_count": normalized["record_count"],
            "page_count": normalized["page_count"],
            "page_size": normalized["page_size"],
            "source_dataset_sha256": dataset_sha,
        }
    )
    client.insert(
        ISSUE_TABLE,
        [[
            issue,
            normalized["announcement_date"],
            normalized["record_count"],
            normalized["page_count"],
            normalized["page_size"],
            "COMPLETE",
            normalized["source_capture_schema"],
            dataset_sha,
            normalized["source_uri"],
            normalized["collected_at"],
            issue_observation_fingerprint,
        ]],
        column_names=[
            "announcement_issue",
            "announcement_date",
            "record_count",
            "page_count",
            "capture_page_size",
            "capture_completeness",
            "source_capture_schema",
            "source_dataset_sha256",
            "source_uri",
            "collected_at",
            "observation_fingerprint",
        ],
    )

    return GazetteChunkFinalizeReceipt(
        issue,
        dataset_sha,
        normalized["record_count"],
        normalized["page_count"],
        len(staged),
        False,
    )
