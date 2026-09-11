from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.cn.applicant_owner_read import (
    read_applicant_exact as cn_read_applicant_exact,
    read_portfolio as cn_read_applicant_portfolio,
    read_trademark_exact as cn_read_trademark_exact,
)
from app.cn.discovery_preliminary_publication import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PreliminaryPublicationDiscoveryRequest,
    execute_page,
)
from app.component_versions import component_versions
from app.db import clickhouse_client
from app.discovery_contract import DiscoveryContractError, DiscoveryCursorError
from app.integration_contract import CONTRACT_VERSION, SERVICE_ROLE, SOURCE_OWNER
from app.integration_owner_read import (
    OWNER_READ_EXCEPTIONS,
    applicant_source_from_query,
    owner_envelope,
    owner_read_http_error,
    reject_unknown_query,
    request_id_from_request,
    trademark_source_from_query,
)
from app.integration_g0_contract import g0_contract_descriptor
from app.integration_owner_summary import owner_summary
from app.integration_runtime import enforce_integration_rate_limit
from app.integration_security import integration_security_contract, require_integration_auth
from app.main_core import cn_case, health, us_case
from app.platform_contract import platform_contract
from app.us.applicant_owner_read import (
    read_portfolio as us_read_applicant_portfolio,
    revalidate_applicant as us_read_applicant_exact,
    revalidate_trademark as us_read_trademark_exact,
)
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


@router.get("/owner-summary")
def integration_owner_summary() -> dict[str, Any]:
    return owner_summary()


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
        page = execute_page(request, client=clickhouse_client())
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


_OWNER_APPLICANT_QUERY_FIELDS = {
    "requester_workspace_id",
    "applicant_source_id",
    "applicant_source_version",
    "applicant_source_fingerprint_sha256",
    "applicant_observed_at",
}
_OWNER_PORTFOLIO_QUERY_FIELDS = _OWNER_APPLICANT_QUERY_FIELDS | {"page_size", "cursor"}
_OWNER_TRADEMARK_QUERY_FIELDS = _OWNER_APPLICANT_QUERY_FIELDS | {
    "trademark_source_id",
    "trademark_source_version",
    "trademark_source_fingerprint_sha256",
    "trademark_observed_at",
}


def _owner_jurisdiction(value: str) -> str:
    jurisdiction = str(value or "").strip().upper()
    if jurisdiction not in {"CN", "US"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DATA_ENGINE_OWNER_READ_UNSUPPORTED_JURISDICTION",
                "message": "Owner-read jurisdiction must be CN or US.",
                "retryable": False,
            },
        )
    return jurisdiction


def _applicant_source_query(
    *, jurisdiction: str, source_id: str, source_version: str,
    source_fingerprint_sha256: str, observed_at: str,
) -> dict[str, str]:
    try:
        return applicant_source_from_query(
            jurisdiction=jurisdiction,
            source_id=source_id,
            source_version=source_version,
            source_fingerprint_sha256=source_fingerprint_sha256,
            observed_at=observed_at,
        )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc


def _trademark_source_query(
    *, jurisdiction: str, source_id: str, source_version: str,
    source_fingerprint_sha256: str, observed_at: str,
) -> dict[str, str]:
    try:
        return trademark_source_from_query(
            jurisdiction=jurisdiction,
            source_id=source_id,
            source_version=source_version,
            source_fingerprint_sha256=source_fingerprint_sha256,
            observed_at=observed_at,
        )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc


@router.get("/{jurisdiction}/applicants/{applicant_candidate_id}")
def integration_applicant_owner_exact(
    request: Request,
    jurisdiction: str,
    applicant_candidate_id: str,
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_id: Annotated[str, Query(min_length=1, max_length=1000)],
    applicant_source_version: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_fingerprint_sha256: Annotated[str, Query(min_length=1, max_length=128)],
    applicant_observed_at: Annotated[str, Query(min_length=1, max_length=128)],
) -> dict[str, Any]:
    reject_unknown_query(request, _OWNER_APPLICANT_QUERY_FIELDS)
    code = _owner_jurisdiction(jurisdiction)
    source = _applicant_source_query(
        jurisdiction=code,
        source_id=applicant_source_id,
        source_version=applicant_source_version,
        source_fingerprint_sha256=applicant_source_fingerprint_sha256,
        observed_at=applicant_observed_at,
    )
    try:
        if code == "CN":
            result = cn_read_applicant_exact(
                client=clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                applicant_candidate_id=applicant_candidate_id, expected_source=source,
            )
        else:
            result = us_read_applicant_exact(
                clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                candidate_id=applicant_candidate_id, expected_source=source,
            )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    return owner_envelope(
        jurisdiction=code, resource_kind="APPLICANT_IDENTITY_DISCOVERY",
        fact_state=result.fact_state, payload=result.payload,
    )


