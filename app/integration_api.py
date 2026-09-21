from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.cn.applicant_owner_read import (
    current_applicants_for_case as cn_current_applicants_for_case,
    read_applicant_exact as cn_read_applicant_exact,
    read_portfolio as cn_read_applicant_portfolio,
    read_trademark_exact as cn_read_trademark_exact,
)
from app.cn.agent_name_lookup import (
    AgentNameLookupInvalid,
    AgentNameLookupScopeExceeded,
    AgentNameLookupUnavailable,
    agents_by_name,
)
from app.cn.agent_exact_read import (
    AgentExactReadInvalid,
    AgentExactReadUnavailable,
    read_agent_exact,
)
from app.cn.entity_trademark_portfolio import (
    EntityPortfolioInvalid,
    EntityPortfolioRequest,
    EntityPortfolioUnavailable,
    execute_page as execute_cn_entity_portfolio_page,
)
from app.cn.relationship_timeline import (
    RelationshipTimelineInvalid,
    RelationshipTimelineScopeExceeded,
    RelationshipTimelineUnavailable,
    relationships_for_trademark,
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
from app.read_query_capability import read_query_capability_contract
from app.temporal_relationship_contract import temporal_relationship_contract
from app.us.accepted_target_read import accepted_us_target_read_client
from app.us.attorney_name_lookup import (
    AttorneyNameLookupInvalid,
    AttorneyNameLookupScopeExceeded,
    AttorneyNameLookupUnavailable,
    attorneys_by_name,
)
from app.us.ttab_correspondent_history import (
    TTABCorrespondentHistoryInvalid,
    TTABCorrespondentHistoryRequest,
    TTABCorrespondentHistoryUnavailable,
    execute_page as execute_us_ttab_correspondent_history_page,
)
from app.us.applicant_owner_read import (
    current_applicants_for_case as us_current_applicants_for_case,
    discover_applicants_by_name as us_discover_applicants_by_name,
    read_portfolio as us_read_applicant_portfolio,
    revalidate_applicant as us_read_applicant_exact,
    revalidate_trademark as us_read_trademark_exact,
)
from app.us.event_serial_lookup import (
    EventSerialLookupInvalid,
    EventSerialLookupScopeExceeded,
    EventSerialLookupUnavailable,
    events_for_serial,
)
from app.us.natural_lapse_discovery import (
    DEFAULT_PAGE_SIZE as US_NATURAL_LAPSE_DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE as US_NATURAL_LAPSE_MAX_PAGE_SIZE,
    NaturalLapseDiscoveryRequest,
    NaturalLapseUnavailable,
    execute_page as execute_us_natural_lapse_page,
)
from app.us.recorded_party_history import (
    RecordedPartyHistoryInvalid,
    RecordedPartyHistoryRequest,
    RecordedPartyHistoryUnavailable,
    execute_page as execute_us_recorded_party_history_page,
)
from app.us.registration_lookup import (
    RegistrationLookupInvalid,
    RegistrationLookupScopeExceeded,
    RegistrationLookupUnavailable,
    lookup_registration,
)
from app.us.relationship_timeline import (
    USRelationshipTimelineInvalid,
    USRelationshipTimelineScopeExceeded,
    USRelationshipTimelineUnavailable,
    relationships_for_trademark as us_relationships_for_trademark,
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
        "foundation_contracts": {
            "temporal_relationship": temporal_relationship_contract(),
            "read_query_capability": read_query_capability_contract(),
        },
        "g0_contract": descriptor,
    }


@router.get("/cn/cases/{application_number}")
def integration_cn_case(application_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="CN", resource_kind="TRADEMARK_CASE", payload=cn_case(application_number)
    )


@router.get("/cn/agents/by-name")
def integration_cn_agents_by_name(
    name: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, Any]:
    try:
        payload = agents_by_name(clickhouse_client(), name)
    except AgentNameLookupInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_AGENT_NAME_LOOKUP_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except AgentNameLookupScopeExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "DATA_ENGINE_AGENT_NAME_LOOKUP_SCOPE_EXCEEDED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except AgentNameLookupUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_AGENT_NAME_LOOKUP_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    result = _envelope(
        jurisdiction="CN",
        resource_kind="AGENT_NAME_FACT_MATCHES",
        payload=payload,
    )
    if payload["match_count"] == 0:
        result["fact_state"] = "not_found"
    return result


