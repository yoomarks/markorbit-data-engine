from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping


TEMPORAL_RELATIONSHIP_CONTRACT_VERSION = "MARKORBIT_TEMPORAL_RELATIONSHIP_V1"

RESOURCE_TYPES = (
    "TRADEMARK",
    "ENTITY",
    "APPLICANT",
    "OWNER",
    "AGENT",
    "ATTORNEY",
    "CORRESPONDENT",
    "ASSIGNMENT",
    "PROCEEDING",
    "EVENT",
    "GOODS",
    "CLASS",
    "CITATION",
    "REFERENCE",
)

RELATIONSHIP_TYPES = (
    "CURRENT_OWNER",
    "FORMER_OWNER",
    "CURRENT_APPLICANT",
    "FORMER_APPLICANT",
    "CURRENT_AGENT",
    "FORMER_AGENT",
    "ATTORNEY",
    "CORRESPONDENT",
    "ASSIGNOR",
    "ASSIGNEE",
    "ASSIGNMENT_PROPERTY",
    "OPPOSITION_PLAINTIFF",
    "OPPOSITION_DEFENDANT",
    "CANCELLATION_PETITIONER",
    "CANCELLATION_RESPONDENT",
    "EX_PARTE_APPEAL_PARTY",
    "PROCEEDING_PROPERTY",
    "TRADEMARK_GOODS",
    "TRADEMARK_CLASS",
    "CITED_AS_REFERENCE_FOR_REFUSAL",
)

AUTHORITY_LEVELS = (
    "DIRECT_OFFICIAL",
    "DERIVED_FROM_OFFICIAL_HISTORY",
    "INFERRED",
)

EVIDENCE_KINDS = ("STRUCTURED_SOURCE", "OFFICIAL_DOCUMENT")
DERIVATION_KINDS = (
    "DIRECT_MAPPING",
    "HISTORY_DERIVATION",
    "DOCUMENT_EXTRACTION",
    "METHOD_INFERENCE",
)
QUERY_SCOPES = ("current", "historical", "all")


class TemporalRelationshipContractError(ValueError):
    """Raised when an edge would violate the frozen V1 relationship contract."""


def _text(value: Any, label: str, *, max_length: int = 2_048) -> str:
    if not isinstance(value, str):
        raise TemporalRelationshipContractError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise TemporalRelationshipContractError(f"{label} is required")
    if len(normalized) > max_length:
        raise TemporalRelationshipContractError(f"{label} exceeds maximum length {max_length}")
    return normalized


def _optional_text(value: Any, label: str, *, max_length: int = 2_048) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_length=max_length)


def _enum(value: Any, label: str, allowed: tuple[str, ...]) -> str:
    normalized = _text(value, label, max_length=128).upper()
    if normalized not in allowed:
        raise TemporalRelationshipContractError(f"unsupported {label}: {value}")
    return normalized


def _temporal(value: Any, label: str, *, required: bool = False) -> str | None:
    normalized = _optional_text(value, label, max_length=64)
    if normalized is None:
        if required:
            raise TemporalRelationshipContractError(f"{label} is required")
        return None
    candidate = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        if "T" in candidate:
            datetime.fromisoformat(candidate)
        else:
            date.fromisoformat(candidate)
    except ValueError as exc:
        raise TemporalRelationshipContractError(
            f"{label} must be an ISO 8601 date or timestamp"
        ) from exc
    return normalized


def _observed_timestamp(value: Any) -> str:
    normalized = _temporal(value, "observed_at", required=True)
    if normalized is None or "T" not in normalized or not normalized.endswith("Z"):
        raise TemporalRelationshipContractError(
            "observed_at must be an RFC 3339 UTC timestamp ending in Z"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class RelationshipResourceRef:
    resource_type: str
    resource_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "resource_type": _enum(self.resource_type, "resource_type", RESOURCE_TYPES),
            "resource_id": _text(self.resource_id, "resource_id", max_length=512),
        }


