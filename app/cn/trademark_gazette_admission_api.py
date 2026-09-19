from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.cn.trademark_gazette_admission import GazetteAdmissionError
from app.cn.trademark_gazette_chunk_admission import (
    admit_cn_trademark_gazette_chunk,
    finalize_cn_trademark_gazette_chunks,
)
from app.db import clickhouse_client
from app.fact_admission_security import require_fact_admission_auth


router = APIRouter(
    prefix="/api/admin/v2/fact-admissions",
    tags=["fact-admissions"],
    dependencies=[Depends(require_fact_admission_auth)],
)


def _admission_http_error(exc: GazetteAdmissionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "DATA_ENGINE_FACT_ADMISSION_REJECTED",
            "message": str(exc),
            "retryable": False,
        },
    )


def _unavailable_http_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "DATA_ENGINE_FACT_ADMISSION_UNAVAILABLE",
            "message": str(exc),
            "retryable": True,
        },
    )


@router.post("/cn/trademark-gazette/chunks")
def admit_cn_trademark_gazette_chunk_api(package: dict[str, Any]) -> dict[str, Any]:
    try:
        return admit_cn_trademark_gazette_chunk(
            package,
            client=clickhouse_client(),
        ).as_dict()
    except GazetteAdmissionError as exc:
        raise _admission_http_error(exc) from exc
    except Exception as exc:
        raise _unavailable_http_error(exc) from exc


@router.post("/cn/trademark-gazette/finalize")
def finalize_cn_trademark_gazette_chunks_api(
    package: dict[str, Any],
) -> dict[str, Any]:
    try:
        return finalize_cn_trademark_gazette_chunks(
            package,
            client=clickhouse_client(),
        ).as_dict()
    except GazetteAdmissionError as exc:
        raise _admission_http_error(exc) from exc
    except Exception as exc:
        raise _unavailable_http_error(exc) from exc
