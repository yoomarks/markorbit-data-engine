from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import clickhouse_client
from app.fact_admission_execution import ExecutionAlreadyRunning, fact_admission_execution_lock
from app.fact_admission_security import require_fact_admission_auth
from app.integration_security import require_integration_auth
from app.reference_data.wipo_mgs_admission import WipoMgsAdmissionError, admit, normalize
from app.reference_data.wipo_mgs_read import (
    WipoMgsReadError,
    get_current_term,
    search_current_terms,
)

router = APIRouter(
    prefix="/api/admin/v2/fact-admissions/reference/wipo-mgs",
    tags=["reference-fact-admissions"],
    dependencies=[Depends(require_fact_admission_auth)],
)
read_router = APIRouter(
    prefix="/api/v1/reference/wipo-mgs",
    tags=["reference-data"],
    dependencies=[Depends(require_integration_auth)],
)


@router.post("/snapshots")
def admit_wipo_mgs_snapshot(package: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = normalize(package)
        scope = "wipo-mgs:" + normalized.request_language + ":" + str(normalized.nice_class)
        with fact_admission_execution_lock(scope):
            return admit(package, client=clickhouse_client())
    except WipoMgsAdmissionError as exc:
        raise HTTPException(
            400,
            detail={
                "code": "WIPO_MGS_ADMISSION_REJECTED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except ExecutionAlreadyRunning as exc:
        raise HTTPException(
            409,
            detail={
                "code": "WIPO_MGS_ADMISSION_IN_PROGRESS",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "WIPO_MGS_STORAGE_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc


@read_router.get("/terms")
def search_wipo_mgs_terms(
    q: str | None = None,
    nice_class: int | None = None,
    language: str | None = None,
    jurisdiction_code: str | None = None,
    acceptance_status: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        return search_current_terms(
            client=clickhouse_client(),
            q=q,
            nice_class=nice_class,
            language=language,
            jurisdiction_code=jurisdiction_code,
            acceptance_status=acceptance_status,
            limit=limit,
            offset=offset,
        )
    except WipoMgsReadError as exc:
        raise HTTPException(
            400, detail={"code": "WIPO_MGS_QUERY_INVALID", "message": str(exc)}
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={"code": "WIPO_MGS_READ_UNAVAILABLE", "message": str(exc), "retryable": True},
        ) from exc


@read_router.get("/terms/{source_term_id}")
def read_wipo_mgs_term(source_term_id: str, nice_class: int) -> dict[str, Any]:
    try:
        result = get_current_term(
            client=clickhouse_client(), source_term_id=source_term_id, nice_class=nice_class
        )
    except WipoMgsReadError as exc:
        raise HTTPException(
            400, detail={"code": "WIPO_MGS_QUERY_INVALID", "message": str(exc)}
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={"code": "WIPO_MGS_READ_UNAVAILABLE", "message": str(exc), "retryable": True},
        ) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "WIPO_MGS_TERM_NOT_FOUND"})
    return result
