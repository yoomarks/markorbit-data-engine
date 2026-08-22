from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


HTTP_TRANSPORT_VERSION = "TRADEMARK_HTTP_TRANSPORT_V1"
_DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)
_DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _normalized_headers(headers: Mapping[str, str] | HTTPMessage) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _validate_url(url: str, *, allow_insecure_http: bool) -> None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        raise ValueError("HTTP transport requires an absolute URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("HTTP transport forbids credentials embedded in URLs")
    allowed_schemes = {"https"}
    if allow_insecure_http:
        allowed_schemes.add("http")
    if parts.scheme.lower() not in allowed_schemes:
        raise ValueError("HTTP transport requires HTTPS unless allow_insecure_http is explicit")


@dataclass(frozen=True, slots=True)
class HttpRequestSpec:
    url: str
    method: str = "GET"
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    timeout_seconds: float = 30.0
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    allow_insecure_http: bool = False

    def validate(self) -> None:
        _validate_url(self.url, allow_insecure_http=self.allow_insecure_http)
        method = self.method.strip().upper()
        if method not in {"GET", "HEAD"}:
            raise ValueError("HTTP transport V1 supports only read-only GET/HEAD requests")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        for key, value in self.headers.items():
            if not str(key).strip():
                raise ValueError("HTTP header names must not be blank")
            if "\r" in str(key) or "\n" in str(key) or "\r" in str(value) or "\n" in str(value):
                raise ValueError("HTTP headers must not contain CR/LF characters")


@dataclass(frozen=True, slots=True)
class HttpRetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    retry_statuses: tuple[int, ...] = _DEFAULT_RETRY_STATUSES
    respect_retry_after: bool = True

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be non-negative")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be non-negative")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base_delay_seconds must not exceed max_delay_seconds")
        if any(status < 100 or status > 599 for status in self.retry_statuses):
            raise ValueError("retry_statuses must contain valid HTTP status codes")


@dataclass(frozen=True, slots=True)
class RawHttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    final_url: str = ""


class HttpBackend(Protocol):
    def request(self, spec: HttpRequestSpec) -> RawHttpResponse: ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes
    safe_final_url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.strip().lower())

    @property
    def content_type(self) -> str | None:
        return self.header("content-type")

    @property
    def etag(self) -> str | None:
        return self.header("etag")

    @property
    def last_modified(self) -> str | None:
        return self.header("last-modified")


class HttpTransportError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        safe_url: str,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        self.reason = reason
        self.safe_url = safe_url
        self.attempts = attempts
        self.status_code = status_code
        status = f" status={status_code}" if status_code is not None else ""
        super().__init__(f"HTTP transport failed: {reason}; url={safe_url}; attempts={attempts}{status}")


class UrllibHttpBackend:
    """Minimal stdlib backend; retry policy stays in ResilientHttpTransport."""

    def __init__(self, opener: Callable[[Request, float], object] | None = None) -> None:
        self._opener = opener

    def _open(self, request: Request, timeout_seconds: float):
        if self._opener is not None:
            return self._opener(request, timeout_seconds)
        return urlopen(request, timeout=timeout_seconds)  # noqa: S310 - URL validated above

    @staticmethod
    def _read_bounded(response, *, max_response_bytes: int) -> bytes:
        headers = _normalized_headers(response.headers)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_response_bytes:
                    raise HttpTransportError(
                        reason="response content-length exceeds configured limit",
                        safe_url=_safe_url(response.geturl()),
                        attempts=1,
                        status_code=int(response.getcode()),
                    )
            except ValueError:
                pass
        payload = response.read(max_response_bytes + 1)
        if len(payload) > max_response_bytes:
            raise HttpTransportError(
                reason="response body exceeds configured limit",
                safe_url=_safe_url(response.geturl()),
                attempts=1,
                status_code=int(response.getcode()),
            )
        return payload

    def request(self, spec: HttpRequestSpec) -> RawHttpResponse:
        spec.validate()
        request = Request(
            spec.url,
            headers={str(key): str(value) for key, value in spec.headers.items()},
            method=spec.method.strip().upper(),
        )
        try:
            response = self._open(request, spec.timeout_seconds)
        except HTTPError as exc:
            return RawHttpResponse(
                status_code=int(exc.code),
                body=b"",
                headers=_normalized_headers(exc.headers or {}),
                final_url=exc.geturl() or spec.url,
            )

        with response:
            final_url = response.geturl() or spec.url
            _validate_url(final_url, allow_insecure_http=spec.allow_insecure_http)
            status_code = int(response.getcode())
            headers = _normalized_headers(response.headers)
            body = b"" if spec.method.strip().upper() == "HEAD" else self._read_bounded(
                response,
                max_response_bytes=spec.max_response_bytes,
            )
        return RawHttpResponse(
            status_code=status_code,
            body=body,
            headers=headers,
            final_url=final_url,
        )


