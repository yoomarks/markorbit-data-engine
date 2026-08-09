from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.us_ttab import TTAB_SCHEMA_VERSION, TTAB_SEMANTICS
from app.us_ttab.read_model import proceeding_snapshot, proceedings_for_serial
from app.us_ttab.timeline import build_ttab_timeline


router = APIRouter(prefix="/api/us/ttab", tags=["US TTAB"])


@router.get("/schema")
def us_ttab_schema():
    return {
        "schema_version": TTAB_SCHEMA_VERSION,
        "source": "USPTO TTABVUE public rawxml=1 proceeding snapshot XML",
        "verified_real_source_types": ["OPP", "CAN", "EXA", "EXT"],
        "fact_families": ["proceeding", "party", "property", "docket"],
        "code_display_separation": True,
        "semantics": TTAB_SEMANTICS,
        "deadline_validity_inference": False,
        "legal_outcome_conclusion": False,
        "substantive_rights_conclusion": False,
    }


@router.get("/by-serial/{serial_number}")
def us_ttab_by_serial(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    try:
        records = proceedings_for_serial(serial_number, limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_TTAB_INPUT_INVALID", "error": str(exc)},
        ) from exc
    return {
        "serial_number": serial_number,
        "proceeding_count": len(records),
        "proceedings": records,
        "semantics": TTAB_SEMANTICS,
        "legal_outcome_conclusion": False,
    }


@router.get("/timeline/{proceeding_number}")
def us_ttab_timeline(proceeding_number: str):
    try:
        result = build_ttab_timeline(proceeding_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_TTAB_INPUT_INVALID", "error": str(exc)},
        ) from exc
    if result["observation_count"] == 0:
        raise HTTPException(status_code=404, detail={"code": "US_TTAB_PROCEEDING_NOT_FOUND"})
    return result


@router.get("/proceedings/{proceeding_number}")
def us_ttab_proceeding(proceeding_number: str):
    try:
        result = proceeding_snapshot(proceeding_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_TTAB_INPUT_INVALID", "error": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "US_TTAB_PROCEEDING_NOT_FOUND"})
    return result