@dataclass(frozen=True, slots=True)
class RelationshipEvidenceRef:
    evidence_kind: str
    source_authority: str
    source_domain: str
    source_record_id: str | None = None
    source_package_id: str | None = None
    source_uri: str | None = None
    source_hash: str | None = None
    source_document_id: str | None = None
    source_document_version: str | None = None
    evidence_locator: str | None = None

    def as_dict(self) -> dict[str, Any]:
        evidence = {
            "evidence_kind": _enum(self.evidence_kind, "evidence_kind", EVIDENCE_KINDS),
            "source_authority": _text(self.source_authority, "source_authority"),
            "source_domain": _text(self.source_domain, "source_domain").upper(),
            "source_record_id": _optional_text(
                self.source_record_id, "source_record_id", max_length=512
            ),
            "source_package_id": _optional_text(
                self.source_package_id, "source_package_id", max_length=512
            ),
            "source_uri": _optional_text(self.source_uri, "source_uri", max_length=4_096),
            "source_hash": _optional_text(self.source_hash, "source_hash", max_length=256),
            "source_document_id": _optional_text(
                self.source_document_id, "source_document_id", max_length=512
            ),
            "source_document_version": _optional_text(
                self.source_document_version, "source_document_version", max_length=512
            ),
            "evidence_locator": _optional_text(
                self.evidence_locator, "evidence_locator", max_length=2_048
            ),
        }
        if not any(
            evidence[field]
            for field in ("source_record_id", "source_package_id", "source_document_id")
        ):
            raise TemporalRelationshipContractError(
                "evidence requires source_record_id, source_package_id, or source_document_id"
            )
        if evidence["evidence_kind"] == "OFFICIAL_DOCUMENT" and not all(
            evidence[field]
            for field in (
                "source_document_id",
                "source_document_version",
                "evidence_locator",
            )
        ):
            raise TemporalRelationshipContractError(
                "OFFICIAL_DOCUMENT evidence requires document id, version, and locator"
            )
        return evidence


