from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any, Mapping

from app.fact_candidate_admission import (
    FactCandidateAdmissionError,
    admit_fact_candidate_v1,
)
from app.temporal_relationship_contract import TEMPORAL_RELATIONSHIP_CONTRACT_VERSION


ADMISSION_CONTRACT_VERSION = TEMPORAL_RELATIONSHIP_CONTRACT_VERSION
TARGET_TABLE = "markorbit_facts.cn_admitted_citation_relation"


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


def _utc_instant(now: datetime | None) -> str:
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def admit_cn_citation_relation(
    candidate: Mapping[str, Any],
    *,
    client: Any,
    now: datetime | None = None,
) -> CitationRelationAdmissionReceipt:
    if candidate.get("jurisdiction") != "CN":
        raise CitationRelationAdmissionError("only CN citation candidates are supported")

    decided_at = _utc_instant(now)
    try:
        admitted = admit_fact_candidate_v1(candidate, decided_at=decided_at)
    except FactCandidateAdmissionError as exc:
        raise CitationRelationAdmissionError(str(exc)) from exc

    admitted_candidate = admitted["candidate"]
    admission = admitted["admission"]
    fact = admitted["fact"]
    candidate_id = str(admitted_candidate["candidateId"])
    fingerprint = str(admitted_candidate["candidateFingerprintSha256"])

    existing = client.query(
        f"""
        SELECT candidate_id, candidate_fingerprint_sha256, edge_id, contract_version
        FROM {TARGET_TABLE} FINAL
        WHERE candidate_fingerprint_sha256 = %(fingerprint)s
           OR candidate_id = %(candidate_id)s
        LIMIT 2
        """,
        parameters={"fingerprint": fingerprint, "candidate_id": candidate_id},
    ).result_rows
    for stored_candidate_id, stored_fingerprint, edge_id, contract_version in existing:
        if str(stored_fingerprint) == fingerprint:
            return CitationRelationAdmissionReceipt(
                str(edge_id),
                str(contract_version),
                True,
            )
        if str(stored_candidate_id) == candidate_id:
            raise CitationRelationAdmissionError("candidate identity fingerprint conflict")

    temporal = fact["temporal"]
    event_at = temporal["event_at"]
    event_date = date.fromisoformat(event_at) if event_at is not None else None
    admitted_at = datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    client.insert(
        TARGET_TABLE,
        [[
            fact["edge_id"],
            candidate_id,
            fingerprint,
            fact["contract_version"],
            fact["relationship_type"],
            fact["source"]["resource_id"],
            fact["target"]["resource_id"],
            event_date,
            _json(fact["evidence"]),
            _json(fact["provenance"]),
            fact["fingerprint"],
            admitted_at,
        ]],
        column_names=[
            "edge_id",
            "candidate_id",
            "candidate_fingerprint_sha256",
            "contract_version",
            "relationship_type",
            "source_resource_id",
            "target_resource_id",
            "event_date",
            "evidence_json",
            "provenance_json",
            "edge_fingerprint",
            "admitted_at",
        ],
    )
    return CitationRelationAdmissionReceipt(
        str(admission["dataEngineFactId"]),
        str(admission["dataEngineContractVersion"]),
        False,
    )
