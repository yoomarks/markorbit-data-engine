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

## Country scaffold

`TRADEMARK_COUNTRY_SCAFFOLD_V3` generates `acquisition.py` in addition to country declaration,
parser adapter, mapping, schema, preflight, runtime, current projection, assets, acceptance and
fixture guidance.

The generated acquisition adapter remains `NotImplementedError`. Finding an API endpoint is not
enough to enable it: the developer must verify authentication, pagination, rate-limit behavior,
response format, stable source identity, update semantics and representative samples first.

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

## V1 scope

V1 provides the durable page/cursor/materialization primitive and a deterministic fake-API
regression fixture. It intentionally does not ship a generic live HTTP client, guess endpoint or
pagination semantics for unprofiled offices, or start real acquisition for any jurisdiction.

A live authority adapter should be added only after its official source contract is known. The next
reusable layer can add transport helpers (HTTP retry/rate-limit, cursor/page-number/offset helpers,
SFTP/download helpers) when concrete source implementations demonstrate the common requirements.
