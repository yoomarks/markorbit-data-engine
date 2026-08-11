from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.uspto_odp_bulk_metadata import PRODUCT_IDENTITY


FETCH_VERSION = "USPTO_ODP_PRODUCT_METADATA_FETCH_V1"
PRODUCT_DATA_BASE_URL = "https://api.uspto.gov/api/v1/datasets/products"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_METADATA_BYTES = 64 * 1024 * 1024
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class MetadataFetchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _normalized_domain(domain: str) -> str:
    value = domain.strip().lower()
    if value not in PRODUCT_IDENTITY:
        raise MetadataFetchError(
            "ODP_DOMAIN_INVALID",
            f"domain must be one of {sorted(PRODUCT_IDENTITY)}",
        )
    return value


def product_data_url(domain: str) -> str:
    normalized = _normalized_domain(domain)
    slug = PRODUCT_IDENTITY[normalized]["dataset_slug"]
    return f"{PRODUCT_DATA_BASE_URL}/{slug}"


def _validate_header_name(value: str) -> str:
    header = value.strip()
    if not header or not _HEADER_NAME.fullmatch(header):
        raise MetadataFetchError(
            "ODP_API_KEY_HEADER_INVALID",
            "USPTO_ODP_API_KEY_HEADER must be an explicit valid HTTP header name.",
        )
    return header


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _observed_product_identifiers(payload: Any) -> set[str]:
    identifiers: set[str] = set()
    for row in _walk_dicts(payload):
        for key in ("productIdentifier", "product_identifier", "productId", "product_id"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                identifiers.add(value.strip())
    return identifiers


def _validate_payload_identity(domain: str, payload: Any) -> set[str]:
    identity = PRODUCT_IDENTITY[domain]
    accepted = set(identity.values())
    observed = _observed_product_identifiers(payload)
    if not observed:
        raise MetadataFetchError(
            "ODP_PRODUCT_IDENTIFIER_MISSING",
            "USPTO ODP Product Data response did not expose a product identifier.",
        )
    if not observed.issubset(accepted):
        raise MetadataFetchError(
            "ODP_PRODUCT_IDENTIFIER_MISMATCH",
            "USPTO ODP Product Data response does not match the requested authoritative dataset.",
        )
    return observed


def fetch_product_metadata(
    *,
    domain: str,
    api_key: str,
    api_key_header: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    open_url: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    normalized = _normalized_domain(domain)
    key = api_key.strip()
    if not key:
        raise MetadataFetchError(
            "ODP_API_KEY_MISSING",
            "USPTO_ODP_API_KEY must be configured before metadata can be fetched.",
        )
    header = _validate_header_name(api_key_header)
    endpoint = product_data_url(normalized)
    request = Request(
        endpoint,
        headers={
            header: key,
            "Accept": "application/json",
            "User-Agent": "MarkOrbit-Data-Engine/ODP-Metadata-Fetch-V1",
        },
        method="GET",
    )

    try:
        response = open_url(request, timeout=timeout_seconds)
        try:
            raw = response.read(MAX_METADATA_BYTES + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except HTTPError as exc:
        raise MetadataFetchError(
            "ODP_HTTP_ERROR",
            f"USPTO ODP Product Data request failed with HTTP {exc.code}.",
        ) from None
    except (URLError, TimeoutError, OSError):
        raise MetadataFetchError(
            "ODP_NETWORK_ERROR",
            "USPTO ODP Product Data request failed before a valid response was received.",
        ) from None

    if len(raw) > MAX_METADATA_BYTES:
        raise MetadataFetchError(
            "ODP_METADATA_RESPONSE_TOO_LARGE",
            f"USPTO ODP Product Data response exceeded {MAX_METADATA_BYTES} bytes.",
        )
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MetadataFetchError(
            "ODP_METADATA_JSON_INVALID",
            "USPTO ODP Product Data response was not valid UTF-8 JSON.",
        ) from None

    observed = _validate_payload_identity(normalized, payload)
    identity = PRODUCT_IDENTITY[normalized]
    return {
        "fetch_version": FETCH_VERSION,
        "status": "FETCHED",
        "safe": True,
        "domain": normalized,
        "endpoint": endpoint,
        "odp_dataset_slug": identity["dataset_slug"],
        "federal_catalog_identifier": identity["federal_catalog_identifier"],
        "metadata_product_identifiers_observed": sorted(observed),
        "response_byte_count": len(raw),
        "response_sha256": sha256(raw).hexdigest(),
        "api_key_header": header,
        "api_key_exposed": False,
        "metadata": payload,
    }


def _error_report(domain: str, exc: MetadataFetchError) -> dict[str, Any]:
    normalized = domain.strip().lower()
    identity = PRODUCT_IDENTITY.get(normalized, {})
    return {
        "fetch_version": FETCH_VERSION,
        "status": "FAILED",
        "safe": False,
        "domain": normalized,
        "odp_dataset_slug": identity.get("dataset_slug"),
        "federal_catalog_identifier": identity.get("federal_catalog_identifier"),
        "issue": {"type": exc.code, "message": str(exc)},
        "api_key_exposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch authoritative USPTO ODP Product Data metadata for a frozen bulk dataset"
    )
    parser.add_argument("--domain", required=True, choices=sorted(PRODUCT_IDENTITY))
    args = parser.parse_args()
    try:
        report = fetch_product_metadata(
            domain=args.domain,
            api_key=os.environ.get("USPTO_ODP_API_KEY", ""),
            api_key_header=os.environ.get("USPTO_ODP_API_KEY_HEADER", ""),
        )
    except MetadataFetchError as exc:
        print(json.dumps(_error_report(args.domain, exc), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