@dataclass(frozen=True, slots=True)
class RelationshipDerivationRef:
    derivation_kind: str
    identity: str
    version: str

    def as_dict(self) -> dict[str, str]:
        return {
            "derivation_kind": _enum(self.derivation_kind, "derivation_kind", DERIVATION_KINDS),
            "identity": _text(self.identity, "derivation identity", max_length=512),
            "version": _text(self.version, "derivation version", max_length=256),
        }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fingerprint(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_temporal_relationship_edge(
    *,
    jurisdiction: str,
    relationship_type: str,
    source: RelationshipResourceRef,
    target: RelationshipResourceRef,
    authority_level: str,
    evidence: RelationshipEvidenceRef,
    observed_at: str,
    is_current: bool,
    valid_from: str | None = None,
    valid_to: str | None = None,
    event_at: str | None = None,
    derivation: RelationshipDerivationRef | None = None,
) -> dict[str, Any]:
    if type(is_current) is not bool:
        raise TemporalRelationshipContractError("is_current must be a boolean")

    relation = _enum(relationship_type, "relationship_type", RELATIONSHIP_TYPES)
    authority = _enum(authority_level, "authority_level", AUTHORITY_LEVELS)
    source_ref = source.as_dict()
    target_ref = target.as_dict()
    evidence_ref = evidence.as_dict()
    derivation_ref = derivation.as_dict() if derivation is not None else None
    temporal = {
        "valid_from": _temporal(valid_from, "valid_from"),
        "valid_to": _temporal(valid_to, "valid_to"),
        "observed_at": _observed_timestamp(observed_at),
        "event_at": _temporal(event_at, "event_at"),
        "is_current": is_current,
    }
    if is_current and temporal["valid_to"] is not None:
        raise TemporalRelationshipContractError("a current relationship must not have valid_to")

    derivation_kind = derivation_ref["derivation_kind"] if derivation_ref else None
    if authority == "DIRECT_OFFICIAL" and derivation_kind not in {
        None,
        "DIRECT_MAPPING",
        "DOCUMENT_EXTRACTION",
    }:
        raise TemporalRelationshipContractError(
            "DIRECT_OFFICIAL permits only DIRECT_MAPPING or DOCUMENT_EXTRACTION identity"
        )
    if authority == "DERIVED_FROM_OFFICIAL_HISTORY" and derivation_kind != "HISTORY_DERIVATION":
        raise TemporalRelationshipContractError(
            "DERIVED_FROM_OFFICIAL_HISTORY requires HISTORY_DERIVATION identity"
        )
    if authority == "INFERRED" and derivation_kind != "METHOD_INFERENCE":
        raise TemporalRelationshipContractError("INFERRED requires METHOD_INFERENCE identity")
    if relation == "CITED_AS_REFERENCE_FOR_REFUSAL":
        if authority != "DIRECT_OFFICIAL":
            raise TemporalRelationshipContractError(
                "citation relationship requires DIRECT_OFFICIAL authority"
            )
        if evidence_ref["evidence_kind"] != "OFFICIAL_DOCUMENT":
            raise TemporalRelationshipContractError(
                "citation relationship requires direct OFFICIAL_DOCUMENT evidence"
            )
        if derivation_kind != "DOCUMENT_EXTRACTION":
            raise TemporalRelationshipContractError(
                "citation relationship requires versioned DOCUMENT_EXTRACTION identity"
            )

    if relation.startswith("CURRENT_") and not is_current:
        raise TemporalRelationshipContractError(f"{relation} requires is_current=true")
    if relation.startswith("FORMER_") and is_current:
        raise TemporalRelationshipContractError(f"{relation} requires is_current=false")

    body = {
        "contract_version": TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
        "jurisdiction": _text(jurisdiction, "jurisdiction", max_length=32).upper(),
        "relationship_type": relation,
        "source": source_ref,
        "target": target_ref,
        "temporal": temporal,
        "evidence": evidence_ref,
        "provenance": {
            "authority_level": authority,
            "derivation": derivation_ref,
        },
        "serving_authority": (
            "METHOD_OUTPUT_ONLY" if authority == "INFERRED" else "DATA_ENGINE_FACT_READ_MODEL"
        ),
        "legal_conclusion": False,
    }
    fingerprint = _fingerprint(body)
    return {
        **body,
        "edge_id": f"rel_{fingerprint.removeprefix('sha256:')[:32]}",
        "fingerprint": fingerprint,
    }


def temporal_relationship_contract() -> dict[str, Any]:
    return {
        "contract_version": TEMPORAL_RELATIONSHIP_CONTRACT_VERSION,
        "resource_types": list(RESOURCE_TYPES),
        "relationship_types": list(RELATIONSHIP_TYPES),
        "authority_levels": list(AUTHORITY_LEVELS),
        "evidence_kinds": list(EVIDENCE_KINDS),
        "derivation_kinds": list(DERIVATION_KINDS),
        "query": {
            "scopes": list(QUERY_SCOPES),
            "scope_semantics": {
                "current": "is_current=true",
                "historical": "is_current=false",
                "all": "current and historical edges",
            },
            "pagination": "KEYSET_CURSOR_BOUND_TO_QUERY_AND_SNAPSHOT",
            "unsupported_filter_behavior": "CAPABILITY_ERROR_FAIL_CLOSED",
            "arbitrary_sql": False,
        },
        "admission": {
            "direct_official_fact": "DATA_ENGINE_FACT_READ_MODEL",
            "derived_official_history_fact": "DATA_ENGINE_FACT_READ_MODEL",
            "inferred": "METHOD_OUTPUT_ONLY_NOT_DATA_ENGINE_FACT_TRUTH",
            "citation_requires_direct_official_document": True,
            "refusal_event_alone_is_citation_evidence": False,
        },
        "ownership": {
            "documents": "MARKORBIT_KNOWLEDGE",
            "facts": "MARKORBIT_DATA_ENGINE",
            "inference_methods": "BRAIN_METHOD",
            "method_execution": "CAPABILITY",
            "business_state": "PRODUCT_OR_WORKSPACE",
        },
        "legal_conclusion": False,
        "consumer_writeback": False,
    }
