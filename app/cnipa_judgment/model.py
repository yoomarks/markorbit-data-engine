from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping


DOCUMENT_KINDS = (
    "REGISTRATION_EXAMINATION",
    "OPPOSITION_DECISION",
    "REVIEW_ADJUDICATION",
)


@dataclass(frozen=True)
class CnipaJudgmentListFact:
    document_kind: str
    source_record_id: str
    semantic_family: str
    detail_canonical_uri: str
    source_row_sha256: str
    observed_at: datetime
    source_artifact_ref: str
    source_fields: Mapping[str, Any] = field(default_factory=dict)
    application_number: str = ""
    registration_number: str = ""
    trademark_name: str = ""
    source_title: str = ""
    source_date: str = ""
    source_document_number: str = ""
    cited_registration_text: str = ""
    initial_markdown_ref: str = ""
    initial_markdown_sha256: str = ""


@dataclass(frozen=True)
class CnipaJudgmentWindowObservation:
    document_kind: str
    observed_at: datetime
    source_artifact_ref: str
    record_count: int
    query_from: date | None = None
    query_to: date | None = None
