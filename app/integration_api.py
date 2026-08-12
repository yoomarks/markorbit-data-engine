from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.component_versions import component_versions
from app.integration_contract import CONTRACT_VERSION, SERVICE_ROLE, SOURCE_OWNER
from app.integration_security import integration_security_contract, require_integration_auth
from app.main_core import cn_case, health, us_case
from app.us.case360_api import us_case_360
from app.us.change_history_api import us_case_history, us_change_feed
from app.us_assignment.api import us_assignments_for_serial
from app.us_ttab.api import us_ttab_by_serial
from app.version import engine_version


router = APIRouter(
    prefix="/api/v1",
    tags=["MarkOrbit integration V1"],
    dependencies=[Depends(require_integration_auth)],
)


def _envelope(
    *,
    jurisdiction: str,
    resource_kind: str,
    payload: Any,
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "jurisdiction": jurisdiction,
        "resource_kind": resource_kind,
        "authority": "DATA_ENGINE_FACT_READ_MODEL",
        "legal_conclusion": False,
        "payload": payload,
    }


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


@router.get("/contract")
def integration_contract() -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "component_versions": component_versions(),
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
            "request_id_echoed": True,
            "contract_version_header": "X-MarkOrbit-Contract-Version",
            "source_owner_header": "X-MarkOrbit-Source-Owner",
        },
        "planes": {
            "query": {
                "prefix": "/api/v1",
                "methods": ["GET"],
            },
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
            "/api/v1/health",
            "/api/v1/cn/cases/{application_number}",
            "/api/v1/us/cases/{serial_number}",
            "/api/v1/us/cases/{serial_number}/360",
            "/api/v1/us/cases/{serial_number}/history",
            "/api/v1/us/cases/{serial_number}/assignments",
            "/api/v1/us/cases/{serial_number}/ttab",
            "/api/v1/us/changes",
        ],
    }


@router.get("/cn/cases/{application_number}")
def integration_cn_case(application_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="CN",
        resource_kind="TRADEMARK_CASE",
        payload=cn_case(application_number),
    )


@router.get("/us/cases/{serial_number}")
def integration_us_case(serial_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_CASE",
        payload=us_case(serial_number),
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
