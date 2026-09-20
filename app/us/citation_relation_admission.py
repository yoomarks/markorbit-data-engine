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
TARGET_TABLE = "markorbit_facts.us_admitted_citation_relation"


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


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    return str(value)


def admit_us_citation_relation(
    candidate: Mapping[str, Any],
    *,
    client: Any,
    now: datetime | None = None,
) -> CitationRelationAdmissionReceipt:
    if candidate.get("jurisdiction") != "US":
        raise CitationRelationAdmissionError("only US citation candidates are supported")

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
    source_resource_id = str(fact["source"]["resource_id"])

    fingerprint_rows = client.query(
        f"""
        SELECT candidate_id, candidate_fingerprint_sha256, edge_id, contract_version
        FROM {TARGET_TABLE} FINAL
        WHERE source_resource_id = %(source_resource_id)s
          AND candidate_fingerprint_sha256 = %(fingerprint)s
        LIMIT 1
        """,
        parameters={
            "source_resource_id": source_resource_id,
            "fingerprint": fingerprint,
        },
    ).result_rows
    if fingerprint_rows:
        _, _, edge_id, contract_version = fingerprint_rows[0]
        return CitationRelationAdmissionReceipt(
            str(edge_id),
            str(contract_version),
            True,
        )

    identity_rows = client.query(
        f"""
        SELECT candidate_id, candidate_fingerprint_sha256, edge_id, contract_version
        FROM {TARGET_TABLE} FINAL
        WHERE candidate_id = %(candidate_id)s
        LIMIT 1
        """,
        parameters={"candidate_id": candidate_id},
    ).result_rows
    if identity_rows:
        _, stored_fingerprint, _, _ = identity_rows[0]
        if _text(stored_fingerprint) != fingerprint:
            raise CitationRelationAdmissionError("candidate identity fingerprint conflict")
        raise CitationRelationAdmissionError("candidate replay identity lookup drifted")

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
            _json(fact),
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
            "fact_json",
            "edge_fingerprint",
            "admitted_at",
        ],
    )
    return CitationRelationAdmissionReceipt(
        str(admission["dataEngineFactId"]),
        str(admission["dataEngineContractVersion"]),
        False,
    )
