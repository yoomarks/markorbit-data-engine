from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CONTRACT_VERSION = "DATA_ENGINE_DISCOVERY_CONTRACT_V1"
CURSOR_VERSION = "DATA_ENGINE_DISCOVERY_CURSOR_V1"

ABSOLUTE_MAX_PAGE_SIZE = 200
ABSOLUTE_MAX_PAGES = 100
ABSOLUTE_MAX_RESULTS = 10_000
MAX_CURSOR_JSON_BYTES = 4_096
MAX_CURSOR_TOKEN_LENGTH = 8_192


class DiscoveryContractError(ValueError):
    """Raised when a Discovery query or provenance contract is invalid."""


class DiscoveryCursorError(DiscoveryContractError):
    """Raised when an opaque Discovery cursor cannot be trusted for replay."""


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    page_size: int
    max_pages: int
    max_results: int

    def __post_init__(self) -> None:
        _require_positive_int("page_size", self.page_size)
        _require_positive_int("max_pages", self.max_pages)
        _require_positive_int("max_results", self.max_results)
        if self.page_size > ABSOLUTE_MAX_PAGE_SIZE:
            raise DiscoveryContractError(
                f"page_size exceeds absolute ceiling {ABSOLUTE_MAX_PAGE_SIZE}"
            )
        if self.max_pages > ABSOLUTE_MAX_PAGES:
            raise DiscoveryContractError(
                f"max_pages exceeds absolute ceiling {ABSOLUTE_MAX_PAGES}"
            )
        if self.max_results > ABSOLUTE_MAX_RESULTS:
            raise DiscoveryContractError(
                f"max_results exceeds absolute ceiling {ABSOLUTE_MAX_RESULTS}"
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "page_size": self.page_size,
            "max_pages": self.max_pages,
            "max_results": self.max_results,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DiscoveryLimits:
        expected = {"page_size", "max_pages", "max_results"}
        if set(value) != expected:
            raise DiscoveryContractError("limits must contain exactly page_size/max_pages/max_results")
        return cls(
            page_size=_require_int("page_size", value["page_size"]),
            max_pages=_require_int("max_pages", value["max_pages"]),
            max_results=_require_int("max_results", value["max_results"]),
        )


def _require_int(name: str, value: Any) -> int:
    if type(value) is not int:
        raise DiscoveryContractError(f"{name} must be an integer")
    return value


def _require_positive_int(name: str, value: Any) -> int:
    number = _require_int(name, value)
    if number <= 0:
        raise DiscoveryContractError(f"{name} must be positive")
    return number


def _require_text(name: str, value: Any, *, max_length: int = 512) -> str:
    if not isinstance(value, str):
        raise DiscoveryContractError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise DiscoveryContractError(f"{name} must not be empty")
    if len(normalized) > max_length:
        raise DiscoveryContractError(f"{name} exceeds maximum length {max_length}")
    return normalized


def _validate_json_value(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return
    if isinstance(value, float):
        raise DiscoveryContractError(
            f"{path} contains a float; V1 query identity requires integer/string JSON scalars"
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DiscoveryContractError(f"{path} contains a non-string object key")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise DiscoveryContractError(f"{path} contains unsupported JSON value {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_identity(value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _require_query_hash(value: Any) -> str:
    query_hash = _require_text("query_hash", value, max_length=71)
    prefix, separator, digest = query_hash.partition(":")
    if separator != ":" or prefix != "sha256" or len(digest) != 64:
        raise DiscoveryContractError("query_hash must use sha256:<64 lowercase hex> format")
    if any(character not in "0123456789abcdef" for character in digest):
        raise DiscoveryContractError("query_hash must use lowercase hexadecimal sha256")
    return query_hash


def _normalize_projection_fields(fields: Sequence[str]) -> list[str]:
    if isinstance(fields, (str, bytes)):
        raise DiscoveryContractError("projection_fields must be a sequence of field names")
    normalized = [_require_text("projection_field", item) for item in fields]
    if not normalized:
        raise DiscoveryContractError("projection_fields must not be empty")
    if len(normalized) != len(set(normalized)):
        raise DiscoveryContractError("projection_fields must not contain duplicates")
    return normalized


def build_query_identity(
    *,
    stream_id: str,
    source_schema_id: str,
    candidate_type: str,
    projection_fields: Sequence[str],
    scope: Mapping[str, Any],
    limits: DiscoveryLimits,
) -> dict[str, Any]:
    if not isinstance(scope, Mapping):
        raise DiscoveryContractError("scope must be a mapping")
    normalized_scope = dict(scope)
    _validate_json_value(normalized_scope, path="$.scope")
    body = {
        "contract_version": CONTRACT_VERSION,
        "stream_id": _require_text("stream_id", stream_id),
        "source_schema_id": _require_text("source_schema_id", source_schema_id),
        "candidate_type": _require_text("candidate_type", candidate_type),
        "projection_fields": _normalize_projection_fields(projection_fields),
        "scope": normalized_scope,
        "limits": limits.to_dict(),
    }
    return {**body, "query_hash": _sha256_identity(body)}


def verify_query_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    expected_keys = {
        "contract_version",
        "stream_id",
        "source_schema_id",
        "candidate_type",
        "projection_fields",
        "scope",
        "limits",
        "query_hash",
    }
    if set(identity) != expected_keys:
        raise DiscoveryContractError("query identity fields do not match V1 contract")
    if identity["contract_version"] != CONTRACT_VERSION:
        raise DiscoveryContractError("unsupported Discovery query contract version")
    if not isinstance(identity["limits"], Mapping):
        raise DiscoveryContractError("limits must be a mapping")
    if not isinstance(identity["scope"], Mapping):
        raise DiscoveryContractError("scope must be a mapping")
    limits = DiscoveryLimits.from_mapping(identity["limits"])
    expected = build_query_identity(
        stream_id=identity["stream_id"],
        source_schema_id=identity["source_schema_id"],
        candidate_type=identity["candidate_type"],
        projection_fields=identity["projection_fields"],
        scope=identity["scope"],
        limits=limits,
    )
    query_hash = _require_query_hash(identity["query_hash"])
    if not hmac.compare_digest(query_hash, expected["query_hash"]):
        raise DiscoveryContractError("query identity hash mismatch")
    return expected


def build_snapshot_ref(
    *,
    snapshot_id: str,
    snapshot_kind: str,
    watermark: str,
    source_version: str,
) -> dict[str, str]:
    return {
        "snapshot_id": _require_text("snapshot_id", snapshot_id),
        "snapshot_kind": _require_text("snapshot_kind", snapshot_kind),
        "watermark": _require_text("watermark", watermark, max_length=2_048),
        "source_version": _require_text("source_version", source_version),
    }


def verify_snapshot_ref(snapshot: Mapping[str, Any]) -> dict[str, str]:
    expected_keys = {"snapshot_id", "snapshot_kind", "watermark", "source_version"}
    if set(snapshot) != expected_keys:
        raise DiscoveryContractError("snapshot reference fields do not match V1 contract")
    return build_snapshot_ref(
        snapshot_id=snapshot["snapshot_id"],
        snapshot_kind=snapshot["snapshot_kind"],
        watermark=snapshot["watermark"],
        source_version=snapshot["source_version"],
    )


def _normalize_position(position: Sequence[Any]) -> list[Any]:
    if isinstance(position, (str, bytes)):
        raise DiscoveryCursorError("cursor position must be a sequence of keyset scalars")
    normalized = list(position)
    if not normalized:
        raise DiscoveryCursorError("cursor position must not be empty")
    for index, item in enumerate(normalized):
        if item is None or isinstance(item, (str, bool)) or type(item) is int:
            continue
        raise DiscoveryCursorError(
            f"cursor position[{index}] must be an integer/string/bool/null scalar"
        )
    return normalized


def _cursor_checksum(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def encode_cursor(
    *,
    query_hash: str,
    snapshot_id: str,
    position: Sequence[Any],
    next_page: int,
    emitted_count: int,
    limits: DiscoveryLimits,
) -> str:
    query_hash = _require_query_hash(query_hash)
    snapshot_id = _require_text("snapshot_id", snapshot_id)
    next_page = _require_int("next_page", next_page)
    emitted_count = _require_int("emitted_count", emitted_count)
    if next_page < 2 or next_page > limits.max_pages:
        raise DiscoveryCursorError("next_page is outside the query hard bounds")
    if emitted_count <= 0 or emitted_count >= limits.max_results:
        raise DiscoveryCursorError("continuation cursor would exceed the result hard bound")
    payload = {
        "cursor_version": CURSOR_VERSION,
        "query_hash": query_hash,
        "snapshot_id": snapshot_id,
        "position": _normalize_position(position),
        "next_page": next_page,
        "emitted_count": emitted_count,
    }
    envelope = {"payload": payload, "checksum": _cursor_checksum(payload)}
    raw = _canonical_json(envelope).encode("utf-8")
    if len(raw) > MAX_CURSOR_JSON_BYTES:
        raise DiscoveryCursorError("cursor payload is too large")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(
    token: str,
    *,
    expected_query_hash: str,
    expected_snapshot_id: str,
    limits: DiscoveryLimits,
) -> dict[str, Any]:
    if not isinstance(token, str) or not token or len(token) > MAX_CURSOR_TOKEN_LENGTH:
        raise DiscoveryCursorError("cursor token is missing or too large")
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise DiscoveryCursorError("cursor token must be URL-safe ASCII") from exc
    padding = b"=" * (-len(encoded) % 4)
    try:
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise DiscoveryCursorError("cursor token is not valid URL-safe base64") from exc
    if len(raw) > MAX_CURSOR_JSON_BYTES:
        raise DiscoveryCursorError("cursor payload is too large")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryCursorError("cursor payload is not valid UTF-8 JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "checksum"}:
        raise DiscoveryCursorError("cursor envelope fields do not match V1 contract")
    payload = envelope["payload"]
    checksum = envelope["checksum"]
    if not isinstance(payload, dict) or not isinstance(checksum, str):
        raise DiscoveryCursorError("cursor envelope has invalid field types")
    expected_payload_keys = {
        "cursor_version",
        "query_hash",
        "snapshot_id",
        "position",
        "next_page",
        "emitted_count",
    }
    if set(payload) != expected_payload_keys:
        raise DiscoveryCursorError("cursor payload fields do not match V1 contract")
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise DiscoveryCursorError("cursor checksum is malformed")
    if not hmac.compare_digest(checksum, _cursor_checksum(payload)):
        raise DiscoveryCursorError("cursor checksum mismatch")
    if payload["cursor_version"] != CURSOR_VERSION:
        raise DiscoveryCursorError("unsupported Discovery cursor version")

    query_hash = _require_query_hash(payload["query_hash"])
    expected_query_hash = _require_query_hash(expected_query_hash)
    if not hmac.compare_digest(query_hash, expected_query_hash):
        raise DiscoveryCursorError("cursor/query mismatch")

    snapshot_id = _require_text("snapshot_id", payload["snapshot_id"])
    expected_snapshot_id = _require_text("expected_snapshot_id", expected_snapshot_id)
    if not hmac.compare_digest(snapshot_id, expected_snapshot_id):
        raise DiscoveryCursorError("cursor/snapshot mismatch")

    position = payload["position"]
    if not isinstance(position, list):
        raise DiscoveryCursorError("cursor position must be a list")
    normalized_position = _normalize_position(position)
    next_page = _require_int("next_page", payload["next_page"])
    emitted_count = _require_int("emitted_count", payload["emitted_count"])
    if next_page < 2 or next_page > limits.max_pages:
        raise DiscoveryCursorError("cursor next_page is outside the query hard bounds")
    if emitted_count <= 0 or emitted_count >= limits.max_results:
        raise DiscoveryCursorError("cursor emitted_count is outside the query hard bounds")

    return {
        "cursor_version": CURSOR_VERSION,
        "query_hash": query_hash,
        "snapshot_id": snapshot_id,
        "position": normalized_position,
        "next_page": next_page,
        "emitted_count": emitted_count,
    }


def build_page_provenance(
    *,
    query_identity: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    engine_version: str,
    page_number: int,
    result_count: int,
    emitted_count: int,
    next_cursor: str | None,
) -> dict[str, Any]:
    query = verify_query_identity(query_identity)
    snapshot_ref = verify_snapshot_ref(snapshot)
    limits = DiscoveryLimits.from_mapping(query["limits"])
    page_number = _require_int("page_number", page_number)
    result_count = _require_int("result_count", result_count)
    emitted_count = _require_int("emitted_count", emitted_count)
    if page_number <= 0 or page_number > limits.max_pages:
        raise DiscoveryContractError("page_number is outside the query hard bounds")
    if result_count < 0 or result_count > limits.page_size:
        raise DiscoveryContractError("result_count is outside the page hard bound")
    if emitted_count < result_count or emitted_count > limits.max_results:
        raise DiscoveryContractError("emitted_count is outside the result hard bound")
    if next_cursor is not None:
        if result_count == 0:
            raise DiscoveryContractError("an empty page must not emit a continuation cursor")
        decoded = decode_cursor(
            next_cursor,
            expected_query_hash=query["query_hash"],
            expected_snapshot_id=snapshot_ref["snapshot_id"],
            limits=limits,
        )
        if decoded["next_page"] != page_number + 1:
            raise DiscoveryContractError("next cursor page does not follow the emitted page")
        if decoded["emitted_count"] != emitted_count:
            raise DiscoveryContractError("next cursor emitted_count does not match page provenance")

    return {
        "contract_version": CONTRACT_VERSION,
        "query_hash": query["query_hash"],
        "stream_id": query["stream_id"],
        "candidate_type": query["candidate_type"],
        "source_schema_id": query["source_schema_id"],
        "projection_fields": list(query["projection_fields"]),
        "scope": dict(query["scope"]),
        "limits": dict(query["limits"]),
        "snapshot": snapshot_ref,
        "engine_version": _require_text("engine_version", engine_version),
        "page_number": page_number,
        "result_count": result_count,
        "emitted_count": emitted_count,
        "has_more": next_cursor is not None,
    }
