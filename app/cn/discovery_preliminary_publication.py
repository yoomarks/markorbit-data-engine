from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from app.cn.research_filing_to_prelim_duration import _engine_version, _serving_epoch
from app.discovery_contract import (
    DiscoveryContractError,
    DiscoveryCursorError,
    DiscoveryLimits,
    build_page_provenance,
    build_query_identity,
    build_snapshot_ref,
    decode_cursor,
    encode_cursor,
)

STREAM_ID = "CN_PRELIMINARY_PUBLICATION_FACT_DISCOVERY_V2"
SOURCE_SCHEMA_ID = "CN_CASE_CURRENT_PRELIMINARY_PUBLICATION_DISCOVERY_V2"
CANDIDATE_TYPE = "CN_TRADEMARK_PRELIMINARY_PUBLICATION"
SOURCE_TABLE = "markorbit_facts.cn_case_current"
SNAPSHOT_KIND = "CN_QUIESCENT_SERVING_EPOCH"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RESULTS = 1_000
MAX_ROWS_TO_READ = 250_000
MAX_BYTES_TO_READ = 256 * 1024 * 1024

PROJECTION_FIELDS: tuple[str, ...] = (
    "case_id",
    "application_number",
    "mark_name_raw",
    "classes",
    "filing_date",
    "prelim_pub_date",
    "prelim_pub_issue",
    "source_effective_date",
    "source_package_id",
    "source_row_hash",
    "record_hash",
    "source_rank",
)
ORDERING: tuple[str, ...] = (
    "application_number ASC",
    "toString(case_id) ASC",
)
READ_SETTINGS: dict[str, Any] = {
    "max_rows_to_read": MAX_ROWS_TO_READ,
    "max_bytes_to_read": MAX_BYTES_TO_READ,
    "read_overflow_mode": "throw",
}


@dataclass(frozen=True, slots=True)
class PreliminaryPublicationDiscoveryRequest:
    application_number_start: str
    application_number_end: str
    page_size: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    def __post_init__(self) -> None:
        start = str(self.application_number_start or "").strip()
        end = str(self.application_number_end or "").strip()
        if not start or not end:
            raise DiscoveryContractError("application-number bounds must be non-empty")
        if start >= end:
            raise DiscoveryContractError(
                "application_number_start must be lexically less than application_number_end"
            )
        if type(self.page_size) is not int or self.page_size <= 0:
            raise DiscoveryContractError("page_size must be a positive integer")
        if self.page_size > MAX_PAGE_SIZE:
            raise DiscoveryContractError(f"page_size exceeds pilot ceiling {MAX_PAGE_SIZE}")
        if self.cursor is not None and (not isinstance(self.cursor, str) or not self.cursor):
            raise DiscoveryCursorError("cursor must be a non-empty string when provided")
        object.__setattr__(self, "application_number_start", start)
        object.__setattr__(self, "application_number_end", end)

    @property
    def limits(self) -> DiscoveryLimits:
        return DiscoveryLimits(
            page_size=self.page_size,
            max_pages=MAX_PAGES,
            max_results=MAX_RESULTS,
        )

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "jurisdiction": "CN",
            "application_number": {
                "start_inclusive": self.application_number_start,
                "end_exclusive": self.application_number_end,
            },
            "is_deleted": 0,
            "prelim_pub_date_not_null": True,
            "ordering": list(ORDERING),
            "ranking": "NONE",
            "joins": "NONE",
            "read_budget": {
                "max_rows_to_read": MAX_ROWS_TO_READ,
                "max_bytes_to_read": MAX_BYTES_TO_READ,
                "overflow_mode": "throw",
            },
        }

    @property
    def query_identity(self) -> dict[str, Any]:
        return build_query_identity(
            stream_id=STREAM_ID,
            source_schema_id=SOURCE_SCHEMA_ID,
            candidate_type=CANDIDATE_TYPE,
            projection_fields=PROJECTION_FIELDS,
            scope=self.scope,
            limits=self.limits,
        )


def _sql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _as_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise DiscoveryContractError(f"{field} must be an ISO date") from exc


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DiscoveryContractError(f"{field} is required in Discovery source rows")
    return text


