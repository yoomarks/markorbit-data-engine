# Legacy CN Refinery Review

## Reused

- Encoding scoring across UTF-8, GB18030, GBK, CP936, Big5 and Latin-1
- Conservative cell-level mojibake repair
- ZIP inner filename recovery when GB18030 names lack the UTF-8 flag
- Stable-tail repair for unquoted commas
- 21/22-column basic-file compatibility
- Applicant-name masked identity suffix cleanup
- Source row and repair metrics

## Replaced

- Per-run Parquet snapshots
- Permanent item-level goods table
- `application_number + class` as the primary mark identity
- Status values presented as if supplied by CNIPA
- Whole-file ZIP member reads for normal top-level CSV files

## Added

- Physical-line based logical record reconstruction
- Unbalanced quote containment within one detected record
- One case row per full application number
- One compact scope row per application number + class
- PostgreSQL entity mentions before canonical global entity resolution
- ClickHouse temporary staging with TTL
- Official observed facts separated from future inference
