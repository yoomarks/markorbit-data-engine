from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATASET_REF_PREFIX = "research-dataset_"
_COMPLETENESS = {"COMPLETE_BOUNDED", "COMPLETE_TO_WATERMARK", "PAGE_STREAM"}


class ResearchDatasetContractError(ValueError):
    """Raised when a ResearchDatasetRefV1 contract is invalid."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchDatasetContractError(f"{field} must be a non-empty string")
    return value.strip()


def _instant(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchDatasetContractError(f"{field} must be an ISO-8601 timestamp") from exc
    return text


def _string_tuple(value: Any, field: str, *, uppercase: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ResearchDatasetContractError(f"{field} must be a non-empty array")
    items = tuple(_text(item, field) for item in value)
    if uppercase:
        items = tuple(item.upper() for item in items)
    if len(set(items)) != len(items):
        raise ResearchDatasetContractError(f"{field} must not contain duplicates")
    return tuple(sorted(items))


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchDatasetContractError(f"{field} must be an object")
    return dict(value)


def _validate_optional_sampling(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    sampling = _mapping(value, "sampling")
    strategy = _text(sampling.get("strategy"), "sampling.strategy")
    seed = sampling.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ResearchDatasetContractError("sampling.seed must be an integer")
    return {"strategy": strategy, "seed": seed, **{k: v for k, v in sampling.items() if k not in {"strategy", "seed"}}}


@dataclass(frozen=True)
class ResearchDatasetRefV1:
    contract_version: int
    dataset_ref_id: str
    engine_version: str
    fact_schema_version: str
    jurisdictions: tuple[str, ...]
    resource_kinds: tuple[str, ...]
    query: dict[str, Any]
    as_of: str | None
    watermark: str | None
    completeness: str
    pagination: dict[str, Any] | None
    aggregation: dict[str, Any] | None
    sampling: dict[str, Any] | None
    partition: dict[str, Any] | None
    row_count: int
    generated_at: str
    query_fingerprint_sha256: str
    integrity_sha256: str

    @property
    def replay_identity(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "engine_version": self.engine_version,
            "fact_schema_version": self.fact_schema_version,
            "jurisdictions": self.jurisdictions,
            "resource_kinds": self.resource_kinds,
            "query": self.query,
            "as_of": self.as_of,
            "watermark": self.watermark,
            "completeness": self.completeness,
            "pagination": self.pagination,
            "aggregation": self.aggregation,
            "sampling": self.sampling,
            "partition": self.partition,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "dataset_ref_id": self.dataset_ref_id,
            "engine_version": self.engine_version,
            "fact_schema_version": self.fact_schema_version,
            "jurisdictions": list(self.jurisdictions),
            "resource_kinds": list(self.resource_kinds),
            "query": self.query,
            "as_of": self.as_of,
            "watermark": self.watermark,
            "completeness": self.completeness,
            "pagination": self.pagination,
            "aggregation": self.aggregation,
            "sampling": self.sampling,
            "partition": self.partition,
            "row_count": self.row_count,
            "generated_at": self.generated_at,
            "query_fingerprint_sha256": self.query_fingerprint_sha256,
            "integrity_sha256": self.integrity_sha256,
        }


def research_query_fingerprint(value: Mapping[str, Any]) -> str:
    identity = {
        "contract_version": 1,
        "engine_version": value["engine_version"],
        "fact_schema_version": value["fact_schema_version"],
        "jurisdictions": sorted(value["jurisdictions"]),
        "resource_kinds": sorted(value["resource_kinds"]),
        "query": value["query"],
        "as_of": value.get("as_of"),
        "watermark": value.get("watermark"),
        "completeness": value["completeness"],
        "pagination": value.get("pagination"),
        "aggregation": value.get("aggregation"),
        "sampling": value.get("sampling"),
        "partition": value.get("partition"),
    }
    return _sha256(identity)


def build_research_dataset_ref_v1(
    *,
    engine_version: str,
    fact_schema_version: str,
    jurisdictions: Sequence[str],
    resource_kinds: Sequence[str],
    query: Mapping[str, Any],
    completeness: str,
    row_count: int,
    generated_at: str,
    integrity_sha256: str,
    as_of: str | None = None,
    watermark: str | None = None,
    pagination: Mapping[str, Any] | None = None,
    aggregation: Mapping[str, Any] | None = None,
    sampling: Mapping[str, Any] | None = None,
    partition: Mapping[str, Any] | None = None,
) -> ResearchDatasetRefV1:
    if (as_of is None) == (watermark is None):
        raise ResearchDatasetContractError("exactly one of as_of or watermark is required")
    if completeness not in _COMPLETENESS:
        raise ResearchDatasetContractError("completeness is invalid")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise ResearchDatasetContractError("row_count must be a non-negative integer")
    digest = _text(integrity_sha256, "integrity_sha256").lower()
    if not _SHA256.fullmatch(digest):
        raise ResearchDatasetContractError("integrity_sha256 must be a lowercase SHA-256 digest")

    normalized: dict[str, Any] = {
        "engine_version": _text(engine_version, "engine_version"),
        "fact_schema_version": _text(fact_schema_version, "fact_schema_version"),
        "jurisdictions": _string_tuple(jurisdictions, "jurisdictions", uppercase=True),
        "resource_kinds": _string_tuple(resource_kinds, "resource_kinds"),
        "query": _mapping(query, "query"),
        "as_of": _instant(as_of, "as_of") if as_of is not None else None,
        "watermark": _text(watermark, "watermark") if watermark is not None else None,
        "completeness": completeness,
        "pagination": None if pagination is None else _mapping(pagination, "pagination"),
        "aggregation": None if aggregation is None else _mapping(aggregation, "aggregation"),
        "sampling": _validate_optional_sampling(sampling),
        "partition": None if partition is None else _mapping(partition, "partition"),
    }
    fingerprint = research_query_fingerprint(normalized)
    return ResearchDatasetRefV1(
        contract_version=1,
        dataset_ref_id=f"{_DATASET_REF_PREFIX}{fingerprint}",
        engine_version=normalized["engine_version"],
        fact_schema_version=normalized["fact_schema_version"],
        jurisdictions=normalized["jurisdictions"],
        resource_kinds=normalized["resource_kinds"],
        query=normalized["query"],
        as_of=normalized["as_of"],
        watermark=normalized["watermark"],
        completeness=normalized["completeness"],
        pagination=normalized["pagination"],
        aggregation=normalized["aggregation"],
        sampling=normalized["sampling"],
        partition=normalized["partition"],
        row_count=row_count,
        generated_at=_instant(generated_at, "generated_at"),
        query_fingerprint_sha256=fingerprint,
        integrity_sha256=digest,
    )


def parse_research_dataset_ref_v1(value: Mapping[str, Any]) -> ResearchDatasetRefV1:
    if value.get("contract_version") != 1:
        raise ResearchDatasetContractError("contract_version must be 1")
    ref = build_research_dataset_ref_v1(
        engine_version=value.get("engine_version"),
        fact_schema_version=value.get("fact_schema_version"),
        jurisdictions=value.get("jurisdictions"),
        resource_kinds=value.get("resource_kinds"),
        query=value.get("query"),
        as_of=value.get("as_of"),
        watermark=value.get("watermark"),
        completeness=value.get("completeness"),
        pagination=value.get("pagination"),
        aggregation=value.get("aggregation"),
        sampling=value.get("sampling"),
        partition=value.get("partition"),
        row_count=value.get("row_count"),
        generated_at=value.get("generated_at"),
        integrity_sha256=value.get("integrity_sha256"),
    )
    if value.get("dataset_ref_id") != ref.dataset_ref_id:
        raise ResearchDatasetContractError("dataset_ref_id does not match replay identity")
    if value.get("query_fingerprint_sha256") != ref.query_fingerprint_sha256:
        raise ResearchDatasetContractError("query_fingerprint_sha256 does not match replay identity")
    return ref


def replay_matches(left: ResearchDatasetRefV1, right: ResearchDatasetRefV1) -> bool:
    """True only when both refs describe the same factual scope and identical result snapshot."""

    return (
        left.query_fingerprint_sha256 == right.query_fingerprint_sha256
        and left.row_count == right.row_count
        and left.integrity_sha256 == right.integrity_sha256
    )
