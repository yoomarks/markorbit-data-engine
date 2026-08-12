# Universal Contact Ingestion V1

`CONTACT_INGEST_V1` is the Data Engine ingestion boundary for externally collected organization, person, and contact-channel data. It is deliberately separate from marketing execution and does not change CN/US trademark fact semantics.

## Scope

V1.3 accepts structured `.xlsx`, `.csv`, `.tsv`, `.json`, `.jsonl`, `.ndjson`, `.txt`, `.html`, `.htm`, `.pdf`, `.docx`, `.doc`, and `.zip` inputs. It automatically locates a likely header row, maps known Chinese/English field names, classifies the source profile, normalizes contact values, and produces a deterministic import plan. Dry-run is the default.

Document readers preserve structure where possible:

- TXT accepts delimited tables, JSON-like records, whitespace tables, and `field: value` contact cards;
- HTML reads native `<table>` content and falls back to visible structured text;
- PDF extracts native text/tables; image-only/scanned PDFs are rejected with an explicit OCR-required message rather than silently producing empty contacts;
- DOCX reads native Word tables and structured paragraphs;
- legacy DOC uses the `antiword` runtime extractor inside the API container;
- ZIP members may contain any supported non-ZIP format above.

Source profiles currently include:

- `QCC_COMPANY_EXPORT`
- `AGENT_CONTACT_LIST`
- `GENERIC_CONTACT_TABLE`
- `GENERIC_ENTITY_TABLE`

Unsupported or low-confidence tables are not silently applied.

## Data model

V1 reuses the existing `entity.entity` / `entity.entity_mention` identity hub instead of creating a parallel company table. It adds:

- `entity.entity_identifier` for external stable identifiers such as CN unified social credit codes;
- `contact.person` for named people;
- `contact.entity_person_relation` for legal representative, attorney, contact-person, and similar relations;
- `contact.channel` for owner-scoped phone, email, website and WhatsApp values;
- `contact.channel_observation` for source evidence each time a channel is collected;
- `contact.source`, `contact.import_run`, and `contact.raw_record` for provenance and idempotent import evidence;
- `contact.v_marketing_contacts` as a read-only projection joining contacts to resolved trademark mentions.

A channel belongs to exactly one entity or one person. Repeated collection of the same normalized channel does not duplicate the channel; source observations are preserved independently.

## Source semantics

QCC-style exports treat legal representative names as entity-person relations only. Company phone/email/website columns do **not** become personal contact details merely because a legal representative appears in the same row.

Agent contact lists can attach phone/email/WhatsApp to an explicit contact person. Firm websites remain entity-owned.

## Trademark linking

The importer may reuse an existing Entity Hub organization by stable identifier, exact name+address, or a unique exact normalized name. If no entity exists it creates a deterministic candidate. Only unresolved `entity.entity_mention` rows can be auto-linked; existing trademark links are never reassigned. Ambiguous duplicate-name candidates are not automatically linked to trademark mentions.

## Operator commands

Dry-run / preview:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\import-contacts.ps1 `
  -File "D:\data\contacts.xlsx"
```

Apply to PostgreSQL:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\import-contacts.ps1 `
  -File "D:\data\contacts.xlsx" `
  -Apply
```

The wrapper uses a one-shot worker container with `--no-deps`. It never starts the persistent worker. Apply requires PostgreSQL to already be running.

## Explicit non-goals

V1 does not send email, WhatsApp, SMS, or other marketing messages; it does not store campaign/open/reply/opt-out state; it does not infer UBO/control relationships; and it does not overwrite authoritative trademark facts.
