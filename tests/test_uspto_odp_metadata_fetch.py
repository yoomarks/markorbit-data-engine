import json
from urllib.error import HTTPError

import pytest

from app.uspto_odp_bulk_metadata import evaluate_metadata
from app.uspto_odp_metadata_fetch import (
    MAX_METADATA_BYTES,
    MetadataFetchError,
    fetch_product_metadata,
    product_data_url,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.raw = json.dumps(payload).encode("utf-8")
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.raw if size < 0 else self.raw[:size]

    def close(self) -> None:
        self.closed = True


def test_product_data_url_is_bound_to_frozen_authoritative_dataset_slug() -> None:
    assert product_data_url("assignment") == (
        "https://api.uspto.gov/api/v1/datasets/products/trtdxfag"
    )
    assert product_data_url("ttab") == "https://api.uspto.gov/api/v1/datasets/products/ttabtdxf"


def test_fetch_uses_explicit_api_key_header_without_exposing_secret() -> None:
    seen: dict[str, object] = {}
    secret = "super-secret-odp-api-key"
    response = FakeResponse(
        {
            "productIdentifier": "trtdxfag",
            "files": [{"fileName": "asb260809.zip", "fileDate": "2026-08-09"}],
        }
    )

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = {key.lower(): value for key, value in request.header_items()}
        seen["timeout"] = timeout
        return response

    report = fetch_product_metadata(
        domain="assignment",
        api_key=secret,
        api_key_header="X-ODP-Test-Key",
        open_url=fake_open,
    )

    assert seen["url"] == "https://api.uspto.gov/api/v1/datasets/products/trtdxfag"
    assert seen["method"] == "GET"
    assert seen["headers"]["x-odp-test-key"] == secret
    assert report["status"] == "FETCHED"
    assert report["metadata_product_identifiers_observed"] == ["trtdxfag"]
    assert report["api_key_exposed"] is False
    assert secret not in json.dumps(report)
    assert response.closed is True


def test_fetch_refuses_to_guess_api_key_header_name() -> None:
    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="assignment",
            api_key="configured-secret",
            api_key_header="",
            open_url=lambda *_args, **_kwargs: None,
        )

    assert exc_info.value.code == "ODP_API_KEY_HEADER_INVALID"


def test_fetch_fails_closed_on_dataset_identity_mismatch() -> None:
    def fake_open(_request, timeout):
        assert timeout > 0
        return FakeResponse({"productIdentifier": "ttabtdxf", "files": []})

    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="assignment",
            api_key="secret",
            api_key_header="X-Test-Key",
            open_url=fake_open,
        )

    assert exc_info.value.code == "ODP_PRODUCT_IDENTIFIER_MISMATCH"


def test_fetch_fails_closed_when_product_identifier_is_missing() -> None:
    def fake_open(_request, timeout):
        assert timeout > 0
        return FakeResponse({"files": []})

    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="assignment",
            api_key="secret",
            api_key_header="X-Test-Key",
            open_url=fake_open,
        )

    assert exc_info.value.code == "ODP_PRODUCT_IDENTIFIER_MISSING"


def test_fetch_maps_http_error_without_leaking_api_key() -> None:
    secret = "do-not-leak-this-key"

    def fake_open(request, timeout):
        assert timeout > 0
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="ttab",
            api_key=secret,
            api_key_header="X-Test-Key",
            open_url=fake_open,
        )

    assert exc_info.value.code == "ODP_HTTP_ERROR"
    assert secret not in str(exc_info.value)


def test_fetch_rejects_invalid_json() -> None:
    class InvalidJsonResponse:
        def read(self, size: int = -1) -> bytes:
            return b"not-json"

        def close(self) -> None:
            pass

    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="assignment",
            api_key="secret",
            api_key_header="X-Test-Key",
            open_url=lambda *_args, **_kwargs: InvalidJsonResponse(),
        )

    assert exc_info.value.code == "ODP_METADATA_JSON_INVALID"


def test_fetch_rejects_oversized_response() -> None:
    class LargeResponse:
        def read(self, size: int = -1) -> bytes:
            assert size == MAX_METADATA_BYTES + 1
            return b"x" * (MAX_METADATA_BYTES + 1)

        def close(self) -> None:
            pass

    with pytest.raises(MetadataFetchError) as exc_info:
        fetch_product_metadata(
            domain="assignment",
            api_key="secret",
            api_key_header="X-Test-Key",
            open_url=lambda *_args, **_kwargs: LargeResponse(),
        )

    assert exc_info.value.code == "ODP_METADATA_RESPONSE_TOO_LARGE"


def test_fetched_assignment_metadata_preserves_existing_authoritative_date_policy() -> None:
    metadata = {
        "productIdentifier": "trtdxfag",
        "files": [{"fileName": "asb260809.zip", "fileDate": "2026-08-09"}],
    }
    report = fetch_product_metadata(
        domain="assignment",
        api_key="secret",
        api_key_header="X-Test-Key",
        open_url=lambda *_args, **_kwargs: FakeResponse(metadata),
    )

    preflight = evaluate_metadata(
        domain="assignment",
        metadata=report["metadata"],
        expected_file_names=["asb260809.zip"],
    )

    assert preflight["status"] == "READY"
    assert preflight["plan"][0]["effective_date"] == "2026-08-09"
    assert preflight["effective_date_inferred_from_filename"] is False


def test_fetched_ttab_date_only_metadata_remains_not_ready() -> None:
    metadata = {
        "productIdentifier": "ttabtdxf",
        "files": [{"fileName": "tt260809.zip", "fileDate": "2026-08-09"}],
    }
    report = fetch_product_metadata(
        domain="ttab",
        api_key="secret",
        api_key_header="X-Test-Key",
        open_url=lambda *_args, **_kwargs: FakeResponse(metadata),
    )

    preflight = evaluate_metadata(
        domain="ttab",
        metadata=report["metadata"],
        expected_file_names=["tt260809.zip"],
    )

    assert preflight["status"] == "NOT_READY"
    assert preflight["issues"][0]["type"] == "AUTHORITATIVE_TIMESTAMP_PRECISION_MISSING"
    assert preflight["timestamp_midnight_manufactured_from_date"] is False