def _retry_after_seconds(
    value: str | None,
    *,
    now: Callable[[], datetime],
    max_delay_seconds: float,
) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        seconds = float(stripped)
    except ValueError:
        try:
            target = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        seconds = (target - current).total_seconds()
    return min(max(seconds, 0.0), max_delay_seconds)


def _exponential_delay(policy: HttpRetryPolicy, *, attempt: int) -> float:
    return min(policy.base_delay_seconds * (2 ** max(attempt - 1, 0)), policy.max_delay_seconds)


class ResilientHttpTransport:
    """Read-only HTTP transport with bounded retry/rate-limit behavior.

    Credentials may be supplied in HttpRequestSpec.headers by a source-specific adapter, but this
    transport never serializes them, includes them in repr(), or places header values in errors.
    """

    def __init__(
        self,
        *,
        backend: HttpBackend | None = None,
        retry_policy: HttpRetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._backend = backend or UrllibHttpBackend()
        self._retry_policy = retry_policy or HttpRetryPolicy()
        self._sleep = sleep
        self._now = now
        self._retry_policy.validate()

    def _delay_for(self, response: RawHttpResponse, *, attempt: int) -> float:
        if self._retry_policy.respect_retry_after:
            retry_after = _retry_after_seconds(
                _normalized_headers(response.headers).get("retry-after"),
                now=self._now,
                max_delay_seconds=self._retry_policy.max_delay_seconds,
            )
            if retry_after is not None:
                return retry_after
        return _exponential_delay(self._retry_policy, attempt=attempt)

    def fetch(self, spec: HttpRequestSpec) -> HttpResponse:
        spec.validate()
        safe_url = _safe_url(spec.url)
        last_network_reason = "network error"

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                response = self._backend.request(spec)
            except HttpTransportError:
                raise
            except (URLError, TimeoutError, ConnectionError) as exc:
                last_network_reason = type(exc).__name__
                if attempt >= self._retry_policy.max_attempts:
                    raise HttpTransportError(
                        reason=last_network_reason,
                        safe_url=safe_url,
                        attempts=attempt,
                    ) from exc
                delay = _exponential_delay(self._retry_policy, attempt=attempt)
                if delay > 0:
                    self._sleep(delay)
                continue

            final_url = response.final_url or spec.url
            _validate_url(final_url, allow_insecure_http=spec.allow_insecure_http)
            status_code = int(response.status_code)
            headers = _normalized_headers(response.headers)
            if 200 <= status_code < 300:
                if len(response.body) > spec.max_response_bytes:
                    raise HttpTransportError(
                        reason="response body exceeds configured limit",
                        safe_url=_safe_url(final_url),
                        attempts=attempt,
                        status_code=status_code,
                    )
                return HttpResponse(
                    status_code=status_code,
                    body=response.body,
                    safe_final_url=_safe_url(final_url),
                    headers=headers,
                )

            retryable = status_code in self._retry_policy.retry_statuses
            if not retryable or attempt >= self._retry_policy.max_attempts:
                raise HttpTransportError(
                    reason="retryable HTTP status exhausted" if retryable else "non-retryable HTTP status",
                    safe_url=_safe_url(final_url),
                    attempts=attempt,
                    status_code=status_code,
                )

            delay = self._delay_for(response, attempt=attempt)
            if delay > 0:
                self._sleep(delay)

        raise HttpTransportError(
            reason=last_network_reason,
            safe_url=safe_url,
            attempts=self._retry_policy.max_attempts,
        )
