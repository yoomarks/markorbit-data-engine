from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping
import uuid

ADMISSION_CONTRACT_VERSION = "CN_CITATION_RELATION_ADMISSION_V1"
TARGET_TABLE = "markorbit_facts.cn_admitted_citation_relation"
_FACT_TYPE = "CITED_AS_REFERENCE_FOR_REFUSAL"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CANDIDATE_ID = re.compile(r"^fac_[0-9A-HJKMNP-TV-Z]{26}$")
_SOURCE_ID = re.compile(r"^src_[0-9A-HJKMNP-TV-Z]{26}$")
_DOCUMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,127}$")
_RESOURCE_NUMBER = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,63}$")
_COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class CitationRelationAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class CitationRelationAdmissionReceipt:
    data_engine_fact_id: str
    data_engine_contract_version: str
    replayed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "ADMITTED",
            "data_engine_fact_id": self.data_engine_fact_id,
            "data_engine_contract_version": self.data_engine_contract_version,
            "replayed": self.replayed,
        }


def _record(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CitationRelationAdmissionError(f"{label} must be an object")
    return value


def _exact(item: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(item) != keys:
        raise CitationRelationAdmissionError(f"{label} shape is invalid")


def _text(value: Any, label: str, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CitationRelationAdmissionError(f"{label} is invalid")
    return value.strip()


def _pattern(value: Any, pattern: re.Pattern[str], label: str, maximum: int = 1000) -> str:
    text = _text(value, label, maximum)
    if not pattern.fullmatch(text):
        raise CitationRelationAdmissionError(f"{label} is invalid")
    return text


def _instant(value: Any, label: str) -> datetime:
    text = _pattern(value, _INSTANT, label, 64)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CitationRelationAdmissionError(f"{label} is invalid") from exc


def _sha(value: Any, label: str) -> str:
    text = _text(value, label, 64)
    if not _SHA256.fullmatch(text):
        raise CitationRelationAdmissionError(f"{label} must be lowercase SHA-256")
    return text


def _trademark_ref(value: Any, label: str) -> tuple[str, str]:
    item = _record(value, label)
    _exact(item, {"resourceType", "applicationNumber", "registrationNumber"}, label)
    if item.get("resourceType") != "TRADEMARK":
        raise CitationRelationAdmissionError(f"{label}.resourceType must be TRADEMARK")
    application = item.get("applicationNumber")
    registration = item.get("registrationNumber")
    if application is not None:
        application = _pattern(
            application, _RESOURCE_NUMBER, f"{label}.applicationNumber", 64
        )
    if registration is not None:
        registration = _pattern(
            registration, _RESOURCE_NUMBER, f"{label}.registrationNumber", 64
        )
    if application is None and registration is None:
        raise CitationRelationAdmissionError(
            f"{label} requires an application or registration number"
        )
    return str(application or ""), str(registration or "")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint_material(candidate: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "contractVersion", "objectType", "factType", "jurisdiction", "subject",
        "object", "eventDate", "effectiveDate", "sourceDocument", "evidenceLocator",
        "sourceAuthority", "extractionMethod", "confidence", "legalConclusion",
    )
    return {key: candidate[key] for key in keys}


def _evidence_locator(value: Any) -> str:
    item = _record(value, "evidenceLocator")
    locator_type = item.get("locatorType")
    common = {"locatorType", "startOffset", "endOffset", "textSha256"}
    if locator_type == "PAGE_TEXT_RANGE":
        _exact(item, common | {"pageNumber"}, "evidenceLocator")
        if not isinstance(item.get("pageNumber"), int) or item["pageNumber"] <= 0:
            raise CitationRelationAdmissionError("evidenceLocator.pageNumber is invalid")
    elif locator_type == "CANONICAL_TEXT_RANGE":
        _exact(item, common, "evidenceLocator")
    else:
        raise CitationRelationAdmissionError("evidenceLocator.locatorType is invalid")
    start = item.get("startOffset")
    end = item.get("endOffset")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or start < 0
        or not isinstance(end, int)
        or isinstance(end, bool)
        or end <= start
    ):
        raise CitationRelationAdmissionError("evidenceLocator range is invalid")
    _sha(item.get("textSha256"), "evidenceLocator.textSha256")
    return _canonical(item)


def _validate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "contractVersion", "objectType", "candidateId", "factType", "jurisdiction",
        "subject", "object", "eventDate", "effectiveDate", "sourceDocument",
        "evidenceLocator", "sourceAuthority", "extractionMethod", "confidence",
        "candidateFingerprintSha256", "ingestion", "producedAt", "legalConclusion",
    }
    _exact(candidate, required, "Fact Candidate")
    if candidate["contractVersion"] != "MARKORBIT_FACT_CANDIDATE_V1":
        raise CitationRelationAdmissionError("unsupported Fact Candidate contract")
    candidate_id = _pattern(candidate["candidateId"], _CANDIDATE_ID, "candidateId", 64)
    if candidate["objectType"] != "FACT_CANDIDATE" or candidate["factType"] != _FACT_TYPE:
        raise CitationRelationAdmissionError("unsupported fact type")
    if candidate["jurisdiction"] != "CN":
        raise CitationRelationAdmissionError("only CN citation candidates are supported")
    if candidate["sourceAuthority"] != "DIRECT_OFFICIAL" or candidate["legalConclusion"] is not False:
        raise CitationRelationAdmissionError("candidate authority semantics are invalid")

    ingestion = _record(candidate["ingestion"], "ingestion")
    _exact(ingestion, {"status", "validation", "admission"}, "ingestion")
    if ingestion.get("status") != "VALIDATED":
        raise CitationRelationAdmissionError("candidate must be VALIDATED and accepted")
    validation = _record(ingestion.get("validation"), "ingestion.validation")
    _exact(
        validation,
        {"outcome", "validatorId", "validatorVersion", "decidedAt", "reasonCode"},
        "ingestion.validation",
    )
    if validation.get("outcome") != "ACCEPTED" or validation.get("reasonCode") is not None:
        raise CitationRelationAdmissionError("candidate must be VALIDATED and accepted")
    _pattern(
        validation.get("validatorId"), _COMPONENT_ID, "ingestion.validation.validatorId", 128
    )
    _pattern(
        validation.get("validatorVersion"), _SEMVER, "ingestion.validation.validatorVersion", 128
    )
    validation_decided_at = _instant(
        validation.get("decidedAt"), "ingestion.validation.decidedAt"
    )
    if ingestion.get("admission") is not None:
        raise CitationRelationAdmissionError("candidate is already admission-finalized")

    produced_at = _instant(candidate["producedAt"], "producedAt")
    if validation_decided_at < produced_at:
        raise CitationRelationAdmissionError("validation cannot predate candidate production")
    effective_date = candidate["effectiveDate"]
    if effective_date is not None:
        effective_date = _text(effective_date, "effectiveDate", 10)
        try:
            date.fromisoformat(effective_date)
        except ValueError as exc:
            raise CitationRelationAdmissionError("effectiveDate must use a real YYYY-MM-DD date") from exc

    subject_app, subject_reg = _trademark_ref(candidate["subject"], "subject")
    object_app, object_reg = _trademark_ref(candidate["object"], "object")
    event_date_text = _text(candidate["eventDate"], "eventDate", 10)
    try:
        date.fromisoformat(event_date_text)
    except ValueError as exc:
        raise CitationRelationAdmissionError("eventDate must use a real YYYY-MM-DD date") from exc

    source = _record(candidate["sourceDocument"], "sourceDocument")
    _exact(
        source,
        {"owner", "sourceId", "documentId", "documentVersion", "documentSha256", "sourceUri"},
        "sourceDocument",
    )
    if source.get("owner") != "MARKORBIT_KNOWLEDGE":
        raise CitationRelationAdmissionError("sourceDocument owner must be MARKORBIT_KNOWLEDGE")
    source_id = _pattern(source.get("sourceId"), _SOURCE_ID, "sourceDocument.sourceId", 128)
    document_id = _pattern(
        source.get("documentId"), _DOCUMENT_ID, "sourceDocument.documentId", 128
    )
    document_version = source.get("documentVersion")
    if (
        not isinstance(document_version, int)
        or isinstance(document_version, bool)
        or document_version <= 0
    ):
        raise CitationRelationAdmissionError("sourceDocument.documentVersion must be positive")
    document_sha = _sha(source.get("documentSha256"), "sourceDocument.documentSha256")
    source_uri = _text(source.get("sourceUri"), "sourceDocument.sourceUri", 4000)
    if not source_uri.startswith("https://"):
        raise CitationRelationAdmissionError("sourceDocument.sourceUri must be HTTPS")

    method = _record(candidate["extractionMethod"], "extractionMethod")
    _exact(method, {"owner", "methodId", "methodVersion"}, "extractionMethod")
    if method.get("owner") != "MARKORBIT_BRAIN_METHOD":
        raise CitationRelationAdmissionError("extractionMethod owner is invalid")
    method_id = _pattern(
        method.get("methodId"), _COMPONENT_ID, "extractionMethod.methodId", 128
    )
    method_version = _pattern(
        method.get("methodVersion"), _SEMVER, "extractionMethod.methodVersion", 128
    )

    confidence = _record(candidate["confidence"], "confidence")
    _exact(confidence, {"scoreBasisPoints", "evidenceLevel"}, "confidence")
    score = confidence.get("scoreBasisPoints")
    if (
        not isinstance(score, int)
        or isinstance(score, bool)
        or not 0 <= score <= 10_000
    ):
        raise CitationRelationAdmissionError("confidence score is invalid")
    if confidence.get("evidenceLevel") != "EXPLICIT_DOCUMENT_TEXT":
        raise CitationRelationAdmissionError("confidence evidence level is invalid")
    evidence_locator_json = _evidence_locator(candidate["evidenceLocator"])
    fingerprint = _sha(candidate["candidateFingerprintSha256"], "candidateFingerprintSha256")
    expected = sha256(_canonical(_fingerprint_material(candidate)).encode("utf-8")).hexdigest()
    if fingerprint != expected:
        raise CitationRelationAdmissionError("candidate fingerprint mismatch")

    return {
        "candidate_id": candidate_id,
        "fingerprint": fingerprint,
        "subject_application_number": subject_app,
        "subject_registration_number": subject_reg,
        "object_application_number": object_app,
        "object_registration_number": object_reg,
        "event_date": event_date_text,
        "source_id": source_id,
        "document_id": document_id,
        "document_version": document_version,
        "document_sha256": document_sha,
        "source_uri": source_uri,
        "evidence_locator_json": evidence_locator_json,
        "method_id": method_id,
        "method_version": method_version,
        "confidence_score_basis_points": score,
    }


def _fact_id(fingerprint: str) -> str:
    return f"cn_citation_{fingerprint[:32]}"


def _relation_uuid(fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"markorbit:cn:citation:{fingerprint}")


def admit_cn_citation_relation(
    candidate: Mapping[str, Any],
    *,
    client: Any,
    now: datetime | None = None,
) -> CitationRelationAdmissionReceipt:
    item = _validate(candidate)
    existing = client.query(
        f"""
        SELECT candidate_id, candidate_fingerprint_sha256
        FROM {TARGET_TABLE} FINAL
        WHERE candidate_fingerprint_sha256 = %(fingerprint)s
           OR candidate_id = %(candidate_id)s
        LIMIT 2
        """,
        parameters={
            "fingerprint": item["fingerprint"],
            "candidate_id": item["candidate_id"],
        },
    ).result_rows
    for candidate_id, fingerprint in existing:
        if str(fingerprint) == item["fingerprint"]:
            return CitationRelationAdmissionReceipt(
                _fact_id(item["fingerprint"]),
                ADMISSION_CONTRACT_VERSION,
                True,
            )
        if str(candidate_id) == item["candidate_id"]:
            raise CitationRelationAdmissionError("candidate identity fingerprint conflict")

    admitted_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    relation_id = _relation_uuid(item["fingerprint"])
    record_hash = sha256(
        _canonical({**item, "relation_id": str(relation_id)}).encode("utf-8")
    ).hexdigest()
    row = [
        relation_id,
        item["candidate_id"],
        item["fingerprint"],
        _FACT_TYPE,
        item["subject_application_number"],
        item["subject_registration_number"],
        item["object_application_number"],
        item["object_registration_number"],
        date.fromisoformat(item["event_date"]),
        item["source_id"],
        item["document_id"],
        item["document_version"],
        item["document_sha256"],
        item["source_uri"],
        item["evidence_locator_json"],
        item["method_id"],
        item["method_version"],
        item["confidence_score_basis_points"],
        admitted_at,
        record_hash,
    ]
    client.insert(
        TARGET_TABLE,
        [row],
        column_names=[
            "relation_id", "candidate_id", "candidate_fingerprint_sha256", "relation_type",
            "subject_application_number", "subject_registration_number",
            "object_application_number", "object_registration_number", "event_date",
            "source_id", "source_document_id", "source_document_version",
            "source_document_sha256", "source_uri", "evidence_locator_json",
            "extraction_method_id", "extraction_method_version",
            "confidence_score_basis_points", "admitted_at", "record_hash",
        ],
    )
    return CitationRelationAdmissionReceipt(
        _fact_id(item["fingerprint"]),
        ADMISSION_CONTRACT_VERSION,
        False,
    )
