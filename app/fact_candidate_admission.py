from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Mapping

from app.temporal_relationship_contract import (
    TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
    RelationshipDerivationRef,
    RelationshipEvidenceRef,
    RelationshipResourceRef,
    build_temporal_relationship_edge,
)

FACT_CANDIDATE_CONTRACT_VERSION_V1 = "MARKORBIT_FACT_CANDIDATE_V1"
FACT_CANDIDATE_OBJECT_TYPE_V1 = "FACT_CANDIDATE"
FACT_TYPE_CITATION = "CITED_AS_REFERENCE_FOR_REFUSAL"

_CANDIDATE_ID = re.compile(r"^fac_[0-9A-HJKMNP-TV-Z]{26}$")
_SOURCE_ID = re.compile(r"^src_[0-9A-HJKMNP-TV-Z]{26}$")
_DOCUMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,127}$")
_RESOURCE_NUMBER = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,63}$")
_COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class FactCandidateAdmissionError(ValueError):
    """Raised when a Fact Candidate V1 cannot enter Data Engine fact truth."""


def _record(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FactCandidateAdmissionError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise FactCandidateAdmissionError(
            f"{label} fields are invalid; missing={missing}, unknown={unknown}"
        )


def _parse_instant(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _INSTANT.fullmatch(value):
        raise FactCandidateAdmissionError(f"{label} must be an RFC3339 UTC instant")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise FactCandidateAdmissionError(f"{label} must be a valid instant") from exc
    return value


def _parse_date(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise FactCandidateAdmissionError(f"{label} must be an ISO date")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise FactCandidateAdmissionError(f"{label} must be a valid date") from exc
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    return value


def _fingerprint_material(candidate: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "contractVersion",
        "objectType",
        "factType",
        "jurisdiction",
        "subject",
        "object",
        "eventDate",
        "effectiveDate",
        "sourceDocument",
        "evidenceLocator",
        "sourceAuthority",
        "extractionMethod",
        "confidence",
        "legalConclusion",
    )
    return {field: copy.deepcopy(candidate[field]) for field in fields}


def fact_candidate_fingerprint_sha256_v1(candidate: Mapping[str, Any]) -> str:
    material = _canonical(_fingerprint_material(candidate))
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_trademark_ref(value: Any, label: str) -> tuple[str | None, str | None]:
    ref = _record(value, label)
    _exact_keys(ref, {"resourceType", "applicationNumber", "registrationNumber"}, label)
    if ref["resourceType"] != "TRADEMARK":
        raise FactCandidateAdmissionError(f"{label}.resourceType must be TRADEMARK")
    app = ref["applicationNumber"]
    reg = ref["registrationNumber"]
    for name, item in (("applicationNumber", app), ("registrationNumber", reg)):
        if item is not None and (
            not isinstance(item, str) or not _RESOURCE_NUMBER.fullmatch(item)
        ):
            raise FactCandidateAdmissionError(f"{label}.{name} is invalid")
    if (app is None) == (reg is None):
        raise FactCandidateAdmissionError(
            f"{label} must carry exactly one applicationNumber or registrationNumber"
        )
    return app, reg


def _resource_id(jurisdiction: str, value: Mapping[str, Any], label: str) -> str:
    app, reg = _validate_trademark_ref(value, label)
    number = app if app is not None else reg
    assert number is not None
    return f"{jurisdiction}:{number}"


def _validate_source_document(value: Any) -> Mapping[str, Any]:
    doc = _record(value, "sourceDocument")
    _exact_keys(
        doc,
        {
            "owner",
            "sourceId",
            "documentId",
            "documentVersion",
            "documentSha256",
            "sourceUri",
        },
        "sourceDocument",
    )
    if doc["owner"] != "MARKORBIT_KNOWLEDGE":
        raise FactCandidateAdmissionError("sourceDocument.owner must be MARKORBIT_KNOWLEDGE")
    if not isinstance(doc["sourceId"], str) or not _SOURCE_ID.fullmatch(doc["sourceId"]):
        raise FactCandidateAdmissionError("sourceDocument.sourceId is invalid")
    if not isinstance(doc["documentId"], str) or not _DOCUMENT_ID.fullmatch(doc["documentId"]):
        raise FactCandidateAdmissionError("sourceDocument.documentId is invalid")
    if (
        not isinstance(doc["documentVersion"], int)
        or isinstance(doc["documentVersion"], bool)
        or doc["documentVersion"] <= 0
    ):
        raise FactCandidateAdmissionError("sourceDocument.documentVersion must be positive integer")
    if not isinstance(doc["documentSha256"], str) or not _SHA256.fullmatch(
        doc["documentSha256"]
    ):
        raise FactCandidateAdmissionError("sourceDocument.documentSha256 is invalid")
    if not isinstance(doc["sourceUri"], str) or not doc["sourceUri"].startswith("https://"):
        raise FactCandidateAdmissionError("sourceDocument.sourceUri must be official https URI")
    return doc

def _validate_locator(value: Any) -> Mapping[str, Any]:
    locator = _record(value, "evidenceLocator")
    common = {"locatorType", "startOffset", "endOffset", "textSha256"}
    if locator.get("locatorType") == "PAGE_TEXT_RANGE":
        _exact_keys(locator, common | {"pageNumber"}, "evidenceLocator")
        page = locator["pageNumber"]
        if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
            raise FactCandidateAdmissionError("evidenceLocator.pageNumber must be positive integer")
    elif locator.get("locatorType") == "CANONICAL_TEXT_RANGE":
        _exact_keys(locator, common, "evidenceLocator")
    else:
        raise FactCandidateAdmissionError("unsupported evidenceLocator.locatorType")
    start = locator["startOffset"]
    end = locator["endOffset"]
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or start < 0
        or not isinstance(end, int)
        or isinstance(end, bool)
        or end <= start
    ):
        raise FactCandidateAdmissionError("evidenceLocator offsets are invalid")
    if not isinstance(locator["textSha256"], str) or not _SHA256.fullmatch(
        locator["textSha256"]
    ):
        raise FactCandidateAdmissionError("evidenceLocator.textSha256 is invalid")
    return locator


def _locator_string(locator: Mapping[str, Any]) -> str:
    if locator["locatorType"] == "PAGE_TEXT_RANGE":
        return (
            f"PAGE_TEXT_RANGE:page={locator['pageNumber']};"
            f"start={locator['startOffset']};end={locator['endOffset']};"
            f"text_sha256={locator['textSha256']}"
        )
    return (
        f"CANONICAL_TEXT_RANGE:start={locator['startOffset']};"
        f"end={locator['endOffset']};text_sha256={locator['textSha256']}"
    )


def _validate_extraction(value: Any) -> Mapping[str, Any]:
    method = _record(value, "extractionMethod")
    _exact_keys(method, {"owner", "methodId", "methodVersion"}, "extractionMethod")
    if method["owner"] != "MARKORBIT_BRAIN_METHOD":
        raise FactCandidateAdmissionError(
            "extractionMethod.owner must be MARKORBIT_BRAIN_METHOD"
        )
    if not isinstance(method["methodId"], str) or not _COMPONENT_ID.fullmatch(
        method["methodId"]
    ):
        raise FactCandidateAdmissionError("extractionMethod.methodId is invalid")
    if not isinstance(method["methodVersion"], str) or not _SEMVER.fullmatch(
        method["methodVersion"]
    ):
        raise FactCandidateAdmissionError("extractionMethod.methodVersion is invalid")
    return method


def _validate_confidence(value: Any) -> None:
    confidence = _record(value, "confidence")
    _exact_keys(confidence, {"scoreBasisPoints", "evidenceLevel"}, "confidence")
    score = confidence["scoreBasisPoints"]
    if (
        not isinstance(score, int)
        or isinstance(score, bool)
        or score < 0
        or score > 10_000
    ):
        raise FactCandidateAdmissionError("confidence.scoreBasisPoints is invalid")
    if confidence["evidenceLevel"] != "EXPLICIT_DOCUMENT_TEXT":
        raise FactCandidateAdmissionError(
            "citation admission requires EXPLICIT_DOCUMENT_TEXT"
        )


def _validate_ingestion(value: Any, produced_at: str) -> Mapping[str, Any]:
    ingestion = _record(value, "ingestion")
    _exact_keys(ingestion, {"status", "validation", "admission"}, "ingestion")
    if ingestion["status"] != "VALIDATED" or ingestion["admission"] is not None:
        raise FactCandidateAdmissionError(
            "Data Engine admission requires VALIDATED candidate with no prior admission"
        )
    validation = _record(ingestion["validation"], "ingestion.validation")
    _exact_keys(
        validation,
        {"outcome", "validatorId", "validatorVersion", "decidedAt", "reasonCode"},
        "ingestion.validation",
    )
    if validation["outcome"] != "ACCEPTED" or validation["reasonCode"] is not None:
        raise FactCandidateAdmissionError("candidate validation must be ACCEPTED")
    if not isinstance(validation["validatorId"], str) or not _COMPONENT_ID.fullmatch(
        validation["validatorId"]
    ):
        raise FactCandidateAdmissionError("validatorId is invalid")
    if not isinstance(validation["validatorVersion"], str) or not _SEMVER.fullmatch(
        validation["validatorVersion"]
    ):
        raise FactCandidateAdmissionError("validatorVersion is invalid")
    decided_at = _parse_instant(validation["decidedAt"], "ingestion.validation.decidedAt")
    if datetime.fromisoformat(decided_at[:-1] + "+00:00") < datetime.fromisoformat(
        produced_at[:-1] + "+00:00"
    ):
        raise FactCandidateAdmissionError("validation cannot precede candidate production")
    return validation


def _validate_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(
        candidate,
        {
            "contractVersion",
            "objectType",
            "candidateId",
            "factType",
            "jurisdiction",
            "subject",
            "object",
            "eventDate",
            "effectiveDate",
            "sourceDocument",
            "evidenceLocator",
            "sourceAuthority",
            "extractionMethod",
            "confidence",
            "candidateFingerprintSha256",
            "ingestion",
            "producedAt",
            "legalConclusion",
        },
        "candidate",
    )
    if candidate["contractVersion"] != FACT_CANDIDATE_CONTRACT_VERSION_V1:
        raise FactCandidateAdmissionError("unsupported Fact Candidate contract version")
    if candidate["objectType"] != FACT_CANDIDATE_OBJECT_TYPE_V1:
        raise FactCandidateAdmissionError("objectType must be FACT_CANDIDATE")
    if not isinstance(candidate["candidateId"], str) or not _CANDIDATE_ID.fullmatch(
        candidate["candidateId"]
    ):
        raise FactCandidateAdmissionError("candidateId is invalid")
    if candidate["factType"] != FACT_TYPE_CITATION:
        raise FactCandidateAdmissionError("unsupported factType")
    if candidate["jurisdiction"] not in {"US", "CN"}:
        raise FactCandidateAdmissionError("unsupported jurisdiction")
    _validate_trademark_ref(candidate["subject"], "subject")
    _validate_trademark_ref(candidate["object"], "object")
    _parse_date(candidate["eventDate"], "eventDate")
    if candidate["effectiveDate"] is not None:
        _parse_date(candidate["effectiveDate"], "effectiveDate")
    _validate_source_document(candidate["sourceDocument"])
    _validate_locator(candidate["evidenceLocator"])
    if candidate["sourceAuthority"] != "DIRECT_OFFICIAL":
        raise FactCandidateAdmissionError("citation sourceAuthority must be DIRECT_OFFICIAL")
    _validate_extraction(candidate["extractionMethod"])
    _validate_confidence(candidate["confidence"])
    if candidate["legalConclusion"] is not False:
        raise FactCandidateAdmissionError("legalConclusion must be false")
    produced_at = _parse_instant(candidate["producedAt"], "producedAt")
    _validate_ingestion(candidate["ingestion"], produced_at)
    actual = fact_candidate_fingerprint_sha256_v1(candidate)
    if candidate["candidateFingerprintSha256"] != actual:
        raise FactCandidateAdmissionError("candidate fingerprint does not reproduce")
    return copy.deepcopy(dict(candidate))

def admit_fact_candidate_v1(
    candidate: Mapping[str, Any],
    *,
    decided_at: str,
) -> dict[str, Any]:
    accepted = _validate_candidate(candidate)
    admitted_at = _parse_instant(decided_at, "decided_at")
    validation_at = accepted["ingestion"]["validation"]["decidedAt"]
    if datetime.fromisoformat(admitted_at[:-1] + "+00:00") < datetime.fromisoformat(
        validation_at[:-1] + "+00:00"
    ):
        raise FactCandidateAdmissionError("Data Engine admission cannot precede validation")

    jurisdiction = accepted["jurisdiction"]
    document = accepted["sourceDocument"]
    locator = accepted["evidenceLocator"]
    extraction = accepted["extractionMethod"]
    subject_id = _resource_id(jurisdiction, accepted["subject"], "subject")
    object_id = _resource_id(jurisdiction, accepted["object"], "object")

    edge = build_temporal_relationship_edge(
        jurisdiction=jurisdiction,
        relationship_type=FACT_TYPE_CITATION,
        source=RelationshipResourceRef("TRADEMARK", subject_id),
        target=RelationshipResourceRef("TRADEMARK", object_id),
        authority_level="DIRECT_OFFICIAL",
        evidence=RelationshipEvidenceRef(
            evidence_kind="OFFICIAL_DOCUMENT",
            source_authority="USPTO" if jurisdiction == "US" else "CNIPA",
            source_domain="US_TSDR" if jurisdiction == "US" else "CNIPA_DOCUMENT",
            source_record_id=subject_id,
            source_package_id=document["sourceId"],
            source_uri=document["sourceUri"],
            source_hash=f"sha256:{document['documentSha256']}",
            source_document_id=document["documentId"],
            source_document_version=f"v{document['documentVersion']}",
            evidence_locator=_locator_string(locator),
        ),
        observed_at=admitted_at,
        event_at=accepted["eventDate"],
        is_current=False,
        derivation=RelationshipDerivationRef(
            "DOCUMENT_EXTRACTION",
            extraction["methodId"],
            extraction["methodVersion"],
        ),
    )

    admission = {
        "outcome": "ADMITTED",
        "dataEngineFactId": edge["edge_id"],
        "dataEngineContractVersion": TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
        "decidedAt": admitted_at,
        "reasonCode": None,
    }
    admitted_candidate = copy.deepcopy(accepted)
    admitted_candidate["ingestion"] = {
        "status": "ADMITTED",
        "validation": copy.deepcopy(accepted["ingestion"]["validation"]),
        "admission": admission,
    }

    return {
        "candidate": admitted_candidate,
        "admission": admission,
        "fact": edge,
    }
