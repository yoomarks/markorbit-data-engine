# M1.4 Historical Date Patch

- Migrates CN legal-date columns from `Nullable(Date)` to `Nullable(Date32)`.
- Supports historical filing and registration dates before 1970.
- Applies the migration automatically on API startup, worker startup, and before CN ingestion.
- Invalid or impossible dates outside the Date32 range are normalized to null rather than crashing a package.
- Adds `scripts/check-cn-counts.ps1` to avoid PowerShell quoting problems when checking ClickHouse counts.
