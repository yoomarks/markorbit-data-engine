# Trademark Source Acquisition V1

## Purpose

`TRADEMARK_SOURCE_ACQUISITION_V1` is the reusable acquisition layer for trademark sources that must
be fetched before the country-native parser can run, especially paginated/cursor APIs.

It completes the intended separation:

`authority source/API -> acquisition -> immutable raw objects -> runtime preflight/parser -> country-native store`

Acquisition is deliberately separated from parsing. A remote API response should become replayable
raw evidence first; a parser should not have to contact the authority again merely because parsing,
mapping or storage logic changed.

## Shared executor

`app.trademark_framework.acquisition.materialize_acquisition()` owns the mechanics that should not
be reimplemented for every trademark office:

- ordered page execution;
- opaque cursor resume;
- bounded `max_pages` invocations;
- `PARTIAL` versus `COMPLETE` session state;
- atomic raw-object writes;
- SHA256 for every raw object;
- deterministic object sequencing;
- durable `acquisition-ledger.json` after every committed page;
- verification of all existing objects before resume/replay;
- cursor-loop and non-advancing-cursor detection;
- repeated page-key detection;
- fail-closed session identity checks;
- no network/database dependency in the shared executor itself.

The source-specific adapter has a very small contract:

```python
class SourceAcquisitionAdapter(Protocol):
    adapter_id: str

    def initial_cursor(self) -> str | None: ...
    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage: ...
```

`fetch_page()` is where the country/source implementation translates a generic cursor request into
the authority's real HTTP/SOAP/SFTP/download operation and translates the authority response back
into raw bytes plus the next source-declared cursor.

## Raw evidence boundary

Each committed page is written under:

```text
<output_root>/<jurisdiction>/<source_id>/<session_key>/
  acquisition-ledger.json
  objects/
    00000001-<sha-prefix>.raw
    00000002-<sha-prefix>.raw
    ...
```

The ledger records logical page key, sequence, relative object key, SHA256, byte size, media type,
request cursor and next cursor. It intentionally does not accept authentication headers, API keys or
password fields.

A later replay of a `COMPLETE` session performs no source fetch. It verifies the materialized raw
objects against the ledger and returns the existing evidence. If bytes were altered, replay/resume
fails closed.

## Bounded acquisition and resume

Large APIs can be acquired in bounded invocations:

```python
result = materialize_acquisition(
    adapter=adapter,
    jurisdiction="XX",
    source_id="OFFICIAL_API",
    session_key="2026-08-22",
    output_root=raw_root,
    max_pages=100,
)
```

If the source still has a `next_cursor`, the ledger remains `PARTIAL`. A later invocation with the
same source/session verifies already materialized bytes and continues from the exact saved cursor.

This is acquisition resume, not ingestion resume. After acquisition, the existing Global Trademark
source-object/manifest/ingest-run layer still owns parser/storage replay and release acceptance.
The two checkpoints are intentionally separate because a network fetch and a database ingest fail in
different ways.

## Security boundary

Credentials are runtime/transport configuration, not provenance.

An acquisition adapter may obtain API keys, OAuth tokens, cookies, certificates or SFTP credentials
from the approved runtime secret mechanism, but it must not place those values into:

- `page_key`;
- pagination cursor values when the value is actually an authentication token;
- source metadata intended for persistence;
- `acquisition-ledger.json`;
- raw-object filenames.

If an authority conflates pagination state with a sensitive credential, the country adapter must
persist a non-secret resumable position instead of handing the credential to the generic ledger.

## Shared HTTP transport

`TRADEMARK_HTTP_TRANSPORT_V1` supplies the common read-only HTTP mechanics that concrete authority
adapters would otherwise repeat. It is deliberately transport-only: endpoint paths, authentication,
response parsing, record identity and next-page semantics remain source-specific.

The shared transport provides:

