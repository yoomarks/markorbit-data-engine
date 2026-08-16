from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping


FACT_EVENT_ENVELOPE_VERSION = "MARKORBIT_FACT_EVENT_ENVELOPE_V1"
RESOURCE_KINDS = ("FACT", "EVENT", "RELATION", "SNAPSHOT")
SEMANTIC_FAMILIES = (
    "APPLICATION",
    "EXAMINATION",
    "PUBLICATION",
    "REGISTRATION",
    "GOODS_SERVICES",
    "PARTY",
    "REPRESENTATION",
    "PRIORITY",
    "MADRID",
    "OWNERSHIP_RECORDATION",
    "MAINTENANCE_RENEWAL",
    "PROCEEDING",
    "CASE_RELATION",
    "SOURCE_QUALITY",
    "OTHER",
)
NORMALIZATION_SEMANTICS = (
    "NAVIGATION_GROUPING_ONLY_NOT_CROSS_JURISDICTION_LEGAL_EQUIVALENCE"
)


@dataclass(frozen=True)
class SubjectRef:
    subject_type: str
    subject_key: str

    def as_dict(self) -> dict[str, str]:
        subject_type = self.subject_type.strip().upper()
        subject_key = self.subject_key.strip()
        if not subject_type:
            raise ValueError("subject_type is required")
        if not subject_key:
            raise ValueError("subject_key is required")
        return {"subject_type": subject_type, "subject_key": subject_key}


@dataclass(frozen=True)
class ProvenanceRef:
    source_authority: str
    source_domain: str
    source_package_id: str = ""
    source_rank: int | None = None
    source_effective_at: date | datetime | str | None = None
    source_file: str = ""
    source_start_line: int | None = None
    source_end_line: int | None = None
    source_row_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        source_authority = self.source_authority.strip()
        source_domain = self.source_domain.strip().upper()
        if not source_authority:
            raise ValueError("source_authority is required")
        if not source_domain:
            raise ValueError("source_domain is required")
        if self.source_rank is not None and self.source_rank < 0:
            raise ValueError("source_rank must be non-negative")
        if self.source_start_line is not None and self.source_start_line < 0:
            raise ValueError("source_start_line must be non-negative")
        if self.source_end_line is not None and self.source_end_line < 0:
            raise ValueError("source_end_line must be non-negative")
        if (
            self.source_start_line is not None
            and self.source_end_line is not None
            and self.source_end_line < self.source_start_line
        ):
            raise ValueError("source_end_line must not precede source_start_line")
        effective = self.source_effective_at
        if isinstance(effective, (date, datetime)):
            effective = effective.isoformat()
        return {
            "source_authority": source_authority,
            "source_domain": source_domain,
            "source_package_id": self.source_package_id.strip(),
            "source_rank": self.source_rank,
            "source_effective_at": effective,
            "source_file": self.source_file,
            "source_start_line": self.source_start_line,
            "source_end_line": self.source_end_line,
            "source_row_hash": self.source_row_hash,
        }


def _observed_at(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    cleaned = str(value).strip()
    return cleaned or None


def build_fact_event_envelope(
    *,
    jurisdiction: str,
    resource_kind: str,
    semantic_family: str,
    subject: SubjectRef,
    provenance: ProvenanceRef,
    payload: Mapping[str, Any] | None = None,
    source_type: str = "",
    source_code: str = "",
    source_text: str = "",
    normalized_type: str = "",
    normalization_confidence: float | None = None,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    jurisdiction = jurisdiction.strip().upper()
    if not jurisdiction:
        raise ValueError("jurisdiction is required")
    kind = resource_kind.strip().upper()
    if kind not in RESOURCE_KINDS:
        raise ValueError(f"unsupported resource_kind: {resource_kind}")
    family = semantic_family.strip().upper()
    if family not in SEMANTIC_FAMILIES:
        raise ValueError(f"unsupported semantic_family: {semantic_family}")
    if normalization_confidence is not None and not 0 <= normalization_confidence <= 1:
        raise ValueError("normalization_confidence must be between 0 and 1")

    source_type = source_type.strip()
    source_code = source_code.strip()
    source_text = source_text.strip()
    normalized_type = normalized_type.strip()
    if normalized_type and not (source_type or source_code or source_text):
        raise ValueError(
            "normalized_type requires retained source_type, source_code, or source_text"
        )

    return {
        "envelope_version": FACT_EVENT_ENVELOPE_VERSION,
        "jurisdiction": jurisdiction,
        "resource_kind": kind,
        "semantic_family": family,
        "subject": subject.as_dict(),
        "provenance": provenance.as_dict(),
        "observation": {
            "observed_at": _observed_at(observed_at),
            "source_type": source_type,
            "source_code": source_code,
            "source_text": source_text,
        },
        "normalization": {
            "normalized_type": normalized_type,
            "confidence": normalization_confidence,
            "semantics": NORMALIZATION_SEMANTICS,
            "cross_jurisdiction_legal_equivalence": False,
        },
        "authority": "DATA_ENGINE_SOURCE_FACT",
        "legal_conclusion": False,
        "actionability": "SOURCE_FACT_ONLY",
        "payload": dict(payload or {}),
    }


def fact_event_envelope_contract() -> dict[str, Any]:
    return {
        "version": FACT_EVENT_ENVELOPE_VERSION,
        "role": "GLOBAL_SOURCE_FACT_AND_EVENT_OUTER_ENVELOPE",
        "resource_kinds": list(RESOURCE_KINDS),
        "semantic_families": list(SEMANTIC_FAMILIES),
        "required_sections": [
            "subject",
            "provenance",
            "observation",
            "normalization",
            "payload",
        ],
        "source_preservation": {
            "raw_source_type_retained": True,
            "raw_source_code_retained": True,
            "raw_source_text_retained": True,
            "source_package_and_rank_supported": True,
            "source_file_and_line_range_supported": True,
            "source_row_hash_supported": True,
        },
        "normalization": {
            "semantics": NORMALIZATION_SEMANTICS,
            "cross_jurisdiction_legal_equivalence": False,
            "automatic_legal_status_equivalence": False,
            "source_specific_payload_remains_authoritative": True,
        },
        "layer_boundary": {
            "source_fact_or_source_event_only": True,
            "alert_or_signal_is_not_implicitly_source_fact": True,
            "brain_reasoning_is_outside_envelope": True,
            "business_workflow_is_outside_envelope": True,
        },
        "legal_conclusion": False,
        "consumer_writeback": False,
    }
