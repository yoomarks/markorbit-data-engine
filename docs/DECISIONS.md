# Frozen Decisions — M1

## Deployment

- Local Windows computer
- Docker Compose
- Raw root: `D:\yoomarks\markorbit-data-engine\raw_data`
- Delivery as versioned ZIP packages

## Storage

- PostgreSQL: control plane, deterministic entity candidates/mentions, quality issues and jobs
- ClickHouse: large CN trademark facts and observed changes
- Raw ZIP/XML: exactly one retained source copy
- DuckDB: future temporary validation only
- Legacy `D:\MarkReg`: read-only comparison source

## Ingestion

- China: user copies monthly ZIP into `raw_data/incoming/cn`
- Worker scans and imports automatically
- United States fixed-URL downloader starts after CN M1 local acceptance
- Same SHA-256 package cannot create duplicate facts
- No permanent per-run Parquet
- Top-level CSV is streamed directly from ZIP
- Staging rows have TTL and are deleted after successful publication

## CN model

- One current case row per complete application number
- One current scope row per application number + class
- Goods item details are compactly aggregated into scope rows
- A/B/AA suffixes establish a structural derived-case family link; the legal reason remains UNKNOWN without evidence
- G-prefixed Madrid designations remain CN cases; the WIPO IR number is a cross-source link
- Official facts, structural inference and inferred legal status remain separate

## Product boundary

- No non-public refusal/opposition list acquisition
- No status scraping in M1
- Future Lite private official-document analysis remains tenant-isolated