@router.get("/cn/agents/{agent_code}")
def integration_cn_agent_exact(agent_code: str) -> dict[str, Any]:
    try:
        payload = read_agent_exact(clickhouse_client(), agent_code)
    except AgentExactReadInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_AGENT_EXACT_READ_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except AgentExactReadUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_AGENT_EXACT_READ_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    result = _envelope(
        jurisdiction="CN",
        resource_kind="AGENT_SOURCE_RECORD",
        payload=payload,
    )
    if payload["record"] is None:
        result["fact_state"] = "not_found"
    return result


@router.get("/cn/entities/{entity_id}/trademarks")
def integration_cn_entity_trademarks(
    entity_id: str,
    role: Annotated[str | None, Query(pattern="^(OWNER|CO_OWNER|AGENT)$")] = None,
    scope: Annotated[str, Query(pattern="^(current|historical|all)$")] = "all",
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    try:
        payload = execute_cn_entity_portfolio_page(
            EntityPortfolioRequest(
                entity_id=entity_id,
                role=role,
                scope=scope,
                page_size=page_size,
                cursor=cursor,
            ),
            client=clickhouse_client(),
        )
    except EntityPortfolioInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_ENTITY_PORTFOLIO_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except EntityPortfolioUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_ENTITY_PORTFOLIO_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    return _envelope(
        jurisdiction="CN",
        resource_kind="ENTITY_TRADEMARK_PORTFOLIO",
        payload=payload,
    )


@router.get("/cn/cases/{application_number}/relationships")
def integration_cn_case_relationships(
    application_number: str,
    scope: Annotated[str, Query(pattern="^(current|historical|all)$")] = "all",
) -> dict[str, Any]:
    try:
        payload = relationships_for_trademark(
            clickhouse_client(), application_number, scope=scope
        )
    except RelationshipTimelineInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_RELATIONSHIP_TIMELINE_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RelationshipTimelineScopeExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "DATA_ENGINE_RELATIONSHIP_TIMELINE_SCOPE_EXCEEDED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RelationshipTimelineUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_RELATIONSHIP_TIMELINE_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    return _envelope(
        jurisdiction="CN",
        resource_kind="TRADEMARK_RELATIONSHIP_TIMELINE",
        payload=payload,
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


_US_NATURAL_LAPSE_QUERY_FIELDS = {
    "lapse_reason",
    "event_date_start",
    "event_date_end",
    "nice_class",
    "serial_number_start",
    "serial_number_end",
    "page_size",
    "cursor",
}


@router.get("/us/discovery/natural-lapses")
def integration_us_natural_lapse_discovery(
    request: Request,
    lapse_reason: str = Query(..., min_length=1, max_length=128),
    event_date_start: date = Query(...),
    event_date_end: date = Query(...),
    nice_class: int | None = Query(default=None, ge=1, le=45),
    serial_number_start: str | None = Query(default=None, min_length=1, max_length=128),
    serial_number_end: str | None = Query(default=None, min_length=1, max_length=128),
    page_size: int = Query(
        default=US_NATURAL_LAPSE_DEFAULT_PAGE_SIZE,
        ge=1,
        le=US_NATURAL_LAPSE_MAX_PAGE_SIZE,
    ),
    cursor: str | None = Query(default=None, min_length=1, max_length=8192),
) -> dict[str, Any]:
    reject_unknown_query(request, _US_NATURAL_LAPSE_QUERY_FIELDS)
    try:
        page = execute_us_natural_lapse_page(
            NaturalLapseDiscoveryRequest(
                lapse_reason=lapse_reason,
                event_date_start=event_date_start,
                event_date_end=event_date_end,
                nice_class=nice_class,
                serial_number_start=serial_number_start,
                serial_number_end=serial_number_end,
                page_size=page_size,
                cursor=cursor,
            ),
            client=accepted_us_target_read_client(),
        )
    except NaturalLapseUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_NATURAL_LAPSE_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
                "result_state": "UNAVAILABLE",
            },
        ) from exc
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    return _envelope(
        jurisdiction="US",
        resource_kind="NATURAL_LAPSE_SOURCE_FACT_DISCOVERY",
        payload=page,
    )