def _optional_date(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    return _as_date(value, field).isoformat()


def _normalize_classes(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise DiscoveryContractError("classes must be an array in Discovery source rows")
    classes: list[int] = []
    for item in value:
        if type(item) is not int or item < 1 or item > 45:
            raise DiscoveryContractError("classes contains an invalid Nice class")
        classes.append(item)
    return classes


def normalize_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    source_rank = row.get("source_rank")
    if type(source_rank) is not int or source_rank < 0:
        raise DiscoveryContractError("source_rank must be a non-negative integer")
    return {
        "candidate_type": CANDIDATE_TYPE,
        "case_id": _required_text(row.get("case_id"), "case_id"),
        "application_number": _required_text(
            row.get("application_number"), "application_number"
        ),
        "mark_name_raw": str(row.get("mark_name_raw") or ""),
        "classes": _normalize_classes(row.get("classes")),
        "filing_date": _optional_date(row.get("filing_date"), "filing_date"),
        "prelim_pub_date": _as_date(row.get("prelim_pub_date"), "prelim_pub_date").isoformat(),
        "prelim_pub_issue": str(row.get("prelim_pub_issue") or ""),
        "source_effective_date": _optional_date(
            row.get("source_effective_date"), "source_effective_date"
        ),
        "source_package_id": _required_text(
            row.get("source_package_id"), "source_package_id"
        ),
        "source_row_hash": _required_text(row.get("source_row_hash"), "source_row_hash"),
        "record_hash": _required_text(row.get("record_hash"), "record_hash"),
        "source_rank": source_rank,
    }


def _cursor_position(candidate: Mapping[str, Any]) -> list[str]:
    return [
        _required_text(candidate.get("application_number"), "application_number"),
        _required_text(candidate.get("case_id"), "case_id"),
    ]


def _validate_cursor_position(position: Sequence[Any]) -> tuple[str, str]:
    if len(position) != 2:
        raise DiscoveryCursorError("CN preliminary-publication cursor must have 2 keyset values")
    application_number = _required_text(position[0], "cursor.application_number")
    case_id = _required_text(position[1], "cursor.case_id")
    return application_number, case_id


def build_page_sql(
    request: PreliminaryPublicationDiscoveryRequest,
    *,
    position: Sequence[Any] | None = None,
    fetch_limit: int | None = None,
) -> str:
    limit = request.page_size + 1 if fetch_limit is None else int(fetch_limit)
    if limit <= 0 or limit > request.page_size + 1:
        raise DiscoveryContractError("fetch_limit exceeds page_size + 1 continuation bound")

    keyset = ""
    if position is not None:
        application_number, case_id = _validate_cursor_position(position)
        application_sql = _sql_text(application_number)
        case_id_sql = _sql_text(case_id)
        keyset = f"""
          AND (
                application_number > {application_sql}
             OR (
                    application_number = {application_sql}
                AND toString(case_id) > {case_id_sql}
             )
          )"""

    return f"""
        SELECT
            toString(case_id) AS case_id,
            application_number,
            mark_name_raw,
            classes,
            filing_date,
            prelim_pub_date,
            prelim_pub_issue,
            source_effective_date,
            toString(last_source_package_id) AS source_package_id,
            source_row_hash,
            record_hash,
            source_rank
        FROM {SOURCE_TABLE} FINAL
        WHERE application_number >= {_sql_text(request.application_number_start)}
          AND application_number < {_sql_text(request.application_number_end)}
          AND is_deleted = 0
          AND prelim_pub_date IS NOT NULL{keyset}
        ORDER BY application_number ASC, toString(case_id) ASC
        LIMIT {limit}
    """


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _snapshot_for_epoch(epoch: Any, *, source_version: str) -> dict[str, str]:
    watermark = epoch.watermark
    return build_snapshot_ref(
        snapshot_id=watermark,
        snapshot_kind=SNAPSHOT_KIND,
        watermark=watermark,
        source_version=source_version,
    )


def execute_page(
    request: PreliminaryPublicationDiscoveryRequest,
    *,
    client: Any,
    serving_epoch_getter: Any = _serving_epoch,
    engine_version: str | None = None,
) -> dict[str, Any]:
    version = engine_version or _engine_version()
    query_identity = request.query_identity
    limits = request.limits

    before = serving_epoch_getter()
    snapshot = _snapshot_for_epoch(before, source_version=version)

    page_number = 1
    emitted_before = 0
    position: Sequence[Any] | None = None
    if request.cursor is not None:
        decoded = decode_cursor(
            request.cursor,
            expected_query_hash=query_identity["query_hash"],
            expected_snapshot_id=snapshot["snapshot_id"],
            limits=limits,
        )
        page_number = int(decoded["next_page"])
        emitted_before = int(decoded["emitted_count"])
        position = decoded["position"]
        _validate_cursor_position(position)

    remaining = limits.max_results - emitted_before
    if remaining <= 0:
        raise DiscoveryCursorError("Discovery result hard bound is already exhausted")
    page_capacity = min(limits.page_size, remaining)

    result = client.query(
        build_page_sql(
            request,
            position=position,
            fetch_limit=min(page_capacity + 1, request.page_size + 1),
        ),
        settings=READ_SETTINGS,
    )
    raw_rows = _dict_rows(result)

    after = serving_epoch_getter()
    if after != before:
        raise DiscoveryContractError(
            "CN serving epoch changed during Discovery page execution; replay is unsafe"
        )

    has_extra = len(raw_rows) > page_capacity
    page_rows = raw_rows[:page_capacity]
    candidates = [normalize_candidate(row) for row in page_rows]
    emitted_count = emitted_before + len(candidates)

    next_cursor: str | None = None
    continuation_allowed = (
        has_extra
        and bool(candidates)
        and page_number < limits.max_pages
        and emitted_count < limits.max_results
    )
    if continuation_allowed:
        next_cursor = encode_cursor(
            query_hash=query_identity["query_hash"],
            snapshot_id=snapshot["snapshot_id"],
            position=_cursor_position(candidates[-1]),
            next_page=page_number + 1,
            emitted_count=emitted_count,
            limits=limits,
        )

    provenance = build_page_provenance(
        query_identity=query_identity,
        snapshot=snapshot,
        engine_version=version,
        page_number=page_number,
        result_count=len(candidates),
        emitted_count=emitted_count,
        next_cursor=next_cursor,
    )

    return {
        "stream_id": STREAM_ID,
        "candidate_type": CANDIDATE_TYPE,
        "query": query_identity,
        "snapshot": snapshot,
        "results": candidates,
        "next_cursor": next_cursor,
        "provenance": provenance,
        "bounded_truncation": bool(has_extra and not continuation_allowed),
        "read_budget": dict(READ_SETTINGS),
    }
