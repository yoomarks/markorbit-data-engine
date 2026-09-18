from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.discovery_contract import (
    DiscoveryCursorError,
    DiscoveryLimits,
    build_page_provenance,
    build_query_identity,
    build_snapshot_ref,
    decode_cursor,
    encode_cursor,
)
from app.read_performance_baseline import DEFAULT_QUERY_BUDGET
from app.version import engine_version

SCHEMA_VERSION = "US_RECORDED_PARTY_HISTORY_SCHEMA_V2"
READY_VERSION = "US_RECORDED_PARTY_HISTORY_READY_V1"
SOURCE_TABLE = "markorbit_facts.us_recorded_party_relationship_event"
READINESS_TABLE = "markorbit_facts.us_recorded_party_history_readiness"
STREAM_ID = "us_recorded_party_history"
CANDIDATE_TYPE = "US_RECORDED_PARTY_RELATIONSHIP"
SUPPORTED_DOMAINS = ("ALL", "US_ASSIGNMENT", "US_TTAB")
MAX_PAGE_SIZE = 100
MAX_RESULTS = 10_000
READ_SETTINGS = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_PAGE_SIZE + 1}


class RecordedPartyHistoryInvalid(ValueError):
    pass


class RecordedPartyHistoryUnavailable(RuntimeError):
    pass


def normalize_recorded_party_name(value: str) -> str:
    normalized = " ".join(str(value or "").strip().split()).casefold()
    if not normalized or len(normalized) > 512:
        raise RecordedPartyHistoryInvalid("party name must contain 1 to 512 characters")
    return normalized


@dataclass(frozen=True, slots=True)
class RecordedPartyHistoryRequest:
    name: str
    source_domain: str = "ALL"
    relationship_type: str | None = None
    page_size: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        normalize_recorded_party_name(self.name)
        if str(self.source_domain).upper() not in SUPPORTED_DOMAINS:
            raise RecordedPartyHistoryInvalid(
                "source_domain must be ALL, US_ASSIGNMENT, or US_TTAB"
            )
        if self.relationship_type is not None:
            relation = str(self.relationship_type).strip().upper()
            if not relation or len(relation) > 64:
                raise RecordedPartyHistoryInvalid("relationship_type is invalid")
        if type(self.page_size) is not int or not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise RecordedPartyHistoryInvalid(
                f"page_size must be between 1 and {MAX_PAGE_SIZE}"
            )
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not self.cursor
        ):
            raise RecordedPartyHistoryInvalid(
                "cursor must be a non-empty string when provided"
            )

    @property
    def normalized_name(self) -> str:
        return normalize_recorded_party_name(self.name)

    @property
    def normalized_domain(self) -> str:
        return str(self.source_domain).upper()

    @property
    def normalized_relationship_type(self) -> str | None:
        if self.relationship_type is None:
            return None
        return str(self.relationship_type).strip().upper()

    @property
    def limits(self) -> DiscoveryLimits:
        return DiscoveryLimits(
            page_size=self.page_size,
            max_pages=100,
            max_results=MAX_RESULTS,
        )

    @property
    def query_identity(self) -> dict[str, Any]:
        return build_query_identity(
            stream_id=STREAM_ID,
            source_schema_id=SCHEMA_VERSION,
            candidate_type=CANDIDATE_TYPE,
            projection_fields=[
                "source_domain",
                "relationship_type",
                "party_name",
                "serial_number",
                "registration_number",
                "resource_type",
                "resource_id",
                "event_date",
                "first_observed_at",
                "last_observed_at",
            ],
            scope={
                "jurisdiction": "US",
                "normalized_name": self.normalized_name,
                "source_domain": self.normalized_domain,
                "relationship_type": self.normalized_relationship_type,
            },
            limits=self.limits,
        )


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [
        dict(zip(result.column_names, row, strict=True))
        for row in result.result_rows
    ]


def _readiness(client: Any) -> dict[str, Any]:
    try:
        rows = _dict_rows(
            client.query(
                f"""
                SELECT ready_version, assignment_max_rank, ttab_max_rank,
                       implementation_sha, accepted_at
                FROM {READINESS_TABLE} FINAL
                ORDER BY accepted_at DESC
                LIMIT 1
                """,
                settings=DEFAULT_QUERY_BUDGET,
            )
        )
    except Exception as exc:
        raise RecordedPartyHistoryUnavailable(str(exc)) from exc
    if not rows or str(rows[0]["ready_version"]) != READY_VERSION:
        raise RecordedPartyHistoryUnavailable(
            "US recorded party history is not backfilled and accepted"
        )
    row = rows[0]
    if int(row.get("assignment_max_rank") or 0) <= 0:
        raise RecordedPartyHistoryUnavailable(
            "US recorded party Assignment watermark is invalid"
        )
    if int(row.get("ttab_max_rank") or 0) <= 0:
        raise RecordedPartyHistoryUnavailable(
            "US recorded party TTAB watermark is invalid"
        )
    return row


