# M1 Acceptance

1. Copy an unmodified CN ZIP into `raw_data/incoming/cn`.
2. Run `scripts/run-cn.ps1`.
3. Confirm the dashboard shows:
   - package registered
   - ingestion success
   - file roles and counts
   - repair metrics
4. Copy the same ZIP back into `incoming/cn`.
5. Confirm SHA-256 identifies it as a duplicate and no duplicate facts/events are created.
6. Query ClickHouse:
   - one current case row per application number
   - one scope row per application number + class
   - goods item detail is embedded compactly, not stored as a permanent billion-row table
7. Confirm the successful original ZIP is in `raw_data/archive/cn`.
8. Confirm malformed rows, replacement bytes and unclassified files appear in the quality tables.
