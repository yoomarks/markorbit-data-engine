# M1.3 Retry Queue Patch

- Retry endpoint now selects only `FAILED` packages.
- Normal ingestion selects only `REGISTERED` packages.
- Missing source files are marked `MISSING_FILE` once and no longer block later packages.
- Missing files are reported as `skipped_missing`, not as failed ingestion attempts.
- Retry metrics now remain internally consistent.
