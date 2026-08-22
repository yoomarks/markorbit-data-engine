from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.trademark_framework.acquisition import (
    AcquisitionPageRequest,
    AcquisitionStatus,
    materialize_acquisition,
)
from app.trademark_framework.http_acquisition import (
    HTTP_ACQUISITION_ADAPTER_VERSION,
    HasMoreContinuation,
    HttpPageInterpretation,
    HttpPaginatedAcquisitionAdapter,
    SourceCursorContinuation,
)
from app.trademark_framework.http_transport import (
    HttpRequestSpec,
    RawHttpResponse,
    ResilientHttpTransport,
)
from app.trademark_framework.pagination import OpaqueCursorPagination, PageNumberPagination


@dataclass
class FakeAuthorityBackend:
    calls: list[HttpRequestSpec] = field(default_factory=list)

    def request(self, spec: HttpRequestSpec) -> RawHttpResponse:
        self.calls.append(spec)
        query = parse_qs(urlsplit(spec.url).query)
        page = int(query["page"][0])
        payload = json.dumps(
            {
                "page": page,
                "has_more": page < 3,
                "records": [{"application_number": f"APP-{page}"}],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return RawHttpResponse(
            status_code=200,
            body=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            final_url=spec.url,
        )


@dataclass
class FakeCursorBackend:
    calls: list[HttpRequestSpec] = field(default_factory=list)

    def request(self, spec: HttpRequestSpec) -> RawHttpResponse:
        self.calls.append(spec)
        query = parse_qs(urlsplit(spec.url).query)
        cursor = query.get("cursor", [None])[0]
        if cursor is None:
            payload = {"page_key": "cursor:first", "next": "cursor-2", "records": [1]}
        else:
            payload = {"page_key": "cursor:second", "next": None, "records": [2]}
        return RawHttpResponse(
            status_code=200,
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            final_url=spec.url,
        )


def _interpret_page_number(
    _request: AcquisitionPageRequest,
    response,
) -> HttpPageInterpretation:
    payload = json.loads(response.body)
    page = int(payload["page"])
    return HttpPageInterpretation(
        page_key=f"release:page:{page}",
        continuation=HasMoreContinuation(has_more=bool(payload["has_more"])),
    )


def _interpret_cursor(
    _request: AcquisitionPageRequest,
    response,
) -> HttpPageInterpretation:
    payload = json.loads(response.body)
    return HttpPageInterpretation(
        page_key=str(payload["page_key"]),
        continuation=SourceCursorContinuation(next_cursor=payload["next"]),
    )


def main() -> int:
    page_backend = FakeAuthorityBackend()
    page_adapter = HttpPaginatedAcquisitionAdapter(
        adapter_id="FAKE_JPO_PAGE_API_V1",
        base_url="https://authority.example/trademarks?dataset=marks",
        pagination=PageNumberPagination(
            page_param="page",
            start_page=1,
            page_size_param="size",
            page_size=100,
        ),
        interpret_page=_interpret_page_number,
        transport=ResilientHttpTransport(backend=page_backend),
        headers_provider=lambda _request: {"Authorization": "Bearer runtime-secret"},
        query_provider=lambda _request: {"lang": "en"},
        max_response_bytes=1024,
    )
    assert "runtime-secret" not in repr(page_adapter)
    assert "authority.example" not in repr(page_adapter)

    with tempfile.TemporaryDirectory(prefix="http-acquisition-adapter-") as temporary:
        root = Path(temporary)
        first = materialize_acquisition(
            adapter=page_adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_PAGE_API",
            session_key="2026-08-22",
            output_root=root,
            max_pages=2,
        )
        assert first.status == AcquisitionStatus.PARTIAL
        assert first.invocation_pages == 2
        assert first.cumulative_pages == 2
        assert [page.page_key for page in first.pages] == ["release:page:1", "release:page:2"]
        assert all(page.media_type == "application/json" for page in first.pages)

        request_queries = [parse_qs(urlsplit(call.url).query) for call in page_backend.calls]
        assert [query["page"][0] for query in request_queries] == ["1", "2"]
        assert all(query["size"] == ["100"] for query in request_queries)
        assert all(query["dataset"] == ["marks"] for query in request_queries)
        assert all(query["lang"] == ["en"] for query in request_queries)
        assert all(call.headers["Authorization"] == "Bearer runtime-secret" for call in page_backend.calls)

        ledger_text = first.ledger_path.read_text(encoding="utf-8")
        assert "runtime-secret" not in ledger_text
        assert "Authorization" not in ledger_text
        assert "authority.example" not in ledger_text

        resumed = materialize_acquisition(
            adapter=page_adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_PAGE_API",
            session_key="2026-08-22",
            output_root=root,
        )
        assert resumed.status == AcquisitionStatus.COMPLETE
        assert resumed.invocation_pages == 1
        assert resumed.cumulative_pages == 3
        assert len(page_backend.calls) == 3
        assert parse_qs(urlsplit(page_backend.calls[-1].url).query)["page"] == ["3"]

        replay = materialize_acquisition(
            adapter=page_adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_PAGE_API",
            session_key="2026-08-22",
            output_root=root,
        )
        assert replay.status == AcquisitionStatus.COMPLETE
        assert replay.invocation_pages == 0
        assert len(page_backend.calls) == 3

        cursor_backend = FakeCursorBackend()
        cursor_adapter = HttpPaginatedAcquisitionAdapter(
            adapter_id="FAKE_CH_CURSOR_API_V1",
            base_url="https://authority.example/search",
            pagination=OpaqueCursorPagination(cursor_param="cursor", first_cursor=None),
            interpret_page=_interpret_cursor,
            transport=ResilientHttpTransport(backend=cursor_backend),
            max_response_bytes=1024,
        )
        cursor_result = materialize_acquisition(
            adapter=cursor_adapter,
            jurisdiction="CH",
            source_id="IPI_FAKE_CURSOR_API",
            session_key="cursor-session",
            output_root=root,
        )
        assert cursor_result.status == AcquisitionStatus.COMPLETE
        assert [page.page_key for page in cursor_result.pages] == [
            "cursor:first",
            "cursor:second",
        ]
        assert len(cursor_backend.calls) == 2
        assert "cursor" not in parse_qs(urlsplit(cursor_backend.calls[0].url).query)
        assert parse_qs(urlsplit(cursor_backend.calls[1].url).query)["cursor"] == ["cursor-2"]

    print(
        {
            "status": "PASS",
            "http_acquisition_adapter_version": HTTP_ACQUISITION_ADAPTER_VERSION,
            "page_number_partial_resume_complete": True,
            "opaque_cursor_complete": True,
            "transport_to_raw_materialization_integrated": True,
            "runtime_credentials_not_persisted": True,
            "complete_replay_no_fetch": True,
            "network_used": False,
            "database_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
