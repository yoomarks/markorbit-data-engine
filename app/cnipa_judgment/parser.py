from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from app.fact_event_envelope import ProvenanceRef, SubjectRef, build_fact_event_envelope

from .model import CnipaJudgmentListFact, DOCUMENT_KINDS


_SCHEMA_VERSION = "cnipa-list-fact-projection-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DETAIL_PATHS = {
    "REGISTRATION_EXAMINATION": (
        "/toas-pub-prod/pub-prod-api/pubnotice/portal/tmscJudgment/queryInfo"
    ),
    "OPPOSITION_DECISION": (
        "/toas-pub-prod/pub-prod-api/pubnotice/portal/tmyyJudgment/queryInfo"
    ),
    "REVIEW_ADJUDICATION": (
        "/toas-pub-prod/pub-prod-api/pubnotice/portal/tmpsJudgment/queryInfo"
    ),
}
_ID_FIELDS = {
    "REGISTRATION_EXAMINATION": "adjuOpenId",
    "OPPOSITION_DECISION": "adjuOpenId",
    "REVIEW_ADJUDICATION": "pubId",
}
_COMMON_FIELDS = {
    "REGISTRATION_EXAMINATION": {
        "application_number": "applyNo",
        "registration_number": "regNo",
        "trademark_name": "tmName",
        "source_title": "adjuTitle",
        "source_date": "returnDateStr",
        "source_document_number": "sendNoStr",
        "cited_registration_text": "citeTmRegNo",
    },
    "OPPOSITION_DECISION": {
        "application_number": "applyNo",
        "registration_number": "regNo",
        "trademark_name": "tmName",
        "source_title": "adjuTitle",
        "source_date": "returnDateStr",
        "source_document_number": "snedNoStr",
        "cited_registration_text": "citeTms",
    },
    "REVIEW_ADJUDICATION": {
        "application_number": "applyNo",
        "registration_number": "regNo",
        "trademark_name": "tmName",
        "source_title": "fileTitle",
        "source_date": "judgeDateStr",
        "source_document_number": "sendDocNo",
        "cited_registration_text": "",
    },
}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    raise ValueError("CNIPA projected source fields must remain scalar")


def _observed_at(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("observed_at must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include timezone information")
    return parsed.astimezone(timezone.utc)


def _detail_uri(document_kind: str, source_record_id: str) -> str:
    from urllib.parse import quote

    return (
        "https://pub.sbj.cnipa.gov.cn"
        + _DETAIL_PATHS[document_kind]
        + "?id="
        + quote(source_record_id, safe="")
    )


def parse_cnipa_list_fact_projection(
    projection: Mapping[str, Any],
    *,
    detail_canonical_uri: str,
    observed_at: datetime | str,
    source_artifact_ref: str,
    initial_markdown_ref: str = "",
    initial_markdown_sha256: str = "",
) -> CnipaJudgmentListFact:
    if projection.get("schemaVersion") != _SCHEMA_VERSION:
        raise ValueError("unsupported CNIPA LIST fact projection schemaVersion")
    if projection.get("jurisdiction") != "CN" or projection.get("sourceAuthority") != "CNIPA":
        raise ValueError("CNIPA LIST fact projection authority/jurisdiction mismatch")

    document_kind = _text(projection.get("documentKind"), "documentKind").upper()
    if document_kind not in DOCUMENT_KINDS:
        raise ValueError(f"unsupported CNIPA document kind: {document_kind}")
    source_record_id = _text(projection.get("sourceRecordId"), "sourceRecordId")

    source_fields_raw = projection.get("sourceFields")
    if not isinstance(source_fields_raw, Mapping):
        raise ValueError("sourceFields must be an object")
    source_fields = dict(source_fields_raw)
    for value in source_fields.values():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("CNIPA projected source fields must remain scalar")

    identity_field = _ID_FIELDS[document_kind]
    if _optional_text(source_fields.get(identity_field)) != source_record_id:
        raise ValueError(
            f"sourceRecordId must match sourceFields.{identity_field}"
        )

    row_sha256 = _text(projection.get("sourceRowSha256"), "sourceRowSha256").lower()
    if not _SHA256.fullmatch(row_sha256):
        raise ValueError("sourceRowSha256 must be a lowercase SHA-256")

    expected_detail_uri = _detail_uri(document_kind, source_record_id)
    if detail_canonical_uri.strip() != expected_detail_uri:
        raise ValueError("detail_canonical_uri does not match CNIPA stable identity")

    markdown_sha = initial_markdown_sha256.strip().lower()
    if markdown_sha and not _SHA256.fullmatch(markdown_sha):
        raise ValueError("initial_markdown_sha256 must be a lowercase SHA-256")
    if bool(initial_markdown_ref.strip()) != bool(markdown_sha):
        raise ValueError("initial Markdown reference and SHA-256 must be supplied together")

    common = _COMMON_FIELDS[document_kind]
    values = {
        name: _optional_text(source_fields.get(field_name)) if field_name else ""
        for name, field_name in common.items()
    }
    semantic_family = (
        "EXAMINATION" if document_kind == "REGISTRATION_EXAMINATION" else "PROCEEDING"
    )

    return CnipaJudgmentListFact(
        document_kind=document_kind,
        source_record_id=source_record_id,
        semantic_family=semantic_family,
        detail_canonical_uri=expected_detail_uri,
        source_row_sha256=row_sha256,
        observed_at=_observed_at(observed_at),
        source_artifact_ref=_text(source_artifact_ref, "source_artifact_ref"),
        source_fields=source_fields,
        initial_markdown_ref=initial_markdown_ref.strip(),
        initial_markdown_sha256=markdown_sha,
        **values,
    )


def cnipa_fact_event_envelope(fact: CnipaJudgmentListFact) -> dict[str, Any]:
    payload = {
        "document_kind": fact.document_kind,
        "source_record_id": fact.source_record_id,
        "detail_canonical_uri": fact.detail_canonical_uri,
        "application_number": fact.application_number,
        "registration_number": fact.registration_number,
        "trademark_name": fact.trademark_name,
        "source_title": fact.source_title,
        "source_date": fact.source_date,
        "source_document_number": fact.source_document_number,
        "cited_registration_text": fact.cited_registration_text,
        "source_fields": dict(fact.source_fields),
        "initial_markdown_ref": fact.initial_markdown_ref,
        "initial_markdown_sha256": fact.initial_markdown_sha256,
    }
    return build_fact_event_envelope(
        jurisdiction="CN",
        resource_kind="FACT",
        semantic_family=fact.semantic_family,
        subject=SubjectRef(
            subject_type="CNIPA_JUDGMENT",
            subject_key=f"{fact.document_kind}:{fact.source_record_id}",
        ),
        provenance=ProvenanceRef(
            source_authority="CNIPA",
            source_domain="CNIPA_JUDGMENT_LIST",
            source_file=fact.source_artifact_ref,
            source_row_hash=fact.source_row_sha256,
        ),
        payload=payload,
        source_type=fact.document_kind,
        normalized_type=fact.document_kind,
        observed_at=fact.observed_at,
    )
