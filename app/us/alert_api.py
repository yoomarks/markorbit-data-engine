from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.us.alert_engine import (
    alert_engine_schema,
    scan_assignment_alerts,
    scan_case_change_alerts,
    scan_deadline_alerts,
    scan_reviewed_event_alerts,
    scan_ttab_alerts,
)
from app.us.monitoring_readiness import build_monitoring_readiness


router = APIRouter(prefix="/api/us/alerts", tags=["US Alert Engine"])


def _value_error(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "US_ALERT_INPUT_INVALID", "error": str(exc)},
    )


def _unavailable(feed: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "US_ALERT_FEED_UNAVAILABLE",
            "feed": feed,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


@router.get("/schema")
def us_alert_schema():
    return alert_engine_schema()


@router.get("/readiness")
def us_alert_readiness(
    expected_history_parts: Annotated[int | None, Query(ge=1)] = None,
    verify_sources: bool = False,
):
    try:
        return build_monitoring_readiness(
            raw_root=Path(get_settings().raw_data_root),
            expected_history_parts=expected_history_parts,
            verify_sources=verify_sources,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("readiness", exc) from exc


@router.get("/case-changes")
def us_case_change_alerts(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_serial: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_case_change_alerts(
            after_source_rank=after_source_rank,
            after_serial=after_serial,
            scan_limit=scan_limit,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("case_changes", exc) from exc


@router.get("/assignments")
def us_assignment_alerts(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_reel_frame: str = "",
    after_package_id: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_assignment_alerts(
            after_source_rank=after_source_rank,
            after_reel_frame=after_reel_frame,
            after_package_id=after_package_id,
            scan_limit=scan_limit,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("assignments", exc) from exc


@router.get("/ttab")
def us_ttab_alerts(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_proceeding: str = "",
    after_package_id: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_ttab_alerts(
            after_source_rank=after_source_rank,
            after_proceeding=after_proceeding,
            after_package_id=after_package_id,
            scan_limit=scan_limit,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("ttab", exc) from exc


@router.get("/reviewed-events")
def us_reviewed_event_alerts(
    after_source_rank: Annotated[int, Query(ge=0)] = 0,
    after_event_key: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    try:
        return scan_reviewed_event_alerts(
            raw_root=Path(get_settings().raw_data_root),
            after_source_rank=after_source_rank,
            after_event_key=after_event_key,
            scan_limit=scan_limit,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("reviewed_events", exc) from exc


@router.get("/deadlines")
def us_deadline_alerts(
    as_of: date | None = None,
    after_serial: str = "",
    scan_limit: Annotated[int, Query(ge=1, le=500)] = 200,
    horizon_days: Annotated[int, Query(ge=0, le=3650)] = 90,
    recent_past_days: Annotated[int, Query(ge=0, le=365)] = 30,
):
    try:
        return scan_deadline_alerts(
            raw_root=Path(get_settings().raw_data_root),
            as_of=as_of or date.today(),
            after_serial=after_serial,
            scan_limit=scan_limit,
            horizon_days=horizon_days,
            recent_past_days=recent_past_days,
        )
    except ValueError as exc:
        raise _value_error(exc) from exc
    except Exception as exc:
        raise _unavailable("deadlines", exc) from exc
