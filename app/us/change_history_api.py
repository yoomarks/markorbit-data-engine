from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.us.change_history import build_case_timeline, scan_change_feed_page


router = APIRouter(prefix="/api/us", tags=["US change history"])


def _strict_serial(serial_number: str) -> str:
    serial = serial_number.strip()
    if len(serial) != 8 or not serial.isdigit():
        raise HTTPException(
            status_code=400,
            detail="USPTO serial number must contain exactly 8 digits",
        )
    return serial


@router.get("/history/{serial_number}")
def us_case_history(
    serial_number: str,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
):
    serial = _strict_serial(serial_number)
    try:
        report = build_case_timeline(serial, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_CHANGE_HISTORY_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
    if report["observation_count"] == 0:
        raise HTTPException(status_code=404, detail="US trademark history not found")
    return report


@router.get("/changes")
def us_change_feed(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_serial: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_change_feed_page(
            after_source_rank=after_source_rank,
            after_serial=after_serial,
            scan_limit=scan_limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "US_CHANGE_FEED_INPUT_INVALID", "error": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "US_CHANGE_FEED_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        ) from exc
