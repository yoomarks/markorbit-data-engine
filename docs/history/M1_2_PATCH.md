# M1.2 ClickHouse Compatibility and Retry Safety

- Fix ClickHouse 24.8 `FINAL` placement in aliased JOIN tables.
- Avoid the alias `current`; use `cur` with `AS cur FINAL`.
- Clear package-scoped staging rows before retry.
- Clear package-scoped partial publish rows after failure and before retry.
- Prevent goods counts and observed events from being duplicated after a failed attempt.

The failed `2000.zip` package can be retried with `scripts/retry-cn.ps1`; the raw ZIP does not need to be copied again.