@router.get("/us/cases/{serial_number}")
def integration_us_case(serial_number: str) -> dict[str, Any]:
    return _envelope(
        jurisdiction="US", resource_kind="TRADEMARK_CASE", payload=us_case(serial_number)
    )


@router.get("/us/cases/{serial_number}/events")
def integration_us_case_events(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> dict[str, Any]:
    try:
        payload = events_for_serial(
            accepted_us_target_read_client(), serial_number, limit=limit
        )
    except EventSerialLookupInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_EVENT_TIMELINE_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except EventSerialLookupScopeExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "DATA_ENGINE_EVENT_TIMELINE_SCOPE_EXCEEDED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except EventSerialLookupUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_EVENT_TIMELINE_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_EVENT_TIMELINE",
        payload=payload,
    )


@router.get("/us/attorneys/by-name")
def integration_us_attorneys_by_name(
    name: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, Any]:
    try:
        payload = attorneys_by_name(accepted_us_target_read_client(), name)
    except AttorneyNameLookupInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_ATTORNEY_NAME_LOOKUP_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except AttorneyNameLookupScopeExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "DATA_ENGINE_ATTORNEY_NAME_LOOKUP_SCOPE_EXCEEDED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except AttorneyNameLookupUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_ATTORNEY_NAME_LOOKUP_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    result = _envelope(
        jurisdiction="US",
        resource_kind="ATTORNEY_NAME_FACT_MATCHES",
        payload=payload,
    )
    if payload["match_count"] == 0:
        result["fact_state"] = "not_found"
    return result


@router.get("/us/correspondents/by-name/ttab-history")
def integration_us_ttab_correspondent_history(
    name: Annotated[str, Query(min_length=1, max_length=512)],
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    try:
        payload = execute_us_ttab_correspondent_history_page(
            TTABCorrespondentHistoryRequest(
                name=name,
                page_size=page_size,
                cursor=cursor,
            ),
            client=accepted_us_target_read_client(),
        )
    except TTABCorrespondentHistoryInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_US_TTAB_CORRESPONDENT_HISTORY_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except TTABCorrespondentHistoryUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_US_TTAB_CORRESPONDENT_HISTORY_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    return _envelope(
        jurisdiction="US",
        resource_kind="TTAB_CORRESPONDENT_HANDLED_MARK_HISTORY",
        payload=payload,
    )


@router.get("/us/recorded-parties/by-name")
def integration_us_recorded_parties_by_name(
    name: Annotated[str, Query(min_length=1, max_length=512)],
    source_domain: Annotated[
        str, Query(pattern="^(ALL|US_ASSIGNMENT|US_TTAB)$")
    ] = "ALL",
    relationship_type: Annotated[
        str | None, Query(min_length=1, max_length=64)
    ] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    try:
        payload = execute_us_recorded_party_history_page(
            RecordedPartyHistoryRequest(
                name=name,
                source_domain=source_domain,
                relationship_type=relationship_type,
                page_size=page_size,
                cursor=cursor,
            ),
            client=clickhouse_client(),
        )
    except RecordedPartyHistoryInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_US_RECORDED_PARTY_HISTORY_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RecordedPartyHistoryUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_US_RECORDED_PARTY_HISTORY_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    return _envelope(
        jurisdiction="US",
        resource_kind="RECORDED_PARTY_RELATIONSHIP_HISTORY",
        payload=payload,
    )


@router.get("/us/registrations/{registration_number}")
def integration_us_registration(registration_number: str) -> dict[str, Any]:
    try:
        payload = lookup_registration(accepted_us_target_read_client(), registration_number)
    except RegistrationLookupInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_REGISTRATION_LOOKUP_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RegistrationLookupScopeExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "DATA_ENGINE_REGISTRATION_LOOKUP_SCOPE_EXCEEDED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RegistrationLookupUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_REGISTRATION_LOOKUP_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    result = _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_CASE_BY_REGISTRATION",
        payload=payload,
    )
    if payload["match_count"] == 0:
        result["fact_state"] = "not_found"
    return result


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


@router.get("/us/cases/{serial_number}/relationships")
def integration_us_case_relationships(
    serial_number: str,
    scope: Annotated[str, Query(pattern="^(current|historical|all)$")] = "all",
) -> dict[str, Any]:
    try:
        payload = us_relationships_for_trademark(
            clickhouse_client(), serial_number, scope=scope
        )
    except USRelationshipTimelineInvalid as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "US_RELATIONSHIP_TIMELINE_INPUT_INVALID", "error": str(exc)},
        ) from exc
    except USRelationshipTimelineScopeExceeded as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_RELATIONSHIP_TIMELINE_SCOPE_EXCEEDED", "error": str(exc)},
        ) from exc
    except USRelationshipTimelineUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "US_RELATIONSHIP_TIMELINE_UNAVAILABLE", "error": str(exc)},
        ) from exc
    return _envelope(
        jurisdiction="US",
        resource_kind="TRADEMARK_RELATIONSHIP_TIMELINE",
        payload=payload,
    )


