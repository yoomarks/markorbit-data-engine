from typing import Any

from fastapi import HTTPException, Request, status

from app.applicant_owner_read import (
    APPLICANT_RESOURCE_KIND,
    PORTFOLIO_RESOURCE_KIND,
    OwnerReadConflict,
    OwnerReadInvalid,
    OwnerReadUnavailable,
    source_reference,
)
from app.discovery_contract import DiscoveryCursorError
from app.integration_contract import CONTRACT_VERSION, SOURCE_OWNER
from app.version import engine_version

FACT_AUTHORITY = "DATA_ENGINE_FACT_READ_MODEL"


def owner_envelope(*, jurisdiction: str, resource_kind: str, fact_state: str, payload: Any) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": engine_version(),
        "source_owner": SOURCE_OWNER,
        "jurisdiction": jurisdiction,
        "resource_kind": resource_kind,
        "authority": FACT_AUTHORITY,
        "legal_conclusion": False,
        "fact_state": fact_state,
        "payload": payload,
    }


def applicant_source_from_query(
    *,
    jurisdiction: str,
    source_id: str,
    source_version: str,
    source_fingerprint_sha256: str,
    observed_at: str,
) -> dict[str, str]:
    return source_reference(
        jurisdiction=jurisdiction,
        source_kind="APPLICANT_IDENTITY",
        source_id=source_id,
        source_version=source_version,
        fingerprint=source_fingerprint_sha256,
        observed_at=observed_at,
    )


def trademark_source_from_query(
    *,
    jurisdiction: str,
    source_id: str,
    source_version: str,
    source_fingerprint_sha256: str,
    observed_at: str,
) -> dict[str, str]:
    return source_reference(
        jurisdiction=jurisdiction,
        source_kind="TRADEMARK_RECORD",
        source_id=source_id,
        source_version=source_version,
        fingerprint=source_fingerprint_sha256,
        observed_at=observed_at,
    )


def reject_unknown_query(request: Request, allowed: set[str]) -> None:
    unknown = sorted(set(request.query_params.keys()) - allowed)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_OWNER_READ_UNKNOWN_QUERY_FIELD",
                "message": "Owner-read query contains unsupported field(s).",
                "retryable": False,
                "fields": unknown,
            },
        )


def request_id_from_request(request: Request) -> str:
    request_id = str(getattr(request.state, "request_id", "") or "").strip()
    if not request_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATA_ENGINE_OWNER_READ_REQUEST_CONTEXT_UNAVAILABLE",
                "message": "Trusted integration request context is unavailable.",
                "retryable": True,
            },
        )
    return request_id


def owner_read_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OwnerReadConflict):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DATA_ENGINE_OWNER_READ_CONFLICT", "message": str(exc), "retryable": False},
        )
    if isinstance(exc, DiscoveryCursorError):
        message = str(exc)
        conflict = any(token in message for token in ("cursor/query mismatch", "cursor/snapshot mismatch"))
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DATA_ENGINE_OWNER_READ_CURSOR_CONFLICT" if conflict else "DATA_ENGINE_OWNER_READ_INVALID_CURSOR",
                "message": message,
                "retryable": False,
            },
        )
    if isinstance(exc, OwnerReadInvalid):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "DATA_ENGINE_OWNER_READ_INVALID", "message": str(exc), "retryable": False},
        )
    if isinstance(exc, OwnerReadUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DATA_ENGINE_OWNER_READ_UNAVAILABLE", "message": str(exc), "retryable": True},
        )
    raise exc


def resource_kind_for_operation(operation: str) -> str:
    return APPLICANT_RESOURCE_KIND if operation == "applicant" else PORTFOLIO_RESOURCE_KIND


OWNER_READ_EXCEPTIONS = (
    OwnerReadConflict,
    OwnerReadInvalid,
    OwnerReadUnavailable,
    DiscoveryCursorError,
)
