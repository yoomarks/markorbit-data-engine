# US Official Trademark Status Reference

Status: OFFICIAL REFERENCE LAYER — NOT MARKORBIT LEGAL INTERPRETATION

The durable US fact layer continues to store USPTO `status_code` and `status_date` exactly as observed in TDXF. This reference layer is separate. It can attach the USPTO's own published description/definition for a raw code at read/audit time, but it does not convert that code into a MarkOrbit legal conclusion such as `ACTIVE`, `DEAD`, `REGISTERED`, maintenance eligibility, or a deadline.

## Current official source metadata

The current XML Resources page identifies this Application TDXF status reference document:

- document: `Table1TrademarkStatusCodes_20250813.doc`
- document date encoded in the published name: `2025-08-13`
- official file endpoint: `https://data.uspto.gov/ui/datasets/products/files/TRTDXFAP/Table1TrademarkStatusCodes_20250813.doc`
- official XML Resources page: `https://www.uspto.gov/trademarks/trademark-updates-and-announcements/xml-resources`

The repository does **not** ship a transcription of the 2025 status table unless the official document contents have actually been materialized and evidence-hashed. Do not populate current mappings from memory, a third-party list, an old USPTO table, or a MarkOrbit Skill.

## Storage model

PostgreSQL uses a dedicated `reference` schema:

- `reference.us_trademark_status_reference_version`
- `reference.us_trademark_status_code`

Each imported version records:

- `reference_version`;
- authority and reference kind;
- official source document name/date/URL;
- SHA-256 of the official source document;
- SHA-256 of the normalized MarkOrbit reference payload;
- record count;
- active flag and import timestamp;
- evidence note.

Only one version can be active at a time. Code rows are keyed by `(reference_version, raw_code)`. Reference-version deletion is restricted by foreign key; the importer never replaces or deletes a prior version.

## Normalized payload contract

First materialize the official DOC and calculate its SHA-256. Then transcribe/export the official table into a JSON file under the host raw-data tree:

```text
RAW_DATA_PATH/reference/us/
```

The JSON contract is:

```json
{
  "schema": "MARKORBIT_USPTO_STATUS_REFERENCE_V1",
  "authority": "USPTO",
  "reference_kind": "TRADEMARK_STATUS_CODES",
  "reference_version": "USPTO_STATUS_CODES_20250813",
  "source": {
    "document_name": "Table1TrademarkStatusCodes_20250813.doc",
    "document_date": "2025-08-13",
    "url": "https://data.uspto.gov/ui/datasets/products/files/TRTDXFAP/Table1TrademarkStatusCodes_20250813.doc",
    "sha256": "<64-hex SHA-256 of the official DOC>",
    "evidence_note": "Transcribed directly from the official USPTO document."
  },
  "records": [
    {
      "code": "<raw numeric USPTO code>",
      "official_description": "<official text>",
      "official_definition": "<official text only if the source provides it>",
      "official_category": "<official category only if the source provides it>",
      "source_locator": "<row/section/page locator>"
    }
  ]
}
```

Rules enforced by the importer:

- source URL must be HTTPS on a USPTO domain;
- source SHA-256 must be 64 hexadecimal characters;
- status codes must be numeric strings;
- duplicate codes are rejected;
- official description cannot be blank;
- normalized payload SHA-256 is calculated deterministically;
- importing the same version with identical evidence is idempotent;
- importing the same version with a different source SHA or normalized payload is rejected;
- a newly imported version is active by default unless explicitly imported with `--no-activate`.

Do not add a category/definition that is not explicitly present in the official source. Blank is preferable to inference.

## Apply schema

The existing additive US schema command now also creates the PostgreSQL reference tables:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\apply-us-m1-schema.ps1
```

This does not import any status mappings.

## Import a normalized official payload

Place the JSON under `RAW_DATA_PATH/reference/us`, then run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\import-us-status-reference.ps1 -ReferenceFileName uspto_status_codes_20250813.json
```

To preserve it as an inactive historical version:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\import-us-status-reference.ps1 -ReferenceFileName uspto_status_codes_20250813.json -NoActivate
```

The import is transactional and takes a PostgreSQL advisory transaction lock. Activating a version deactivates the prior active version in the same transaction.

## Inventory observed raw codes

After a reference version is active:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\audit-us-status-reference.ps1
```

The report compares distinct current raw `us_case_current.status_code` values with the active official reference version and returns:

- mapped raw codes;
- unmapped raw codes and case counts;
- reference version/evidence metadata;
- explicit semantic marker `USPTO_OFFICIAL_REFERENCE_NOT_MARKORBIT_LEGAL_CONCLUSION`.

An unmapped code is evidence that the active reference payload does not cover an observed code. It must remain unmapped until official evidence is added; it is never guessed.

## CI fixture boundary

CI uses synthetic text and fake source-document SHA values to test versioning/import mechanics. Those fixture descriptions are not production USPTO mappings and are deleted after the live fixture completes.

## Next layer

Once the current official table has been materialized, hashed, transcribed, imported, and the unknown-code inventory is clean or explained, the read API can expose `official_status_reference` beside the raw case status. A later MarkOrbit interpretation layer, if built, must be a separate evidence/rule model with its own version, confidence, and source references.
