# Trademark HTTP Transport V1

## Purpose

`TRADEMARK_HTTP_TRANSPORT_V1` is the reusable read-only network transport beneath trademark source acquisition adapters.

It standardizes the mechanics that should not be reimplemented for each office while leaving endpoint shape, authentication, pagination fields, response semantics and source completeness rules in the source-specific adapter.

The intended stack is:

`official API -> source adapter -> resilient HTTP transport -> acquisition ledger/raw objects -> runtime parser -> country-native store`

## Safety defaults

V1 is intentionally narrow:

- GET and HEAD only;
- HTTPS required unless a source adapter explicitly opts into insecure HTTP;
- credentials embedded in URL user-info are rejected;
- request URL and headers are excluded from object `repr()`;
- errors expose only scheme/host/path and never query strings;
- timeout is explicit and positive;
- response body size is bounded;
- retry attempts and retry delay are bounded;
- 429, 500, 502, 503 and 504 are retryable by default;
- ordinary 4xx responses fail fast;
- `Retry-After` seconds or HTTP-date is respected but capped by the configured maximum delay.

Transport success means only that bytes were fetched. It is not source acceptance, parser success, release completeness, jurisdiction currentness or a legal conclusion.

## Retry contract

`HttpRetryPolicy` controls maximum attempts, exponential-backoff base/max delay, retryable status codes and `Retry-After` handling. Network timeouts/connection failures use the same bounded attempt envelope.

The policy deliberately has no global request-per-second default. Rate limits vary by authority; a source adapter may combine this transport with source-specific throttling once official terms are known.

## Response contract

A successful fetch returns `HttpResponse` with:

- status code;
- raw response bytes;
- lower-cased response headers;
- a query-redacted final URL;
- helpers for `Content-Type`, `ETag` and `Last-Modified`.

The raw bytes should normally be handed to `TRADEMARK_SOURCE_ACQUISITION_V1`, which materializes immutable SHA256-backed evidence before parsing.

## Pagination helpers

`TRADEMARK_API_PAGINATION_V1` supplies deterministic request-position helpers for three common patterns:

- page number;
- offset/limit;
- opaque cursor.

They do not decide whether another page exists. The source-specific adapter must derive `has_more` or the next cursor from official response semantics. This prevents generic infrastructure from guessing termination rules.

## Secret handling

Headers may contain Authorization/API-key material at runtime, and some authorities may force query credentials. The transport does not persist either and excludes both URL/header values from routine object rendering. Errors use a redacted URL with the query removed.

Acquisition ledgers still must not contain authentication material. If a source returns a pagination token that is itself credential-equivalent, the source adapter must persist a non-secret resumable position instead of handing the sensitive token to the generic acquisition ledger.

## Explicit non-goals

V1 does not:

- discover endpoints;
- guess authentication methods;
- perform OAuth token acquisition/refresh;
- infer rate limits;
- parse API response JSON/XML;
- infer total pages or source completeness;
- write to a country store;
- enable a real jurisdiction source merely because transport support exists.

A concrete office adapter remains responsible for verified source behavior and must pass bounded real-source validation before production use.
