from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.trademark_framework.http_transport import (
    HTTP_TRANSPORT_VERSION,
    HttpRequestSpec,
    HttpRetryPolicy,
    HttpTransportError,
    RawHttpResponse,
    ResilientHttpTransport,
)
from app.trademark_framework.pagination import (
    PAGINATION_HELPER_VERSION,
    OffsetLimitPagination,
    OpaqueCursorPagination,
    PageNumberPagination,
    append_query,
)


@dataclass
class SequenceBackend:
    responses: list[RawHttpResponse | BaseException]
    calls: list[HttpRequestSpec] = field(default_factory=list)

    def request(self, spec: HttpRequestSpec) -> RawHttpResponse:
        self.calls.append(spec)
        item = self.responses[len(self.calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


def _response(status: int, *, body: bytes = b"", headers: dict[str, str] | None = None):
    return RawHttpResponse(
        status_code=status,
        body=body,
        headers=headers or {},
        final_url="https://authority.example/api?server_token=hidden",
    )


def main() -> int:
    sleeps: list[float] = []
    backend = SequenceBackend(
        responses=[
            _response(429, headers={"Retry-After": "3"}),
            _response(503),
            _response(200, body=b'{"records":[1]}', headers={"ETag": '"abc"'}),
        ]
    )
    transport = ResilientHttpTransport(
        backend=backend,
        retry_policy=HttpRetryPolicy(
            max_attempts=4,
            base_delay_seconds=1,
            max_delay_seconds=10,
        ),
        sleep=sleeps.append,
        now=lambda: datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc),
    )
    spec = HttpRequestSpec(
        url="https://authority.example/api?api_key=super-secret",
        headers={"Authorization": "Bearer very-secret"},
        timeout_seconds=5,
        max_response_bytes=1024,
    )
    success = transport.fetch(spec)
    assert success.status_code == 200
    assert success.body == b'{"records":[1]}'
    assert success.etag == '"abc"'
    assert success.safe_final_url == "https://authority.example/api"
    assert sleeps == [3.0, 2.0]
    assert len(backend.calls) == 3
    assert "very-secret" not in repr(spec)

    non_retry_backend = SequenceBackend(responses=[_response(404)])
    try:
        ResilientHttpTransport(
            backend=non_retry_backend,
            retry_policy=HttpRetryPolicy(max_attempts=5),
            sleep=lambda _seconds: None,
        ).fetch(spec)
        raise AssertionError("404 should fail closed")
    except HttpTransportError as exc:
        assert exc.status_code == 404
        assert exc.attempts == 1
        assert "api_key" not in str(exc)
        assert "super-secret" not in str(exc)
    assert len(non_retry_backend.calls) == 1

    timeout_sleeps: list[float] = []
    timeout_backend = SequenceBackend(
        responses=[TimeoutError("simulated timeout"), _response(200, body=b"ok")]
    )
    timeout_result = ResilientHttpTransport(
        backend=timeout_backend,
        retry_policy=HttpRetryPolicy(max_attempts=2, base_delay_seconds=0.25, max_delay_seconds=1),
        sleep=timeout_sleeps.append,
    ).fetch(spec)
    assert timeout_result.body == b"ok"
    assert timeout_sleeps == [0.25]

    oversized_backend = SequenceBackend(responses=[_response(200, body=b"12345")])
    try:
        ResilientHttpTransport(
            backend=oversized_backend,
            retry_policy=HttpRetryPolicy(max_attempts=1),
        ).fetch(
            HttpRequestSpec(
                url="https://authority.example/large",
                max_response_bytes=4,
            )
        )
        raise AssertionError("oversized responses must fail closed")
    except HttpTransportError as exc:
        assert "exceeds configured limit" in str(exc)

    insecure_blocked = False
    try:
        HttpRequestSpec(url="http://authority.example/api").validate()
    except ValueError as exc:
        insecure_blocked = "HTTPS" in str(exc)
    assert insecure_blocked is True

    embedded_credentials_blocked = False
    try:
        HttpRequestSpec(url="https://user:password@authority.example/api").validate()
    except ValueError as exc:
        embedded_credentials_blocked = "embedded" in str(exc)
    assert embedded_credentials_blocked is True

    page = PageNumberPagination(page_param="page", start_page=1, page_size_param="size", page_size=50)
    assert page.initial_cursor() == "1"
    assert page.query_for("2") == {"page": "2", "size": "50"}
    assert page.advance("2", has_more=True) == "3"
    assert page.advance("2", has_more=False) is None

    offset = OffsetLimitPagination(limit=250)
    assert offset.initial_cursor() == "0"
    assert offset.query_for("500") == {"offset": "500", "limit": "250"}
    assert offset.advance("500", has_more=True) == "750"

    opaque = OpaqueCursorPagination(cursor_param="next", first_cursor=None)
    assert opaque.query_for(None) == {}
    assert opaque.query_for("abc") == {"next": "abc"}
    assert opaque.normalize_next_cursor("  xyz  ") == "xyz"
    assert opaque.normalize_next_cursor("") is None

    query_url = append_query(
        "https://authority.example/search?lang=en",
        {"page": "2", "size": "50"},
    )
    assert query_url == "https://authority.example/search?lang=en&page=2&size=50"

    print(
        {
            "status": "PASS",
            "http_transport_version": HTTP_TRANSPORT_VERSION,
            "pagination_helper_version": PAGINATION_HELPER_VERSION,
            "retry_after_respected": True,
            "exponential_retry": True,
            "non_retryable_4xx_fail_fast": True,
            "network_timeout_retry": True,
            "response_size_gate": True,
            "https_default": True,
            "embedded_url_credentials_blocked": True,
            "secret_values_not_in_error": True,
            "page_number_pagination": True,
            "offset_limit_pagination": True,
            "opaque_cursor_pagination": True,
            "network_used": False,
            "database_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
