from dataclasses import dataclass
from datetime import date, datetime
import json
import re
from typing import Any, Callable, Mapping, Sequence

from app.db import postgres_conn
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
from app.us.applicant_candidate_backfill_control import (
    USApplicantServingEpoch,
    current_us_applicant_serving_epoch,
)
from app.version import engine_version

STREAM_ID = "US_NATURAL_LAPSE_DISCOVERY_V1"
SOURCE_SCHEMA_ID = "US_NATURAL_LAPSE_PROJECTION_V1"
RECORD_TYPE = "US_NATURAL_LAPSE_SOURCE_FACT"
PROJECTION_TABLE = "markorbit_facts.us_natural_lapse_discovery_current"
STATE_COMPONENT = "US_NATURAL_LAPSE_DISCOVERY_V1"
PROJECTION_JOB_TYPE = "US_NATURAL_LAPSE_PROJECTION_BACKFILL_V1"
SNAPSHOT_KIND = "US_NATURAL_LAPSE_PROJECTION_SNAPSHOT"
ADMITTED_REASON = "REGISTRATION_MAINTENANCE_LAPSE"
ADMITTED_EVENT_CODE = "CAEX"
ADMITTED_EVENT_TYPE_CODE = "O"
ADMITTED_DESCRIPTION = "CANCELLED SEC. 8 (10-YR)/EXPIRED SECTION 9"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RESULTS = 1_000
MAX_DATE_SPAN_DAYS = 366
MAX_ROWS_TO_READ = 250_000
MAX_BYTES_TO_READ = 256 * 1024 * 1024
READ_SETTINGS = {
    "max_rows_to_read": MAX_ROWS_TO_READ,
    "max_bytes_to_read": MAX_BYTES_TO_READ,
    "read_overflow_mode": "throw",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROJECTION_FIELDS = (
    "lapse_event_key",
    "serial_number",
    "registration_number",
    "mark_identification",
    "mark_drawing_code",
    "nice_class",
    "owner_names",
    "observed_status_code",
    "observed_status_date",
    "cancellation_date",
    "renewal_date",
    "lapse_event_date",
    "event_code",
    "event_type_code",
    "description_text",
    "event_source_package_kind",
    "event_source_effective_date",
    "event_source_file",
    "event_source_row_hash",
    "event_source_package_id",
    "event_source_rank",
    "event_observed_at",
    "case_source_package_kind",
    "case_source_effective_date",
    "case_source_file",
    "case_source_row_hash",
    "case_source_package_id",
    "case_record_hash",
    "case_source_rank",
    "class_lineage_json",
    "owner_lineage_json",
    "source_manifest_fingerprint",
    "projection_snapshot_id",
)


class NaturalLapseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NaturalLapseDiscoveryRequest:
    lapse_reason: str
    event_date_start: date
    event_date_end: date
    nice_class: int | None = None
    serial_number_start: str | None = None
    serial_number_end: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    cursor: str | None = None

    def __post_init__(self) -> None:
        reason = str(self.lapse_reason or "").strip()
        if reason != ADMITTED_REASON:
            raise DiscoveryContractError("unsupported natural-lapse reason")
        if not isinstance(self.event_date_start, date) or not isinstance(self.event_date_end, date):
            raise DiscoveryContractError("event-date bounds must be dates")
        if self.event_date_start >= self.event_date_end:
            raise DiscoveryContractError("event_date_start must be before event_date_end")
        if (self.event_date_end - self.event_date_start).days > MAX_DATE_SPAN_DAYS:
            raise DiscoveryContractError(f"event-date window exceeds {MAX_DATE_SPAN_DAYS} days")
        if self.nice_class is not None and (
            type(self.nice_class) is not int or not 1 <= self.nice_class <= 45
        ):
            raise DiscoveryContractError("nice_class must be between 1 and 45")
        serial_start = str(self.serial_number_start or "").strip() or None
        serial_end = str(self.serial_number_end or "").strip() or None
        if (serial_start is None) != (serial_end is None):
            raise DiscoveryContractError("serial-number bounds must be provided together")
        if serial_start is not None:
            if len(serial_start) > 128 or len(serial_end or "") > 128:
                raise DiscoveryContractError("serial-number bounds exceed 128 characters")
            if serial_start >= str(serial_end):
                raise DiscoveryContractError(
                    "serial_number_start must be less than serial_number_end"
                )
        if type(self.page_size) is not int or not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise DiscoveryContractError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if self.cursor is not None and (not isinstance(self.cursor, str) or not self.cursor):
            raise DiscoveryCursorError("cursor must be a non-empty string when provided")
        object.__setattr__(self, "lapse_reason", reason)
        object.__setattr__(self, "serial_number_start", serial_start)
        object.__setattr__(self, "serial_number_end", serial_end)

    @property
    def limits(self) -> DiscoveryLimits:
        return DiscoveryLimits(
            page_size=self.page_size, max_pages=MAX_PAGES, max_results=MAX_RESULTS
        )

    @property
    def scope(self) -> dict[str, Any]:
        return {
            "jurisdiction": "US",
            "lapse_reason": self.lapse_reason,
            "nice_class": self.nice_class,
            "event_date": {
                "start_inclusive": self.event_date_start.isoformat(),
                "end_exclusive": self.event_date_end.isoformat(),
            },
            "serial_number": (
                None
                if self.serial_number_start is None
                else {
                    "start_inclusive": self.serial_number_start,
                    "end_exclusive": self.serial_number_end,
                }
            ),
            "source_event": {
                "event_code": ADMITTED_EVENT_CODE,
                "event_type_code": ADMITTED_EVENT_TYPE_CODE,
                "description_text": ADMITTED_DESCRIPTION,
            },
            "ordering": [
                "lapse_event_date ASC",
                "nice_class ASC",
                "lapse_event_key ASC",
                "serial_number ASC",
            ],
            "ranking": "NONE",
            "read_budget": dict(READ_SETTINGS),
        }

    @property
    def query_identity(self) -> dict[str, Any]:
        return build_query_identity(
            stream_id=STREAM_ID,
            source_schema_id=SOURCE_SCHEMA_ID,
            candidate_type=RECORD_TYPE,
            projection_fields=PROJECTION_FIELDS,
            scope=self.scope,
            limits=self.limits,
        )


def _sql_text(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _dict_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(zip(result.column_names, row, strict=True)) for row in result.result_rows]


def _date_text(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise DiscoveryContractError(f"{field} must be an ISO date") from exc


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DiscoveryContractError(f"{field} is required in natural-lapse projection rows")
    return text


def _json_array(value: Any, field: str) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise DiscoveryContractError(f"{field} must be valid JSON") from exc
    if not isinstance(decoded, list):
        raise DiscoveryContractError(f"{field} must decode to an array")
    return decoded


def accepted_projection_state(
    *, connection_factory: Callable[..., Any] = postgres_conn
) -> dict[str, Any] | None:
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, payload, metrics, finished_at
                FROM control.job_run
                WHERE job_type = %s
                ORDER BY started_at DESC, run_id DESC
                """,
                (PROJECTION_JOB_TYPE,),
            )
            rows = [dict(row) for row in cur.fetchall()]
    if any(str(row.get("status") or "") == "RUNNING" for row in rows):
        raise NaturalLapseUnavailable("natural-lapse projection publication is in flight")
    for row in rows:
        if str(row.get("status") or "") != "SUCCESS":
            continue
        payload = dict(row.get("payload") or {})
        metrics = dict(row.get("metrics") or {})
        snapshot_id = str(payload.get("projection_snapshot_id") or "").lower()
        manifest = str(payload.get("source_manifest_fingerprint") or "").lower()
        if not _HEX64.fullmatch(snapshot_id) or not _HEX64.fullmatch(manifest):
            continue
        return {
            "run_id": str(row.get("run_id") or ""),
            "implementation_sha": str(payload.get("implementation_sha") or ""),
            "plan_sha256": str(payload.get("plan_sha256") or ""),
            "projection_snapshot_id": snapshot_id,
            "source_manifest_fingerprint": manifest,
            "source_epoch": dict(payload.get("source_epoch") or {}),
            "source_identity": dict(metrics.get("source_identity") or {}),
            "projection_identity": dict(metrics.get("projection_identity") or {}),
            "completed_at": str(row.get("finished_at") or payload.get("completed_at") or ""),
        }
    return None


def _validate_cursor_position(position: Sequence[Any]) -> tuple[str, int, str, str]:
    if len(position) != 4:
        raise DiscoveryCursorError("US natural-lapse cursor must have 4 keyset values")
    event_date = _required_text(position[0], "cursor.lapse_event_date")
    _date_text(event_date, "cursor.lapse_event_date")
    try:
        nice_class = int(position[1])
    except (TypeError, ValueError) as exc:
        raise DiscoveryCursorError("cursor.nice_class must be an integer") from exc
    if not 1 <= nice_class <= 45:
        raise DiscoveryCursorError("cursor.nice_class must be between 1 and 45")
    event_key = _required_text(position[2], "cursor.lapse_event_key")
    serial = _required_text(position[3], "cursor.serial_number")
    return event_date, nice_class, event_key, serial


def build_page_sql(
    request: NaturalLapseDiscoveryRequest,
    *,
    snapshot_id: str,
    position: Sequence[Any] | None = None,
    fetch_limit: int | None = None,
) -> str:
    if not _HEX64.fullmatch(str(snapshot_id or "")):
        raise DiscoveryContractError("projection snapshot id must be a 64-character SHA-256")
    limit = request.page_size + 1 if fetch_limit is None else int(fetch_limit)
    if limit <= 0 or limit > request.page_size + 1:
        raise DiscoveryContractError("fetch_limit exceeds page_size + 1 continuation bound")
    filters = [
        f"projection_snapshot_id = {_sql_text(snapshot_id)}",
        "jurisdiction = 'US'",
        f"lapse_reason = {_sql_text(request.lapse_reason)}",
        f"lapse_event_date >= toDate32({_sql_text(request.event_date_start.isoformat())})",
        f"lapse_event_date < toDate32({_sql_text(request.event_date_end.isoformat())})",
        "legal_conclusion = 0",
    ]
    if request.nice_class is not None:
        filters.append(f"nice_class = {request.nice_class}")
    if request.serial_number_start is not None:
        filters.append(f"serial_number >= {_sql_text(request.serial_number_start)}")
        filters.append(f"serial_number < {_sql_text(str(request.serial_number_end))}")
    if position is not None:
        cursor_date, cursor_class, event_key, serial = _validate_cursor_position(position)
        filters.append(
            "(lapse_event_date, nice_class, lapse_event_key, serial_number) > "
            f"(toDate32({_sql_text(cursor_date)}), {cursor_class}, "
            f"{_sql_text(event_key)}, {_sql_text(serial)})"
        )
    where = "\n          AND ".join(filters)
    return f"""
        SELECT lapse_event_key, serial_number, registration_number,
               mark_identification, mark_drawing_code, nice_class, owner_names,
               observed_status_code, observed_status_date, cancellation_date,
               renewal_date, lapse_event_date, event_code, event_type_code,
               description_text, event_source_package_kind,
               event_source_effective_date, event_source_file,
               event_source_row_hash, toString(event_source_package_id) AS event_source_package_id,
               event_source_rank, event_observed_at, case_source_package_kind,
               case_source_effective_date, case_source_file, case_source_row_hash,
               toString(case_source_package_id) AS case_source_package_id,
               case_record_hash, case_source_rank, class_lineage_json,
               owner_lineage_json, source_manifest_fingerprint,
               projection_snapshot_id, legal_conclusion
        FROM {PROJECTION_TABLE} FINAL
        WHERE {where}
        ORDER BY lapse_event_date ASC, nice_class ASC,
                 lapse_event_key ASC, serial_number ASC
        LIMIT {limit}
    """


def normalize_record(row: Mapping[str, Any], *, state: Mapping[str, Any]) -> dict[str, Any]:
    event_key = _required_text(row.get("lapse_event_key"), "lapse_event_key")
    serial_number = _required_text(row.get("serial_number"), "serial_number")
    event_date = _required_text(
        _date_text(row.get("lapse_event_date"), "lapse_event_date"), "lapse_event_date"
    )
    event_code = _required_text(row.get("event_code"), "event_code")
    event_type_code = _required_text(row.get("event_type_code"), "event_type_code")
    description = _required_text(row.get("description_text"), "description_text")
    if (event_code, event_type_code, description) != (
        ADMITTED_EVENT_CODE,
        ADMITTED_EVENT_TYPE_CODE,
        ADMITTED_DESCRIPTION,
    ):
        raise DiscoveryContractError("projection row is outside the admitted source-event mapping")
    if int(row.get("legal_conclusion") or 0) != 0:
        raise DiscoveryContractError("natural-lapse projection row asserted a legal conclusion")
    snapshot_id = _required_text(row.get("projection_snapshot_id"), "projection_snapshot_id")
    manifest = _required_text(row.get("source_manifest_fingerprint"), "source_manifest_fingerprint")
    if (
        snapshot_id != state["projection_snapshot_id"]
        or manifest != state["source_manifest_fingerprint"]
    ):
        raise DiscoveryContractError(
            "projection row provenance does not match accepted snapshot state"
        )
    nice_class = int(row.get("nice_class") or 0)
    if not 1 <= nice_class <= 45:
        raise DiscoveryContractError("projection row contains an invalid Nice class")
    event_source_rank = int(row.get("event_source_rank") or 0)
    case_source_rank = int(row.get("case_source_rank") or 0)
    return {
        "record_type": RECORD_TYPE,
        "record_id": f"us:natural-lapse:{event_key}:{nice_class}",
        "jurisdiction": "US",
        "serial_number": serial_number,
        "registration_number": str(row.get("registration_number") or ""),
        "mark_identification": str(row.get("mark_identification") or ""),
        "mark_drawing_code": str(row.get("mark_drawing_code") or ""),
        "nice_class": nice_class,
        "owner_names": sorted(
            {str(item) for item in (row.get("owner_names") or []) if str(item).strip()}
        ),
        "observed_current_status": {
            "status_code": str(row.get("observed_status_code") or ""),
            "status_date": _date_text(row.get("observed_status_date"), "observed_status_date"),
        },
        "lapse_reason": ADMITTED_REASON,
        "lifecycle_event": {
            "event_key": event_key,
            "event_date": event_date,
            "event_code": event_code,
            "event_type_code": event_type_code,
            "description_text": description,
        },
        "dates": {
            "cancellation_date": _date_text(row.get("cancellation_date"), "cancellation_date"),
            "renewal_date": _date_text(row.get("renewal_date"), "renewal_date"),
        },
        "source": {
            "source_id": f"us:event:{event_key}",
            "source_version": (
                f"us-package:{_required_text(row.get('event_source_package_id'), 'event_source_package_id')}"
                f":rank:{event_source_rank}"
            ),
            "source_package_kind": str(row.get("event_source_package_kind") or ""),
            "source_effective_date": _date_text(
                row.get("event_source_effective_date"), "event_source_effective_date"
            ),
            "source_file": str(row.get("event_source_file") or ""),
            "source_fingerprint_sha256": "sha256:"
            + _required_text(row.get("event_source_row_hash"), "event_source_row_hash"),
            "source_package_id": _required_text(
                row.get("event_source_package_id"), "event_source_package_id"
            ),
            "source_rank": event_source_rank,
            "observed_at": str(row.get("event_observed_at") or ""),
            "replay_identity": f"{event_key}:{nice_class}",
        },
        "case_lineage": {
            "source_package_kind": str(row.get("case_source_package_kind") or ""),
            "source_effective_date": _date_text(
                row.get("case_source_effective_date"), "case_source_effective_date"
            ),
            "source_file": str(row.get("case_source_file") or ""),
            "source_package_id": _required_text(
                row.get("case_source_package_id"), "case_source_package_id"
            ),
            "source_row_hash": _required_text(
                row.get("case_source_row_hash"), "case_source_row_hash"
            ),
            "record_hash": _required_text(row.get("case_record_hash"), "case_record_hash"),
            "source_rank": case_source_rank,
        },
        "class_lineage": _json_array(row.get("class_lineage_json"), "class_lineage_json"),
        "owner_lineage": _json_array(row.get("owner_lineage_json"), "owner_lineage_json"),
        "serving": {
            "projection_snapshot_id": snapshot_id,
            "source_manifest_fingerprint": manifest,
            "accepted_us_serving_epoch": dict(state.get("source_epoch") or {}),
            "projection_completed_at": str(state.get("completed_at") or ""),
        },
        "legal_conclusion": False,
    }


def _cursor_position(record: Mapping[str, Any]) -> list[Any]:
    lifecycle = dict(record.get("lifecycle_event") or {})
    return [
        _required_text(lifecycle.get("event_date"), "event_date"),
        int(record.get("nice_class") or 0),
        _required_text(lifecycle.get("event_key"), "event_key"),
        _required_text(record.get("serial_number"), "serial_number"),
    ]


def _result_state(
    request: NaturalLapseDiscoveryRequest, state: Mapping[str, Any], result_count: int
) -> str:
    if result_count:
        return "RESULTS"
    source_identity = dict(state.get("source_identity") or {})
    if int(source_identity.get("rows") or 0) == 0:
        return "NOT_OBSERVED"
    min_date = _date_text(source_identity.get("min_event_date"), "min_event_date")
    max_date = _date_text(source_identity.get("max_event_date"), "max_event_date")
    if min_date and max_date:
        if request.event_date_end <= date.fromisoformat(min_date):
            return "NOT_COVERED"
        if request.event_date_start > date.fromisoformat(max_date):
            return "NOT_COVERED"
    return "EMPTY"


def execute_page(
    request: NaturalLapseDiscoveryRequest,
    *,
    client: Any,
    serving_epoch_getter: Callable[
        [], USApplicantServingEpoch
    ] = current_us_applicant_serving_epoch,
    state_getter: Callable[[], dict[str, Any] | None] = accepted_projection_state,
    engine_version_value: str | None = None,
) -> dict[str, Any]:
    version = engine_version_value or engine_version()
    try:
        before = serving_epoch_getter()
        state = state_getter()
    except (RuntimeError, NaturalLapseUnavailable) as exc:
        raise NaturalLapseUnavailable(str(exc)) from exc
    if state is None:
        raise NaturalLapseUnavailable("natural-lapse projection has no accepted state")
    if dict(state.get("source_epoch") or {}) != before.to_dict():
        raise NaturalLapseUnavailable(
            "natural-lapse projection is stale for accepted US serving epoch"
        )

    query_identity = request.query_identity
    snapshot = build_snapshot_ref(
        snapshot_id=state["projection_snapshot_id"],
        snapshot_kind=SNAPSHOT_KIND,
        watermark=before.token,
        source_version=version,
    )
    page_number = 1
    emitted_before = 0
    position: Sequence[Any] | None = None
    if request.cursor is not None:
        decoded = decode_cursor(
            request.cursor,
            expected_query_hash=query_identity["query_hash"],
            expected_snapshot_id=snapshot["snapshot_id"],
            limits=request.limits,
        )
        page_number = int(decoded["next_page"])
        emitted_before = int(decoded["emitted_count"])
        position = decoded["position"]
        _validate_cursor_position(position)

    remaining = request.limits.max_results - emitted_before
    if remaining <= 0:
        raise DiscoveryCursorError("Discovery result hard bound is already exhausted")
    page_capacity = min(request.limits.page_size, remaining)
    result = client.query(
        build_page_sql(
            request,
            snapshot_id=state["projection_snapshot_id"],
            position=position,
            fetch_limit=min(page_capacity + 1, request.page_size + 1),
        ),
        settings=READ_SETTINGS,
    )
    raw_rows = _dict_rows(result)
    try:
        after = serving_epoch_getter()
        after_state = state_getter()
    except (RuntimeError, NaturalLapseUnavailable) as exc:
        raise NaturalLapseUnavailable(str(exc)) from exc
    if after != before:
        raise NaturalLapseUnavailable("US serving epoch changed during natural-lapse discovery")
    if (
        after_state is None
        or after_state["projection_snapshot_id"] != state["projection_snapshot_id"]
    ):
        raise NaturalLapseUnavailable(
            "natural-lapse projection snapshot changed during page execution"
        )

    has_extra = len(raw_rows) > page_capacity
    records = [normalize_record(row, state=state) for row in raw_rows[:page_capacity]]
    emitted_count = emitted_before + len(records)
    next_cursor = None
    if (
        has_extra
        and records
        and page_number < request.limits.max_pages
        and emitted_count < request.limits.max_results
    ):
        next_cursor = encode_cursor(
            query_hash=query_identity["query_hash"],
            snapshot_id=snapshot["snapshot_id"],
            position=_cursor_position(records[-1]),
            next_page=page_number + 1,
            emitted_count=emitted_count,
            limits=request.limits,
        )

    provenance = build_page_provenance(
        query_identity=query_identity,
        snapshot=snapshot,
        engine_version=version,
        page_number=page_number,
        result_count=len(records),
        emitted_count=emitted_count,
        next_cursor=next_cursor,
    )
    return {
        "stream_id": STREAM_ID,
        "record_type": RECORD_TYPE,
        "result_state": _result_state(request, state, len(records)),
        "query": query_identity,
        "snapshot": snapshot,
        "projection": {
            "component": STATE_COMPONENT,
            "implementation_sha": str(state.get("implementation_sha") or ""),
            "plan_sha256": str(state.get("plan_sha256") or ""),
            "source_manifest_fingerprint": state["source_manifest_fingerprint"],
            "completed_at": str(state.get("completed_at") or ""),
            "source_identity": dict(state.get("source_identity") or {}),
            "projection_identity": dict(state.get("projection_identity") or {}),
        },
        "results": records,
        "next_cursor": next_cursor,
        "provenance": provenance,
        "bounded_truncation": bool(
            has_extra
            and not (
                records
                and page_number < request.limits.max_pages
                and emitted_count < request.limits.max_results
            )
        ),
        "read_budget": dict(READ_SETTINGS),
        "legal_conclusion": False,
    }
