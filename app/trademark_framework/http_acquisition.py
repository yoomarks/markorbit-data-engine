from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.trademark_framework.acquisition import AcquisitionPage, AcquisitionPageRequest
from app.trademark_framework.http_transport import (
    HttpRequestSpec,
    HttpResponse,
    ResilientHttpTransport,
)
from app.trademark_framework.pagination import append_query


HTTP_ACQUISITION_ADAPTER_VERSION = "TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1"


@runtime_checkable
class QueryPagination(Protocol):
    def initial_cursor(self) -> str | None: ...

    def query_for(self, cursor: str | None) -> dict[str, str]: ...


@runtime_checkable
class AdvancingPagination(QueryPagination, Protocol):
    def advance(self, cursor: str | None, *, has_more: bool) -> str | None: ...


@dataclass(frozen=True, slots=True)
class HasMoreContinuation:
    """Continuation for page-number/offset APIs whose response declares only has-more."""

    has_more: bool


@dataclass(frozen=True, slots=True)
class SourceCursorContinuation:
    """Continuation for APIs that return the next opaque cursor directly."""

    next_cursor: str | None


PageContinuation = HasMoreContinuation | SourceCursorContinuation


@dataclass(frozen=True, slots=True)
class HttpPageInterpretation:
    """Source-specific interpretation of one successful HTTP response.

    The generic adapter deliberately does not inspect JSON/XML. The source implementation owns
    stable page identity and termination semantics, while the shared adapter owns request assembly,
    resilient transport and conversion into the raw AcquisitionPage contract.
    """

    page_key: str
    continuation: PageContinuation
    media_type: str | None = None

    def validate(self) -> None:
        if not self.page_key.strip():
            raise ValueError("HTTP acquisition page_key is required")
        if self.media_type is not None and not self.media_type.strip():
            raise ValueError("HTTP acquisition media_type must not be blank")


HeadersProvider = Callable[[AcquisitionPageRequest], Mapping[str, str]]
QueryProvider = Callable[[AcquisitionPageRequest], Mapping[str, str]]
PageInterpreter = Callable[[AcquisitionPageRequest, HttpResponse], HttpPageInterpretation]


def _no_headers(_request: AcquisitionPageRequest) -> Mapping[str, str]:
    return {}


def _no_query(_request: AcquisitionPageRequest) -> Mapping[str, str]:
    return {}


def _response_media_type(response: HttpResponse) -> str:
    content_type = response.content_type
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";", 1)[0].strip() or "application/octet-stream"


class HttpPaginatedAcquisitionAdapter:
    """Bridge common HTTP/pagination mechanics into SourceAcquisitionAdapter.

    A jurisdiction adapter supplies only source-specific query/header additions and a response
    interpreter. Authentication can be injected at request time by ``headers_provider`` and is not
    stored on the acquisition ledger by this layer.
    """

    def __init__(
        self,
        *,
        adapter_id: str,
        base_url: str,
        pagination: QueryPagination,
        interpret_page: PageInterpreter,
        transport: ResilientHttpTransport | None = None,
        headers_provider: HeadersProvider = _no_headers,
        query_provider: QueryProvider = _no_query,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 64 * 1024 * 1024,
        allow_insecure_http: bool = False,
    ) -> None:
        normalized_adapter_id = adapter_id.strip()
        if not normalized_adapter_id:
            raise ValueError("HTTP acquisition adapter_id is required")
        if not base_url.strip():
            raise ValueError("HTTP acquisition base_url is required")
        if timeout_seconds <= 0:
            raise ValueError("HTTP acquisition timeout_seconds must be positive")
        if max_response_bytes < 1:
            raise ValueError("HTTP acquisition max_response_bytes must be positive")

        self.adapter_id = normalized_adapter_id
        self._base_url = base_url
        self._pagination = pagination
        self._interpret_page = interpret_page
        self._transport = transport or ResilientHttpTransport()
        self._headers_provider = headers_provider
        self._query_provider = query_provider
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._allow_insecure_http = allow_insecure_http

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(adapter_id={self.adapter_id!r}, "
            f"pagination={type(self._pagination).__name__})"
        )

    def initial_cursor(self) -> str | None:
        return self._pagination.initial_cursor()

    def _next_cursor(
        self,
        request: AcquisitionPageRequest,
        continuation: PageContinuation,
    ) -> str | None:
        if isinstance(continuation, SourceCursorContinuation):
            if continuation.next_cursor is None:
                return None
            normalized = str(continuation.next_cursor).strip()
            return normalized or None

        if not isinstance(self._pagination, AdvancingPagination):
            raise TypeError(
                "HasMoreContinuation requires a pagination helper implementing advance()"
            )
        return self._pagination.advance(request.cursor, has_more=continuation.has_more)

    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage:
        query = dict(self._pagination.query_for(request.cursor))
        for key, value in self._query_provider(request).items():
            normalized_key = str(key).strip()
            if not normalized_key:
                raise ValueError("HTTP acquisition query parameter names must not be blank")
            query[normalized_key] = str(value)

        response = self._transport.fetch(
            HttpRequestSpec(
                url=append_query(self._base_url, query),
                headers={
                    str(key): str(value)
                    for key, value in self._headers_provider(request).items()
                },
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
                allow_insecure_http=self._allow_insecure_http,
            )
        )
        interpretation = self._interpret_page(request, response)
        interpretation.validate()
        return AcquisitionPage(
            page_key=interpretation.page_key,
            payload=response.body,
            next_cursor=self._next_cursor(request, interpretation.continuation),
            media_type=interpretation.media_type or _response_media_type(response),
        )