_US_APPLICANT_NAME_QUERY_FIELDS = {
    "name", "requester_workspace_id", "page_size", "cursor",
}


@router.get("/us/applicants/by-name")
def integration_us_applicants_by_name(
    request: Request,
    name: Annotated[str, Query(min_length=1, max_length=512)],
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    reject_unknown_query(request, _US_APPLICANT_NAME_QUERY_FIELDS)
    try:
        result = us_discover_applicants_by_name(
            accepted_us_target_read_client(),
            workspace_id=requester_workspace_id,
            request_id=request_id_from_request(request),
            name=name, page_size=page_size, cursor=cursor,
        )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    return owner_envelope(
        jurisdiction="US", resource_kind="APPLICANT_IDENTITY_DISCOVERY",
        fact_state=result.fact_state, payload=result.payload,
    )


_CASE_APPLICANT_QUERY_FIELDS = {"requester_workspace_id"}


@router.get("/{jurisdiction}/cases/{case_key}/applicants")
def integration_case_current_applicants(
    request: Request,
    jurisdiction: str,
    case_key: str,
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, Any]:
    reject_unknown_query(request, _CASE_APPLICANT_QUERY_FIELDS)
    code = _owner_jurisdiction(jurisdiction)
    try:
        if code == "CN":
            result = cn_current_applicants_for_case(
                client=clickhouse_client(),
                workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                application_number=case_key,
            )
        else:
            result = us_current_applicants_for_case(
                accepted_us_target_read_client(),
                workspace_id=requester_workspace_id,
                request_id=request_id_from_request(request),
                serial_number=case_key,
            )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    return owner_envelope(
        jurisdiction=code,
        resource_kind="CASE_CURRENT_APPLICANT_DISCOVERY",
        fact_state=result.fact_state,
        payload=result.payload,
    )


_OWNER_APPLICANT_QUERY_FIELDS = {
    "requester_workspace_id",
    "applicant_source_id",
    "applicant_source_version",
    "applicant_source_fingerprint_sha256",
    "applicant_observed_at",
}
_OWNER_PORTFOLIO_QUERY_FIELDS = _OWNER_APPLICANT_QUERY_FIELDS | {"page_size", "cursor"}
_US_APPLICANT_RECORDED_HISTORY_QUERY_FIELDS = _OWNER_APPLICANT_QUERY_FIELDS | {
    "source_domain", "relationship_type", "page_size", "cursor",
}
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
                accepted_us_target_read_client(), workspace_id=requester_workspace_id,
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
                accepted_us_target_read_client(), workspace_id=requester_workspace_id,
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


@router.get("/us/applicants/{applicant_candidate_id}/recorded-history")
def integration_us_applicant_recorded_history(
    request: Request,
    applicant_candidate_id: str,
    requester_workspace_id: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_id: Annotated[str, Query(min_length=1, max_length=1000)],
    applicant_source_version: Annotated[str, Query(min_length=1, max_length=512)],
    applicant_source_fingerprint_sha256: Annotated[str, Query(min_length=1, max_length=128)],
    applicant_observed_at: Annotated[str, Query(min_length=1, max_length=128)],
    source_domain: Annotated[str, Query(pattern="^(ALL|US_ASSIGNMENT|US_TTAB)$")] = "ALL",
    relationship_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=8192)] = None,
) -> dict[str, Any]:
    reject_unknown_query(request, _US_APPLICANT_RECORDED_HISTORY_QUERY_FIELDS)
    source = _applicant_source_query(
        jurisdiction="US",
        source_id=applicant_source_id,
        source_version=applicant_source_version,
        source_fingerprint_sha256=applicant_source_fingerprint_sha256,
        observed_at=applicant_observed_at,
    )
    try:
        current = us_read_applicant_exact(
            accepted_us_target_read_client(),
            workspace_id=requester_workspace_id,
            request_id=request_id_from_request(request),
            candidate_id=applicant_candidate_id,
            expected_source=source,
        )
    except OWNER_READ_EXCEPTIONS as exc:
        raise owner_read_http_error(exc) from exc
    if current.fact_state != "observed" or not current.payload:
        return owner_envelope(
            jurisdiction="US",
            resource_kind="APPLICANT_RECORDED_RELATIONSHIP_DISCOVERY",
            fact_state=current.fact_state,
            payload=current.payload,
        )
    candidates = list(current.payload.get("results") or [])
    if len(candidates) != 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_OWNER_READ_UNAVAILABLE",
                "message": "Exact US Applicant revalidation did not return one candidate.",
                "retryable": True,
            },
        )
    applicant = candidates[0]
    try:
        history = execute_us_recorded_party_history_page(
            RecordedPartyHistoryRequest(
                name=str(applicant["display_name"]),
                source_domain=source_domain,
                relationship_type=relationship_type,
                page_size=page_size,
                cursor=cursor,
            ),
            client=clickhouse_client(),
        )
    except RecordedPartyHistoryInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_US_RECORDED_PARTY_HISTORY_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except RecordedPartyHistoryUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_US_RECORDED_PARTY_HISTORY_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
    payload = {
        "current_applicant_candidate": applicant,
        "current_source_snapshot": current.payload.get("source_snapshot"),
        "recorded_relationship_history": history,
        "historical_match_state": (
            "observed" if int(history.get("result_count") or 0) > 0 else "not_found"
        ),
        "identity_resolution_claimed": False,
        "review_required": True,
        "legal_conclusion": False,
        "semantics": (
            "CURRENT_APPLICANT_CANDIDATE_NAME_TO_EXACT_NORMALIZED_RECORDED_RELATIONSHIP_DISCOVERY;"
            "NOT_CROSS_SOURCE_IDENTITY_RESOLUTION_OR_LEGAL_TITLE_CONCLUSION"
        ),
        "authority_consequences": {
            "verifiedLegalIdentityEstablished": False,
            "historicalOwnershipEstablished": False,
            "historicalRepresentationEstablished": False,
            "customerRelationshipEstablished": False,
            "legalConclusionCreated": False,
            "externalActionAuthorized": False,
        },
    }
    return owner_envelope(
        jurisdiction="US",
        resource_kind="APPLICANT_RECORDED_RELATIONSHIP_DISCOVERY",
        fact_state="observed",
        payload=payload,
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
                accepted_us_target_read_client(), workspace_id=requester_workspace_id,
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
