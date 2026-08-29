from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.cn.discovery_preliminary_publication import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PreliminaryPublicationDiscoveryRequest,
    execute_preliminary_publication_discovery,
)
from app.discovery_contract import DiscoveryContractError, DiscoveryCursorError
from app.integration_runtime import enforce_integration_rate_limit
from app.integration_security import require_integration_auth


router = APIRouter(
    prefix="/api/v1",
    tags=["MarkOrbit integration V1"],
    dependencies=[Depends(require_integration_auth), Depends(enforce_integration_rate_limit)],
)


def _discovery_http_error(exc: DiscoveryContractError) -> HTTPException:
    message = str(exc)
    conflict = isinstance(exc, DiscoveryCursorError) and (
        "cursor/query mismatch" in message
        or "cursor/snapshot mismatch" in message
        or "unsupported Discovery cursor version" in message
    )
    return HTTPException(
        status_code=(
            status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
        ),
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
        return execute_preliminary_publication_discovery(request)
    except DiscoveryContractError as exc:
        raise _discovery_http_error(exc) from exc