- HTTPS by default; insecure HTTP requires an explicit per-request opt-in;
- read-only `GET`/`HEAD` methods only in V1;
- configurable per-request timeout;
- configurable maximum response size with both `Content-Length` and actual-byte enforcement;
- retry for `429`, `500`, `502`, `503` and `504` by default;
- bounded exponential retry for transient network failures;
- `Retry-After` support for both seconds and HTTP-date values;
- configurable attempt/delay caps;
- fail-fast behavior for non-retryable 4xx responses;
- query-string redaction from transport errors and final-URL metadata;
- request URL/header values excluded from dataclass `repr()` where credentials could appear;
- rejection of credentials embedded in URL userinfo and CR/LF header injection.

## Pagination helpers

`TRADEMARK_API_PAGINATION_V1` provides small deterministic query helpers for three common API shapes:

- `PageNumberPagination` for `page=1`, `page=2`, ... style APIs;
- `OffsetLimitPagination` for `offset=0&limit=N` style APIs;
- `OpaqueCursorPagination` for source-declared opaque next-cursor APIs;
- `append_query()` for deterministic query merging.

The helpers construct/advance request positions only. They do **not** guess whether more pages exist.
A country adapter must derive `has_more` or the next opaque cursor from the authority's documented
response contract. This avoids silently imposing one office's pagination semantics on another.

## Paginated HTTP acquisition bridge

`TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` connects the two reusable layers above directly into the
raw-evidence executor. A normal HTTP API country adapter no longer needs to write its own request
loop, retry loop, page advance, raw-byte conversion or acquisition-resume plumbing.

The jurisdiction-specific implementation supplies only:

- the verified base endpoint;
- a pagination helper matching the authority contract;
- optional runtime header/query providers;
- a response interpreter that returns a stable `page_key` and either:
  - `HasMoreContinuation(has_more=...)`, or
  - `SourceCursorContinuation(next_cursor=...)`.

Example shape:

```python
adapter = HttpPaginatedAcquisitionAdapter(
    adapter_id="OFFICE_API_V1",
    base_url=official_endpoint,
    pagination=PageNumberPagination(page_param="page", page_size_param="size", page_size=100),
    headers_provider=lambda request: {"Authorization": runtime_token()},
    interpret_page=interpret_official_response,
)

materialize_acquisition(
    adapter=adapter,
    jurisdiction="XX",
    source_id="OFFICIAL_API",
    session_key=release_key,
    output_root=raw_root,
    max_pages=100,
)
```

The interpreter remains source-native. The generic adapter never opens JSON/XML to decide whether a
page is complete, never invents a next cursor, and never decides legal/current-state semantics. It
only converts verified source-specific interpretation into the standard `AcquisitionPage` contract.

Runtime headers are injected at request time and are not copied into acquisition ledger fields. The
adapter representation also omits base URL/header values so normal debugging does not expose source
credentials or endpoint query secrets.

## Country scaffold

The country scaffold keeps acquisition as a separate implementation surface in addition to country
declaration, parser adapter, mapping, schema, preflight, runtime, current projection, assets,
acceptance and fixture guidance.

Finding an API endpoint is not enough to enable a generated pack: authentication, pagination,
rate-limit behavior, response format, stable source identity, update semantics and representative
samples must still be verified. Once those are known, HTTP/S REST-style adapters should reuse
`TRADEMARK_HTTP_ACQUISITION_ADAPTER_V1` instead of recreating HTTP/pagination/acquisition mechanics.

## Acceptance boundary

Acquisition `COMPLETE` means only that the adapter reached the source-declared terminal page and all
committed raw objects match their ledger hashes.

It does **not** mean:

```text
acquisition COMPLETE
= source release accepted
= parser complete
= jurisdiction current
= trusted for silence
= legal conclusion
```

Those remain separate downstream evidence gates.

## Current scope

The acquisition stack now provides durable raw-page materialization, reusable read-only HTTP
transport, standard pagination request-position helpers, and a shared HTTP-to-acquisition bridge. CI
uses deterministic fake authority backends and does not contact any trademark authority.

It still intentionally does not guess endpoints, credentials, response schemas or pagination
termination rules for unprofiled offices, and it does not start real acquisition for any
jurisdiction. SOAP/SFTP/signed-download helpers should be added only when concrete verified sources
demonstrate reusable requirements.
