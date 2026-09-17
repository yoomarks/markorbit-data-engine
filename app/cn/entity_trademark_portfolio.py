from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import uuid

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

SCHEMA_VERSION = "CN_ENTITY_TRADEMARK_PORTFOLIO_SCHEMA_V1"
READY_VERSION = "CN_ENTITY_TRADEMARK_PORTFOLIO_READY_V1"
SOURCE_TABLE = "markorbit_facts.cn_entity_trademark_portfolio"
READINESS_TABLE = "markorbit_facts.cn_entity_trademark_portfolio_readiness"
STREAM_ID = "cn_entity_trademark_portfolio"
CANDIDATE_TYPE = "ENTITY_TRADEMARK_RELATIONSHIP"
SUPPORTED_ROLES = ("OWNER", "CO_OWNER", "AGENT")
SUPPORTED_SCOPES = ("current", "historical", "all")
MAX_PAGE_SIZE = 100
MAX_RESULTS = 10_000
READ_SETTINGS = {**DEFAULT_QUERY_BUDGET, "max_result_rows": MAX_PAGE_SIZE + 1}


class EntityPortfolioInvalid(ValueError):
    pass


class EntityPortfolioUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EntityPortfolioRequest:
    entity_id: str
    role: str | None = None
    scope: str = "all"
    page_size: int = 50
    cursor: str | None = None

    def __post_init__(self) -> None:
        try:
            uuid.UUID(str(self.entity_id))
        except (ValueError, AttributeError) as exc:
            raise EntityPortfolioInvalid("entity_id must be a UUID") from exc
        if self.role is not None and str(self.role).upper() not in SUPPORTED_ROLES:
            raise EntityPortfolioInvalid("role must be OWNER, CO_OWNER, or AGENT")
        if str(self.scope).lower() not in SUPPORTED_SCOPES:
            raise EntityPortfolioInvalid("scope must be current, historical, or all")
        if type(self.page_size) is not int or not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise EntityPortfolioInvalid(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if self.cursor is not None and (not isinstance(self.cursor, str) or not self.cursor):
            raise EntityPortfolioInvalid("cursor must be a non-empty string when provided")

    @property
    def normalized_entity_id(self) -> str:
        return str(uuid.UUID(str(self.entity_id)))

    @property
    def normalized_role(self) -> str | None:
        return str(self.role).upper() if self.role is not None else None

    @property
    def normalized_scope(self) -> str:
        return str(self.scope).lower()

    @property
    def limits(self) -> DiscoveryLimits:
        return DiscoveryLimits(page_size=self.page_size, max_pages=100, max_results=MAX_RESULTS)

    @property
    def query_identity(self) -> dict[str, Any]:
        return build_query_identity(
            stream_id=STREAM_ID,
            source_schema_id=SCHEMA_VERSION,
            candidate_type=CANDIDATE_TYPE,
            projection_fields=[
                "entity_id",
                "role",
                "application_number",
                "relationship_states",
                "mark_name_raw",
                "classes",
                "first_observed_at",
                "last_observed_at",
                "relation_count",
            ],
            scope={
                "jurisdiction": "CN",
                "entity_id": self.normalized_entity_id,
                "role": self.normalized_role,
                "relationship_scope": self.normalized_scope,
            },
            limits=self.limits,
        )


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _readiness(client: Any) -> dict[str, Any]:
    try:
        rows = _dict_rows(
            client.query(
                f"""
                SELECT ready_version, source_watermark, source_max_rank, implementation_sha, accepted_at
                FROM {READINESS_TABLE} FINAL
                ORDER BY accepted_at DESC
                LIMIT 1
                """,
                settings=DEFAULT_QUERY_BUDGET,
            )
        )
    except Exception as exc:
        raise EntityPortfolioUnavailable(str(exc)) from exc
    if not rows or str(rows[0]["ready_version"]) != READY_VERSION:
        raise EntityPortfolioUnavailable(
            "CN entity trademark portfolio is not backfilled and accepted"
        )
    row = rows[0]
    watermark = str(row.get("source_watermark") or "").strip()
    if not watermark:
        raise EntityPortfolioUnavailable("CN entity portfolio readiness watermark is missing")
    return row


def _cursor_position(row: Mapping[str, Any]) -> list[str]:
    return [str(row["role"]), str(row["application_number"])]


def _validate_position(position: Sequence[Any]) -> tuple[str, str]:
    if len(position) != 2:
        raise DiscoveryCursorError("CN entity portfolio cursor must contain role/application")
    role = str(position[0] or "").upper()
    application = str(position[1] or "").strip()
    if role not in SUPPORTED_ROLES or not application:
        raise DiscoveryCursorError("CN entity portfolio cursor position is malformed")
    return role, application


def _scope_clause(scope: str) -> str:
    if scope == "current":
        return "has_current = 1"
    if scope == "historical":
        return "has_former = 1"
    return "(has_current = 1 OR has_former = 1)"


def _page_sql(
    request: EntityPortfolioRequest,
    *,
    position: Sequence[Any] | None,
    fetch_limit: int,
) -> str:
    conditions = [
        f"entity_id = toUUID({_sql_text(request.normalized_entity_id)})",
        _scope_clause(request.normalized_scope),
    ]
    if request.normalized_role is not None:
        conditions.append(f"role = {_sql_text(request.normalized_role)}")
    if position is not None:
        role, application = _validate_position(position)
        conditions.append(
            f"tuple(role, application_number) > tuple({_sql_text(role)}, {_sql_text(application)})"
        )
    where = "\n          AND ".join(conditions)
    return f"""
        SELECT toString(entity_id) AS entity_id, role, application_number,
               has_current, has_former, relation_count,
               first_observed_at, last_observed_at,
               latest_source_rank, latest_source_package_id, latest_event_hash
        FROM {SOURCE_TABLE} FINAL
        WHERE {where}
        ORDER BY entity_id, role, application_number
        LIMIT {int(fetch_limit)}
    """


def _case_rows(client: Any, applications: list[str]) -> dict[str, dict[str, Any]]:
    if not applications:
        return {}
    literals = ", ".join(_sql_text(item) for item in applications)
    try:
        rows = _dict_rows(
            client.query(
                f"""
                SELECT application_number, mark_name_raw, classes
                FROM markorbit_facts.cn_case_current FINAL
                WHERE is_deleted = 0 AND application_number IN ({literals})
                ORDER BY application_number
                """,
                settings=DEFAULT_QUERY_BUDGET,
            )
        )
    except Exception as exc:
        raise EntityPortfolioUnavailable(str(exc)) from exc
    return {str(row["application_number"]): row for row in rows}


def _candidate(row: Mapping[str, Any], case: Mapping[str, Any] | None) -> dict[str, Any]:
    states: list[str] = []
    role = str(row["role"])
    normalized_role = "OWNER" if role == "CO_OWNER" else role
    if int(row.get("has_current") or 0):
        states.append(f"CURRENT_{normalized_role}")
    if int(row.get("has_former") or 0):
        states.append(f"FORMER_{normalized_role}")
    return {
        "candidate_type": CANDIDATE_TYPE,
        "entity_id": str(row["entity_id"]),
        "role": role,
        "application_number": str(row["application_number"]),
        "relationship_states": states,
        "mark_name_raw": str((case or {}).get("mark_name_raw") or "") or None,
        "classes": sorted({int(item) for item in ((case or {}).get("classes") or [])}),
        "relation_count": int(row.get("relation_count") or 0),
        "first_observed_at": str(row.get("first_observed_at") or ""),
        "last_observed_at": str(row.get("last_observed_at") or ""),
        "latest_source_rank": int(row.get("latest_source_rank") or 0),
        "latest_source_package_id": str(row.get("latest_source_package_id") or ""),
        "latest_event_hash": str(row.get("latest_event_hash") or ""),
        "legal_conclusion": False,
        "identity_resolution_claimed": False,
    }


def execute_page(
    request: EntityPortfolioRequest,
    *,
    client: Any,
    runtime_engine_version: str | None = None,
) -> dict[str, Any]:
    ready = _readiness(client)
    query = request.query_identity
    snapshot = build_snapshot_ref(
        snapshot_id=f"{READY_VERSION}:{ready['source_watermark']}",
        snapshot_kind="CN_ENTITY_PORTFOLIO_ACCEPTED_BACKFILL",
        watermark=str(ready["source_watermark"]),
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
        raise DiscoveryCursorError("CN entity portfolio hard result bound is exhausted")
    capacity = min(request.page_size, remaining)
    try:
        raw = _dict_rows(
            client.query(
                _page_sql(request, position=position, fetch_limit=capacity + 1),
                settings=READ_SETTINGS,
            )
        )
    except Exception as exc:
        raise EntityPortfolioUnavailable(str(exc)) from exc
    page_rows = raw[:capacity]
    cases = _case_rows(client, [str(row["application_number"]) for row in page_rows])
    results = [_candidate(row, cases.get(str(row["application_number"]))) for row in page_rows]
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
        "query": query,
        "snapshot": snapshot,
        "results": results,
        "next_cursor": next_cursor,
        "provenance": provenance,
        "bounded_truncation": bool(has_extra and next_cursor is None),
        "read_budget": dict(READ_SETTINGS),
    }
