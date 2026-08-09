# US Application Sample Audit M1.0

## Purpose

The U.S. application corpus is the primary trademark fact spine. Assignment and TTAB are retained as independent supplemental source domains, but corpus-scale linking and readiness work follows application ingestion.

The current sequence is:

`Application historical sample -> Application daily sample -> sample ingest/replay validation -> full historical corpus -> daily continuation -> Assignment -> TTAB`

This milestone adds only the first read-only sample gate. It does not authorize full-corpus scale-up.

## Raw storage policy

Official bulk ZIP packages remain compressed on disk.

- do not persistently extract XML into `raw_data`;
- inspect ZIP metadata in place;
- open XML members with `zipfile.ZipFile.open()`;
- parse case records with streaming `xml.etree.ElementTree.iterparse()`;
- archive the original ZIP as the immutable source artifact.

This is the same policy used by the production application ingestion path.

## Run

Historical sample:

```powershell
.\scripts\audit-us-application-sample.ps1 `
  -SourcePath "/data/raw/incoming/us/<historical-part>.zip" `
  -SourceKind HISTORICAL `
  -EffectiveDate "YYYY-MM-DD"
```

Daily sample:

```powershell
.\scripts\audit-us-application-sample.ps1 `
  -SourcePath "/data/raw/incoming/us/<daily-package>.zip" `
  -SourceKind DAILY `
  -EffectiveDate "YYYY-MM-DD"
```

The effective date is explicit metadata and is never inferred by this audit from a filename.

## Output

The audit reports:

- SHA-256;
- compressed/source byte size;
- total uncompressed XML member bytes without extracting them;
- XML member names;
- case, owner, classification, event, statement, correspondent and other official fact counts;
- selected field coverage;
- transaction dates and serial examples;
- warnings and a sample-ingest gate.

Every report freezes `scale_up_authorized=false`.

## Full-corpus gate

After a real historical application ZIP and a real daily application ZIP both pass sample validation, validate their database behavior. Only then obtain the complete historical `01..N` set, explicitly pin `ExpectedHistoryParts=N`, run source preflight, deterministic historical-first replay, daily continuation, and source-backed acceptance.
