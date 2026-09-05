from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.admin_progress import domain_progress_snapshot
from app.cn.discovery_preliminary_publication import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PreliminaryPublicationDiscoveryRequest,
)
from app.cn.discovery_preliminary_publication_owner import execute_live_page
from app.component_versions import component_versions
from app.discovery_contract import DiscoveryContractError, DiscoveryCursorError
from app.integration_contract import CONTRACT_VERSION, SERVICE_ROLE, SOURCE_OWNER
from app.integration_g0_contract import g0_contract_descriptor
from app.integration_runtime import enforce_integration_rate_limit
from app.integration_security import integration_security_contract, require_integration_auth
from app.main_core import cn_case, health, us_case
from app.operations_v2 import operations_snapshot
from app.platform_contract import platform_contract
from app.us.case360_api import us_case_360
from app.us.change_history_api import us_case_history, us_change_feed
from app.us_assignment.api import us_assignments_for_serial
from app.us_ttab.api import us_ttab_by_serial
from app.version import engine_version


router = APIRouter(
    prefix="/api/v1",
    tags=["MarkOrbit integration V1"],
    dependencies=[Depends(require_integration_auth), Depends(enforce_integration_rate_limit)],
)

_CONTROL_PLANE_OPERATION_COUNTERS = (
    "active_human_actions",
    "failed_human_actions",
    "active_admin_tasks",
    "admin_domains_with_errors",
    "failed_operational_jobs",
    "domain_runs_with_readiness_failures",
    "replay_lanes_with_readiness_failures",
)
_CONTROL_PLANE_FAILURE_COUNTERS = (
    "failed_human_actions",
    "admin_domains_with_errors",
    "failed_operational_jobs",
    "domain_runs_with_readiness_failures",
    "replay_lanes_with_readiness_failures",
)
_CONTROL_PLANE_UNAVAILABLE_DETAIL = "Data Engine owner summary is unavailable"


def _envelope(
    *, jurisdiction: str, resource_kind: str, payload: Any
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "jurisdiction": jurisdiction,
        "resource_kind": resource_kind,
        "authority": "DATA_ENGINE_FACT_READ_MODEL",
        "legal_conclusion": False,
        "fact_state": "observed",
        "payload": payload,
    }


def _discovery_http_error(exc: DiscoveryContractError) -> HTTPException:
    message = str(exc)
    conflict = isinstance(exc, DiscoveryCursorError) and (
        "cursor/query mismatch" in message
        or "cursor/snapshot mismatch" in message
        or "unsupported Discovery cursor version" in message
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST,
        detail={
            "code": (
                "DATA_ENGINE_DISCOVERY_CURSOR_CONFLICT"
                if conflict
                else "DATA_ENGINE_DISCOVERY_QUERY_INVALID"
            ),
            "message": message,
            "retryable": False,
        },
    )


def _control_plane_counter(summary: dict[str, Any], name: str) -> int:
    value = summary.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid owner counter: {name}")
    return value


def _control_plane_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"invalid owner field: {name}")
    return value


def _control_plane_generated_at(payload: dict[str, Any]) -> str:
    value = payload.get("generated_at")
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("invalid owner field: generated_at")
    return value.isoformat()


@router.get("/health")
def integration_health() -> dict[str, Any]:
    dependency_health = health()
    dependencies_ok = all(
        dependency_health.get(name) == "ok" for name in ("api", "postgres", "clickhouse")
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "service_role": SERVICE_ROLE,
        "status": "ok" if dependencies_ok else "degraded",
        "dependencies": dependency_health,
    }


@router.get("/data-engine/control-plane")
def integration_data_engine_control_plane() -> dict[str, Any]:
    """Expose the bounded owner-local Data Engine control-plane read model."""
    try:
        dependency_health = health()
        operations = operations_snapshot()
        admin_progress = domain_progress_snapshot()
        if not isinstance(dependency_health, dict):
            raise ValueError("owner health payload is malformed")
        if not isinstance(operations, dict):
            raise ValueError("operations payload is malformed")
        if not isinstance(admin_progress, dict):
            raise ValueError("admin progress payload is malformed")

        raw_summary = operations.get("summary")
        if not isinstance(raw_summary, dict):
            raise ValueError("operations summary is malformed")
        summary = {
            name: _control_plane_counter(raw_summary, name)
            for name in _CONTROL_PLANE_OPERATION_COUNTERS
        }
        operations_version = _control_plane_text(operations, "version")
        action_authority = _control_plane_text(operations, "action_authority")
        admin_version = _control_plane_text(admin_progress, "version")
        admin_active_count = _control_plane_counter(admin_progress, "active_count")
        generated_at = _control_plane_generated_at(admin_progress)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_CONTROL_PLANE_UNAVAILABLE_DETAIL,
        ) from None

    dependencies_ok = all(
        dependency_health.get(name) == "ok" for name in ("api", "postgres", "clickhouse")
    )
    failures_clear = all(summary[name] == 0 for name in _CONTROL_PLANE_FAILURE_COUNTERS)
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "authority": "DATA_ENGINE_FACT_READ_MODEL",
        "read_only": True,
        "generated_at": generated_at,
        "health": "ok" if dependencies_ok and failures_clear else "degraded",
        "operations": {
            "version": operations_version,
            "action_authority": action_authority,
            "summary": summary,
        },
        "admin_progress": {
            "version": admin_version,
            "active_count": admin_active_count,
        },
    }


