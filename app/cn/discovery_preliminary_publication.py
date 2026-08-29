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

STREAM_ID = "CN_PRELIMINARY_PUBLICATION_FACT_DISCOVERY_V1"
SOURCE_SCHEMA_ID = "CN_CASE_CURRENT_PRELIMINARY_PUBLICATION_DISCOVERY_V1"
CANDIDATE_TYPE = "CN_TRADEMARK_PRELIMINARY_PUBLICATION"
SOURCE_TABLE = "markorbit_facts.cn_case_current"
SNAPSHOT_KIND = "CN_QUIESCENT_SERVING_EPOCH"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RESULTS = 1_000
MAX_INTERVAL_DAYS = 31

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
    "prelim_pub_date ASC",
    "application_number ASC",
    "toString(case_id) ASC",
)


@dataclass(frozen=True, slots=True)
class PreliminaryPublicationDiscoveryRequest:
    start_date: date
    end_date: date
    page_size: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start_date, date) or isinstance(self.start_date, datetime):
            raise DiscoveryContractError("start_date must be a date")
        if not isinstance(self.end_date, date) or isinstance(self.end_date, datetime):
            raise DiscoveryContractError("end_date must be a date")
        interval_days = (self.end_date - self.start_date).days
        if interval_days <= 0:
            raise DiscoveryContractError("discovery date interval must be positive")
        if interval_days > MAX_INTERVAL_DAYS:
            raise DiscoveryContractError(
                f"discovery date interval exceeds {MAX_INTERVAL_DAYS} calendar days"
            )
        if type(self.page_size) is not int or self.page_size <= 0:
            raise DiscoveryContractError("page_size must be a positive integer")
        if self.page_size > MAX_PAGE_SIZE:
            raise DiscoveryContractError(f"page_size exceeds pilot ceiling {MAX_PAGE_SIZE}")
        if self.cursor is not None and (not isinstance(self.cursor, str) or not self.cursor):
            raise DiscoveryCursorError("cursor must be a non-empty string when provided")

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
            "prelim_pub_date": {
                "start_inclusive": self.start_date.isoformat(),
                "end_exclusive": self.end_date.isoformat(),
            },
            "is_deleted": 0,
            "prelim_pub_date_not_null": True,
            "ordering": list(ORDERING),
            "ranking": "NONE",
            "joins": "NONE",
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
        _required_text(candidate.get("prelim_pub_date"), "prelim_pub_date"),
        _required_text(candidate.get("application_number"), "application_number"),
        _required_text(candidate.get("case_id"), "case_id"),
    ]


def _validate_cursor_position(position: Sequence[Any]) -> tuple[date, str, str]:
    if len(position) != 3:
        raise DiscoveryCursorError("CN preliminary-publication cursor must have 3 keyset values")
    cursor_date = _as_date(position[0], "cursor.prelim_pub_date")
    application_number = _required_text(position[1], "cursor.application_number")
    case_id = _required_text(position[2], "cursor.case_id")
    return cursor_date, application_number, case_id


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
        cursor_date, application_number, case_id = _validate_cursor_position(position)
        cursor_date_sql = f"toDate32({_sql_text(cursor_date.isoformat())})"
        application_sql = _sql_text(application_number)
        case_id_sql = _sql_text(case_id)
        keyset = f"""
          AND (
                prelim_pub_date > {cursor_date_sql}
             OR (
                    prelim_pub_date = {cursor_date_sql}
                AND application_number > {application_sql}
             )
             OR (
                    prelim_pub_date = {cursor_date_sql}
                AND application_number = {application_sql}
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
        WHERE is_deleted = 0
          AND prelim_pub_date IS NOT NULL
          AND prelim_pub_date >= toDate32({_sql_text(request.start_date.isoformat())})
          AND prelim_pub_date < toDate32({_sql_text(request.end_date.isoformat())}){keyset}
        ORDER BY prelim_pub_date ASC, application_number ASC, toString(case_id) ASC
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
        )
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
    }
