# UKIPO historical stock source staging v2 (#855)

## Verified scope and identity

This is source-evidence staging, not production Data Engine ingestion and
not a current British trademark register. The original two ZIPs stay at
F:\MarkOrbitData\raw\incoming\uk. gb_stock_snapshot_v2.py reads the
actual UTF-16 LE, BOM-prefixed, pipe-delimited text members directly from
their frozen ZIP SHA-256; it does not extract or modify source files.

Domestic (OpenDataDomestic.txt) contains 1,188,992 source records:
1,188,886 accepted source rows and 106 field-width quarantines.
Madrid-IR (OpenDataIR.txt) contains 109,695 records: 109,687 accepted
and 8 field-width quarantines. The 45 Class1-Class45 flags are
interpreted only as literal 0/1 category membership, never as goods text.

Legacy UK application IDs may have a single-letter A/B or double-letter
AA/AB suffix. The actual Domestic source includes 32 double-letter IDs.
Madrid-IR includes single-letter suffixes. Each raw ID is preserved in
full; a suffix is neither silently stripped nor conflated with the
unsuffixed case. Multiple physical owner rows for the same exact ID
are retained separately using source ZIP SHA, member and record ordinal.

Each accepted record preserves original owner name, raw source status,
all original source fields, class flags, source-cells SHA and row ordinal.
Exactly 114 malformed-width records are preserved in independent
quarantine JSONL; the tool refuses unreviewed IDs, source fields, ZIP
members, classes or data-volume/cardinality drift.

## Immutable stage receipts

The accepted files are under
D:\yoomarks\governed-plans\855\historical-stock.
Domestic manifest SHA-256:
aa7f52cfed7ff850af4e56060bd196d05010613ec2d930cc82bb1b74c4018dbe.
Madrid-IR manifest SHA-256:
a56eb8fce40241176a1a353d918a7dfb1dc4bd17ad5cdb21d3bb8bba1a7693c2.

The independent auditor re-read all 1,298,687 original source rows and
compared each accepted/quarantined JSONL record to the exact original
UTF-16 source row, verifying owner, source identity, class flags, hash,
ordinal and all duplicate mark identities. Audit receipt:
D:\yoomarks\governed-plans\855\gb-historical-stock-independent-audit-r1.json;
SHA-256:
320c40b1cea7d5593ee8dbb99dfcbc1f534b71bd682281ab42fd719873a9d0c5.
Results: 1,298,573 accepted rows, 114 quarantined rows, 1,280,258
stream-scoped distinct mark IDs and 18,315 preserved duplicate-mark rows.

## Production admission boundary

Existing trademark_gb.historical_record is keyed only by application
number and its old UTF-8 importer overwrites co-owners; never feed
these stages into that importer. A separately reviewed GB source-row
schema and resumable idempotent importer must preserve every original
row and historical-only status. UKIPO journal XHTML detail and E:
original-logo evidence is governed separately in Knowledge #910.
Neither the stage nor this PR applies SQL migrations, registers
canonical Knowledge RawArtifacts, enables APIs or asserts current
registration status. A source-backed read projection must not be
activated until the later ingestion/coverage audit is accepted.
