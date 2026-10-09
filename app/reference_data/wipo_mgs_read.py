from __future__ import annotations

import json
import re
from typing import Any

from app.reference_data.wipo_mgs_admission import TABLE, require_wipo_mgs_ready

READ_CONTRACT_VERSION = "WIPO_MGS_REFERENCE_READ_V1"
_LANGUAGE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_JURISDICTION = re.compile(r"^[A-Z][A-Z0-9-]{1,7}$")
_STATUSES = frozenset({"accepted", "rejected", "conflict", "unknown"})
_CURRENT_JOIN = (
    " FROM " + TABLE + " AS observation INNER JOIN ("
    "SELECT language, nice_class, "
    "argMax(tuple(request_language, source_response_sha256), "
    "tuple(observed_at, ingested_at)) AS head "
    "FROM " + TABLE + " GROUP BY language, nice_class) AS latest "
    "ON observation.language = latest.language "
    "AND observation.nice_class = latest.nice_class "
    "AND tuple(observation.request_language, observation.source_response_sha256) = latest.head"
)
_SELECT = (
    "SELECT observation.source_term_id, observation.nice_class, observation.language, "
    "observation.request_language, observation.source_version, observation.term_text, "
    "observation.seq_json, observation.src_json, observation.prf_json, "
    "observation.acc_raw_json, observation.rej_raw_json, "
    "observation.accepted_jurisdictions, observation.rejected_jurisdictions, "
    "observation.jurisdiction_statuses_json, observation.raw_payload_json, "
    "observation.content_sha256, observation.source_response_sha256, "
    "observation.evidence_canonical_uri, observation.evidence_sha256, "
    "observation.source_uri, observation.observed_at"
)


class WipoMgsReadError(ValueError):
    pass


def _bounded_text(value: str | None, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise WipoMgsReadError(label + " must be a bounded non-empty string")
    return cleaned


def _nice_class(value: int | None, *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 45:
        raise WipoMgsReadError("nice_class must be an integer from 1 to 45")
    return value


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise WipoMgsReadError("limit must be an integer from 1 to 100")
    return value


def _offset(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WipoMgsReadError("offset must be a non-negative integer")
    return value


def _fixed_string(value: Any) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("ascii")
    return str(value)


def _item(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        source_term_id,
        nice_class,
        language,
        request_language,
        source_version,
        term_text,
        seq_json,
        src_json,
        prf_json,
        acc_raw_json,
        rej_raw_json,
        accepted,
        rejected,
        statuses_json,
        raw_payload_json,
        content_sha256,
        response_sha256,
        evidence_uri,
        evidence_sha256,
        source_uri,
        observed_at,
    ) = row
    return {
        "source_term_id": source_term_id,
        "nice_class": nice_class,
        "language": language,
        "request_language": request_language,
        "source_version": source_version,
        "term_text": term_text,
        "seq": json.loads(seq_json),
        "src": json.loads(src_json),
        "prf": json.loads(prf_json),
        "acc_raw": json.loads(acc_raw_json),
        "rej_raw": json.loads(rej_raw_json),
        "accepted_jurisdictions": list(accepted),
        "rejected_jurisdictions": list(rejected),
        "jurisdiction_statuses": json.loads(statuses_json),
        "raw_payload": json.loads(raw_payload_json),
        "content_sha256": _fixed_string(content_sha256),
        "source_response_sha256": _fixed_string(response_sha256),
        "evidence_canonical_uri": evidence_uri,
        "evidence_sha256": _fixed_string(evidence_sha256),
        "source_uri": source_uri,
        "observed_at": observed_at.isoformat(),
    }


def search_current_terms(
    *,
    client: Any,
    q: str | None = None,
    nice_class: int | None = None,
    language: str | None = None,
    jurisdiction_code: str | None = None,
    acceptance_status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    require_wipo_mgs_ready(client)
    query = _bounded_text(q, "q", 512)
    cls = _nice_class(nice_class)
    lang = _bounded_text(language, "language", 32)
    if lang is not None and not _LANGUAGE.fullmatch(lang):
        raise WipoMgsReadError("language is invalid")
    jurisdiction = _bounded_text(jurisdiction_code, "jurisdiction_code", 8)
    if jurisdiction is not None:
        jurisdiction = jurisdiction.upper()
        if not _JURISDICTION.fullmatch(jurisdiction):
            raise WipoMgsReadError("jurisdiction_code is invalid")
    status = _bounded_text(acceptance_status, "acceptance_status", 16)
    if status is not None and status not in _STATUSES:
        raise WipoMgsReadError("acceptance_status is invalid")
    if status is not None and jurisdiction is None:
        raise WipoMgsReadError("acceptance_status requires jurisdiction_code")

    conditions: list[str] = []
    params: dict[str, Any] = {"limit": _limit(limit), "offset": _offset(offset)}
    if query is not None:
        conditions.append("positionCaseInsensitiveUTF8(observation.term_text, %(q)s) > 0")
        params["q"] = query
    if cls is not None:
        conditions.append("observation.nice_class = %(nice_class)s")
        params["nice_class"] = cls
    if lang is not None:
        conditions.append("observation.language = %(language)s")
        params["language"] = lang
    if jurisdiction is not None:
        params["jurisdiction"] = jurisdiction
        accepted = "has(observation.accepted_jurisdictions, %(jurisdiction)s)"
        rejected = "has(observation.rejected_jurisdictions, %(jurisdiction)s)"
        if status == "accepted":
            conditions.append(accepted + " AND NOT " + rejected)
        elif status == "rejected":
            conditions.append(rejected + " AND NOT " + accepted)
        elif status == "conflict":
            conditions.append(accepted + " AND " + rejected)
        elif status == "unknown":
            conditions.append("NOT " + accepted + " AND NOT " + rejected)
        else:
            conditions.append("(" + accepted + " OR " + rejected + ")")
    where = " WHERE " + " AND ".join("(" + item + ")" for item in conditions) if conditions else ""
    sql = (
        _SELECT
        + _CURRENT_JOIN
        + where
        + " ORDER BY observation.nice_class, observation.source_term_id, observation.language "
        "LIMIT %(limit)s OFFSET %(offset)s"
    )
    rows = client.query(sql, parameters=params).result_rows
    return {
        "contract_version": READ_CONTRACT_VERSION,
        "current_state_basis": "LATEST_ADMITTED_LANGUAGE_CLASS_SNAPSHOT",
        "items": [_item(row) for row in rows],
        "limit": params["limit"],
        "offset": params["offset"],
    }


def get_current_term(*, client: Any, source_term_id: str, nice_class: int) -> dict[str, Any] | None:
    require_wipo_mgs_ready(client)
    identity = _bounded_text(source_term_id, "source_term_id", 128)
    cls = _nice_class(nice_class, required=True)
    rows = client.query(
        _SELECT + _CURRENT_JOIN + " WHERE observation.source_term_id = %(source_term_id)s "
        "AND observation.nice_class = %(nice_class)s "
        "ORDER BY observation.language LIMIT 100",
        parameters={"source_term_id": identity, "nice_class": cls},
    ).result_rows
    if not rows:
        return None
    return {
        "contract_version": READ_CONTRACT_VERSION,
        "current_state_basis": "LATEST_ADMITTED_LANGUAGE_CLASS_SNAPSHOT",
        "source_term_id": identity,
        "nice_class": cls,
        "localized_terms": [_item(row) for row in rows],
    }