def _cursor_position(row: Mapping[str, Any]) -> list[str]:
    return [
        str(row["source_domain"]),
        str(row["relationship_type"]),
        str(row["serial_number"]),
        str(row["resource_id"]),
        str(row["party_key"]),
    ]


def _validate_position(position: Sequence[Any]) -> tuple[str, str, str, str, str]:
    if len(position) != 5:
        raise DiscoveryCursorError(
            "US recorded party cursor must contain domain/relation/serial/resource/party"
        )
    values = tuple(str(item or "").strip() for item in position)
    if (
        values[0] not in {"US_ASSIGNMENT", "US_TTAB"}
        or not values[1]
        or not values[3]
        or len(values[4]) != 64
    ):
        raise DiscoveryCursorError("US recorded party cursor position is malformed")
    return values  # type: ignore[return-value]


def _page_sql(
    request: RecordedPartyHistoryRequest,
    *,
    position: Sequence[Any] | None,
    fetch_limit: int,
    assignment_max_rank: int,
    ttab_max_rank: int,
) -> str:
    domain_clause = ""
    if request.normalized_domain != "ALL":
        domain_clause = (
            f"AND source_domain = {_sql_text(request.normalized_domain)}"
        )
    relation_clause = ""
    if request.normalized_relationship_type is not None:
        relation_clause = (
            "AND relationship_type = "
            f"{_sql_text(request.normalized_relationship_type)}"
        )
    cursor_clause = ""
    if position is not None:
        domain, relation, serial, resource, party_key = _validate_position(position)
        cursor_clause = (
            "AND tuple(source_domain, relationship_type, serial_number, "
            "resource_id, toString(party_key)) > "
            f"tuple({_sql_text(domain)}, {_sql_text(relation)}, "
            f"{_sql_text(serial)}, {_sql_text(resource)}, {_sql_text(party_key)})"
        )
    return f"""
        WITH grouped AS
        (
            SELECT
                source_domain,
                relationship_type,
                serial_number,
                argMax(
                    registration_number,
                    tuple(source_rank, relationship_observation_hash)
                ) AS registration_number,
                resource_type,
                resource_id,
                party_key,
                argMax(
                    party_name,
                    tuple(source_rank, relationship_observation_hash)
                ) AS party_name,
                argMax(
                    party_side,
                    tuple(source_rank, relationship_observation_hash)
                ) AS party_side,
                argMax(
                    source_role,
                    tuple(source_rank, relationship_observation_hash)
                ) AS source_role,
                argMax(
                    event_date,
                    tuple(source_rank, relationship_observation_hash)
                ) AS event_date,
                min(observed_at) AS first_observed_at,
                max(observed_at) AS last_observed_at,
                max(source_rank) AS latest_source_rank
            FROM {SOURCE_TABLE} FINAL
            WHERE normalized_name = {_sql_text(request.normalized_name)}
              AND (
                    (source_domain = 'US_ASSIGNMENT'
                     AND source_rank <= {int(assignment_max_rank)})
                 OR (source_domain = 'US_TTAB'
                     AND source_rank <= {int(ttab_max_rank)})
              )
              {domain_clause}
              {relation_clause}
            GROUP BY
                source_domain,
                relationship_type,
                serial_number,
                resource_type,
                resource_id,
                party_key
        )
        SELECT
            source_domain,
            relationship_type,
            party_name,
            party_side,
            source_role,
            serial_number,
            registration_number,
            resource_type,
            resource_id,
            event_date,
            first_observed_at,
            last_observed_at,
            latest_source_rank,
            toString(party_key) AS party_key
        FROM grouped
        WHERE 1
          {cursor_clause}
        ORDER BY
            source_domain,
            relationship_type,
            serial_number,
            resource_id,
            party_key
        LIMIT {int(fetch_limit)}
    """


