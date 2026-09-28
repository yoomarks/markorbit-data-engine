# CN Hot exact-key read-only route canary (#843)

The operator scripts/run_cn_hot_c2b_exact_read_canary.py is a separately
governed follow-up to the merged-main plan-only receipt:

D:\yoomarks\governed-plans\843\cn-hot-c2b-next-plans-r3.json
SHA-256: 69052db6e09b11eba457aabb4bee9d89f4506659621103bf069d1a98860dc1ba.

It compares the actual CN preliminary-publication discovery page for two
distinct application-number strings: 10002014 and "10002014" with literal
quote characters. Every request is one bounded key interval, page size 1,
no cursor. It reuses the existing production route query builder, result
normalization, snapshot and provenance contract; no custom semantics or
query fallback is permitted.

Before any query, it verifies the exact plan and route/epoch/planner file
hashes, runs the independent six-table and route-dependency read-only audit,
requires stopped API writers and zero source/target six-table merges, and
matches the live PostgreSQL CN serving epoch to the frozen plan. Each source
and target page independently checks the serving epoch before and after;
the canary also checks the epoch between backend requests.

The ClickHouse client is read-only, limited to one SELECT from the accepted
cn_case_current table with FINAL, the exact route LIMIT 2, one thread,
32768 rows, 32 MiB bytes, 10 seconds, 256 MiB query memory and at most two
JSONCompact result rows. The adapter converts UInt64 JSON strings to Python
integers so the existing route normalization remains unchanged. Any SQL,
result, read statistics, epoch, cursor, candidate, source reference,
snapshot or provenance mismatch fails closed with no accepted receipt.

The --preflight-only mode performs no target FINAL data reads and takes
no authority token. Actual read-only paired FINAL probes require the exact
separately reviewed authorization token for the frozen plan. After success,
a single immutable receipt is written; it proves only those two small
point-range samples and explicitly does not authorize route activation,
API/worker startup, write cutover, merge execution or source reclamation.

Do not turn the narrow two-sample result into global CN API serving
acceptance. Additional representative samples, load/performance evidence
and an approved bounded routing/rollback contract remain separate gates.

## Real route SQL preflight guard

The first authorized attempt failed closed before its first ClickHouse query:
the route SQL renders `SELECT` followed by a newline, whereas the initial
canary guard matched only `SELECT ` with an immediate space. No data read
or success receipt resulted. The corrected validator accepts SQL whitespace
while still requiring exactly one SELECT, the exact accepted FINAL table,
route LIMIT 2 and strict resource settings. `--preflight-only` now renders
both real request variants through the same SQL guard before reporting PASS.
A change to the operator must pass code review and CI before execution;
this fix does not expand the accepted plan or authorize serving cutover.
