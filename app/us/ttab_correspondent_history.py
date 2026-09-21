from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
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


SCHEMA_VERSION = "US_TTAB_CORRESPONDENT_MARK_HISTORY_SCHEMA_V1"
READY_VERSION = "US_TTAB_CORRESPONDENT_MARK_HISTORY_READY_V1"
TARGET_TABLE = "markorbit_facts.us_ttab_correspondent_mark_history"
READINESS_TABLE = "markorbit_facts.us_ttab_correspondent_mark_history_readiness"
WATERMARK_TABLE = "markorbit_facts.us_ttab_correspondent_mark_history_watermark"
TARGET_COLUMNS = [
    "observation_key",
    "relationship_key",
    "normalized_name",
    "correspondent_name",
    "correspondent_organization",
    "proceeding_number",
    "party_side",
    "party_ordinal",
    "party_name",
    "party_role",
    "party_key",
    "property_key",
    "mark_identity",
    "serial_number",
    "registration_number",
    "mark_text",
    "source_kind",
    "source_snapshot_at",
    "source_file",
    "source_package_id",
    "source_rank",
    "serving_generation",
]
STREAM_ID = "us_ttab_correspondent_mark_history"
CANDIDATE_TYPE = "US_TTAB_CORRESPONDENT_MARK_RELATIONSHIP"
MAX_PAGE_SIZE = 100
MAX_RESULTS = 10_000
READ_SETTINGS = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_PAGE_SIZE + 1}


class TTABCorrespondentHistoryInvalid(ValueError):
    pass


class TTABCorrespondentHistoryUnavailable(RuntimeError):
    pass