def _candidate(row: Mapping[str, Any], normalized_name: str) -> dict[str, Any]:
    return {
        "candidate_type": CANDIDATE_TYPE,
        "normalized_name": normalized_name,
        "source_domain": str(row["source_domain"]),
        "relationship_type": str(row["relationship_type"]),
        "party_key": str(row["party_key"]),
        "party_name": str(row.get("party_name") or "") or None,
        "party_side": str(row.get("party_side") or "") or None,
        "source_role": str(row.get("source_role") or "") or None,
        "serial_number": str(row.get("serial_number") or "") or None,
        "registration_number": str(row.get("registration_number") or "") or None,
        "resource_type": str(row["resource_type"]),
        "resource_id": str(row["resource_id"]),
        "event_date": str(row.get("event_date") or "") or None,
        "first_observed_at": str(row.get("first_observed_at") or ""),
        "last_observed_at": str(row.get("last_observed_at") or ""),
        "latest_source_rank": int(row.get("latest_source_rank") or 0),
        "authority_level": "DIRECT_OFFICIAL",
        "legal_conclusion": False,
        "identity_resolution_claimed": False,
        "review_required": True,
    }


def execute_page(
    request: RecordedPartyHistoryRequest,
    *,
    client: Any,
    runtime_engine_version: str | None = None,
) -> dict[str, Any]:
    ready = _readiness(client)
    query = request.query_identity
    watermark = (
        f"assignment_rank:{int(ready['assignment_max_rank'])}:"
        f"ttab_rank:{int(ready['ttab_max_rank'])}"
    )
    snapshot = build_snapshot_ref(
        snapshot_id=f"{READY_VERSION}:{watermark}",
        snapshot_kind="US_RECORDED_PARTY_HISTORY_ACCEPTED_WATERMARK",
        watermark=watermark,
        source_version=READY_VERSION,
    )
    page_number = 1
    emitted_before = 0
    position: Sequence[Any] | None = None
    if request.cursor is not None:
        decoded = decode_cursor(
            request.cursor,
            expected_query_hash=query["query_hash"],
            expected_snapshot_id=snapshot["snapshot_id"],
            limits=request.limits,
        )
        page_number = int(decoded["next_page"])
        emitted_before = int(decoded["emitted_count"])
        position = decoded["position"]
        _validate_position(position)
    remaining = request.limits.max_results - emitted_before
    if remaining <= 0:
        raise DiscoveryCursorError(
            "US recorded party history hard result bound is exhausted"
        )
    capacity = min(request.page_size, remaining)
    try:
        raw = _dict_rows(
            client.query(
                _page_sql(
                    request,
                    position=position,
                    fetch_limit=capacity + 1,
                    assignment_max_rank=int(ready["assignment_max_rank"]),
                    ttab_max_rank=int(ready["ttab_max_rank"]),
                ),
                settings=READ_SETTINGS,
            )
        )
    except Exception as exc:
        raise RecordedPartyHistoryUnavailable(str(exc)) from exc
    page_rows = raw[:capacity]
    results = [
        _candidate(row, request.normalized_name)
        for row in page_rows
    ]
    emitted = emitted_before + len(results)
    has_extra = len(raw) > capacity
    next_cursor = None
    if (
        has_extra
        and results
        and page_number < request.limits.max_pages
        and emitted < request.limits.max_results
    ):
        next_cursor = encode_cursor(
            query_hash=query["query_hash"],
            snapshot_id=snapshot["snapshot_id"],
            position=_cursor_position(page_rows[-1]),
            next_page=page_number + 1,
            emitted_count=emitted,
            limits=request.limits,
        )
    provenance = build_page_provenance(
        query_identity=query,
        snapshot=snapshot,
        engine_version=runtime_engine_version or engine_version(),
        page_number=page_number,
        result_count=len(results),
        emitted_count=emitted,
        next_cursor=next_cursor,
    )
    return {
        "stream_id": STREAM_ID,
        "candidate_type": CANDIDATE_TYPE,
        "input_name": str(request.name).strip(),
        "normalized_name": request.normalized_name,
        "source_domain": request.normalized_domain,
        "relationship_type": request.normalized_relationship_type,
        "results": results,
        "result_count": len(results),
        "next_cursor": next_cursor,
        "provenance": provenance,
        "semantics": (
            "EXACT_NORMALIZED_NAME_RECORDED_USPTO_ASSIGNMENT_AND_TTAB_HISTORY;"
            "NOT_CROSS_SOURCE_IDENTITY_RESOLUTION_OR_LEGAL_TITLE_CONCLUSION"
        ),
    }