@router.get("/contract")
def integration_contract() -> dict[str, Any]:
    descriptor = g0_contract_descriptor()
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "component_versions": component_versions(),
        "platformization": platform_contract(),
        "source_owner": SOURCE_OWNER,
        "service_role": SERVICE_ROLE,
        "consumer_policy": {
            "query_plane_read_only": True,
            "change_feed_read_only": True,
            "cross_service_database_access": False,
            "consumer_writeback_to_source_facts": False,
            "business_state_owned_outside_data_engine": True,
        },
        "security": integration_security_contract(),
        "transport": {
            "request_id_header": "X-Request-ID",
            "correlation_id_header": "x-correlation-id",
            "request_id_echoed": True,
            "correlation_id_echoed": True,
            "contract_version_header": "X-MarkOrbit-Contract-Version",
            "source_owner_header": "X-MarkOrbit-Source-Owner",
        },
        "planes": {
            "query": {"prefix": "/api/v1", "methods": ["GET"]},
            "change_feed": {
                "path": "/api/v1/us/changes",
                "methods": ["GET"],
                "cursor_semantics": "LOSSLESS_OBSERVATION_CURSOR_NOT_LEGAL_CONCLUSION",
            },
            "admin": {
                "prefixes": ["/api/admin", "/api/jobs"],
                "part_of_consumer_contract": False,
            },
        },
        "stable_resources": [
            resource["path"] for resource in descriptor["query_contract"]["resources"]
        ],
        "g0_contract": descriptor,
    }


@router.get("/cn/cases/{application_number}")
def integration_cn_case(application_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="CN", resource_kind="TRADEMARK_CASE", payload=cn_case(application_number)
    )


@router.get("/cn/discovery/preliminary-publications")
def integration_cn_preliminary_publication_discovery(
    application_number_start: Annotated[str, Query(min_length=1, max_length=128)],
    application_number_end: Annotated[str, Query(min_length=1, max_length=128)],
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    try:
        request = PreliminaryPublicationDiscoveryRequest(
            application_number_start=application_number_start,
            application_number_end=application_number_end,
            page_size=page_size,
            cursor=cursor,
        )
        page = execute_live_page(request)
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    return _envelope(
        jurisdiction="CN",
        resource_kind="PRELIMINARY_PUBLICATION_FACT_DISCOVERY",
        payload=page,
    )


@router.get("/us/cases/{serial_number}")
def integration_us_case(serial_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US", resource_kind="TRADEMARK_CASE", payload=us_case(serial_number)
    )


@router.get("/us/cases/{serial_number}/360")
def integration_us_case_360(
    serial_number: str,
    as_of: date | None = None,
    history_limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    assignment_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ttab_limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_CASE_360",
        payload=us_case_360(
            serial_number,
            as_of=as_of,
            history_limit=history_limit,
            assignment_limit=assignment_limit,
            ttab_limit=ttab_limit,
        ),
    )


@router.get("/us/cases/{serial_number}/history")
def integration_us_case_history(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_CASE_HISTORY",
        payload=us_case_history(serial_number, limit=limit),
    )


@router.get("/us/changes")
def integration_us_changes(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_serial: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_CHANGE_FEED",
        payload=us_change_feed(
            after_source_rank=after_source_rank,
            after_serial=after_serial,
            scan_limit=scan_limit,
        ),
    )


@router.get("/us/cases/{serial_number}/assignments")
def integration_us_assignments(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="RECORDED_ASSIGNMENT_FACTS",
        payload=us_assignments_for_serial(serial_number, limit=limit),
    )


@router.get("/us/cases/{serial_number}/ttab")
def integration_us_ttab(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="TTAB_PROCEEDING_FACTS",
        payload=us_ttab_by_serial(serial_number, limit=limit),
    )