def normalize_correspondent_name(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    if not normalized or len(normalized) > 512:
        raise TTABCorrespondentHistoryInvalid(
            "correspondent name must contain 1 to 512 characters"
        )
    return normalized


def mark_identity(serial_number: str, registration_number: str) -> str:
    serial = str(serial_number or "").strip()
    registration = str(registration_number or "").strip()
    if serial:
        return f"SERIAL:{serial}"
    if registration:
        return f"REG:{registration}"
    return ""


def _digest_key(*parts: object) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def correspondent_relationship_key(
    normalized_name: str,
    proceeding_number: str,
    party_side: str,
    party_ordinal: int,
    serial_number: str,
    registration_number: str,
) -> str:
    identity = mark_identity(serial_number, registration_number)
    if not identity:
        raise TTABCorrespondentHistoryInvalid(
            "TTAB correspondent relationship requires serial or registration identity"
        )
    return _digest_key(
        normalized_name,
        proceeding_number,
        party_side,
        int(party_ordinal),
        identity,
    )


def correspondent_observation_key(
    source_package_id: object,
    proceeding_number: str,
    party_key: str,
    property_key: str,
    normalized_name: str,
) -> str:
    return _digest_key(
        source_package_id,
        proceeding_number,
        party_key,
        property_key,
        normalized_name,
    )


def _table_exists(client: Any, table_name: str) -> bool:
    result = client.query(
        f"""
        SELECT count()
        FROM system.tables
        WHERE database = 'markorbit_facts'
          AND name = {_sql_text(table_name)}
        """,
        settings=DEFAULT_QUERY_BUDGET,
    ).result_rows
    return bool(result and int(result[0][0]) == 1)


def current_serving_watermark(client: Any) -> dict[str, Any] | None:
    if not _table_exists(
        client, "us_ttab_correspondent_mark_history_watermark"
    ):
        return None
    result = client.query(
        f"""
        SELECT serving_generation, source_max_rank,
               toString(source_package_id), updated_at
        FROM {WATERMARK_TABLE}
        WHERE ready_version = {_sql_text(READY_VERSION)}
        ORDER BY serving_generation DESC, updated_at DESC
        LIMIT 1
        """,
        settings=DEFAULT_QUERY_BUDGET,
    )
    if not result.result_rows:
        return None
    row = result.result_rows[0]
    return {
        "serving_generation": int(row[0]),
        "source_max_rank": int(row[1]),
        "source_package_id": str(row[2]),
        "updated_at": row[3],
    }


def history_ready(client: Any) -> bool:
    try:
        if not _table_exists(
            client, "us_ttab_correspondent_mark_history_readiness"
        ):
            return False
        rows = client.query(
            f"""
            SELECT ready_version, accepted_serving_generation,
                   accepted_source_max_rank
            FROM {READINESS_TABLE} FINAL
            WHERE ready_version = {_sql_text(READY_VERSION)}
            ORDER BY accepted_at DESC
            LIMIT 1
            """,
            settings=DEFAULT_QUERY_BUDGET,
        ).result_rows
        if not rows or str(rows[0][0]) != READY_VERSION:
            return False
        watermark = current_serving_watermark(client)
        if watermark is None:
            return False
        return (
            int(watermark["serving_generation"]) >= int(rows[0][1])
            and int(watermark["source_max_rank"]) >= int(rows[0][2])
        )
    except Exception:
        return False


def next_serving_generation(client: Any) -> int:
    if not history_ready(client):
        raise TTABCorrespondentHistoryUnavailable(
            "US TTAB correspondent history is not READY"
        )
    watermark = current_serving_watermark(client)
    if watermark is None:
        raise TTABCorrespondentHistoryUnavailable(
            "US TTAB correspondent serving watermark is absent"
        )
    current_generation = int(watermark["serving_generation"])
    pending = client.query(
        f"""
        SELECT count()
        FROM {TARGET_TABLE}
        WHERE serving_generation > {current_generation}
        """,
        settings=DEFAULT_QUERY_BUDGET,
    ).result_rows
    if pending and int(pending[0][0]) > 0:
        raise TTABCorrespondentHistoryUnavailable(
            "uncommitted TTAB correspondent generation rows require cleanup"
        )
    return current_generation + 1


def advance_serving_watermark(
    client: Any,
    *,
    serving_generation: int,
    source_rank: int,
    source_package_id: Any,
) -> None:
    if serving_generation < 2 or source_rank <= 0:
        raise TTABCorrespondentHistoryUnavailable(
            "invalid TTAB correspondent serving watermark advance"
        )
    current = current_serving_watermark(client)
    if current is None or int(current["serving_generation"]) + 1 != serving_generation:
        raise TTABCorrespondentHistoryUnavailable(
            "TTAB correspondent serving generation advanced concurrently"
        )
    next_source_max = max(int(current["source_max_rank"]), int(source_rank))
    client.insert(
        WATERMARK_TABLE,
        [[
            READY_VERSION,
            int(serving_generation),
            next_source_max,
            source_package_id,
        ]],
        column_names=[
            "ready_version",
            "serving_generation",
            "source_max_rank",
            "source_package_id",
        ],
    )
    observed = current_serving_watermark(client)
    if (
        observed is None
        or int(observed["serving_generation"]) != serving_generation
        or int(observed["source_max_rank"]) != next_source_max
    ):
        raise TTABCorrespondentHistoryUnavailable(
            "TTAB correspondent serving watermark advance was not durable"
        )


@dataclass(frozen=True, slots=True)
class TTABCorrespondentHistoryRequest:
    name: str
    page_size: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        normalize_correspondent_name(self.name)
        if type(self.page_size) is not int or not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise TTABCorrespondentHistoryInvalid(
                f"page_size must be between 1 and {MAX_PAGE_SIZE}"
            )
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not self.cursor
        ):
            raise TTABCorrespondentHistoryInvalid(
                "cursor must be a non-empty string when provided"
            )

    @property
    def normalized_name(self) -> str:
        return normalize_correspondent_name(self.name)

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
                "correspondent_name",
                "correspondent_organization",
                "proceeding_number",
                "party_side",
                "party_ordinal",
                "party_name",
                "party_role",
                "serial_number",
                "registration_number",
                "mark_text",
                "first_observed_at",
                "last_observed_at",
                "latest_serving_generation",
                "latest_source_package_id",
            ],
            scope={
                "jurisdiction": "US",
                "source_domain": "US_TTAB",
                "normalized_name": self.normalized_name,
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
                SELECT ready_version, accepted_source_max_rank,
                       accepted_serving_generation, source_joined_rows,
                       target_rows, target_relationship_count,
                       implementation_sha, accepted_at
                FROM {READINESS_TABLE} FINAL
                WHERE ready_version = {_sql_text(READY_VERSION)}
                ORDER BY accepted_at DESC
                LIMIT 1
                """,
                settings=DEFAULT_QUERY_BUDGET,
            )
        )
    except Exception as exc:
        raise TTABCorrespondentHistoryUnavailable(str(exc)) from exc
    if not rows or str(rows[0]["ready_version"]) != READY_VERSION:
        raise TTABCorrespondentHistoryUnavailable(
            "US TTAB correspondent history is not backfilled and accepted"
        )
    row = rows[0]
    watermark = current_serving_watermark(client)
    if watermark is None:
        raise TTABCorrespondentHistoryUnavailable(
            "US TTAB correspondent serving watermark is absent"
        )
    if (
        int(watermark["serving_generation"])
        < int(row.get("accepted_serving_generation") or 0)
        or int(watermark["source_max_rank"])
        < int(row.get("accepted_source_max_rank") or 0)
    ):
        raise TTABCorrespondentHistoryUnavailable(
            "US TTAB correspondent serving watermark is behind READY acceptance"
        )
    row["serving_generation"] = int(watermark["serving_generation"])
    row["source_max_rank"] = int(watermark["source_max_rank"])
    return row


def _cursor_position(row: Mapping[str, Any]) -> list[str]:
    return [
        str(row["proceeding_number"]),
        str(row["mark_identity"]),
        str(row["relationship_key"]),
    ]


def _validate_position(position: Sequence[Any]) -> tuple[str, str, str]:
    if len(position) != 3:
        raise DiscoveryCursorError(
            "US TTAB correspondent history cursor must contain proceeding/mark/relation"
        )
    values = tuple(str(item or "").strip() for item in position)
    if not values[0] or not values[1] or len(values[2]) != 64:
        raise DiscoveryCursorError(
            "US TTAB correspondent history cursor position is malformed"
        )
    return values  # type: ignore[return-value]


def _page_sql(
    request: TTABCorrespondentHistoryRequest,
    *,
    position: Sequence[Any] | None,
    fetch_limit: int,
    serving_generation: int,
) -> str:
    cursor_clause = ""
    if position is not None:
        proceeding, identity, relationship_key = _validate_position(position)
        cursor_clause = (
            "AND tuple(proceeding_number, mark_identity, toString(relationship_key)) > "
            f"tuple({_sql_text(proceeding)}, {_sql_text(identity)}, "
            f"{_sql_text(relationship_key)})"
        )
    return f"""
        WITH accepted_observations AS
        (
            SELECT
                observation_key,
                argMax(relationship_key, serving_generation) AS relationship_key,
                argMax(mark_identity, serving_generation) AS mark_identity,
                argMax(correspondent_name, serving_generation) AS correspondent_name,
                argMax(correspondent_organization, serving_generation)
                    AS correspondent_organization,
                argMax(proceeding_number, serving_generation) AS proceeding_number,
                argMax(party_side, serving_generation) AS party_side,
                argMax(party_ordinal, serving_generation) AS party_ordinal,
                argMax(party_name, serving_generation) AS party_name,
                argMax(party_role, serving_generation) AS party_role,
                argMax(serial_number, serving_generation) AS serial_number,
                argMax(registration_number, serving_generation) AS registration_number,
                argMax(mark_text, serving_generation) AS mark_text,
                argMax(source_snapshot_at, serving_generation) AS source_snapshot_at,
                argMax(source_rank, serving_generation) AS source_rank,
                argMax(source_kind, serving_generation) AS source_kind,
                argMax(source_file, serving_generation) AS source_file,
                argMax(source_package_id, serving_generation) AS source_package_id,
                max(serving_generation) AS serving_generation
            FROM {TARGET_TABLE} FINAL
            WHERE normalized_name = {_sql_text(request.normalized_name)}
              AND serving_generation <= {int(serving_generation)}
            GROUP BY observation_key
        ),
        grouped AS
        (
            SELECT
                relationship_key,
                argMax(mark_identity, tuple(source_rank, observation_key)) AS mark_identity,
                argMax(correspondent_name, tuple(source_rank, observation_key)) AS correspondent_name,
                argMax(correspondent_organization, tuple(source_rank, observation_key))
                    AS correspondent_organization,
                argMax(proceeding_number, tuple(source_rank, observation_key)) AS proceeding_number,
                argMax(party_side, tuple(source_rank, observation_key)) AS party_side,
                argMax(party_ordinal, tuple(source_rank, observation_key)) AS party_ordinal,
                argMax(party_name, tuple(source_rank, observation_key)) AS party_name,
                argMax(party_role, tuple(source_rank, observation_key)) AS party_role,
                argMax(serial_number, tuple(source_rank, observation_key)) AS serial_number,
                argMax(registration_number, tuple(source_rank, observation_key))
                    AS registration_number,
                argMax(mark_text, tuple(source_rank, observation_key)) AS mark_text,
                min(source_snapshot_at) AS first_observed_at,
                max(source_snapshot_at) AS last_observed_at,
                min(source_rank) AS first_source_rank,
                max(source_rank) AS latest_source_rank,
                max(serving_generation) AS latest_serving_generation,
                count() AS observation_count,
                argMax(source_kind, tuple(source_rank, observation_key)) AS latest_source_kind,
                argMax(source_file, tuple(source_rank, observation_key)) AS latest_source_file,
                argMax(toString(source_package_id), tuple(source_rank, observation_key))
                    AS latest_source_package_id
            FROM accepted_observations
            GROUP BY relationship_key
        )
        SELECT
            toString(relationship_key) AS relationship_key,
            mark_identity,
            correspondent_name,
            correspondent_organization,
            proceeding_number,
            party_side,
            party_ordinal,
            party_name,
            party_role,
            serial_number,
            registration_number,
            mark_text,
            first_observed_at,
            last_observed_at,
            first_source_rank,
            latest_source_rank,
            latest_serving_generation,
            observation_count,
            latest_source_kind,
            latest_source_file,
            latest_source_package_id
        FROM grouped
        WHERE 1
          {cursor_clause}
        ORDER BY proceeding_number, mark_identity, relationship_key
        LIMIT {int(fetch_limit)}
    """


def _candidate(row: Mapping[str, Any], normalized_name: str) -> dict[str, Any]:
    return {
        "candidate_type": CANDIDATE_TYPE,
        "normalized_name": normalized_name,
        "relationship_key": str(row["relationship_key"]),
        "correspondent_name": str(row.get("correspondent_name") or "") or None,
        "correspondent_organization": (
            str(row.get("correspondent_organization") or "") or None
        ),
        "proceeding_number": str(row["proceeding_number"]),
        "party_side": str(row.get("party_side") or "") or None,
        "party_ordinal": int(row.get("party_ordinal") or 0),
        "party_name": str(row.get("party_name") or "") or None,
        "party_role": str(row.get("party_role") or "") or None,
        "serial_number": str(row.get("serial_number") or "") or None,
        "registration_number": str(row.get("registration_number") or "") or None,
        "mark_text": str(row.get("mark_text") or "") or None,
        "first_observed_at": str(row.get("first_observed_at") or ""),
        "last_observed_at": str(row.get("last_observed_at") or ""),
        "first_source_rank": int(row.get("first_source_rank") or 0),
        "latest_source_rank": int(row.get("latest_source_rank") or 0),
        "latest_serving_generation": int(
            row.get("latest_serving_generation") or 0
        ),
        "observation_count": int(row.get("observation_count") or 0),
        "latest_source_kind": str(row.get("latest_source_kind") or ""),
        "latest_source_file": str(row.get("latest_source_file") or ""),
        "latest_source_package_id": str(row.get("latest_source_package_id") or ""),
        "authority_level": "DIRECT_OFFICIAL",
        "source_domain": "US_TTAB",
        "identity_resolution_claimed": False,
        "continuing_representation_claimed": False,
        "legal_conclusion": False,
        "review_required": True,
    }


def execute_page(
    request: TTABCorrespondentHistoryRequest,
    *,
    client: Any,
    runtime_engine_version: str | None = None,
) -> dict[str, Any]:
    ready = _readiness(client)
    query = request.query_identity
    watermark = (
        f"generation:{int(ready['serving_generation'])}:"
        f"ttab_rank:{int(ready['source_max_rank'])}"
    )
    snapshot = build_snapshot_ref(
        snapshot_id=f"{READY_VERSION}:{watermark}",
        snapshot_kind="US_TTAB_CORRESPONDENT_HISTORY_ACCEPTED_WATERMARK",
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
            "US TTAB correspondent history hard result bound is exhausted"
        )
    capacity = min(request.page_size, remaining)
    try:
        raw = _dict_rows(
            client.query(
                _page_sql(
                    request,
                    position=position,
                    fetch_limit=capacity + 1,
                    serving_generation=int(ready["serving_generation"]),
                ),
                settings=READ_SETTINGS,
            )
        )
    except Exception as exc:
        raise TTABCorrespondentHistoryUnavailable(str(exc)) from exc
    page_rows = raw[:capacity]
    results = [
        _candidate(row, request.normalized_name)
        for row in page_rows
    ]
    emitted = emitted_before + len(results)
    next_cursor = None
    if (
        len(raw) > capacity
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
        "source_domain": "US_TTAB",
        "results": results,
        "result_count": len(results),
        "next_cursor": next_cursor,
        "provenance": provenance,
        "semantics": (
            "DIRECT_OFFICIAL_USPTO_TTAB_PARTY_CORRESPONDENT_HISTORY;"
            "EXCLUDES_TTAB_INTERLOCUTORY_STAFF_ATTORNEY;"
            "NO_CROSS_SOURCE_IDENTITY_OR_CONTINUING_REPRESENTATION_OR_LEGAL_CONCLUSION"
        ),
    }
