from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from app.discovery_contract import (
    CONTRACT_VERSION as DISCOVERY_CONTRACT_VERSION,
    DiscoveryContractError,
    DiscoveryCursorError,
    DiscoveryLimits,
    decode_cursor,
    encode_cursor,
)

APPLICANT_RESOURCE_KIND = "APPLICANT_IDENTITY_DISCOVERY"
PORTFOLIO_RESOURCE_KIND = "APPLICANT_PORTFOLIO_DISCOVERY"
APPLICANT_CANDIDATE_TYPE = "APPLICANT_IDENTITY"
TRADEMARK_CANDIDATE_TYPE = "DISCOVERED_TRADEMARK"
SOURCE_OWNER = "MARKORBIT_DATA_ENGINE"
FACT_AUTHORITY = "DATA_ENGINE_FACT_READ_MODEL"
NO_AUTHORITY_CONSEQUENCES = {
    "verifiedLegalIdentityEstablished": False,
    "customerRelationshipEstablished": False,
    "trademarkAssetCreated": False,
    "managedRelationshipEstablished": False,
    "representedRelationshipEstablished": False,
    "ownedRelationshipEstablished": False,
    "watchCreated": False,
    "legalConclusionCreated": False,
    "officialTruthCreated": False,
    "externalActionAuthorized": False,
}


class OwnerReadInvalid(DiscoveryContractError):
    pass


class OwnerReadConflict(DiscoveryContractError):
    pass


