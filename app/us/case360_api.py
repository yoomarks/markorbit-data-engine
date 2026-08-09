from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.us.case360 import build_case_360, case_360_schema


router = APIRouter(prefix="/api/us", tags=["US Case 360"])


@router.get("/case-360/schema")
def us_case_360_schema():
    return case_360_schema()


@router.get("/cases/{serial_number}/360")
def us_case_360(
    serial_number: str,
    as_of: date | None = None,
    history_limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    assignment_limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ttab_limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    try:
        result = build_case_360(
            serial_number,
            as_of=as_of,
            history_limit=history_limit,
            assignment_limit=assignment_limit,
            ttab_limit=ttab_limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "US_CASE_360_INPUT_INVALID", "error": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_CASE_360_BASE_FACTS_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="US trademark case not found")
    return result
