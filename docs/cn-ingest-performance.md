# CN ingestion performance

## Scope and invariants

This change is deliberately mechanical. It does not alter goods identity, status
resolution, omission handling, lifecycle behavior, case identity, lineage, replay
ordering, validation, or any M1.6 schema meaning. Rows are produced by the same
normalizers and SQL builders in the same deterministic order.

## Instrumentation

Every successful package now records a `performance` object in the job metrics and
package profile. It contains total and staging elapsed time, parser/transform/buffer
time (staging wall time less synchronous database time), ClickHouse stage-insert time
and request count, PostgreSQL identity time/transactions/SQL executions, publication
time, processed rows and rows/sec, configured batch sizes, and process peak RSS.

Peak RSS is process-wide Linux `ru_maxrss`, so it is an intentionally conservative
approximation rather than package-exclusive allocation. Parser/transform time is also
approximate because parsing, normalization, and buffering form one streaming loop.
The stage and identity timings are measured directly around their synchronous calls.

## Baseline and comparison

The requested 1999--2002 archives and database services are not present in the Codex
execution environment (`docker` is unavailable). Consequently, elapsed-time and RSS
figures for real packages must not be invented. The table distinguishes measured
structural improvements from the production benchmark still required before merge.

| Phase | BEFORE (M1.6) | AFTER (this branch) | Result |
|---|---:|---:|---|
| ZIP/CSV parsing | not instrumented | timed within streaming parse/transform phase | production run required |
| Python transform/normalization/hash | not instrumented | timed within streaming parse/transform phase | production run required |
| ClickHouse stage batch | 20,000 rows | 50,000 rows | unchanged row order; 60% fewer full-batch requests |
| Goods stage requests, 1999 (6.09M) | 305 | 122 | 183 fewer requests (calculated) |
| Goods stage requests, 2000 (1.48M) | 74 | 30 | 44 fewer requests (calculated) |
| Goods stage requests, 2001 (2.07M) | 104 | 42 | 62 fewer requests (calculated) |
| Goods stage requests, 2002 (3.40M) | 170 | 68 | 102 fewer requests (calculated) |
| PostgreSQL identity buffer | 5,000 | 20,000 | up to 75% fewer transactions |
| PostgreSQL connections per identity flush | 2 | 1 | 50% fewer connection handshakes |
| PostgreSQL statements per flush | 3 `executemany` calls | same 3 calls | semantics unchanged |
| M1.6 publication client creation | lifecycle and legacy publishers each create one | one client shared by both | one connection setup removed |
| ClickHouse publication / `FINAL` | unchanged | elapsed time instrumented; SQL unchanged | production run required |
| Peak memory | not instrumented | process peak RSS reported | production run required |
| Total | not instrumented | elapsed, rows, and rows/sec reported | production run required |

The request counts use `ceil(rows / batch_size)` for the goods table alone. Other
tables have independent buffers, so their flush boundaries and logical data are
unchanged even though their request counts may also fall. Memory remains bounded by
one buffer per stage table plus the identity buffer.

## Why these changes were retained

The optimizations are intentionally small: constants, transaction reuse, and safe
ClickHouse client reuse. They reduce handshakes, commits, and inserts without COPY
staging tables, SQL rewrites, concurrency, or new recovery state. PostgreSQL
`executemany` remains in place because replacing its conflict behavior with a more
complex bulk staging design was not justified without a database benchmark.

No `FINAL` query was removed or rewritten. Doing so without measuring table merge
state could change the selected current rows. Publication aggregation SQL is also
unchanged to protect frozen lifecycle and lineage semantics.

## Required production benchmark before merge

Run the same isolated ClickHouse/PostgreSQL deployment twice from a clean M1.6 replay
boundary: once at the parent commit and once on this branch. Ingest identical copies
of 1999.zip, 2000.zip, 2001.zip, and 2002.zip in order. Capture each job's
`totals.performance`, ClickHouse query log duration/read rows/memory, PostgreSQL
statement statistics, output table counts, and identity/hash audit results.

Populate the measured cells above and compare summed total elapsed time. **Do not
recommend merge unless the representative large-package total is at least 30% lower.**
If it is not, revert the batch-size changes or continue profiling; do not weaken any
contract to reach the target.
