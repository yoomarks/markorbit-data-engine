# USPTO ODP Authoritative Metadata Acquisition

## Scope

This workflow acquires authoritative USPTO Open Data Portal (ODP) Product Data metadata for the frozen US Assignment and US TTAB bulk datasets.

It does **not** download trademark source packages, infer package chronology from filenames, mutate PostgreSQL/ClickHouse, start ingestion/replay, or manufacture TTAB timestamps.

Authoritative dataset identities remain frozen as:

| Domain | ODP dataset | Federal catalog identifier |
| --- | --- | --- |
| Assignment | `trtdxfag` | `EIP-5903T-OL` |
| TTAB | `ttabtdxf` | `EIP-5904T-OL` |

Official references:

- USPTO ODP Bulk Data API: `https://data.uspto.gov/apis/bulk-data/search`
- USPTO ODP Product Data documentation: `https://data.uspto.gov/apis/bulk-data/product`
- Assignment dataset catalog: `https://catalog.data.gov/dataset/trademark-assignment-xml-1955-present`
- TTAB dataset catalog: `https://catalog.data.gov/dataset/trademark-trial-and-appeal-board-ttab-xml-1951-present`

## Credential configuration

ODP API calls require an API key. Keep the key only in local/secret environment configuration and never commit it.

Add to local `.env`:

```text
USPTO_ODP_API_KEY=<issued ODP API key>
USPTO_ODP_API_KEY_HEADER=<header name documented for that issued key>
```

The header name is intentionally explicit. The Data Engine fetcher does not guess an authentication header when it is not configured.

The key is supplied to the ephemeral worker container through the existing Compose environment. It is not placed in a command-line argument, metadata file, fetch report, URL, or log message.

## Step 1 — Fetch authoritative Product Data metadata

Assignment:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\fetch-uspto-odp-bulk-metadata.ps1 `
  -Domain assignment
```

TTAB:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\fetch-uspto-odp-bulk-metadata.ps1 `
  -Domain ttab
```

The script writes two evidence files under `reports/` by default:

- authoritative metadata JSON used by the existing metadata preflight;
- a fetch report containing dataset identity, endpoint, response byte count and response SHA-256, but no API key and no duplicated metadata payload.

Files are written through a temporary file and atomically moved/replaced at the destination.

The fetcher fails closed when:

- the domain is not one of the two frozen datasets;
- the API key or explicit key-header configuration is missing;
- the Product Data request fails;
- the response is not valid UTF-8 JSON;
- the response exceeds the bounded metadata size;
- the response does not expose a product identifier;
- the response product identifier does not match the requested frozen dataset.

## Step 2 — Validate dates/timestamps against the actual source filenames

Fetching metadata does not make the corpus ready. Match the saved metadata to the exact source files that are actually present:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\preflight-uspto-odp-bulk-metadata.ps1 `
  -Domain assignment `
  -MetadataPath .\reports\uspto_odp_assignment_metadata_<timestamp>.json `
  -ExpectedFileName <exact-file-1>,<exact-file-2>
```

or:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\preflight-uspto-odp-bulk-metadata.ps1 `
  -Domain ttab `
  -MetadataPath .\reports\uspto_odp_ttab_metadata_<timestamp>.json `
  -ExpectedFileName <exact-file-1>,<exact-file-2>
```

Frozen chronology policy remains unchanged:

- Assignment may use an explicit authoritative ODP date as `effective_date`.
- Assignment dates are never parsed from filenames.
- TTAB requires an explicit authoritative timezone-aware timestamp as `snapshot_at`.
- TTAB date-only metadata remains `NOT_READY`.
- A date is never promoted to manufactured midnight.

## Step 3 — Build the formal corpus manifest

Only after the metadata preflight is ready, provide the existing explicit source-kind specification and build the manifest:

```powershell
powershell.exe -ExecutionPolicy Bypass -File `
  .\scripts\build-uspto-odp-corpus-manifest.ps1 `
  -Domain assignment `
  -MetadataPath .\reports\uspto_odp_assignment_metadata_<timestamp>.json `
  -SourceSpecPath <explicit-source-spec.json>
```

Use `-Apply -ManifestOutputPath ...` only when the dry-run report is ready.

The source specification remains operator-explicit. Historical/daily source kind is not inferred from the filename by the fetcher, preflight, or manifest builder.

## Operational boundary

Metadata acquisition is safe to perform independently of live trademark replay because it performs a remote metadata GET and writes only local evidence JSON under the requested output path.

It does not:

- reset or migrate a database;
- scan or register raw source packages;
- move incoming/archive files;
- start or continue CN, US Application, Assignment, or TTAB ingestion;
- alter package status;
- create a corpus manifest automatically;
- change Assignment legal-title semantics;
- change TTAB procedural-fact semantics.