@router.get("/{jurisdiction}/applicants/{applicant_candidate_id}/portfolio")
def integration_applicant_owner_portfolio(
    request: Request,
    jurisdiction: str,
    applicant_candidate_id: str,
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_id: Annotated[str, Query(min_length=1, max_length=1000)],
    applicant_source_version: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_fingerprint_sha256: Annotated[str, Query(min_length=1, max_length=128)],
    applicant_observed_at: Annotated[str, Query(min_length=1, max_length=128)],
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    reject_unknown_query(request, _OWNER_PORTFOLIO_QUERY_FIELDS)
    code = _owner_jurisdiction(jurisdiction)
    source = _applicant_source_query(
        jurisdiction=code,
        source_id=applicant_source_id,
        source_version=applicant_source_version,
        source_fingerprint_sha256=applicant_source_fingerprint_sha256,
        observed_at=applicant_observed_at,
    )
    try:
        if code == "CN":
            result = cn_read_applicant_portfolio(
                client=clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                applicant_candidate_id=applicant_candidate_id, expected_source=source,
                page_size=page_size, cursor=cursor,
            )
        else:
            result = us_read_applicant_portfolio(
                clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                candidate_id=applicant_candidate_id, expected_source=source,
                page_size=page_size, cursor=cursor,
            )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    return owner_envelope(
        jurisdiction=code, resource_kind="APPLICANT_PORTFOLIO_DISCOVERY",
        fact_state=result.fact_state, payload=result.payload,
    )


@router.get("/{jurisdiction}/applicants/{applicant_candidate_id}/trademarks/{trademark_candidate_id}")
def integration_trademark_owner_exact(
    request: Request,
    jurisdiction: str,
    applicant_candidate_id: str,
    trademark_candidate_id: str,
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_id: Annotated[str, Query(min_length=1, max_length=1000)],
    applicant_source_version: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_fingerprint_sha256: Annotated[str, Query(min_length=1, max_length=128)],
    applicant_observed_at: Annotated[str, Query(min_length=1, max_length=128)],
    trademark_source_id: Annotated[str, Query(min_length=1, max_length=1000)],
    trademark_source_version: Annotated[str, Query(min_length=1, max_length=512)],
    trademark_source_fingerprint_sha256: Annotated[str, Query(min_length=1, max_length=128)],
    trademark_observed_at: Annotated[str, Query(min_length=1, max_length=128)],
) -> dict[str, Any]:
    reject_unknown_query(request, _OWNER_TRADEMARK_QUERY_FIELDS)
    code = _owner_jurisdiction(jurisdiction)
    applicant_source = _applicant_source_query(
        jurisdiction=code,
        source_id=applicant_source_id,
        source_version=applicant_source_version,
        source_fingerprint_sha256=applicant_source_fingerprint_sha256,
        observed_at=applicant_observed_at,
    )
    trademark_source = _trademark_source_query(
        jurisdiction=code,
        source_id=trademark_source_id,
        source_version=trademark_source_version,
        source_fingerprint_sha256=trademark_source_fingerprint_sha256,
        observed_at=trademark_observed_at,
    )
    try:
        if code == "CN":
            result = cn_read_trademark_exact(
                client=clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                applicant_candidate_id=applicant_candidate_id,
                expected_applicant_source=applicant_source,
                trademark_candidate_id=trademark_candidate_id,
                expected_trademark_source=trademark_source,
            )
        else:
            result = us_read_trademark_exact(
                clickhouse_client(), workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                applicant_candidate_id=applicant_candidate_id,
                expected_applicant_source=applicant_source,
                trademark_candidate_id=trademark_candidate_id,
                expected_trademark_source=trademark_source,
            )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    return owner_envelope(
        jurisdiction=code,
        resource_kind="APPLICANT_PORTFOLIO_DISCOVERY",
        fact_state=result.fact_state,
        payload=result.payload,
    )
