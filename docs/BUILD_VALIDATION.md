# M1.5 Build Validation

Build date: 2026-08-07

Completed in the artifact assembly environment:

- Python full syntax compilation: passed;
- 26 automated tests: passed;
- permanent schema/INSERT column-count contract: passed;
- staging row/schema contract: passed;
- real ZIP member and first-100-row smoke test for every recognized member in
  `2000.zip` and `2023_1.zip`: passed;
- full `2000.zip` goods status profile: 1,481,373 rows parsed; numeric codes
  remain unmapped.

The assembly environment does not expose the user's Docker daemon. Therefore,
ClickHouse 24.8 live SQL execution and full multi-million-row database replay
remain the required local acceptance gate. Use `scripts/reset-m15.ps1`, then
import the packages one by one.
