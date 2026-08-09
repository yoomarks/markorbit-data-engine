# US Source Staging

Status: EXPLICIT FILE-ONLY MUTATION AFTER SOURCE PREFLIGHT

The US source preflight can identify authoritative replay sources that exist only under `raw_data/archive/us`. The normal package discovery flow scans `raw_data/incoming/us`, so a clean rebuild may need those archive-only sources copied back into incoming before registration.

This staging tool performs only that copy. It does not register packages, apply schema, ingest XML, retry failures, or touch PostgreSQL/ClickHouse.

## Dry run first

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\stage-us-replay-sources.ps1 -ExpectedHistoryParts <N>
```

Dry run is the default. It recomputes the source preflight and reports archive-selected sources that require staging.

For deeper ZIP/XML verification during planning:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\stage-us-replay-sources.ps1 -ExpectedHistoryParts <N> -DeepSourceTest
```

## Apply

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\stage-us-replay-sources.ps1 -ExpectedHistoryParts <N> -DeepSourceTest -Apply
```

`-Apply` is mandatory for mutation. The wrapper refuses to run while the persistent worker is active.

## Safety rules

Before any copy, staging requires source preflight `safe_to_replay=true`. This means historical `01..N` completeness is pinned, semantic partitions do not conflict by SHA, source containers are readable, and no daily package violates the historical baseline precedence boundary.

For each archive-only source:

1. Recover the canonical modeled USPTO filename. Archive collision suffixes such as `_deadbeef` are stripped only when the resulting name is a valid modeled package name.
2. Re-hash the archive source immediately before copying and compare it with the preflight SHA-256.
3. Open the destination in exclusive-create mode. Existing files are never overwritten.
4. Stream-copy the source while calculating SHA-256, flush and `fsync`, then compare the destination digest with the expected source digest.
5. Remove a partial destination if the copy raises an ordinary exception or the digest is wrong.
6. Re-run the source preflight after staging. The postflight must remain safe and must report zero remaining archive-selected replay sources.

If the process is forcibly killed at the operating-system level during a copy, an incomplete canonical destination can remain. The next source preflight will treat that file as a conflicting/corrupt source and block replay rather than silently accepting it. Remove the incomplete destination only after comparing it with the authoritative archive source.

## Idempotence

After a successful stage, incoming and archive contain identical semantic copies. The source preflight prefers incoming and treats the duplicate as a non-blocking identical-copy warning. Running staging again is therefore a `NOOP`.

If an incoming destination exists with a different SHA, source preflight blocks before staging. The staging tool also independently refuses to overwrite any destination that appears after planning.

## Status

- `READY`: dry run found one or more verified archive-only replay sources that need copying.
- `NOOP`: all authoritative replay sources are already available in incoming; no mutation is needed.
- `BLOCKED`: the source preflight is unsafe, canonical name recovery failed, or a destination conflict exists.
- `APPLIED`: explicit apply copied the required files, verified their SHA-256, and postflight confirmed the replay source set remains safe.

## Boundary

Staging prepares files only. A later replay executor may register and ingest the now-complete incoming source set, but that is intentionally a separate operation and must preserve package ordering, failure barriers, and full-package replay semantics.