class OwnerReadUnavailable(RuntimeError):
    pass


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8").rstrip("\x00")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8").rstrip("\x00")
    if isinstance(value, memoryview):
        return value.tobytes().decode("utf-8").rstrip("\x00")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def sha256_ref(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        observed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            raise OwnerReadInvalid("observed_at must be a non-empty ISO timestamp")
        try:
            observed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise OwnerReadInvalid("observed_at must be an ISO timestamp") from exc
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def request_context(workspace_id: str, request_id: str) -> dict[str, str]:
    workspace = str(workspace_id or "").strip()
    request = str(request_id or "").strip()
    if not workspace or len(workspace) > 512:
        raise OwnerReadInvalid("requester_workspace_id is required")
    if not request or len(request) > 512:
        raise OwnerReadInvalid("request_id is required")
    return {"requester_workspace_id": workspace, "request_id": request}


def source_reference(
    *, jurisdiction: str, source_kind: str, source_id: str,
    source_version: str, fingerprint: str, observed_at: Any,
) -> dict[str, str]:
    if jurisdiction not in {"CN", "US"}:
        raise OwnerReadInvalid("unsupported owner-read jurisdiction")
    if source_kind not in {"APPLICANT_IDENTITY", "TRADEMARK_RECORD"}:
        raise OwnerReadInvalid("unsupported owner-read source kind")
    source_id = str(source_id or "").strip()
    source_version = str(source_version or "").strip()
    if not source_id or not source_version:
        raise OwnerReadInvalid("source id/version are required")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:"):
        raise OwnerReadInvalid("source fingerprint must use sha256:<hex>")
    digest = fingerprint.removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise OwnerReadInvalid("source fingerprint must use lowercase sha256")
    return {
        "owner": SOURCE_OWNER,
        "authority": FACT_AUTHORITY,
        "jurisdiction": jurisdiction,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_version": source_version,
        "source_fingerprint_sha256": fingerprint,
        "observed_at": iso_timestamp(observed_at),
    }


def assert_exact_source_reference(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> None:
    required = {
        "owner", "authority", "jurisdiction", "source_kind", "source_id",
        "source_version", "source_fingerprint_sha256", "observed_at",
    }
    if set(expected) != required:
        raise OwnerReadInvalid("source reference fields do not match V1 contract")
    normalized = source_reference(
        jurisdiction=str(expected.get("jurisdiction") or ""),
        source_kind=str(expected.get("source_kind") or ""),
        source_id=str(expected.get("source_id") or ""),
        source_version=str(expected.get("source_version") or ""),
        fingerprint=str(expected.get("source_fingerprint_sha256") or ""),
        observed_at=expected.get("observed_at"),
    )
    if expected.get("owner") != SOURCE_OWNER or expected.get("authority") != FACT_AUTHORITY:
        raise OwnerReadInvalid("source owner/authority mismatch")
    if canonical_json(normalized) != canonical_json(actual):
        raise OwnerReadConflict("source reference is stale, forged, or mismatched")


def applicant_reference(candidate_id: str, source: Mapping[str, Any]) -> dict[str, Any]:
    candidate = str(candidate_id or "").strip()
    if not candidate or len(candidate) > 512:
        raise OwnerReadInvalid("applicant_candidate_id is required")
    return {"applicant_candidate_id": candidate, "source_reference": dict(source)}


def source_snapshot(source_version: str, observed_at: Any) -> dict[str, str]:
    return {
        "source_version": str(source_version),
        "observed_at": iso_timestamp(observed_at),
    }


def query_hash(query_without_hash: Mapping[str, Any]) -> str:
    return sha256_ref(query_without_hash)


def applicant_query(
    *, context: Mapping[str, str], jurisdiction: str,
    applicant_candidate_id: str, applicant_source: Mapping[str, Any], page_size: int,
) -> dict[str, Any]:
    if type(page_size) is not int or page_size < 1 or page_size > 100:
        raise OwnerReadInvalid("page_size must be between 1 and 100")
    body = {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "request_context": dict(context),
        "applicant": applicant_reference(applicant_candidate_id, applicant_source),
        "ordering": ["trademark_candidate_id ASC"],
        "ranking_authority": "NONE",
        "limits": {"page_size": page_size, "max_results": 500},
    }
    return {**body, "query_hash": query_hash(body)}


def applicant_revalidation_query(
    *, context: Mapping[str, str], jurisdiction: str,
    applicant_candidate_id: str, page_size: int = 1,
) -> dict[str, Any]:
    body = {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "request_context": dict(context),
        "jurisdiction": jurisdiction,
        "input": {"kind": "EXTERNAL_IDENTITY_HINT", "value": applicant_candidate_id},
        "ordering": ["applicant_candidate_id ASC"],
        "ranking_authority": "NONE",
        "limits": {"page_size": page_size, "max_results": 100},
    }
    return {**body, "query_hash": query_hash(body)}


def page_payload(
    *, query: Mapping[str, Any], snapshot: Mapping[str, str],
    results: Sequence[Mapping[str, Any]], next_cursor: str | None,
    engine_version: str,
) -> dict[str, Any]:
    result_list = [dict(item) for item in results]
    provenance = {
        "query_hash": query["query_hash"],
        "request_context": dict(query["request_context"]),
        "source_snapshot": dict(snapshot),
        "engine_version": engine_version,
        "result_count": len(result_list),
        "has_more": next_cursor is not None,
        "query": dict(query),
    }
    return {
        "query": dict(query),
        "source_snapshot": dict(snapshot),
        "results": result_list,
        "next_cursor": next_cursor,
        "provenance": provenance,
        "authority_consequences": dict(NO_AUTHORITY_CONSEQUENCES),
    }


def portfolio_cursor_state(
    *, token: str | None, query: Mapping[str, Any], source_version: str,
) -> tuple[str, int, int]:
    if token is None:
        return "", 1, 0
    limits = DiscoveryLimits(
        page_size=int(query["limits"]["page_size"]), max_pages=100,
        max_results=int(query["limits"]["max_results"]),
    )
    decoded = decode_cursor(
        token, expected_query_hash=str(query["query_hash"]),
        expected_snapshot_id=source_version, limits=limits,
    )
    position = decoded["position"]
    if len(position) != 1 or not isinstance(position[0], str):
        raise DiscoveryCursorError("Applicant Portfolio cursor must contain one string key")
    return position[0], int(decoded["next_page"]), int(decoded["emitted_count"])


def next_portfolio_cursor(
    *, query: Mapping[str, Any], source_version: str, last_candidate_id: str,
    page_number: int, emitted_count: int,
) -> str | None:
    limits = DiscoveryLimits(
        page_size=int(query["limits"]["page_size"]), max_pages=100,
        max_results=int(query["limits"]["max_results"]),
    )
    if emitted_count >= limits.max_results or page_number >= limits.max_pages:
        return None
    return encode_cursor(
        query_hash=str(query["query_hash"]), snapshot_id=source_version,
        position=[last_candidate_id], next_page=page_number + 1,
        emitted_count=emitted_count, limits=limits,
    )


def max_observed(values: Sequence[Any]) -> str:
    timestamps = [iso_timestamp(value) for value in values if value not in {None, ""}]
    if not timestamps:
        raise OwnerReadUnavailable("owner-read facts have no observation timestamp")
    return max(timestamps)
