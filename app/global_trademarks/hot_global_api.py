from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import clickhouse_client
from app.fact_admission_security import require_fact_admission_auth
from app.global_trademarks.execution import (
    ExecutionAlreadyRunning,
    global_trademark_execution_lock,
)
from app.global_trademarks.hot_global_admission import (
    CONTRACT_VERSION,
    SOURCE,
    TABLE,
    HotGlobalAdmissionError,
    admit,
    normalize,
    require_hot_global_ready,
)
from app.integration_security import require_integration_auth

admission_router = APIRouter(
    prefix="/api/admin/v2/fact-admissions/global",
    tags=["global-fact-admissions"],
    dependencies=[Depends(require_fact_admission_auth)],
)
read_router = APIRouter(
    prefix="/api/v1/global/trademarks",
    tags=["global-trademarks"],
    dependencies=[Depends(require_integration_auth)],
)


@admission_router.post("/observations")
def admit_global_observations(package: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = normalize(package)
        with global_trademark_execution_lock(
            "global-hot:" + SOURCE + ":" + normalized.source_response_sha256
        ):
            return admit(package, client=clickhouse_client())
    except HotGlobalAdmissionError as exc:
        raise HTTPException(
            400,
            detail={
                "code": "GLOBAL_TRADEMARK_ADMISSION_REJECTED",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except ExecutionAlreadyRunning as exc:
        raise HTTPException(
            409,
            detail={
                "code": "GLOBAL_TRADEMARK_ADMISSION_IN_PROGRESS",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "GLOBAL_TRADEMARK_HOT_GLOBAL_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc


@read_router.get("/{jurisdiction}/{source_record_id}")
def read_global_trademark_observations(jurisdiction: str, source_record_id: str) -> dict[str, Any]:
    if jurisdiction != "LA" or not re.fullmatch(r"LA[0-9]{3,10}", source_record_id):
        raise HTTPException(404, detail="Unknown source identity")
    try:
        client = clickhouse_client()
        require_hot_global_ready(client)
        rows = client.query(
            "SELECT observation_kind, page_index, mapping_version, mark_text, "
            "application_number, registration_number, source_status_raw, "
            "applicant_name, nice_classes, filing_date, logo_url, source_language, "
            "evidence_canonical_uri, evidence_sha256, source_response_sha256, observed_at "
            "FROM " + TABLE + " WHERE jurisdiction = %(jurisdiction)s "
            "AND source_id = %(source_id)s AND source_record_id = %(record_id)s "
            "ORDER BY observed_at DESC LIMIT 20",
            parameters={
                "jurisdiction": jurisdiction,
                "source_id": SOURCE,
                "record_id": source_record_id,
            },
        ).result_rows
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "code": "GLOBAL_TRADEMARK_READ_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            },
        ) from exc
    keys = (
        "observation_kind",
        "page_index",
        "mapping_version",
        "mark_text",
        "application_number",
        "registration_number",
        "source_status_raw",
        "applicant_name",
        "nice_classes",
        "filing_date",
        "logo_url",
        "source_language",
        "evidence_canonical_uri",
        "evidence_sha256",
        "source_response_sha256",
        "observed_at",
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "jurisdiction": jurisdiction,
        "source_id": SOURCE,
        "source_record_id": source_record_id,
        "storage_placement": "hot_global",
        "current_state_verified": False,
        "observations": [
            {
                name: value.isoformat() if hasattr(value, "isoformat") else value
                for name, value in zip(keys, row, strict=True)
            }
            for row in rows
        ],
    }
